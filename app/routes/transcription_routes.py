
import logging
import os
import requests
import json
import re
from typing import Optional, List, Dict, Any
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Body, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.config import settings
try:
    from app.prompts import MEDICAL_TERMINOLOGY_CORRECTIONS
except ImportError:
    # Fallback if prompts module is missing or incomplete
    MEDICAL_TERMINOLOGY_CORRECTIONS = {} 

from app.services.transcription_service import (
    save_transcription_to_db,
    get_all_transcriptions,
    get_transcription_by_id,
    update_transcription_in_db,
    delete_transcription_in_db,
    get_transcriptions_count,
    get_user_ids_with_transcriptions,
    get_latest_transcription_by_user_id
)

# Configure logger
logger = logging.getLogger(__name__)

router = APIRouter()

# ==========================================
# Models
# ==========================================

class TranscriptionResponse(BaseModel):
    transcription_id: str
    text: str
    confidence: float
    language: str
    duration: float
    created_at: datetime
    soap_note: Optional[dict] = None
    document_id: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[str] = None
    filename: Optional[str] = None

# ==========================================
# Helpers
# ==========================================

def get_audio_content_type(filename: str) -> str:
    """Get appropriate Content-Type for audio file"""
    filename_lower = filename.lower()
    if filename_lower.endswith('.webm'):
        return 'audio/webm'
    elif filename_lower.endswith('.opus'):
        return 'audio/opus'
    elif filename_lower.endswith('.mp3'):
        return 'audio/mpeg'
    elif filename_lower.endswith('.m4a'):
        return 'audio/mp4'
    elif filename_lower.endswith('.wav'):
        return 'audio/wav'
    else:
        return 'audio/wav'  # Default to WAV

def fix_terms(text: str) -> str:
    """
    Fix common medical terminology errors in transcription using a single-pass regex replacement.
    This prevents double-replacements where a corrected term is further incorrectly modified.
    """
    if not text:
        return ""
    
    if not MEDICAL_TERMINOLOGY_CORRECTIONS:
        return text

    # Pre-compile regex pattern for better performance
    if not hasattr(fix_terms, 'pattern'):
         # Sort by length (descending) to match longest phrases first
         sorted_terms = sorted(MEDICAL_TERMINOLOGY_CORRECTIONS.keys(), key=len, reverse=True)
         escaped_terms = [re.escape(term) for term in sorted_terms]
         fix_terms.pattern = re.compile(r'\b(' + '|'.join(escaped_terms) + r')\b', re.IGNORECASE)
    
    def replace_func(match):
        term = match.group(0).lower()
        # Find the correct replacement (case-insensitive lookup)
        for k, v in MEDICAL_TERMINOLOGY_CORRECTIONS.items():
             if k.lower() == term:
                 return v
        return match.group(0)
    
    return fix_terms.pattern.sub(replace_func, text)

# ==========================================
# Endpoints
# ==========================================

