import logging
from datetime import datetime
from typing import Dict, Optional, List
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

TRANSCRIPTIONS_COLLECTION = 'transcriptions'

async def save_transcription_to_db(transcription_data: dict) -> Dict:
    """Save a transcription to MongoDB"""
    try:
        db = get_database()
        if db is None: raise Exception("Database not available")
        
        transcription_doc = {
            "text": transcription_data.get("text", ""),
            "confidence": transcription_data.get("confidence", 0.0),
            "language": transcription_data.get("language", "unknown"),
            "duration": transcription_data.get("duration", 0.0),
            "filename": transcription_data.get("filename"),
            "audio_file_path": transcription_data.get("audio_file_path"),
            "username": transcription_data.get("username"),
            "user_id": transcription_data.get("user_id"),
            "needs_rfa": transcription_data.get("needs_rfa", False),
            "rfa_name": transcription_data.get("rfa_name"),
            "needs_work_status": transcription_data.get("needs_work_status", False),
            "work_status": transcription_data.get("work_status"),
            "work_status_code": transcription_data.get("work_status_code"),
            "chief_complaint": transcription_data.get("chief_complaint"),
            "history": transcription_data.get("history"),
            "examination": transcription_data.get("examination"),
            "assessment": transcription_data.get("assessment"),
            "treatment_plan": transcription_data.get("treatment_plan"),
            "medications": transcription_data.get("medications"),
            "follow_up": transcription_data.get("follow_up"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db[TRANSCRIPTIONS_COLLECTION].insert_one(transcription_doc)
        transcription_doc["_id"] = str(result.inserted_id)
        return transcription_doc
    except Exception as e:
        logger.error(f"❌ Error saving transcription: {e}")
        raise

async def get_transcription_by_id(transcription_id: str) -> Optional[Dict]:
    """Retrieve a transcription by its ID"""
    try:
        db = get_database()
        if db is None: return None
        try:
            object_id = ObjectId(transcription_id)
        except Exception:
            return None
        doc = await db[TRANSCRIPTIONS_COLLECTION].find_one({"_id": object_id})
        if doc:
            doc["_id"] = str(doc["_id"])
            return doc
        return None
    except Exception as e:
        logger.error(f"Error retrieving transcription: {e}")
        raise

async def update_transcription_in_db(transcription_id: str, update_data: dict) -> bool:
    """Update a transcription in MongoDB"""
    try:
        db = get_database()
        if db is None: return False
        try:
            object_id = ObjectId(transcription_id)
        except Exception:
            return False
        
        update_data["updated_at"] = datetime.utcnow()
        result = await db[TRANSCRIPTIONS_COLLECTION].update_one(
            {"_id": object_id},
            {"$set": update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating transcription: {e}")
        raise

async def get_all_transcriptions(limit: int = 100, skip: int = 0) -> List[Dict]:
    """Get all transcriptions with pagination"""
    try:
        db = get_database()
        if db is None: return []
        docs = await db[TRANSCRIPTIONS_COLLECTION].find().sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return docs
    except Exception as e:
        logger.error(f"Error getting transcriptions: {e}")
        raise

async def get_transcriptions_count() -> int:
    """Get total count of transcriptions"""
    try:
        db = get_database()
        if db is None: return 0
        return await db[TRANSCRIPTIONS_COLLECTION].count_documents({})
    except Exception as e:
        logger.error(f"Error getting transcriptions count: {e}")
        raise

async def delete_transcription_in_db(transcription_id: str) -> bool:
    """Delete a transcription from MongoDB"""
    try:
        db = get_database()
        if db is None: return False
        try:
            object_id = ObjectId(transcription_id)
        except Exception:
            return False
        result = await db[TRANSCRIPTIONS_COLLECTION].delete_one({"_id": object_id})
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Error deleting transcription: {e}")
        raise

async def get_user_ids_with_transcriptions() -> List[str]:
    """Get a list of distinct user_ids that have transcriptions"""
    try:
        db = get_database()
        if db is None: return []
        user_ids = await db[TRANSCRIPTIONS_COLLECTION].distinct("user_id")
        return [str(uid) for uid in user_ids if uid]
    except Exception as e:
        logger.error(f"Error getting user_ids with transcriptions: {e}")
        return []

async def get_latest_transcription_by_user_id(user_id: str) -> Optional[Dict]:
    """Retrieve the latest transcription for a specific user ID"""
    try:
        db = get_database()
        if db is None: return None
        doc = await db[TRANSCRIPTIONS_COLLECTION].find_one(
            {"user_id": user_id},
            sort=[("created_at", -1)]
        )
        if doc:
            doc["_id"] = str(doc["_id"])
            return doc
        return None
    except Exception as e:
        logger.error(f"Error retrieving latest transcription for user {user_id}: {e}")
        raise
