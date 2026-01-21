
import logging
from typing import Optional
from bson import ObjectId
from datetime import datetime
from app.mongodb import get_database

logger = logging.getLogger(__name__)

FOLLOWUP_FORMS_COLLECTION = 'followup_intake_forms'

async def fetch_followup_form_by_id(followup_id: str) -> Optional[dict]:
    """Fetch follow-up form from MongoDB by ID"""
    try:
        db = get_database()
        if db is None:
            logger.warning("Database connection not available")
            return None
        
        collection = db[FOLLOWUP_FORMS_COLLECTION]
        doc = await collection.find_one({"_id": ObjectId(followup_id)})
        
        if doc:
            doc["_id"] = str(doc["_id"])
            logger.info(f"✅ Fetched follow-up form with ID: {doc['_id']}")
            return doc
        return None
    except Exception as e:
        logger.error(f"Error fetching follow-up form by ID: {e}")
        return None


async def fetch_latest_followup_form() -> Optional[dict]:
    """Fetch the latest follow-up form from MongoDB"""
    try:
        db = get_database()
        if db is None:
            logger.warning("Database connection not available")
            return None
        
        collection = db[FOLLOWUP_FORMS_COLLECTION]
        latest_doc = await collection.find_one(sort=[("created_at", -1)])
        
        if latest_doc:
            latest_doc["_id"] = str(latest_doc["_id"])
            logger.info(f"✅ Fetched latest follow-up form with ID: {latest_doc['_id']}")
            return latest_doc
        return None
    except Exception as e:
        logger.error(f"Error fetching latest follow-up form: {e}")
        return None


async def save_followup_form(form_data: dict) -> str:
    """
    Save a new follow-up form to MongoDB.
    Returns the document ID as a string.
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database connection not available")
        
        collection = db[FOLLOWUP_FORMS_COLLECTION]
        
        # Ensure timestamp is set
        if "created_at" not in form_data:
            form_data["created_at"] = datetime.utcnow()
        
        result = await collection.insert_one(form_data)
        logger.info(f"✅ Follow-up intake form saved with ID: {result.inserted_id}")
        
        return str(result.inserted_id)
        
    except Exception as e:
        logger.error(f"Error saving follow-up form: {e}")
        raise
