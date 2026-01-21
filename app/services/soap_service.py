import logging
import asyncio
import re
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.database import get_database
from app.config import settings
from app.utils.soap_utils import (
    extract_soap_sections_from_formatted_note,
    validate_all_cpt_codes_in_soap,
    aggressive_validate_rfa_supportive_cpts
)
from app.utils.text_utils import format_clinical_data, fix_terms
from app.services.transcription_service import TranscriptionService
from app.services.intake_service import IntakeService
from app.prompts import ORTHOPEDIC_SOAP_SYSTEM_PROMPT, ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
from openai import OpenAI
import httpx

logger = logging.getLogger(__name__)

SOAP_COLLECTION = 'soap_notes'

class SOAPService:
    @staticmethod
    async def save_soap_note(data: dict) -> dict:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        data["created_at"] = datetime.utcnow()
        result = await db[SOAP_COLLECTION].insert_one(data)
        data["_id"] = str(result.inserted_id)
        return data

    @staticmethod
    async def get_by_id(soap_id: str) -> Optional[dict]:
        db = get_database()
        if db is None:
            return None
        try:
            doc = await db[SOAP_COLLECTION].find_one({"_id": ObjectId(soap_id)})
            if doc:
                doc["_id"] = str(doc["_id"])
            return doc
        except:
            return None

    @staticmethod
    async def get_by_transcription_id(transcription_id: str) -> Optional[dict]:
        db = get_database()
        if db is None:
            return None
        doc = await db[SOAP_COLLECTION].find_one({"transcription_id": transcription_id}, sort=[("created_at", -1)])
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    @staticmethod
    async def get_all_by_transcription_id(transcription_id: str) -> List[dict]:
        db = get_database()
        if db is None:
            return []
        cursor = db[SOAP_COLLECTION].find({"transcription_id": transcription_id}).sort("created_at", -1)
        docs = await cursor.to_list(length=100)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return docs

    @staticmethod
    async def create_pending_soap_note(user_id: Optional[str], transcription_id: str, patient_info: dict = None) -> dict:
        data = {
            "userId": user_id,
            "transcription_id": transcription_id,
            "patient_info": patient_info or {},
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        return await SOAPService.save_soap_note(data)

    @staticmethod
    def create_openai_client():
        http_client = httpx.Client(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=5, max_connections=10))
        return OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=2, timeout=120.0, http_client=http_client)

    @staticmethod
    async def generate_soap_note(soap_request: Any, intake_doc: Optional[dict] = None) -> Dict[str, Any]:
        """Core SOAP generation logic using OpenAI"""
        try:
            corrected_transcription = fix_terms(soap_request.transcription)
            
            patient_context = ""
            header_section = ""
            if soap_request.patient:
                p = soap_request.patient
                info = []
                if p.name: info.append(f"Name: {p.name}")
                if p.age: info.append(f"Age: {p.age}")
                if p.gender: info.append(f"Gender: {p.gender}")
                if info:
                    patient_context = "**PATIENT INFORMATION:**\n" + "\n".join(info)
                    header_section = f"Patient: {', '.join(info)}\n"
            
            if soap_request.date_of_service: header_section += f"Date of Service: {soap_request.date_of_service}\n"
            if soap_request.location: header_section += f"Location: {soap_request.location}\n"
            if soap_request.reason_for_visit: header_section += f"Reason for Visit: {soap_request.reason_for_visit}\n"
            
            intake_form_text = ""
            if intake_doc:
                from app.utils.text_utils import format_clinical_data
                intake_form_text = format_clinical_data(intake_doc)
            
            system_prompt = soap_request.system_prompt or ORTHOPEDIC_SOAP_SYSTEM_PROMPT
            user_prompt_template = soap_request.user_prompt_template or ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
            
            user_prompt = user_prompt_template.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_form_text
            )
            
            client = SOAPService.create_openai_client()
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=4000
            )
            
            formatted_note = response.choices[0].message.content.strip()
            
            # Post-processing
            formatted_note = validate_all_cpt_codes_in_soap(formatted_note)
            formatted_note = aggressive_validate_rfa_supportive_cpts(formatted_note)
            
            sections = extract_soap_sections_from_formatted_note(formatted_note)
            
            return {
                **sections,
                "formatted_soap_note": formatted_note,
                "transcription": soap_request.transcription,
                "corrected_transcription": corrected_transcription,
                "patient_info": soap_request.patient.model_dump() if soap_request.patient else {},
                "status": "completed",
                "created_at": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Error in generation: {e}")
            raise e

    @staticmethod
    async def run_full_workflow(transcription_id: str, user_id: Optional[str] = "system"):
        """Background workflow for SOAP + PR1 + Work Status"""
        logger.info(f"Starting background workflow for {transcription_id}")
        
        # 1. Fetch Transcription
        transcription = await TranscriptionService.get_by_id(transcription_id)
        if not transcription:
            logger.error(f"Transcription {transcription_id} not found")
            return
            
        # 2. Fetch Latest Intake
        intake_doc = await IntakeService.get_latest()
        
        # 3. Create Pending SOAP
        soap_pending = await SOAPService.create_pending_soap_note(user_id, transcription_id, transcription.get("patient_info"))
        soap_id = soap_pending["_id"]
        
        try:
            # 4. Generate SOAP
            from app.schemas.soap_schema import SOAPRequest, PatientInfo
            p_info = transcription.get("patient_info") or {}
            soap_req = SOAPRequest(
                transcription=transcription["transcription"],
                patient=PatientInfo(name=p_info.get("name"), age=p_info.get("age"), gender=p_info.get("gender")),
                transcription_id=transcription_id
            )
            
            soap_result = await SOAPService.generate_soap_note(soap_req, intake_doc)
            
            # 5. Update SOAP
            db = get_database()
            await db[SOAP_COLLECTION].update_one(
                {"_id": ObjectId(soap_id)},
                {"$set": {**soap_result, "updated_at": datetime.utcnow()}}
            )
            
            # 6. Trigger PR1 and Work Status (Placeholder for now)
            logger.info(f"SOAP {soap_id} generated. PR1/WS would be triggered here.")
            
        except Exception as e:
            logger.error(f"Workflow failed for {transcription_id}: {e}")
            db = get_database()
            await db[SOAP_COLLECTION].update_one(
                {"_id": ObjectId(soap_id)},
                {"$set": {"status": "error", "error_message": str(e)}}
            )

    @staticmethod
    async def update_soap_note(soap_id: str, update_data: dict) -> bool:
        db = get_database()
        if db is None: return False
        try:
            update_data["updated_at"] = datetime.utcnow()
            result = await db[SOAP_COLLECTION].update_one(
                {"_id": ObjectId(soap_id)},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except:
            return False

    @staticmethod
    async def delete_soap_note(soap_id: str) -> bool:
        db = get_database()
        if db is None: return False
        try:
            result = await db[SOAP_COLLECTION].delete_one({"_id": ObjectId(soap_id)})
            return result.deleted_count > 0
        except:
            return False

    @staticmethod
    async def get_stats() -> dict:
        db = get_database()
        if db is None: return {}
        total = await db[SOAP_COLLECTION].count_documents({})
        cursor = db[SOAP_COLLECTION].find().sort("created_at", -1).limit(10)
        recent = await cursor.to_list(length=10)
        for doc in recent: doc["_id"] = str(doc["_id"])
        return {
            "total_soap_notes": total,
            "recent_soap_notes": recent
        }
