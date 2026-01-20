import logging
import re
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List
from bson import ObjectId

from app.database import get_database
from app.services import soap_service, pr1_service

logger = logging.getLogger(__name__)

WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'

# ============================================
# HELPER FUNCTIONS
# ============================================

def serialize_mongodb_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if doc is None: return None
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId): serialized[key] = str(value)
        elif isinstance(value, datetime): serialized[key] = value.isoformat()
        elif isinstance(value, dict): serialized[key] = serialize_mongodb_doc(value)
        elif isinstance(value, list): serialized[key] = [serialize_mongodb_doc(i) if isinstance(i, dict) else str(i) if isinstance(i, (ObjectId, datetime)) else i for i in value]
        else: serialized[key] = value
    return serialized

def to_yyyy_mm_dd(s: Optional[str]) -> Optional[str]:
    """Convert date string to YYYY-MM-DD format (ISO 8601)"""
    if not s:
        return None
    
    # Accept common formats and normalize to YYYY-MM-DD
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    
    return s  # leave as-is if unknown format

def extract_weight_from_text(text: str) -> Optional[str]:
    if not text: return None
    flags = re.IGNORECASE | re.DOTALL
    patterns = [
        r'(?:avoid|no|not).*?(?:lift|push|pull|carry).*?(?:more than|over|greater than|exceeding|>)\s*(\d+)\s*(?:lbs|pounds|lb)',
        r'(?:max|maximum|limit).*?(?:lift|push|pull|carry)?.*?(?:to|of|is)?\s*(\d+)\s*(?:lbs|pounds|lb)',
        r'(?:lift|push|pull|carry).*?(?:<=|<|≤)\s*(\d+)\s*(?:lbs|pounds|lb)',
        r'(?:lift|push|pull|carry).*?restriction.*?\s*(\d+)\s*(?:lbs|pounds|lb)'
    ]
    for pat in patterns:
        match = re.search(pat, text, flags)
        if match: return match.group(1)
    return None

def extract_body_parts_from_diagnoses(
    soap_doc: Optional[Dict[str, Any]],
    section_b: Optional[Dict[str, Any]]
) -> str:
    """Extract body parts injured from diagnoses or assessment"""
    body_parts = []
    
    body_part_keywords = {
        "back": "back", "spine": "back", "lumbar": "lower back", "lumbosacral": "lower back", "thoracic": "mid back", "cervical": "neck",
        "neck": "neck",
        "shoulder": "shoulder", "acromioclavicular": "shoulder", "rotator cuff": "shoulder",
        "arm": "arm", "elbow": "elbow", "forearm": "arm",
        "wrist": "wrist", "hand": "hand", "finger": "finger", "thumb": "thumb",
        "knee": "knee", "meniscus": "knee", "acl": "knee", "patella": "knee",
        "leg": "leg", "ankle": "ankle", "foot": "foot", "toe": "toe",
        "hip": "hip", "thigh": "thigh", "groin": "hip"
    }

    # Helper format function
    def normalize_and_add(text):
        if not text: return
        text_lower = str(text).lower()
        for keyword, part in body_part_keywords.items():
            if keyword in text_lower and part not in body_parts:
                body_parts.append(part)

    # 1. From SOAP Diagnoses
    if soap_doc:
        diagnoses = soap_doc.get("diagnoses") or []
        if isinstance(diagnoses, list):
            for diag in diagnoses:
                if isinstance(diag, dict):
                    cond = diag.get("condition") or diag.get("diagnosis")
                    normalize_and_add(cond)

        # 2. From SOAP Assessment
        assessment = soap_doc.get("discussion_assessment") or soap_doc.get("assessment")
        if isinstance(assessment, str):
            normalize_and_add(assessment)

    # 3. From PR1 Section B Diagnoses
    if section_b:
        # Handle New Backend structure: sectionB["diagnoses"] -> { "primary": {...}, ... }
        if "diagnoses" in section_b and isinstance(section_b["diagnoses"], dict):
            diag_dict = section_b["diagnoses"]
            
            # Primary
            p = diag_dict.get("primary")
            if isinstance(p, dict): normalize_and_add(p.get("condition"))
            
            # Secondary
            s = diag_dict.get("secondary")
            if isinstance(s, dict): normalize_and_add(s.get("condition"))
            
            # Additional
            addl = diag_dict.get("additional")
            if isinstance(addl, list):
                for a in addl:
                    if isinstance(a, dict): normalize_and_add(a.get("condition"))

        # Handle Old Backend structure: section_b["primary_diagnosis"] (string or dict?)
        # Just in case field names differ
        if section_b.get("primary_diagnosis"):
            normalize_and_add(section_b.get("primary_diagnosis"))
        if section_b.get("secondary_diagnosis"):
            normalize_and_add(section_b.get("secondary_diagnosis"))

    # Remove duplicates and format
    body_parts = list(dict.fromkeys(body_parts))
    
    if body_parts:
        return ", ".join(body_parts)
    
    
    if body_parts:
        return ", ".join(body_parts)
    
    return ""

