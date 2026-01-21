import logging
from datetime import datetime
from typing import Optional, Dict, Any
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

SIGNATURES_COLLECTION = 'patient_signatures'

class PatientService:
    @staticmethod
    async def save_signature(data: Dict[str, Any]) -> str:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        data["created_at"] = datetime.utcnow()
        result = await db[SIGNATURES_COLLECTION].insert_one(data)
        return str(result.inserted_id)

    @staticmethod
    async def get_signature_by_soap_id(soap_id: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None:
            return None
        doc = await db[SIGNATURES_COLLECTION].find_one(
            {"soap_id": soap_id},
            sort=[("created_at", -1)]
        )
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
