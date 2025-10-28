"""
Pydantic schemas for API request/response models
"""
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime


# ============================================
# AUTH SCHEMAS
# ============================================

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str


# ============================================
# TRANSCRIPTION SCHEMAS
# ============================================

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
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# FEEDBACK SCHEMAS
# ============================================

class ErrorCorrection(BaseModel):
    """Model for error correction"""
    wrong: str
    correct: str


class FeedbackRequest(BaseModel):
    """Request model for feedback submission"""
    rating: int
    rating_text: Optional[str] = None
    feedback: Optional[str] = None
    transcription_preview: str
    errors_found: List[ErrorCorrection] = []
    total_errors: int = 0
    feedback_type: str = "simple"


class FeedbackResponse(BaseModel):
    """Response model for single feedback entry"""
    id: str
    rating: int
    rating_text: str
    transcription_preview: str
    errors_found: List[dict] = []
    total_errors: int
    feedback_type: str
    feedback: str
    timestamp: datetime
    
    model_config = ConfigDict(from_attributes=True)


class FeedbackStatsResponse(BaseModel):
    """Response model for feedback statistics"""
    total_feedback: int
    average_rating: float
    recent_feedback: List[FeedbackResponse]


class FeedbackListResponse(BaseModel):
    """Response model for all feedback"""
    total_feedback: int
    feedback: List[FeedbackResponse]


# ============================================
# SOAP NOTE SCHEMAS
# ============================================

class SOAPRequest(BaseModel):
    """Request model for SOAP note generation"""
    text: str


class SOAPResponse(BaseModel):
    """Response model for SOAP note generation"""
    text: str
    soap_note: str
    created_at: str

