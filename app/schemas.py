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

class PatientInfo(BaseModel):
    """Patient information"""
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    

class SubjectiveSection(BaseModel):
    """Subjective section data"""
    chief_complaint: Optional[str] = None
    history_of_present_illness: Optional[str] = None
    past_medical_history: Optional[List[str]] = []
    medications: Optional[List[str]] = []
    social_history: Optional[str] = None
    review_of_systems: Optional[str] = None


class ObjectiveSection(BaseModel):
    """Objective section data"""
    general_exam: Optional[str] = None
    vitals: Optional[dict] = None
    local_exam: Optional[dict] = None
    imaging: Optional[List[dict]] = []


class AssessmentItem(BaseModel):
    """Assessment diagnosis item"""
    condition: str
    icd10_code: Optional[str] = None
    notes: Optional[str] = None


class PlanSection(BaseModel):
    """Plan section data"""
    immediate_treatment: Optional[List[str]] = []
    follow_up: Optional[dict] = None
    surgical_plan: Optional[str] = None
    patient_education: Optional[List[str]] = []
    work_status: Optional[str] = None
    rfa: Optional[str] = None


class SOAPRequest(BaseModel):
    """Request model for comprehensive SOAP note generation"""
    # Core transcription (required)
    transcription: str
    
    # Optional structured data
    patient: Optional[PatientInfo] = None
    date_of_service: Optional[str] = None
    location: Optional[str] = None
    reason_for_visit: Optional[str] = None
    
    # Pre-filled sections (optional)
    subjective: Optional[SubjectiveSection] = None
    objective: Optional[ObjectiveSection] = None
    assessment: Optional[List[AssessmentItem]] = []
    plan: Optional[PlanSection] = None


class SOAPResponse(BaseModel):
    """Response model for SOAP note generation"""
    # Original data
    transcription: str
    corrected_transcription: str
    
    # Generated SOAP sections
    subjective: str
    objective: str
    assessment: str
    plan: str
    
    # Full formatted SOAP note (Markdown format, ready for rendering/PDF)
    formatted_soap_note: str
    
    # Metadata
    created_at: str
    patient_info: Optional[dict] = None
    format: str = "markdown"  # Format of the SOAP note

