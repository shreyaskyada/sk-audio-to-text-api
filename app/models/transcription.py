
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime

class TranscriptionRequest(BaseModel):
    """Request model for transcription"""
    generate_soap: bool = False


class TranscriptionResponse(BaseModel):
    """Response model for transcription"""
    transcription_id: str
    text: str
    confidence: float
    language: str
    duration: float
    created_at: datetime
    soap_note: Optional[str] = None
    document_id: Optional[str] = None  # MongoDB document ID if saved to database
    needs_rfa: Optional[bool] = False
    rfa_name: Optional[str] = None
    needs_work_status: Optional[bool] = False
    work_status: Optional[str] = None
    work_status_code: Optional[str] = None
    background_running_status: Optional[str] = None  # Pending, in_progress, completed, failed

    # New SOAP section fields
    chief_complaint: Optional[str] = None
    history: Optional[str] = None
    examination: Optional[str] = None
    assessment: Optional[str] = None
    treatment_plan: Optional[str] = None
    medications: Optional[str] = None
    follow_up: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TranscriptionListItem(BaseModel):
    """Model for transcription list item"""
    id: str
    text: str
    confidence: float
    language: str
    duration: float
    filename: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[str] = None
    created_at: datetime
    needs_rfa: Optional[bool] = False
    rfa_name: Optional[str] = None
    needs_work_status: Optional[bool] = False
    work_status: Optional[str] = None
    work_status_code: Optional[str] = None
    background_running_status: Optional[str] = None

    # New SOAP section fields
    chief_complaint: Optional[str] = None
    history: Optional[str] = None
    examination: Optional[str] = None
    assessment: Optional[str] = None
    treatment_plan: Optional[str] = None
    medications: Optional[str] = None
    follow_up: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TranscriptionListResponse(BaseModel):
    """Response model for transcription list"""
    total: int
    limit: int
    skip: int
    transcriptions: List[TranscriptionListItem]


class TranscriptionCreateRequest(BaseModel):
    """Request model for creating a transcription"""
    text: str
    confidence: Optional[float] = 0.0
    language: Optional[str] = "unknown"
    duration: Optional[float] = 0.0
    filename: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[str] = None
    needs_rfa: Optional[bool] = False
    rfa_name: Optional[str] = None
    needs_work_status: Optional[bool] = False
    work_status: Optional[str] = None
    work_status_code: Optional[str] = None
    background_running_status: Optional[str] = "Pending"

    # New SOAP section fields
    chief_complaint: Optional[str] = None
    history: Optional[str] = None
    examination: Optional[str] = None
    assessment: Optional[str] = None
    treatment_plan: Optional[str] = None
    medications: Optional[str] = None
    follow_up: Optional[str] = None


class TranscriptionUpdateRequest(BaseModel):
    """Request model for updating a transcription"""
    text: Optional[str] = None
    confidence: Optional[float] = None
    language: Optional[str] = None
    duration: Optional[float] = None
    filename: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[str] = None
    needs_rfa: Optional[bool] = None
    rfa_name: Optional[str] = None
    needs_work_status: Optional[bool] = None
    work_status: Optional[str] = None
    work_status_code: Optional[str] = None
    background_running_status: Optional[str] = None

    # New SOAP section fields
    chief_complaint: Optional[str] = None
    history: Optional[str] = None
    examination: Optional[str] = None
    assessment: Optional[str] = None
    treatment_plan: Optional[str] = None
    medications: Optional[str] = None
    follow_up: Optional[str] = None
