from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime

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
    transcription_id: Optional[str] = None
    transcription: Optional[str] = None
    patient: Optional[PatientInfo] = None
    date_of_service: Optional[str] = None
    location: Optional[str] = None
    reason_for_visit: Optional[str] = None
    userId: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    model: Optional[str] = 'gpt-4o'
    subjective: Optional[SubjectiveSection] = None
    objective: Optional[ObjectiveSection] = None
    assessment: Optional[List[AssessmentItem]] = []
    plan: Optional[PlanSection] = None

class SOAPResponse(BaseModel):
    """Response model for SOAP note generation"""
    transcription: str
    corrected_transcription: str
    subjective: str
    objective: str
    assessment: str
    plan: str
    formatted_soap_note: str
    created_at: Optional[str] = None
    patient_info: Optional[dict] = None
    userId: Optional[str] = None
    format: str = "markdown"
    document_id: Optional[str] = None
    status: Optional[str] = None
