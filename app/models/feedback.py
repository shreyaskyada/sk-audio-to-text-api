
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime

class ErrorCorrection(BaseModel):
    """Model for error correction"""
    wrong: str
    correct: str


class FeedbackRequest(BaseModel):
    """Request model for feedback submission"""
    rating: int
    rating_text: Optional[str] = None
    feedback: Optional[str] = None
    transcription_id: str  # MongoDB transcription document ID
    transcription_preview: Optional[str] = None  # Optional preview text
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
