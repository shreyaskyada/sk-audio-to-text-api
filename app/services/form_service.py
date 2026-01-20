import logging
from datetime import datetime
from typing import Dict, Any, Optional
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

INTAKE_FORMS_COLLECTION = 'intake_forms'
FOLLOWUP_FORMS_COLLECTION = 'followup_intake_forms'

def serialize_mongodb_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MongoDB document to JSON-serializable format"""
    if doc is None: return None
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            serialized[key] = str(value)
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, dict):
            serialized[key] = serialize_mongodb_doc(value)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_mongodb_doc(item) if isinstance(item, dict) else
                str(item) if isinstance(item, (ObjectId, datetime)) else item
                for item in value
            ]
        else:
            serialized[key] = value
    return serialized

async def create_intake_form(data: dict) -> str:
    """Create a new intake form"""
    db = get_database()
    if db is None: raise Exception("Database not available")
    data["created_at"] = datetime.utcnow()
    result = await db[INTAKE_FORMS_COLLECTION].insert_one(data)
    return str(result.inserted_id)

async def get_latest_intake_form() -> Optional[dict]:
    """Get the most recently created intake form"""
    db = get_database()
    if db is None: return None
    latest_form = await db[INTAKE_FORMS_COLLECTION].find_one(sort=[("created_at", -1)])
    return serialize_mongodb_doc(latest_form)

async def create_followup_form(data: dict) -> str:
    """Create a new follow-up intake form"""
    db = get_database()
    if db is None: raise Exception("Database not available")
    data["created_at"] = datetime.utcnow()
    result = await db[FOLLOWUP_FORMS_COLLECTION].insert_one(data)
    return str(result.inserted_id)

async def get_latest_followup_form() -> Optional[dict]:
    """Get the most recently created follow-up intake form"""
    db = get_database()
    if db is None: return None
    latest_form = await db[FOLLOWUP_FORMS_COLLECTION].find_one(sort=[("created_at", -1)])
    return serialize_mongodb_doc(latest_form)
