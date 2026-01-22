from pydantic import BaseModel
from typing import Optional, List


class PatientDemographics(BaseModel):
    full_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    age: Optional[str] = None
    gender: Optional[str] = None
    ssn_last4: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    preferred_language: Optional[str] = None
    interpreter_needed: Optional[bool] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_relationship: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class EmploymentInfo(BaseModel):
    employer_name: Optional[str] = None
    employer_address: Optional[str] = None
    occupation: Optional[str] = None
    employment_start_date: Optional[str] = None
    years_in_role: Optional[str] = None
    employment_type: Optional[str] = None
    union_member: Optional[bool] = None
    work_description: Optional[List[str]] = None


class WorkersCompDetails(BaseModel):
    date_of_injury: Optional[str] = None
    date_reported: Optional[str] = None
    injury_type: Optional[str] = None
    body_parts_injured: Optional[str] = None
    mechanism_of_injury: Optional[str] = None
    initial_symptoms: Optional[str] = None
    employer_notified: Optional[bool] = None
    first_provider_seen: Optional[str] = None
    first_treated_date: Optional[str] = None
    injury_report_filed: Optional[bool] = None
    claim_number: Optional[str] = None
    claims_adjuster: Optional[str] = None
    insurance_carrier: Optional[str] = None
    case_manager: Optional[str] = None
    attorney_rep: Optional[bool] = None
    attorney_name: Optional[str] = None
    attorney_phone: Optional[str] = None


class PriorTreatment(BaseModel):
    received_treatment: Optional[bool] = None
    emergency_room_visit: Optional[bool] = None
    primary_physician: Optional[bool] = None
    physical_therapy: Optional[bool] = None
    imaging_xray: Optional[bool] = None
    imaging_mri: Optional[bool] = None
    imaging_ct: Optional[bool] = None
    injections_cortisone: Optional[bool] = None
    injections_prp: Optional[bool] = None
    injections_other: Optional[str] = None
    surgery: Optional[bool] = None
    medications_prescribed: Optional[bool] = None
    work_status: Optional[str] = None
    restrictions: Optional[str] = None


class PastMedicalHistory(BaseModel):
    comorbidities: Optional[List[str]] = None
    past_surgeries: Optional[str] = None
    current_medications: Optional[str] = None
    medication_allergies: Optional[str] = None


class PriorInjuries(BaseModel):
    prior_industrial_injury: Optional[bool] = None
    prior_injury_description: Optional[str] = None
    prior_injury_date: Optional[str] = None
    prior_injury_body_parts: Optional[str] = None
    prior_claim_accepted: Optional[bool] = None
    prior_result: Optional[str] = None
    prior_pd_award: Optional[bool] = None
    similar_non_work_injuries: Optional[bool] = None
    similar_injury_description: Optional[str] = None


class CurrentSymptoms(BaseModel):
    symptoms: Optional[List[str]] = None
    pain_at_rest: Optional[str] = None
    pain_with_activity: Optional[str] = None
    pain_worst: Optional[str] = None
    pain_description: Optional[List[str]] = None
    aggravating_factors: Optional[str] = None
    relieving_factors: Optional[str] = None


class FunctionalLimitations(BaseModel):
    limited_activities: Optional[List[str]] = None
    adl_limitations: Optional[List[str]] = None


class ClinicalInputs(BaseModel):
    bp: Optional[str] = None
    pulse: Optional[str] = None
    temp: Optional[str] = None
    weight: Optional[str] = None
    height: Optional[str] = None
    rom: Optional[str] = None
    strength: Optional[str] = None
    pain_chart_notes: Optional[str] = None


class PatientVerification(BaseModel):
    signature: Optional[str] = None
    date: Optional[str] = None


class IntakeForm(BaseModel):
    section_a: PatientDemographics
    section_b: EmploymentInfo
    section_c: WorkersCompDetails
    section_d: PriorTreatment
    section_e: PastMedicalHistory
    section_f: PriorInjuries
    section_g: CurrentSymptoms
    section_h: FunctionalLimitations
    section_i: ClinicalInputs
    section_j: PatientVerification

