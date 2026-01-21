
from pydantic import BaseModel, field_validator
from typing import Optional, List

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
    # Core transcription - either transcription_id or transcription text
    transcription_id: Optional[str] = None  # MongoDB transcription document ID
    transcription: Optional[str] = None  # Transcription text (if not using transcription_id)
    
    # Optional structured data
    patient: Optional[PatientInfo] = None
    date_of_service: Optional[str] = None
    location: Optional[str] = None
    reason_for_visit: Optional[str] = None
    userId: Optional[str] = None  # Patient ID/Unique identifier
    
    # Custom prompts (optional) - allows frontend to send dynamic prompts
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    
    # Model selection (optional) - only 'gpt-4o' or 'gpt-5.1' allowed
    model: Optional[str] = None
    skip_post_processing: Optional[bool] = False
    
    # Pre-filled sections (optional)
    subjective: Optional[SubjectiveSection] = None
    objective: Optional[ObjectiveSection] = None
    assessment: Optional[List[AssessmentItem]] = []
    plan: Optional[PlanSection] = None
    
    @field_validator('model')
    @classmethod
    def validate_model(cls, v: Optional[str]) -> Optional[str]:
        """Validate that model is one of the allowed models"""
        if v is not None:
            allowed_models = ['gpt-4o', 'gpt-5.1']
            if v not in allowed_models:
                raise ValueError(f"Model '{v}' is not allowed. Only {allowed_models} are permitted.")
        return v


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
    userId: Optional[str] = None  # Patient ID/Unique identifier
    format: str = "markdown"  # Format of the SOAP note
    document_id: Optional[str] = None  # MongoDB document ID if saved to database
    status: Optional[str] = None  # pending, completed
