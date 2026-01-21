import logging
import os
from datetime import datetime
from typing import Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from app.schemas.transcription_schema import (
    TranscriptionResponse, TranscriptionListItem, 
    TranscriptionListResponse, TranscriptionCreateRequest
)
from app.services.transcription_service import TranscriptionService
from app.services.soap_service import SOAPService
from app.utils.audio_utils import get_audio_content_type
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["transcriptions"])

@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_soap: bool = Form(False),
    username: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None)
):
    """Transcribe audio file with optional SOAP note generation"""
    if not file.content_type or not file.content_type.startswith('audio/'):
        raise HTTPException(status_code=400, detail="File must be an audio file")
    
    file_content = await file.read()
    if len(file_content) > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit")
    
    # Transcription logic
    content_type = get_audio_content_type(file.filename)
    try:
        result = await TranscriptionService.run_transcription(file_content, content_type)
    except Exception as e:
        logger.error(f"Transcription failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
    # Save to DB
    doc_data = {
        'text': result['text'],
        'confidence': result['confidence'],
        'language': result['language'],
        'duration': result['duration'],
        'filename': file.filename,
        'username': username,
        'user_id': user_id
    }
    saved_doc = await TranscriptionService.save_transcription(doc_data)
    transcription_id = saved_doc["_id"]
    
    # Trigger background workflow
    background_tasks.add_task(SOAPService.run_full_workflow, transcription_id, user_id)
    
    return TranscriptionResponse(
        _id=transcription_id,
        text=result['text'],
        confidence=result['confidence'],
        language=result['language'],
        duration=result['duration'],
        created_at=doc_data['created_at']
    )

@router.get("/transcriptions/user-ids", response_model=List[str])
async def get_transcription_user_ids():
    return await TranscriptionService.get_user_ids()

@router.get("/transcriptions/user/{user_id}/latest", response_model=Optional[TranscriptionListItem])
async def get_latest_transcription_user(user_id: str):
    doc = await TranscriptionService.get_latest_by_user_id(user_id)
    if not doc:
        return None
    # Convert ObjectId to string if not already done by service
    if "_id" in doc: doc["_id"] = str(doc["_id"])
    return TranscriptionListItem(**doc)

@router.get("/transcriptions", response_model=TranscriptionListResponse)
async def get_transcriptions(limit: int = 100, skip: int = 0):
    docs = await TranscriptionService.get_all(limit, skip)
    total = await TranscriptionService.get_count()
    items = []
    for doc in docs:
        if "_id" in doc: doc["_id"] = str(doc["_id"])
        items.append(TranscriptionListItem(**doc))
    return TranscriptionListResponse(total=total, limit=limit, skip=skip, transcriptions=items)

@router.post("/transcriptions", response_model=TranscriptionListItem)
async def create_transcription_endpoint(create_request: TranscriptionCreateRequest):
    doc = await TranscriptionService.save_transcription(create_request.model_dump())
    if "_id" in doc: doc["_id"] = str(doc["_id"])
    return TranscriptionListItem(**doc)

@router.put("/transcriptions/{transcription_id}", response_model=TranscriptionListItem)
async def update_transcription_endpoint(
    transcription_id: str,
    update_request: Any, # Use Any to be safe or TranscriptionUpdateRequest
    background_tasks: BackgroundTasks
):
    """Update transcription and trigger background workflows if text changed"""
    update_data = update_request.model_dump(exclude_unset=True) if hasattr(update_request, "model_dump") else update_request
    
    success = await TranscriptionService.update_transcription(transcription_id, update_data)
    if not success:
        raise HTTPException(status_code=404, detail="Transcription not found")
    
    updated_doc = await TranscriptionService.get_by_id(transcription_id)
    if "_id" in updated_doc: updated_doc["_id"] = str(updated_doc["_id"])
    
    # If text was updated, we might want to regenerate SOAP (similar to developer backup)
    if "text" in update_data:
        background_tasks.add_task(SOAPService.run_full_workflow, transcription_id, updated_doc.get("user_id"))
        
    return TranscriptionListItem(**updated_doc)

@router.get("/transcriptions/{transcription_id}", response_model=TranscriptionListItem)
async def get_transcription_by_id_endpoint(transcription_id: str):
    doc = await TranscriptionService.get_by_id(transcription_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Transcription not found")
    if "_id" in doc: doc["_id"] = str(doc["_id"])
    return TranscriptionListItem(**doc)
