import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.database import get_database
from app.schemas.pr1_schema import PR1GenerateRequest
from app.services.intake_service import IntakeService
from app.services.followup_service import FollowupService
from app.services.soap_service import SOAPService

logger = logging.getLogger(__name__)

SAVED_PR1_COLLECTION = 'saved_pr1_forms'

class PR1Service:
    @staticmethod
    async def save_pr1(soap_id: str, patient_name: str, form_data: dict, soap_data: dict = None) -> str:
        db = get_database()
        if db is None: raise Exception("Database not available")
        
        doc = {
            "soap_id": soap_id,
            "patient_name": patient_name,
            "form_data": form_data,
            "soap_data": soap_data,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db[SAVED_PR1_COLLECTION].replace_one(
            {"soap_id": soap_id},
            doc,
            upsert=True
        )
        return soap_id

    @staticmethod
    async def get_by_soap_id(soap_id: str) -> Optional[dict]:
        db = get_database()
        if db is None: return None
        doc = await db[SAVED_PR1_COLLECTION].find_one({"soap_id": soap_id})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    @staticmethod
    def format_clinical_data(val: Any) -> str:
        if val is None: return ""
        if isinstance(val, str): return val.strip()
        if isinstance(val, (int, float, bool)): return str(val)
        if isinstance(val, list):
            parts = [PR1Service.format_clinical_data(item) for item in val if item]
            return ", ".join(parts) if all(len(p) < 40 for p in parts) else "\n".join(parts)
        if isinstance(val, dict):
            return " | ".join([f"{k}: {PR1Service.format_clinical_data(v)}" for k, v in val.items() if v])
        return str(val).strip()

    @staticmethod
    def build_pr1_payload(intake: Optional[dict], followup: Optional[dict], soap: Optional[dict], flags: Optional[dict] = None) -> dict:
        """Logic from pr1_generator.py to build the PR1 structure"""
        flags = flags or {}
        
        # 1. Header Information
        patient_name = ""
        patient_dob = ""
        patient_doi = ""
        claim_number = ""
        employer = ""
        
        if intake:
            section_a = intake.get("section_a", {})
            patient_name = section_a.get("full_name", "")
            patient_dob = section_a.get("date_of_birth", "")
            
            section_c = intake.get("section_c", {})
            patient_doi = section_c.get("date_of_injury", "")
            
            section_d = intake.get("section_d", {})
            claim_number = section_d.get("claim_number", "")
            
            section_b = intake.get("section_b", {})
            employer = section_b.get("employer_name", "")
        
        if soap and not patient_name:
            p_info = soap.get("patient_info", {})
            patient_name = p_info.get("name", "")
            patient_dob = p_info.get("dob", "")
            patient_doi = soap.get("date_of_injury", "")
            claim_number = soap.get("claim_number", "")
            employer = soap.get("employer", "")

        # 2. Section A: RFA
        rfa_section = {
            "patientName": patient_name,
            "requests": []
        }
        if soap and soap.get("rfa_items"):
            for item in soap["rfa_items"]:
                rfa_section["requests"].append({
                    "serviceRequested": item.get("service_or_good"),
                    "cpt": item.get("cpt_or_hcpcs"),
                    "diagnosis": soap.get("assessment", "")
                })

        # 3. Subjective
        subjective = ""
        if soap:
            subjective = soap.get("subjective", "")
        
        # 4. Objective
        objective = ""
        if soap:
            objective = soap.get("objective", "")

        # 5. Assessment
        primary_dx = ""
        secondary_dx = ""
        if soap and soap.get("diagnoses"):
            dxs = soap["diagnoses"]
            if len(dxs) > 0: primary_dx = f"{dxs[0].get('icd10')} - {dxs[0].get('condition')}"
            if len(dxs) > 1: secondary_dx = f"{dxs[1].get('icd10')} - {dxs[1].get('condition')}"

        # 6. Plan
        plan = ""
        if soap:
            plan = soap.get("plan", "")

        return {
            "header": {
                "patient_name": patient_name,
                "dob": patient_dob,
                "date_of_injury": patient_doi,
                "claim_number": claim_number,
                "employer": employer
            },
            "section_a_rfa": rfa_section,
            "section_b_subjective": subjective,
            "section_c_objective": objective,
            "section_d_assessment": {
                "primary_diagnosis": primary_dx,
                "secondary_diagnosis": secondary_dx
            },
            "section_e_plan": plan,
            "checkboxes": {
                "progress_report": True,
                "request_for_authorization": len(rfa_section["requests"]) > 0,
                "change_in_work_status": True
            }
        }
