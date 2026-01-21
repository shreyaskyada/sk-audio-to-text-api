import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

PR2_FORMS_COLLECTION = 'pr2_forms'

class PR2Service:
    @staticmethod
    def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
        if doc is None: return None
        serialized = {}
        for key, value in doc.items():
            if isinstance(value, ObjectId):
                serialized[key] = str(value)
            elif isinstance(value, datetime):
                serialized[key] = value.isoformat()
            elif isinstance(value, dict):
                serialized[key] = PR2Service.serialize_doc(value)
            elif isinstance(value, list):
                serialized[key] = [
                    PR2Service.serialize_doc(item) if isinstance(item, dict) else
                    str(item) if isinstance(item, (ObjectId, datetime)) else item
                    for item in value
                ]
            else:
                serialized[key] = value
        return serialized

    @staticmethod
    async def save_form(form_data: Dict[str, Any]) -> str:
        db = get_database()
        if db is None: raise Exception("Database not available")
        form_data["created_at"] = datetime.utcnow()
        result = await db[PR2_FORMS_COLLECTION].insert_one(form_data)
        return str(result.inserted_id)

    @staticmethod
    async def get_by_soap_id(soap_id: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None: return None
        doc = await db[PR2_FORMS_COLLECTION].find_one({"soap_id": soap_id})
        return PR2Service.serialize_doc(doc)

    @staticmethod
    async def get_latest() -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None: return None
        doc = await db[PR2_FORMS_COLLECTION].find_one(sort=[("created_at", -1)])
        return PR2Service.serialize_doc(doc)

    @staticmethod
    async def get_all_saved_soap_ids() -> List[str]:
        db = get_database()
        if db is None: return []
        cursor = db[PR2_FORMS_COLLECTION].find({}, {"soap_id": 1, "_id": 0})
        docs = await cursor.to_list(length=1000)
        return [str(doc["soap_id"]) for doc in docs if doc.get("soap_id")]

    @staticmethod
    def parse_subjective_from_text(text: str) -> Optional[Dict[str, Any]]:
        if not text or not isinstance(text, str) or not text.strip():
            return None
        
        result = {
            "chief_complaint": None,
            "brief_history": None,
            "pain_level": None,
            "mechanism_of_injury": None,
            "other_subjective": None
        }
        
        # Chief Complaint
        cc_patterns = [
            r'Chief\s+Complaint\s*:\s*\n?\s*=\s*(.*?)(?=History|HPI|Objective|##|OBJECTIVE|$)',
            r'Chief\s+Complaint\s*:\s*(.*?)(?=History|HPI|Objective|##|OBJECTIVE|$)',
        ]
        for pattern in cc_patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = re.sub(r'^=\s*', '', match.group(1).strip())
                extracted = re.sub(r'\s+', ' ', extracted).strip()
                if len(extracted) > 3:
                    result["chief_complaint"] = extracted
                    break
        
        # History
        hpi_patterns = [
            r'History\s+of\s+Present\s+Illness\s*\(HPI\)\s*:\s*\n?\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Assessment|Treatment|$)',
            r'HPI\s*:\s*\n?\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Assessment|Treatment|$)',
            r'Brief\s+History\s*:\s*(.*?)(?=Objective|##|OBJECTIVE|Assessment|Treatment|$)',
        ]
        for pattern in hpi_patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = re.sub(r'^[=\s\n]+', '', match.group(1).strip())
                extracted = re.sub(r'\n=\s*', '\n', extracted)
                extracted = re.sub(r'\s+', ' ', extracted).strip()
                if len(extracted) > 10:
                    result["brief_history"] = extracted
                    break
        
        # Mechanism of Injury
        mechanism_patterns = [
            r'mechanism\s+of\s+injury[:\s]*\n?\s*=\s*(.*?)(?=\.\s|Objective|##|OBJECTIVE|$)',
            r'(?:acute\s+injury|injury\s+occurred|while\s+training|snapping\s+sensation)[^.]*\.',
        ]
        for pattern in mechanism_patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = match.group(1).strip() if match.groups() else match.group(0).strip()
                extracted = re.sub(r'^=\s*', '', extracted)
                extracted = re.sub(r'\s+', ' ', extracted).strip()
                if len(extracted) > 10:
                    result["mechanism_of_injury"] = extracted
                    break

        # Pain Level
        pain_patterns = [r'pain\s+level[:\s]*(\d+(?:\/\d+)?)', r'pain[:\s]*(\d+(?:\/\d+)?)']
        for pattern in pain_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["pain_level"] = match.group(1).strip()
                break
                
        return result if any(v for v in result.values()) else None

    @staticmethod
    def parse_objective_from_text(text: str) -> Optional[Dict[str, Any]]:
        if not text or not isinstance(text, str) or not text.strip():
            return None
        
        result = {
            "physical_examination": None,
            "vital_signs": None,
            "test_results": None,
            "other_objective": None
        }
        
        # Physical Examination
        exam_patterns = [
            r'Physical\s+Examination[:\s]*\n?\s*=\s*(.*?)(?=Vital|Test|Objective|##|Assessment|$)',
            r'Physical\s+Exam[:\s]*(.*?)(?=Vital|Test|Objective|##|Assessment|$)',
        ]
        for pattern in exam_patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = re.sub(r'^[=\s\n]+', '', match.group(1).strip())
                extracted = re.sub(r'\s+', ' ', extracted).strip()
                if len(extracted) > 10:
                    result["physical_examination"] = extracted
                    break
        
        # Vital Signs
        vitals_patterns = [r'Vital\s+Signs[:\s]*(.*?)(?=Physical|Test|Objective|##|$)', r'Vitals[:\s]*(.*?)(?=Physical|Test|Objective|##|$)']
        for pattern in vitals_patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = re.sub(r'^[=\s\n]+', '', match.group(1).strip())
                if len(extracted) > 5:
                    result["vital_signs"] = extracted
                    break
                    
        return result if any(v for v in result.values()) else None

    @staticmethod
    def build_pr2_from_data(intake_doc, follow_doc, soap_doc, pr1_doc=None) -> Dict[str, Any]:
        """Build PR2 form data from other documents"""
        logger.info("Building PR2 from existing data")
        
        # Extract patient demographics
        patient_name = ""
        patient_dob = ""
        patient_doi = ""
        patient_sex = ""
        patient_occupation = ""
        claims_admin = ""
        claim_number = ""
        
        if intake_doc:
            section_a = intake_doc.get("section_a", {})
            patient_name = section_a.get("full_name", "")
            patient_dob = section_a.get("date_of_birth", "")
            patient_sex = section_a.get("gender", "") or section_a.get("sex", "")
            
            section_b = intake_doc.get("section_b", {})
            patient_occupation = section_b.get("occupation", "")
            
            section_c = intake_doc.get("section_c", {})
            patient_doi = section_c.get("date_of_injury", "")
            
            section_d = intake_doc.get("section_d", {})
            claims_admin = section_d.get("claims_administrator", "")
            claim_number = section_d.get("claim_number", "")
            
        elif soap_doc:
            p_info = soap_doc.get("patient_info", {})
            if isinstance(p_info, dict):
                patient_name = p_info.get("name", "")
                patient_dob = p_info.get("dob", "")
                patient_doi = soap_doc.get("date_of_injury", "")
        
        name_parts = patient_name.split()
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[-1] if len(name_parts) > 1 else (name_parts[0] if name_parts else "")
        middle = name_parts[1][0] if len(name_parts) > 2 else ""
        
        # Build base PR2 structure
        pr2_data = {
            "reportType": {
                "periodicReport": True,
                "changeInWorkStatus": False
            },
            "patient": {
                "lastName": last_name,
                "firstName": first_name,
                "middleInitial": middle,
                "dateOfBirth": patient_dob,
                "dateOfInjury": patient_doi,
                "sex": patient_sex,
                "occupation": patient_occupation
            },
            "claimsAdministrator": {
                "name": claims_admin,
                "claimNumber": claim_number
            },
            "subjectiveComplaints": "",
            "objectiveFindings": "",
            "diagnoses": [],
            "treatmentPlan": "",
            "workStatus": {},
            "physician": {
                "physicianName": soap_doc.get("physician_name", ""),
                "dateOfExam": datetime.utcnow().strftime("%Y-%m-%d")
            }
        }
        
        # Extract content from SOAP note
        if soap_doc:
            pr2_data["subjectiveComplaints"] = soap_doc.get("subjective", "")
            pr2_data["objectiveFindings"] = soap_doc.get("objective", "")
            pr2_data["treatmentPlan"] = soap_doc.get("plan", "")
            
            # Diagnoses
            soap_dxs = soap_doc.get("diagnoses") or []
            normalized_dxs = []
            for dx in soap_dxs:
                if isinstance(dx, dict):
                    normalized_dxs.append({
                        "diagnosis": dx.get("condition") or dx.get("diagnosis", ""),
                        "icd10": dx.get("icd10") or ""
                    })
                elif isinstance(dx, str):
                    normalized_dxs.append({"diagnosis": dx, "icd10": ""})
            pr2_data["diagnoses"] = normalized_dxs[:12]
            
            # Work Status
            ws = soap_doc.get("work_status_info") or {}
            pr2_data["workStatus"] = {
                "limitationsRestrictions": ws.get("restrictions", ""),
                "returnToFullDutyOn": ws.get("full_duty_date", "")
            }
            
        return pr2_data