def extract_body_parts_fallback(text: str) -> Optional[str]:
    """Fallback extraction of body parts from raw text using patterns"""
    if not text: return None
    
    found_parts = []
    text_lower = text.lower()
    
    # Map keywords to display names
    keywords = {
        "lumbar": "Lumbar Spine", "lumbosacral": "Lumbar Spine", "low back": "Lower Back", 
        "thoracic": "Thoracic Spine", "mid back": "Mid Back",
        "cervical": "Cervical Spine", "neck": "Neck",
        "shoulder": "Shoulder", "rotator cuff": "Shoulder",
        "knee": "Knee", "meniscus": "Knee", "acl": "Knee",
        "wrist": "Wrist", "carpal tunnel": "Wrist",
        "hand": "Hand", "finger": "Finger", "thumb": "Thumb",
        "ankle": "Ankle", "foot": "Foot", 
        "elbow": "Elbow", "arm": "Arm", "forearm": "Arm",
        "hip": "Hip", "groin": "Hip"
    }
    
    # Check each keyword
    for key, display in keywords.items():
        # Simple check: is keyword in text?
        # Better check: is keyword in text AND not negated?
        if key in text_lower:
             # Basic negation check (look back 20 chars for 'no', 'not')
             idx = text_lower.find(key)
             context = text_lower[max(0, idx-20):idx]
             if "no " not in context and "not " not in context and "denies " not in context:
                 found_parts.append(display)
                 
    # Check for Left/Right
    final_parts = []
    for part in set(found_parts):
        # Scan for side in context of the part
        # This is a simple approximation
        side = ""
        if "left " + part.lower() in text_lower: side = "Left "
        elif "right " + part.lower() in text_lower: side = "Right "
        elif "bilateral " + part.lower() in text_lower: side = "Bilateral "
        
        final_parts.append(f"{side}{part}")
        
    return ", ".join(sorted(list(set(final_parts)))) if final_parts else None

