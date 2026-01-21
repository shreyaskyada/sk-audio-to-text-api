
import os
import requests
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from app.schemas import TranscriptionResponse, TranscriptionListResponse, TranscriptionListItem, TranscriptionCreateRequest, TranscriptionUpdateRequest
from app.services.audio import get_audio_content_type
from app.services.text_processing import fix_terms
from app.services.transcription_storage import (
    save_transcription_to_db,
    get_all_transcriptions,
    get_transcription_by_id,
    get_transcriptions_count,
    get_transcriptions_count,
    get_latest_transcription_by_user_id,
    update_transcription_in_db
)
from app.services.appointment_storage import get_all_completed_appointment_ids
from app.services.workflow import run_full_generation_workflow

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
AUDIO_STORAGE_DIR = os.getenv('AUDIO_STORAGE_DIR', 'audio_storage')
os.makedirs(AUDIO_STORAGE_DIR, exist_ok=True)
MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', 100))


@router.post("/transcribe", response_model=TranscriptionResponse, tags=["Transcriptions"])
async def transcribe_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_soap: bool = Form(False),
    username: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None)
):
    """
    Transcribe audio file with optional SOAP note generation.
    """
    try:
        # Validate file type
        if not file.content_type or not file.content_type.startswith('audio/'):
            raise HTTPException(
                status_code=400,
                detail="File must be an audio file"
            )
        
        # Validate file size
        max_size = MAX_FILE_SIZE_MB * 1024 * 1024
        file_content = await file.read()
        
        if len(file_content) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File size exceeds {MAX_FILE_SIZE_MB}MB limit"
            )
        
        logger.info(f"Processing file: {file.filename}, Size: {len(file_content)} bytes, SOAP: {generate_soap}")
        
        # Save original audio file to storage BEFORE any conversions
        saved_file_path = None
        original_file_content = file_content  # Keep original for saving
        try:
            # Generate unique filename with timestamp
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            original_filename = file.filename or "recording"
            # Get file extension from original filename or content type
            if '.' in original_filename:
                file_ext = os.path.splitext(original_filename)[1]
            else:
                # Determine extension from content type
                content_type = file.content_type or 'audio/webm'
                ext_map = {
                    'audio/webm': '.webm',
                    'audio/wav': '.wav',
                    'audio/mpeg': '.mp3',
                    'audio/mp4': '.m4a',
                    'audio/ogg': '.ogg',
                    'audio/opus': '.opus'
                }
                file_ext = ext_map.get(content_type, '.webm')
            
            safe_filename = f"{timestamp}_{original_filename.replace(' ', '_').replace('/', '_')}"
            if not safe_filename.endswith(file_ext):
                safe_filename += file_ext
            
            saved_file_path = os.path.join(AUDIO_STORAGE_DIR, safe_filename)
            
            # Write original file to disk
            with open(saved_file_path, 'wb') as f:
                f.write(original_file_content)
            
            logger.info(f"✅ Audio file saved to: {saved_file_path}")
        except Exception as save_error:
            logger.warning(f"⚠️ Failed to save audio file: {save_error}")
            logger.warning("Continuing without file storage...")
        
        soap_note = None
        
        # If SOAP generation is requested, use enhanced Deepgram API
        # Actually Deepgram params are almost same for both, but logic flow differs slightly in logging/intent
        
        filename_lower = file.filename.lower()
        content_type = get_audio_content_type(file.filename)
        logger.info(f"Uploading raw file for transcription: {file.filename} as {content_type}")
        
        # Build standard Deepgram API URL
        query_params = {
            "model": "nova-2",
            "smart_format": "true",
            "language": "en",
            "punctuate": "true",
            "keywords": "Quervain:1,Tenosynovitis:1"  # Smart keywords with low boost
        }
        
        url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
        headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": content_type}
        
        # Get transcription from Deepgram
        response = requests.post(url, headers=headers, data=file_content)
        response.raise_for_status()
        result = response.json()
        
        raw_transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        logger.info(f"Raw Deepgram Transcript: {raw_transcript}")
        # Apply medical terminology corrections
        text = fix_terms(raw_transcript)
        
        confidence = result["results"]["channels"][0]["alternatives"][0].get("confidence", 0.0)
        duration = result.get("metadata", {}).get("duration", 0.0)
        detected_language = "en-US"
        
        transcription_result = {
            'text': text,
            'confidence': confidence,
            'language': detected_language,
            'duration': duration
        }
        
        logger.info(f"Transcription complete: {len(transcription_result['text'])} chars")
        
        # Save transcription to database
        document_id = None
        try:
            transcription_data = {
                'text': transcription_result['text'],
                'confidence': transcription_result.get('confidence', 0.0),
                'language': transcription_result.get('language', 'unknown'),
                'duration': transcription_result.get('duration', 0.0),
                'filename': file.filename,
                'audio_file_path': saved_file_path,  # Store the saved audio file path
                'username': username,
                'user_id': user_id
            }
            saved_doc = await save_transcription_to_db(transcription_data)
            document_id = str(saved_doc.get('_id'))
            logger.info(f"✅ Transcription saved to database with ID: {document_id}")
            
            # Delete audio file after successful transcription save
            if saved_file_path and os.path.exists(saved_file_path):
                try:
                    os.remove(saved_file_path)
                    logger.info(f"🗑️ Audio file deleted: {saved_file_path}")
                except Exception as delete_error:
                    logger.warning(f"⚠️ Failed to delete audio file {saved_file_path}: {delete_error}")

            # TRIGGER BACKGROUND WORKFLOW ALWAYS (Generate SOAP, etc.)
            if document_id:
                logger.info(f"🚀 Triggering background workflow for transcription {document_id}")
                background_tasks.add_task(run_full_generation_workflow, document_id, user_id, generate_files=False)

        except Exception as db_error:
            logger.error(f"⚠️ Failed to save transcription to database: {db_error}")
            logger.warning("Continuing without database storage...")
            # Still try to delete audio file
            if saved_file_path and os.path.exists(saved_file_path):
                try:
                    os.remove(saved_file_path)
                except:
                    pass
        
        return TranscriptionResponse(
            transcription_id=document_id or f"temp_{datetime.utcnow().timestamp()}",
            text=transcription_result['text'],
            confidence=transcription_result.get('confidence', 0.0),
            language=transcription_result.get('language', 'unknown'),
            duration=transcription_result.get('duration', 0.0),
            created_at=datetime.utcnow(),
            soap_note=soap_note,
            document_id=document_id
        )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during transcription: {str(e)}"
        )