# 1. Transcribe Audio (POST)
@router.post("/api/v1/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_soap: bool = Form(False),
    username: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None)
):
    """Transcribe audio file using Deepgram"""
    try:
        # File type validation relaxed
        MAX_FILE_SIZE_MB = int(getattr(settings, 'MAX_FILE_SIZE_MB', 100))
        max_size = MAX_FILE_SIZE_MB * 1024 * 1024
        file_content = await file.read()
        
        if len(file_content) > max_size:
            raise HTTPException(status_code=413, detail=f"File size exceeds {MAX_FILE_SIZE_MB}MB limit")
        
        logger.info(f"Processing file: {file.filename}, Size: {len(file_content)} bytes")
        
        # Save locally
        saved_file_path = None
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            original_filename = file.filename or "recording"
            safe_filename = f"{timestamp}_{original_filename.replace(' ', '_')}"
            upload_path = getattr(settings, 'UPLOAD_PATH', './uploads')
            os.makedirs(upload_path, exist_ok=True)
            saved_file_path = os.path.join(upload_path, safe_filename)
            with open(saved_file_path, 'wb') as f:
                f.write(file_content)
        except Exception as e:
            logger.warning(f"Failed to save file: {e}")

        # Deepgram Logic
        deepgram_api_key = settings.DEEPGRAM_API_KEY or os.getenv("DEEPGRAM_API_KEY")
        if not deepgram_api_key:
             raise HTTPException(status_code=500, detail="Deepgram API key not configured")

        content_type = get_audio_content_type(file.filename or "audio.wav")
        query_params = {
            "model": "nova-2", 
            "smart_format": "true", 
            "language": "en", 
            "punctuate": "true",
            "keywords": "Quervain:1,Tenosynovitis:1"  # Smart keywords with low boost
        }
        url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
        headers = {"Authorization": f"Token {deepgram_api_key}", "Content-Type": content_type}
        
        response = requests.post(url, headers=headers, data=file_content)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Deepgram Error: {response.text}")
            
        result = response.json()
        text = result["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        text = fix_terms(text)
        confidence = result["results"]["channels"][0]["alternatives"][0].get("confidence", 0.0)
        duration = result.get("metadata", {}).get("duration", 0.0)
        
        logger.info(f"Transcription complete: {len(text)} chars")

        transcription_data = {
            'text': text, 'confidence': confidence, 'language': 'en-US', 'duration': duration,
            'filename': file.filename, 'audio_file_path': saved_file_path,
            'username': username, 'user_id': user_id,
        }
        
        saved_doc = await save_transcription_to_db(transcription_data)
        document_id = str(saved_doc.get('_id'))
        
        return TranscriptionResponse(
            transcription_id=document_id, text=text, confidence=confidence, language='en-US',
            duration=duration, created_at=datetime.utcnow(), document_id=document_id,
            username=username, user_id=user_id, filename=file.filename
        )
    except HTTPException: raise
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# 2. Get All Transcriptions
@router.get("/api/v1/transcriptions")
async def get_transcriptions(limit: int = 100, skip: int = 0):
    try:
        transcriptions = await get_all_transcriptions(limit=limit, skip=skip)
        total = await get_transcriptions_count()
        return {"total": total, "limit": limit, "skip": skip, "transcriptions": transcriptions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. Get User IDs (SPECIFIC PATH BEFORE VARIABLE PATH)
@router.get("/api/v1/transcriptions/user-ids", response_model=List[str])
async def get_transcription_user_ids_endpoint():
    try:
        return await get_user_ids_with_transcriptions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 4. Get Latest Transcription for User (SPECIFIC PATH BEFORE VARIABLE PATH)
@router.get("/api/v1/transcriptions/user/{user_id}/latest")
async def get_latest_transcription_user_endpoint(user_id: str):
    try:
        transcription = await get_latest_transcription_by_user_id(user_id)
        if not transcription:
            # Note: Returning None or 404 depending on frontend expectation. 
            # Log said 404, implying frontend expects to handle 404 as "no previous visit".
            # Or handle empty.
            # Old code returned None (JSON null) if not found (Line 1895). 
            # Wait, Line 1895: `if not transcription: return None`.
            # So it returns 200 OK with null body.
            # But the log `GET ... 404 (Not Found)` means the ENDPOINT wasn't found (because I didn't have it).
            # If I return None, it's 200.
            return None
        return transcription
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 5. Get Transcription By ID
@router.get("/api/v1/transcriptions/{transcription_id}")
async def get_transcription_endpoint(transcription_id: str):
    try:
        transcription = await get_transcription_by_id(transcription_id)
        if not transcription:
            raise HTTPException(status_code=404, detail=f"Transcription not found: {transcription_id}")
        return transcription
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 6. Update Transcription
@router.put("/api/v1/transcriptions/{transcription_id}")
async def update_transcription_endpoint(
    transcription_id: str,
    update_data: Dict[str, Any] = Body(...)
):
    try:
        if '_id' in update_data: del update_data['_id']
        success = await update_transcription_in_db(transcription_id, update_data)
        if not success:
            raise HTTPException(status_code=404, detail=f"Transcription not found: {transcription_id}")
        return {"message": "Updated successfully", "id": transcription_id}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 7. Delete Transcription
@router.delete("/api/v1/transcriptions/{transcription_id}")
async def delete_transcription_endpoint(transcription_id: str):
    try:
        success = await delete_transcription_in_db(transcription_id)
        if not success:
             raise HTTPException(status_code=404, detail=f"Transcription not found")
        return {"message": "Deleted successfully", "id": transcription_id}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
