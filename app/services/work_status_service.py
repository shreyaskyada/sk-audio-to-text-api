import logging
import re
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'

class WorkStatusService:
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
                serialized[key] = WorkStatusService.serialize_doc(value)
            elif isinstance(value, list):
                serialized[key] = [
                    WorkStatusService.serialize_doc(item) if isinstance(item, dict) else
                    str(item) if isinstance(item, (ObjectId, datetime)) else item
                    for item in value
                ]
            else:
                serialized[key] = value
        return serialized

    @staticmethod
    async def get_by_soap_id(soap_id: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None: return None
        doc = await db[WORK_STATUS_FORMS_COLLECTION].find_one({"soap_id": soap_id})
        return WorkStatusService.serialize_doc(doc)

    @staticmethod
    async def get_all_saved_soap_ids() -> List[str]:
        db = get_database()
        if db is None: return []
        cursor = db[WORK_STATUS_FORMS_COLLECTION].find({}, {"soap_id": 1, "_id": 0})
        docs = await cursor.to_list(length=1000)
        return [str(doc["soap_id"]) for doc in docs if doc.get("soap_id")]

    @staticmethod
    async def get_latest() -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None: return None
        doc = await db[WORK_STATUS_FORMS_COLLECTION].find_one(sort=[("created_at", -1)])
        return WorkStatusService.serialize_doc(doc)

    @staticmethod
    async def get_by_id(form_id: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None: return None
        try:
            doc = await db[WORK_STATUS_FORMS_COLLECTION].find_one({"_id": ObjectId(form_id)})
            return WorkStatusService.serialize_doc(doc)
        except:
            return None

    @staticmethod
    async def save_form(soap_id: str, form_data: Dict[str, Any]) -> str:
        db = get_database()
        if db is None: raise Exception("Database not available")
        
        now = datetime.utcnow()
        existing = await db[WORK_STATUS_FORMS_COLLECTION].find_one({"soap_id": soap_id})
        
        if existing:
            form_data["updated_at"] = now
            form_data["created_at"] = existing.get("created_at", now)
            await db[WORK_STATUS_FORMS_COLLECTION].update_one(
                {"soap_id": soap_id}, {"$set": form_data}
            )
            return str(existing["_id"])
        else:
            form_data["soap_id"] = soap_id
            form_data["created_at"] = now
            form_data["updated_at"] = now
            result = await db[WORK_STATUS_FORMS_COLLECTION].insert_one(form_data)
            return str(result.inserted_id)

    @staticmethod
    async def process_extraction(soap_id: str, use_latest_intake: bool = False, use_latest_followup: bool = False, flags: Dict = None):
        """
        Extraction logic from soap note.
        This normally calls PR1 generator's extraction.
        """
        # (This is a simplified version, it should import from pr1_service in a full implementation)
        return {"work_status_data": {}, "status": "pending_implementation"}