@router.get("/transcriptions/user-ids", response_model=list[str], tags=["Transcriptions"])
async def get_transcription_user_ids():
    """
    Get a list of distinct user_ids that have transcriptions.
    """
    try:
        user_ids = await get_all_completed_appointment_ids()
        return user_ids
    except Exception as e:
        logger.error(f"Error getting transcription user IDs: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/transcriptions/user/{user_id}/latest", response_model=Optional[TranscriptionListItem], tags=["Transcriptions"])
async def get_latest_transcription_user(user_id: str):
    """
    Get the latest transcription for a specific user ID.
    """
    try:
        transcription = await get_latest_transcription_by_user_id(user_id)
        if not transcription:
            return None
        return transcription
    except Exception as e:
        logger.error(f"Error getting latest transcription for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/transcriptions", response_model=TranscriptionListResponse, tags=["Transcriptions"])
async def get_transcriptions(
    limit: int = 100,
    skip: int = 0
):
    """
    Get all transcriptions with pagination.
    """
    try:
        if limit > 1000: limit = 1000
        if limit < 1: limit = 100
        if skip < 0: skip = 0
        
        transcriptions = await get_all_transcriptions(limit=limit, skip=skip)
        total = await get_transcriptions_count()
        
        transcription_items = []
        for trans in transcriptions:
            transcription_items.append(
                TranscriptionListItem(
                    id=trans.get("_id", ""),
                    text=trans.get("text", ""),
                    confidence=trans.get("confidence", 0.0),
                    language=trans.get("language", "unknown"),
                    duration=trans.get("duration", 0.0),
                    filename=trans.get("filename"),
                    username=trans.get("username"),
                    user_id=trans.get("user_id"),
                    created_at=trans.get("created_at", datetime.utcnow())
                )
            )
        
        return TranscriptionListResponse(
            total=total,
            limit=limit,
            skip=skip,
            transcriptions=transcription_items
        )
        
    except Exception as e:
        logger.error(f"Error retrieving transcriptions: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error retrieving transcriptions: {str(e)}"
        )


