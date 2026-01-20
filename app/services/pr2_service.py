import logging
from datetime import datetime
from bson import ObjectId
from typing import Optional, Dict, Any, List
from app.database import get_database

logger = logging.getLogger(__name__)

PR2_FORMS_COLLECTION = 'pr2_forms'

async def save_pr2_form(form_data: Dict[str, Any]) -> str:
    db = get_database()
    if db is None: raise Exception("DB not available")
    form_data["created_at"] = datetime.utcnow()
    res = await db[PR2_FORMS_COLLECTION].insert_one(form_data)
    return str(res.inserted_id)

async def get_pr2_form_by_id(form_id: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[PR2_FORMS_COLLECTION].find_one({"_id": ObjectId(form_id)})
    if doc: doc["_id"] = str(doc["_id"])
    return doc

async def get_latest_pr2_form() -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[PR2_FORMS_COLLECTION].find_one(sort=[("created_at", -1)])
    if doc: doc["_id"] = str(doc["_id"])
    return doc
