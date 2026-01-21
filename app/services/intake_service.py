import logging
from datetime import datetime
from typing import Optional, Dict, Any
from app.database import get_database

logger = logging.getLogger(__name__)

class IntakeService:
    @staticmethod
    async def save_form(data: Dict[str, Any]) -> Dict[str, Any]:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        data["created_at"] = datetime.utcnow()
        result = await db["intake_forms"].insert_one(data)
        data["_id"] = str(result.inserted_id)
        return data

    @staticmethod
    async def get_latest() -> Optional[Dict[str, Any]]:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        doc = await db["intake_forms"].find_one(sort=[("created_at", -1)])
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