@router.post("/transcriptions", response_model=TranscriptionListItem, tags=["Transcriptions"])
async def create_transcription_endpoint(
    create_request: TranscriptionCreateRequest
):
    """
    Create a new transcription.
    """
    try:
        if not create_request.text or not create_request.text.strip():
            raise HTTPException(
                status_code=400,
                detail="Text field is required and cannot be empty"
            )
        
        transcription_data = {
            'text': create_request.text.strip(),
            'confidence': create_request.confidence if create_request.confidence is not None else 0.0,
            'language': create_request.language if create_request.language else "unknown",
            'duration': create_request.duration if create_request.duration is not None else 0.0,
            'filename': create_request.filename,
            'username': create_request.username,
            'user_id': create_request.user_id
        }
        
        saved_doc = await save_transcription_to_db(transcription_data)
        document_id = saved_doc.get('_id')
        
        logger.info(f"✅ Transcription created with ID: {document_id}")
        
        return TranscriptionListItem(
            id=document_id,
            text=saved_doc.get("text", ""),
            confidence=saved_doc.get("confidence", 0.0),
            language=saved_doc.get("language", "unknown"),
            duration=saved_doc.get("duration", 0.0),
            filename=saved_doc.get("filename"),
            username=saved_doc.get("username"),
            user_id=saved_doc.get("user_id"),
            created_at=saved_doc.get("created_at", datetime.utcnow())
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error creating transcription: {str(e)}"
        )


@router.get("/transcriptions/{transcription_id}", response_model=TranscriptionListItem, tags=["Transcriptions"])
async def get_transcription_by_id_endpoint(
    transcription_id: str
):
    """
    Get a specific transcription by ID.
    """
    try:
        transcription = await get_transcription_by_id(transcription_id)
        
        if not transcription:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found: {transcription_id}"
            )
        
        return TranscriptionListItem(
            id=transcription.get("_id", ""),
            text=transcription.get("text", ""),
            confidence=transcription.get("confidence", 0.0),
            language=transcription.get("language", "unknown"),
            duration=transcription.get("duration", 0.0),
            filename=transcription.get("filename"),
            username=transcription.get("username"),
            user_id=transcription.get("user_id"),
            created_at=transcription.get("created_at", datetime.utcnow())
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error retrieving transcription: {str(e)}"
        )


@router.put("/transcriptions/{transcription_id}", response_model=TranscriptionListItem, tags=["Transcriptions"])
async def update_transcription_endpoint(
    transcription_id: str,
    update_request: TranscriptionUpdateRequest
):
    """
    Update an existing transcription.
    """
    try:
        # Check if transcription exists
        existing = await get_transcription_by_id(transcription_id)
        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found: {transcription_id}"
            )
            
        # Convert request model to dict, excluding unset fields
        update_data = update_request.dict(exclude_unset=True)
        
        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No fields to update"
            )
            
        success = await update_transcription_in_db(transcription_id, update_data)
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to update transcription"
            )
            
        # Fetch updated document
        updated_doc = await get_transcription_by_id(transcription_id)
        
        return TranscriptionListItem(
            id=updated_doc.get("_id", ""),
            text=updated_doc.get("text", ""),
            confidence=updated_doc.get("confidence", 0.0),
            language=updated_doc.get("language", "unknown"),
            duration=updated_doc.get("duration", 0.0),
            filename=updated_doc.get("filename"),
            username=updated_doc.get("username"),
            user_id=updated_doc.get("user_id"),
            created_at=updated_doc.get("created_at", datetime.utcnow())
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error updating transcription: {str(e)}"
        )