def map_pr1_restrictions_to_new_format(
    restrictions: Dict[str, Any],
    other_restrictions_text: str,
    restrictions_duration: str = ""
) -> Dict[str, Any]:
    """Map PR1 restrictions format to the new functional restrictions structure."""
    
    # Initialize all restriction categories
    functional_restrictions = {
        "liftingPushingPulling": {
            "noLiftingOver": False,
            "weightLimit": "",
            "customWeight": ""
        },
        "upperExtremity": {
            "noAboveShoulderReaching": False,
            "aboveShoulderRight": False,
            "aboveShoulderLeft": False,
            "useLimited": False,
            "useLimitedSide": "",
            "useLimitedHours": "",
            "noRepetitiveGripping": False,
            "noRepetitiveGrippingRight": False,
            "noRepetitiveGrippingLeft": False
        },
        "lowerExtremity": {
            "noRepetitiveKneeling": False,
            "walkingLimited": False,
            "walkingLimit": "",
            "walkingCustomMin": "",
            "walkingOther": "",
            "noClimbingStairs": False
        },
        "spinalTrunk": {
            "noRepetitiveBending": False,
            "noRepetitiveTwisting": False,
            "twistingNeck": False,
            "twistingWaist": False
        },
        "positionTolerance": {
            "alternateSittingStanding": False,
            "alternateInterval": "",
            "alternateOther": "",
            "standingLimited": False,
            "standingLimit": "",
            "standingCustomMin": "",
            "sittingLimited": False,
            "sittingLimit": "",
            "sittingCustomMin": ""
        },
        "handFineMotor": {
            "productiveUseEnabled": False,
            "productiveUseMinutes": "",
            "productiveUseRight": False,
            "productiveUseLeft": False
        },
        "workplaceConditions": {
            "noWorkingAtHeights": False,
            "noSafetySensitiveDuties": False
        },
        "otherRestrictions": "",
        "workRestrictionsDuration": ""
    }
    
    if not isinstance(restrictions, dict):
        # Fallback if restrictions is just a string (common in simple extractions)
        functional_restrictions["otherRestrictions"] = str(restrictions) + " " + (other_restrictions_text or "")
        return functional_restrictions

    # Map lifting/pushing/pulling
    lift_pounds = restrictions.get("liftCarryPounds", "")
    if lift_pounds:
        functional_restrictions["liftingPushingPulling"]["noLiftingOver"] = True
        lift_pounds_str = str(lift_pounds).strip().lower()
        if lift_pounds_str in ["5", "10", "15", "25"]:
            functional_restrictions["liftingPushingPulling"]["weightLimit"] = lift_pounds_str
        else:
            functional_restrictions["liftingPushingPulling"]["weightLimit"] = "custom"
            functional_restrictions["liftingPushingPulling"]["customWeight"] = lift_pounds_str
    
    # Map upper extremity - pushing/pulling
    push_right = restrictions.get("pushingPullingRight")
    push_left = restrictions.get("pushingPullingLeft")
    push_bilateral = restrictions.get("pushingPullingBilateral")
    
    if push_right or push_left or push_bilateral:
        functional_restrictions["upperExtremity"]["useLimited"] = True
        if push_bilateral:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "right" # Default to right if both
        elif push_right:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "right"
        elif push_left:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "left"
        
        push_pull_hours = restrictions.get("pushingPullingHours", "")
        if push_pull_hours:
            functional_restrictions["upperExtremity"]["useLimitedHours"] = str(push_pull_hours).strip()
            
    # Map upper extremity - grasping
    grasp_right = restrictions.get("graspingRight")
    grasp_left = restrictions.get("graspingLeft")
    grasp_bilateral = restrictions.get("graspingBilateral")
    
    if grasp_right or grasp_left or grasp_bilateral:
        functional_restrictions["upperExtremity"]["noRepetitiveGripping"] = True
        if grasp_bilateral:
            functional_restrictions["upperExtremity"]["noRepetitiveGrippingRight"] = True
            functional_restrictions["upperExtremity"]["noRepetitiveGrippingLeft"] = True
        else:
            if grasp_right:
                functional_restrictions["upperExtremity"]["noRepetitiveGrippingRight"] = True
            if grasp_left:
                functional_restrictions["upperExtremity"]["noRepetitiveGrippingLeft"] = True

    # Map other fields safely...
    if restrictions.get("kneeling"):
        functional_restrictions["lowerExtremity"]["noRepetitiveKneeling"] = True
        
    walking = restrictions.get("walking", "")
    if walking:
        functional_restrictions["lowerExtremity"]["walkingLimited"] = True
        if "2" in str(walking) and "hour" in str(walking).lower():
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "2hrs"
        elif "4" in str(walking) and "hour" in str(walking).lower():
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "4hrs"
        else:
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "other"
            functional_restrictions["lowerExtremity"]["walkingOther"] = str(walking)
            
    climbing = restrictions.get("climbing", "")
    if climbing and ("avoid" in str(climbing).lower() or "no" in str(climbing).lower()):
        functional_restrictions["lowerExtremity"]["noClimbingStairs"] = True
        
    if restrictions.get("forwardBending"):
        functional_restrictions["spinalTrunk"]["noRepetitiveBending"] = True
        
    twisting = restrictions.get("twisting", "")
    if twisting:
        functional_restrictions["spinalTrunk"]["noRepetitiveTwisting"] = True
        if "neck" in str(twisting).lower():
            functional_restrictions["spinalTrunk"]["twistingNeck"] = True
        else:
            functional_restrictions["spinalTrunk"]["twistingWaist"] = True
            
    standing = restrictions.get("standing", "")
    if standing:
        functional_restrictions["positionTolerance"]["standingLimited"] = True
        if "2" in str(standing) and "hour" in str(standing).lower():
            functional_restrictions["positionTolerance"]["standingLimit"] = "2hrs"
        elif "4" in str(standing) and "hour" in str(standing).lower():
            functional_restrictions["positionTolerance"]["standingLimit"] = "4hrs"
        else:
            functional_restrictions["positionTolerance"]["standingLimit"] = "custom"
            functional_restrictions["positionTolerance"]["standingCustomMin"] = str(standing)
            
    sitting = restrictions.get("sitting", "")
    if sitting:
        functional_restrictions["positionTolerance"]["sittingLimited"] = True
        if "2" in str(sitting) and "hour" in str(sitting).lower():
            functional_restrictions["positionTolerance"]["sittingLimit"] = "2hrs"
        elif "4" in str(sitting) and "hour" in str(sitting).lower():
            functional_restrictions["positionTolerance"]["sittingLimit"] = "4hrs"
        else:
            functional_restrictions["positionTolerance"]["sittingLimit"] = "custom"
            functional_restrictions["positionTolerance"]["sittingCustomMin"] = str(sitting)
            
    keyboarding = restrictions.get("keyboarding", "")
    if keyboarding:
        functional_restrictions["handFineMotor"]["productiveUseEnabled"] = True
        minutes_match = re.search(r'(\d+)\s*(?:min|minute)', str(keyboarding), re.IGNORECASE)
        functional_restrictions["handFineMotor"]["productiveUseMinutes"] = minutes_match.group(1) if minutes_match else "20"
        functional_restrictions["handFineMotor"]["productiveUseRight"] = True
        functional_restrictions["handFineMotor"]["productiveUseLeft"] = True

    # Workplace conditions
    if other_restrictions_text:
        other_lower = str(other_restrictions_text).lower()
        if any(k in other_lower for k in ["height", "ladder", "scaffold"]):
            functional_restrictions["workplaceConditions"]["noWorkingAtHeights"] = True
        if any(k in other_lower for k in ["safety", "machinery", "equipment", "drive", "driving", "vehicle"]):
            functional_restrictions["workplaceConditions"]["noSafetySensitiveDuties"] = True
            
    functional_restrictions["otherRestrictions"] = other_restrictions_text or ""
    functional_restrictions["workRestrictionsDuration"] = restrictions_duration or ""
    
    return functional_restrictions

