"""
PR-1 Generation Models
Models for generating PR-1 forms from intake, follow-up, and SOAP note data
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal


class SOAPDiagnosis(BaseModel):
    """Diagnosis information for PR-1"""
    condition: Optional[str] = None
    icd10: Optional[str] = None
    notes: Optional[str] = None


class SOAPRFAItem(BaseModel):
    """Request for Authorization (RFA) item"""
    service_or_good: Optional[str] = None
    cpt_or_hcpcs: Optional[str] = None
    diagnosis_icd10: Optional[str] = None
    mtus_consistent: Optional[bool] = None
    justification: Optional[str] = None
    is_drug: Optional[bool] = None
    drug_name: Optional[str] = None
    dose_form: Optional[str] = None
    frequency: Optional[str] = None
    length_or_qty: Optional[str] = None
    exempt_drug_review_requested: Optional[bool] = None


class PatientStatus(BaseModel):
    """Patient status information with checked flags and dates"""
    returnToFullDutyChecked: Optional[bool] = None
    returnToFullDutyDate: Optional[str] = None  # "MM/DD/YYYY"
    returnToModifiedDutyChecked: Optional[bool] = None
    returnToModifiedDutyDate: Optional[str] = None  # "MM/DD/YYYY"
    maxMedicalImprovementChecked: Optional[bool] = None
    maxMedicalImprovementDate: Optional[str] = None  # "MM/DD/YYYY"
    nextVisitChecked: Optional[bool] = None
    nextVisitDate: Optional[str] = None  # "MM/DD/YYYY"
    dischargedFromCareChecked: Optional[bool] = None
    dischargedFromCareDate: Optional[str] = None  # "MM/DD/YYYY"


class SOAPNoteForPR1(BaseModel):
    """SOAP Note structure for PR-1 generation"""
    patient_name: Optional[str] = None
    dob: Optional[str] = None  # "MM/DD/YYYY"
    date_of_injury: Optional[str] = None  # "MM/DD/YYYY"
    claim_number: Optional[str] = None
    employer: Optional[str] = None
    examiner: Optional[str] = None
    specialty: Optional[str] = None
    npi: Optional[str] = None
    state_license: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_fax: Optional[str] = None
    contact_email: Optional[str] = None
    practice_name: Optional[str] = None
    primary_treating_physician: Optional[str] = None
    
    # Section B content
    chief_complaint: Optional[str] = None
    brief_history: Optional[str] = None
    physical_exam: Optional[str] = None
    current_treatments_and_meds: Optional[str] = None
    outcomes_adl: Optional[str] = None
    adl_goal_next_visit: Optional[str] = None
    disability_status: Optional[str] = None
    secondary_physician_reports: Optional[str] = None
    discussion_assessment: Optional[str] = None
    treatment_plan_text: Optional[str] = None
    continue_same_treatment: Optional[bool] = None
    discharge_from_care: Optional[bool] = None
    change_in_treatment_plan: Optional[bool] = None
    dispense_as_written: Optional[bool] = None
    comments: Optional[str] = None  # Comments section for treatment plan
    diagnoses: Optional[List[SOAPDiagnosis]] = None
    rfa_items: Optional[List[SOAPRFAItem]] = None
    
    # Work status
    work_status: Optional[str] = None  # "Full Duty" | "Modified" | "TTD" | etc.
    restrictions: Optional[str] = None
    return_full_duty_date: Optional[str] = None
    return_modified_duty_date: Optional[str] = None
    mmi_date: Optional[str] = None
    next_visit_date: Optional[str] = None
    discharged_date: Optional[str] = None
    meds_affect_alertness: Optional[bool] = None
    meds_effect_description: Optional[str] = None
    restrictions_duration: Optional[str] = None
    
    # Patient status with checked flags and dates
    patientStatus: Optional[PatientStatus] = None


class IntakeFormForPR1(BaseModel):
    """Intake form structure for PR-1 generation (flexible dict structure)"""
    section_a: Optional[Dict[str, Any]] = None
    section_b: Optional[Dict[str, Any]] = None
    section_c: Optional[Dict[str, Any]] = None
    section_d: Optional[Dict[str, Any]] = None
    section_e: Optional[Dict[str, Any]] = None
    section_f: Optional[Dict[str, Any]] = None
    section_g: Optional[Dict[str, Any]] = None
    section_h: Optional[Dict[str, Any]] = None
    section_i: Optional[Dict[str, Any]] = None
    section_j: Optional[Dict[str, Any]] = None


class FollowUpFormForPR1(BaseModel):
    """Follow-up form structure for PR-1 generation (flexible dict structure)"""
    section_a: Optional[Dict[str, Any]] = None
    section_b: Optional[Dict[str, Any]] = None
    section_c: Optional[Dict[str, Any]] = None
    section_d: Optional[Dict[str, Any]] = None


class PR1GenerateRequest(BaseModel):
    """Request model for PR-1 generation"""
    # Pass one or more of these embedded payloads:
    intake: Optional[IntakeFormForPR1] = None
    followup: Optional[FollowUpFormForPR1] = None
    soap: Optional[SOAPNoteForPR1] = None
    
    # Or pass Mongo IDs (string ObjectIds) if you want the API to fetch them:
    intake_id: Optional[str] = None
    followup_id: Optional[str] = None
    soap_id: Optional[str] = None
    
    # Automatic latest data fetching options:
    # If True, automatically fetches the latest intake form from MongoDB
    use_latest_intake: Optional[bool] = Field(default=False, description="Automatically fetch latest intake form")
    # If True, automatically fetches the latest follow-up form from MongoDB
    use_latest_followup: Optional[bool] = Field(default=False, description="Automatically fetch latest follow-up form")
    
    # Optional: treat this request as a specific PR-1 purpose (checkboxes up top)
    flags: Optional[Dict[str, bool]] = None  # e.g., {"progress_report": True, "request_for_authorization": True}

