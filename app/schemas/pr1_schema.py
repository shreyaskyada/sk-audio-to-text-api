from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class SOAPDiagnosis(BaseModel):
    condition: Optional[str] = None
    icd10: Optional[str] = None
    notes: Optional[str] = None

class SOAPRFAItem(BaseModel):
    service_or_good: Optional[str] = None
    cpt_or_hcpcs: Optional[str] = None
    supportive_cpts: Optional[List[str]] = None
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
    returnToFullDutyChecked: Optional[bool] = None
    returnToFullDutyDate: Optional[str] = None
    returnToModifiedDutyChecked: Optional[bool] = None
    returnToModifiedDutyDate: Optional[str] = None
    maxMedicalImprovementChecked: Optional[bool] = None
    maxMedicalImprovementDate: Optional[str] = None
    nextVisitChecked: Optional[bool] = None
    nextVisitDate: Optional[str] = None
    dischargedFromCareChecked: Optional[bool] = None
    dischargedFromCareDate: Optional[str] = None

class SOAPNoteForPR1(BaseModel):
    patient_name: Optional[str] = None
    dob: Optional[str] = None
    date_of_injury: Optional[str] = None
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
    comments: Optional[str] = None
    diagnoses: Optional[List[SOAPDiagnosis]] = None
    rfa_items: Optional[List[SOAPRFAItem]] = None
    work_status: Optional[str] = None
    restrictions: Optional[str] = None
    return_full_duty_date: Optional[str] = None
    return_modified_duty_date: Optional[str] = None
    mmi_date: Optional[str] = None
    next_visit_date: Optional[str] = None
    discharged_date: Optional[str] = None
    meds_affect_alertness: Optional[bool] = None
    meds_effect_description: Optional[str] = None
    restrictions_duration: Optional[str] = None
    patientStatus: Optional[PatientStatus] = None

class IntakeFormForPR1(BaseModel):
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
    section_a: Optional[Dict[str, Any]] = None
    section_b: Optional[Dict[str, Any]] = None
    section_c: Optional[Dict[str, Any]] = None
    section_d: Optional[Dict[str, Any]] = None

class PR1GenerateRequest(BaseModel):
    intake: Optional[IntakeFormForPR1] = None
    followup: Optional[FollowUpFormForPR1] = None
    soap: Optional[SOAPNoteForPR1] = None
    intake_id: Optional[str] = None
    followup_id: Optional[str] = None
    soap_id: Optional[str] = None
    use_latest_intake: Optional[bool] = Field(default=False)
    use_latest_followup: Optional[bool] = Field(default=False)
    flags: Optional[Dict[str, bool]] = None

class SavedPR1Form(BaseModel):
    soap_id: str
    patient_name: str
    form_data: Dict[str, Any]
    soap_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
