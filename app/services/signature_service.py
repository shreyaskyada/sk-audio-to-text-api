import logging
from datetime import datetime
from bson import ObjectId
from typing import Optional, Dict, Any
from app.database import get_database

logger = logging.getLogger(__name__)

SIGNATURES_COLLECTION = 'patient_signatures'

async def save_signature(soap_id: str, signature_data: str, patient_name: Optional[str] = None, form_type: str = "PR1") -> str:
    db = get_database()
    if db is None: raise Exception("DB not available")
    doc = {
        "soap_id": soap_id,
        "signature_data": signature_data,
        "patient_name": patient_name,
        "form_type": form_type,
        "created_at": datetime.utcnow()
    }
    res = await db[SIGNATURES_COLLECTION].insert_one(doc)
    return str(res.inserted_id)

async def get_latest_signature_by_soap_id(soap_id: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[SIGNATURES_COLLECTION].find_one({"soap_id": soap_id}, sort=[("created_at", -1)])
    if doc: doc["_id"] = str(doc["_id"])
    return doc
