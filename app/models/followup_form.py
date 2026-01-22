from pydantic import BaseModel
from typing import Optional


class Identification(BaseModel):
    name: Optional[str] = None
    dob: Optional[str] = None  # "MM/DD/YYYY"
    case_or_claim_no: Optional[str] = None
    visit_no_or_version: Optional[str] = None


class PatientInputs(BaseModel):
    pain_better_since_last: Optional[bool] = None
    pain_score_0_10: Optional[str] = None
    pain_location: Optional[str] = None  # e.g., "Neck/Back/Shoulder/Knee/Hip/Other"
    work_status_changed: Optional[bool] = None
    work_status_perception: Optional[str] = None  # "Full duty / Light duty / Not able"
    perception_checked_yes: Optional[bool] = None  # (Work status perception: Yes/No)
    compliant_current_plan: Optional[bool] = None
    compliant_previous_plan: Optional[bool] = None
    new_or_worsening_symptoms: Optional[bool] = None
    new_worsening_note: Optional[str] = None
    new_tests_or_procedures: Optional[bool] = None
    need_new_referrals_or_auth: Optional[bool] = None


class ClinicalStaffInputs(BaseModel):
    bp: Optional[str] = None
    pulse: Optional[str] = None
    temp: Optional[str] = None
    weight: Optional[str] = None
    height: Optional[str] = None
    rom: Optional[str] = None  # e.g., "_______° / %"
    strength: Optional[str] = None  # MMT or scale
    pain_chart_notes: Optional[str] = None  # body map note


class SignOff(BaseModel):
    patient_signature: Optional[str] = None
    date: Optional[str] = None  # "MM/DD/YYYY"
    staff_clinician_name: Optional[str] = None


class FollowUpForm(BaseModel):
    section_a: Identification
    section_b: PatientInputs
    section_c: ClinicalStaffInputs
    section_d: SignOff