def extract_work_status_format(
    pr1_payload: Dict[str, Any],
    intake_doc: Optional[Dict[str, Any]] = None,
    soap_doc: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract work status data from PR1 payload and format it according to the Work Status Form structure.
    Replicates logic from old backend pr1_generator.py
    """
    # Map new backend structure to variables
    # section_c in new backend is just "sectionC"
    section_c = pr1_payload.get("sectionC") or pr1_payload.get("section_c_work_status") or {}
    section_b = pr1_payload.get("sectionB") or pr1_payload.get("section_b_evaluation_management") or {}
    # header in new backend might be split. Patient name is top level
    patient_name = pr1_payload.get("patientName")
    
    # 1. Extract Employee Information
    employee_name = patient_name or ""
    if not employee_name and soap_doc:
        employee_name = (soap_doc.get("patient_info") or {}).get("name") or soap_doc.get("patient_name") or ""

    # Claim number often in header or section A
    claim_number = ""
    if "sectionA" in pr1_payload:
        claim_number = pr1_payload["sectionA"].get("claim_number") or ""
    if not claim_number and soap_doc:
        claim_number = soap_doc.get("claim_number") or (soap_doc.get("patient_info") or {}).get("claimNumber") or ""

    # Date of Injury
    date_of_injury = to_yyyy_mm_dd(pr1_payload.get("dateOfInjury"))
    if not date_of_injury and soap_doc:
         date_of_injury = to_yyyy_mm_dd(soap_doc.get("date_of_injury"))
         if not date_of_injury:
              pat_info = soap_doc.get("patient_information") or soap_doc.get("patientInfo")
              if isinstance(pat_info, dict):
                   date_of_injury = to_yyyy_mm_dd(pat_info.get("date_of_injury") or pat_info.get("dateOfInjury"))
    
    if not date_of_injury and intake_doc:
         val = (intake_doc.get("section_d") or {}).get("date_of_injury")
         if val: date_of_injury = to_yyyy_mm_dd(val)
         if not date_of_injury:
             val = intake_doc.get("date_of_injury")
             if val: date_of_injury = to_yyyy_mm_dd(val)
    
    # Aggressive falling back to regex extraction from raw text
    if not date_of_injury and (soap_doc or pr1_payload):
        raw_text = str((soap_doc or {}).get("formatted_soap_note") or (soap_doc or {}).get("transcription") or "")
        search_scope = raw_text[:3000]
        doi_patterns = [
            r'(?:Date of Injury|DOI|Injury Date|Date of Acc|Injury|Date of Accident)[^0-9]*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'(?:Date of Injury|DOI|Injury Date|Date of Acc|Injury|Date of Accident)[^0-9]*?((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})',
            r'(?:injury|accident|incident|onset)\s+(?:occurred|sustained|happened)?\s*(?:on)?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
        ]
        for pat in doi_patterns:
            match = re.search(pat, search_scope, re.IGNORECASE)
            if match:
                raw_date = match.group(1)
                date_of_injury = to_yyyy_mm_dd(raw_date)
                if date_of_injury: break
        
        # Fallback to "today" logic
        if not date_of_injury:
            if re.search(r'(?:injury|accident|sustained).{0,50}\s+today', search_scope, re.IGNORECASE):
                # Use date of service as DOI
                date_of_injury = to_yyyy_mm_dd((soap_doc or {}).get("date_of_service"))
    
    date_of_injury = date_of_injury or ""
    
    # Date of Evaluation (DOE)
    date_of_evaluation = ""
    if soap_doc:
        date_of_evaluation = to_yyyy_mm_dd(soap_doc.get("date_of_service") or soap_doc.get("date") or soap_doc.get("created_at"))
    
    if not date_of_evaluation:
        # Try to find in PR1 payload
        date_of_evaluation = to_yyyy_mm_dd(pr1_payload.get("header_admin", {}).get("date_of_first_examination")) or ""
    
    # Aggressive DOE extraction
    if not date_of_evaluation and soap_doc:
        raw_text = str(soap_doc.get("formatted_soap_note") or soap_doc.get("transcription") or "")
        visit_date_match = re.search(r'(?:Date of Visit|Date of Service|Visit Date|Date):\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', raw_text[:1000], re.IGNORECASE)
        if visit_date_match:
            date_of_evaluation = to_yyyy_mm_dd(visit_date_match.group(1))
    
    if not date_of_evaluation:
        date_of_evaluation = datetime.utcnow().strftime("%Y-%m-%d")

    # Generate Body Parts Injured
    body_parts_injured = extract_body_parts_from_diagnoses(soap_doc, section_b)
    
    # Aggressive Body Part Fallback
    if not body_parts_injured or body_parts_injured == "N/A":
        raw_text = str(soap_doc.get("formatted_soap_note") or soap_doc.get("transcription") or "") if soap_doc else ""
        body_parts_injured = extract_body_parts_fallback(raw_text) or ""
        
        # Keyword-based search
        if not body_parts_injured:
            common_parts = ["Lumbar Spine", "Lower Back", "Back", "Cervical Spine", "Neck", 
                           "Left Shoulder", "Right Shoulder", "Shoulder", "Left Knee", "Right Knee", "Knee",
                           "Left Wrist", "Right Wrist", "Wrist", "Left Ankle", "Right Ankle", "Ankle"]
            found = []
            for part in common_parts:
                if re.search(r'\b' + re.escape(part) + r'\b', raw_text[:2000], re.IGNORECASE):
                    if not re.search(r'(?:no|not|denies)\s+' + re.escape(part), raw_text[:2000], re.IGNORECASE):
                        found.append(part)
            if found:
                body_parts_injured = ", ".join(list(set(found)))

    # Next Follow Up
    next_follow_up = to_yyyy_mm_dd(section_c.get("nextVisitDate"))
    
    # CRITICAL OVERRIDE: 4 weeks check (from old backend)
    try:
        ref_start_date = (
            section_c.get("returnToModifiedDutyDate") or 
            section_c.get("returnToFullDutyDate") or 
            section_c.get("unableToReturnStartDate")
        )
        # Use date_of_evaluation if no specific date
        if not ref_start_date: ref_start_date = date_of_evaluation

        if ref_start_date:
            dt_ref = None
            for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
                 try:
                     dt_ref = datetime.strptime(str(ref_start_date).strip(), fmt)
                     break
                 except: continue
            
            if dt_ref:
                forced_date = dt_ref + timedelta(weeks=4)
                next_follow_up = forced_date.strftime("%Y-%m-%d")
                logger.info(f"WorkStatus extraction: Forced nextFollowUpAppointment to 4 weeks: {next_follow_up}")
    except Exception as e:
        logger.warning(f"Error enforcing 4-week override: {e}")

    # Fallback for next follow up
    if not next_follow_up and soap_doc:
        next_visit = soap_doc.get("next_visit_date") or soap_doc.get("mmi_date")
        if not next_visit:
            patient_status = soap_doc.get("patientStatus")
            if isinstance(patient_status, dict):
                next_visit = patient_status.get("nextVisitDate")
        if next_visit:
            next_follow_up = to_yyyy_mm_dd(next_visit)
            
    employee_info = {
        "employeeName": employee_name,
        "claimNumber": claim_number,
        "dateOfInjury": date_of_injury,
        "dateOfEvaluation": date_of_evaluation,
        "bodyPartsInjured": body_parts_injured,
        "nextFollowUpAppointment": next_follow_up or ""
    }
    
    # 2. Extract Work Status
    return_to_full_duty = section_c.get("returnToFullDuty", False) or section_c.get("return_to_full_duty", False)
    return_to_full_duty_date = section_c.get("returnToFullDutyDate") or section_c.get("return_to_full_duty_date") or ""
    
    unable_to_return = section_c.get("unableToReturnToWork", False)
    unable_to_return_start = section_c.get("unableToReturnStartDate") or ""
    unable_to_return_end = section_c.get("unableToReturnEndDate") or ""
    
    return_with_restrictions = section_c.get("returnToWorkWithRestrictions", False)
    if not return_with_restrictions:
        # If restrictions exist, imply return with restrictions
        if section_c.get("restrictions"): return_with_restrictions = True

    work_status_value = "modifiedDuty" # Default fallback
    full_duty_date = ""
    modified_duty_from = ""
    modified_duty_to = ""
    off_work_from = ""
    off_work_to = ""
    permanent_stationary_date = ""
    
    if return_to_full_duty:
        work_status_value = "fullDuty"
        full_duty_date = to_yyyy_mm_dd(return_to_full_duty_date) or ""
    elif unable_to_return:
        work_status_value = "offWork"
        off_work_from = to_yyyy_mm_dd(unable_to_return_start) or ""
        off_work_to = to_yyyy_mm_dd(unable_to_return_end) or ""
        if not off_work_to and next_follow_up:
            off_work_to = next_follow_up
    elif return_with_restrictions:
        work_status_value = "modifiedDuty"
        modified_duty_from = date_of_evaluation
        if return_to_full_duty_date:
            modified_duty_to = to_yyyy_mm_dd(return_to_full_duty_date) or ""
        elif unable_to_return_end:
            modified_duty_to = to_yyyy_mm_dd(unable_to_return_end) or ""
        elif next_follow_up:
            modified_duty_to = next_follow_up

    # Check MMI
    if soap_doc:
        mmi_date = soap_doc.get("mmi_date")
        patient_status = soap_doc.get("patientStatus")
        if isinstance(patient_status, dict):
            if patient_status.get("maxMedicalImprovementChecked"):
                mmi_date = patient_status.get("maxMedicalImprovementDate") or mmi_date
        
        if mmi_date:
            work_status_value = "permanentStationary"
            permanent_stationary_date = to_yyyy_mm_dd(mmi_date) or ""

    work_status = {
        "status": work_status_value,
        "fullDutyEffectiveDate": full_duty_date,
        "modifiedDutyFrom": modified_duty_from,
        "modifiedDutyTo": modified_duty_to,
        "offWorkFrom": off_work_from,
        "offWorkTo": off_work_to,
        "permanentStationaryDate": permanent_stationary_date
    }

    # 3. Functional Restrictions
    restrictions = section_c.get("restrictions") or {}
    other_restrictions_text = section_c.get("otherRestrictions") or ""
    restrictions_duration = section_c.get("workRestrictionsDuration") or section_c.get("restrictions_duration") or ""
    
    # If restrictions is a string, move it to other_restrictions_text
    if isinstance(restrictions, str):
        if not other_restrictions_text:
            other_restrictions_text = restrictions
        else:
            other_restrictions_text = f"{restrictions} {other_restrictions_text}"
        restrictions = {} # Empty dict to safely map things

    functional_restrictions = map_pr1_restrictions_to_new_format(
        restrictions, 
        other_restrictions_text,
        restrictions_duration
    )
    
    # 4. Provider Info
    physician_info = soap_doc.get("patient_info", {}) if soap_doc else {} 
    # Try PR1 header if available
    if "header_admin" in pr1_payload:
        p_info = pr1_payload["header_admin"].get("physician") or {}
        provider_name = p_info.get("physician_name")
    else:
        # New backend PR1 assumes SOAP has info.
        provider_name = soap_doc.get("examiner") or physician_info.get("primaryTreatingPhysician") or ""
    
    clinic = soap_doc.get("practice_name") or "Clinic" if soap_doc else ""
    phone = soap_doc.get("contact_phone") or ""
    
    provider_info = {
        "providerName": provider_name,
        "clinic": clinic,
        "phone": phone,
        "signature": provider_name,
        "date": date_of_evaluation
    }

    return {
        "employeeInfo": employee_info,
        "workStatus": work_status,
        "functionalRestrictions": functional_restrictions,
        "providerInfo": provider_info
    }


# ============================================
# DB FUNCTIONS
# ============================================

async def save_work_status_form(form_data: Dict[str, Any]) -> str:
    db = get_database()
    if db is None: raise Exception("DB not available")
    collection = db[WORK_STATUS_FORMS_COLLECTION]
    now = datetime.utcnow()
    soap_id = form_data.get("soap_id")
    if soap_id:
        existing = await collection.find_one({"soap_id": soap_id})
        if existing:
            form_data["updated_at"] = now
            form_data["created_at"] = existing.get("created_at", now)
            await collection.update_one({"soap_id": soap_id}, {"$set": form_data})
            return str(existing["_id"])
    form_data["created_at"] = now
    form_data["updated_at"] = now
    res = await collection.insert_one(form_data)
    return str(res.inserted_id)

async def get_latest_work_status_form() -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[WORK_STATUS_FORMS_COLLECTION].find_one(sort=[("created_at", -1)])
    return serialize_mongodb_doc(doc)

async def get_all_saved_soap_ids() -> List[str]:
    db = get_database()
    if db is None: return []
    cursor = db[WORK_STATUS_FORMS_COLLECTION].find({}, {"soap_id": 1, "_id": 0})
    docs = await cursor.to_list(length=1000)
    return [str(d["soap_id"]) for d in docs if d.get("soap_id")]

async def get_saved_form_by_soap_id(soap_id: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[WORK_STATUS_FORMS_COLLECTION].find_one({"soap_id": soap_id})
    return serialize_mongodb_doc(doc)


# ============================================
# MAIN PIPELINE FUNCTION
# ============================================

async def process_work_status_generation(soap_id: str):
    """
    Generate Work Status form from SOAP note logic via PR1 Pipeline.
    Restores legacy logic using extract_work_status_format
    """
    try:
        logger.info(f"🔄 Processing Work Status generation for SOAP {soap_id} (Enhanced Pipeline Mode)")
        
        soap_doc = await soap_service.get_soap_note_by_id(soap_id)
        if not soap_doc:
            logger.warning(f"SOAP note {soap_id} not found for Work Status generation")
            return

        # 1. Try to get just-generated PR1 from cache (Best source of truth with GPT enrichment)
        pr1_data = None
        saved_pr1 = await pr1_service.get_saved_pr1(soap_id)
        if saved_pr1 and saved_pr1.get("form_data"):
            logger.info(f"✅ Found saved PR1 for {soap_id}, using it for Work Status source.")
            pr1_data = saved_pr1.get("form_data")
        else:
            # Fallback: Build from scratch (might miss GPT enrichment if DB is stale)
            logger.info(f"⚠️ No saved PR1 found, building from scratch (might be less accurate)...")
            pr1_data = await pr1_service.build_pr1_payload(None, None, soap_doc, {})
        
        # 2. Extract Work Status info using the Ported Logic
        work_status_data = extract_work_status_format(pr1_data, None, soap_doc)
        
        # --- POST-PROCESSING FIX (Ported from Old Backend) ---
        # This block performs aggressive regex extraction on raw text to fill gaps
        # left by structured extraction.
        
        ws_data = work_status_data
        restrictions = ws_data.get("functionalRestrictions", {})
        other_text = restrictions.get("otherRestrictions", "") or ""
        
        # 2.1 Fetch and Construct Full Raw Text
        full_raw_text = ""
        raw_texts = []
        if soap_doc.get("formatted_soap_note"): raw_texts.append(str(soap_doc.get("formatted_soap_note")))
        if soap_doc.get("subjective"): raw_texts.append(str(soap_doc.get("subjective")))
        if soap_doc.get("history") or soap_doc.get("historyOfPresentIllness"): raw_texts.append(str(soap_doc.get("history") or soap_doc.get("historyOfPresentIllness")))
        if soap_doc.get("plan"): raw_texts.append(str(soap_doc.get("plan")))
        if soap_doc.get("transcription"): raw_texts.append(str(soap_doc.get("transcription")))
        full_raw_text = "\n".join(raw_texts)
        
        # 2.2 Fix Date of Injury (DOI) Aggressive Extraction
        emp_info = ws_data.get("employeeInfo", {})
        if not emp_info.get("dateOfInjury"):
             search_scope = full_raw_text[:3000] if len(full_raw_text) > 3000 else full_raw_text
             doi_patterns = [
                 r'(?:Date of Injury|DOI|Injury Date|Date of Acc|Injury|Date of Accident)[^0-9]*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                 r'(?:injury|accident|incident|onset)\s+(?:occurred|sustained|happened)?\s*(?:on)?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
             ]
             for pat in doi_patterns:
                 match = re.search(pat, search_scope, re.IGNORECASE)
                 if match:
                     raw_date = match.group(1)
                     formatted = to_yyyy_mm_dd(raw_date)
                     if formatted:
                         emp_info["dateOfInjury"] = formatted
                         logger.info(f"WorkStatus Fix: Extracted DOI {formatted}")
                         break
                         
        # 2.3 Fix Employee Name Fallback
        if not emp_info.get("employeeName"):
             name_match = re.search(r'(?:Patient Name|Patient|Name):\s*([A-Za-z\s\.]+)', full_raw_text[:1000], re.IGNORECASE)
             if name_match:
                 extracted_name = name_match.group(1).strip()
                 if len(extracted_name) < 40 and "Date" not in extracted_name:
                     emp_info["employeeName"] = extracted_name

        # 2.4 Fix Date of Evaluation (DOE)
        if not emp_info.get("dateOfEvaluation"):
            # Look for "Date of Visit: ..." or "Date: ..." in the first 1000 chars
             visit_date_match = re.search(r'(?:Date of Visit|Date of Service|Visit Date|Date):\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', full_raw_text[:1000], re.IGNORECASE)
             if visit_date_match:
                 raw_date = visit_date_match.group(1)
                 formatted = to_yyyy_mm_dd(raw_date)
                 if formatted:
                     emp_info["dateOfEvaluation"] = formatted
                     
        # 2.5 Fix Body Parts Injured Aggressive Extraction
        if not emp_info.get("bodyPartsInjured") or emp_info.get("bodyPartsInjured") == "N/A":
             body_part = extract_body_parts_fallback(full_raw_text)
             if body_part:
                 emp_info["bodyPartsInjured"] = body_part
             else:
                 common_parts = ["Lumbar Spine", "Lower Back", "Back", "Cervical Spine", "Neck", 
                                "Left Shoulder", "Right Shoulder", "Shoulder", "Left Knee", "Right Knee", "Knee",
                                "Left Wrist", "Right Wrist", "Wrist", "Left Ankle", "Right Ankle", "Ankle"]
                 found_parts = []
                 search_scope = full_raw_text[:2000] if full_raw_text else ""
                 for part in common_parts:
                     if re.search(r'\\b' + re.escape(part) + r'\\b', search_scope, re.IGNORECASE):
                          if not re.search(r'(?:no|not|denies)\s+' + re.escape(part), search_scope, re.IGNORECASE):
                               found_parts.append(part)
                 if found_parts:
                     emp_info["bodyPartsInjured"] = ", ".join(list(set(found_parts)))
        
        # 3. Add metadata and save
        work_status_data["soap_id"] = soap_id
        work_status_data["status"] = "completed"
        
        # Save
        await save_work_status_form(work_status_data)
        logger.info(f"✅ Generated Work Status via Enhanced Pipeline for {soap_id}")

    except Exception as e:
        logger.error(f"Error generating Work Status in service: {e}")
        import traceback
        logger.error(traceback.format_exc())
