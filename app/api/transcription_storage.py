"""
Transcription Storage functions for MongoDB
"""
import logging
from datetime import datetime
from typing import Dict, Optional, List
from bson import ObjectId

from app.mongodb import get_database

logger = logging.getLogger(__name__)

# Configuration
TRANSCRIPTIONS_COLLECTION = 'transcriptions'


# ============================================
# TRANSCRIPTION STORAGE FUNCTIONS (MongoDB)
# ============================================

async def save_transcription_to_db(transcription_data: dict) -> Dict:
    """
    Save a transcription to MongoDB
    
    Args:
        transcription_data: Dictionary containing transcription data including:
            - text: Transcription text
            - confidence: Confidence score
            - language: Detected language
            - duration: Audio duration
            - filename: Original filename (optional)
            - username: Username who created the transcription (optional)
            
    Returns:
        Dictionary with saved document including _id
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Create transcription document
        transcription_doc = {
            "text": transcription_data.get("text", ""),
            "confidence": transcription_data.get("confidence", 0.0),
            "language": transcription_data.get("language", "unknown"),
            "duration": transcription_data.get("duration", 0.0),
            "filename": transcription_data.get("filename"),
            "username": transcription_data.get("username"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        # Insert into MongoDB
        result = await db[TRANSCRIPTIONS_COLLECTION].insert_one(transcription_doc)
        transcription_doc["_id"] = str(result.inserted_id)
        
        logger.info(f"✅ Transcription saved to database with ID: {transcription_doc['_id']}")
        
        return transcription_doc
        
    except Exception as e:
        logger.error(f"❌ Error saving transcription to MongoDB: {e}")
        raise


async def get_transcription_by_id(transcription_id: str) -> Optional[Dict]:
    """
    Retrieve a transcription by its ID
    
    Args:
        transcription_id: The MongoDB ObjectId as string
        
    Returns:
        Dictionary with transcription data or None if not found
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(transcription_id)
        except Exception:
            logger.error(f"Invalid ObjectId format: {transcription_id}")
            return None
        
        # Find document
        doc = await db[TRANSCRIPTIONS_COLLECTION].find_one({"_id": object_id})
        
        if doc:
            doc["_id"] = str(doc["_id"])
            logger.info(f"Retrieved transcription: {transcription_id}")
            return doc
        else:
            logger.warning(f"Transcription not found: {transcription_id}")
            return None
        
    except Exception as e:
        logger.error(f"Error retrieving transcription: {e}")
        raise


async def get_all_transcriptions(limit: int = 100, skip: int = 0) -> List[Dict]:
    """
    Get all transcriptions with pagination
    
    Args:
        limit: Maximum number of documents to return
        skip: Number of documents to skip
        
    Returns:
        List of transcription documents
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Get documents sorted by created_at descending
        docs = await db[TRANSCRIPTIONS_COLLECTION].find().sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        
        # Convert ObjectId to string
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        
        logger.info(f"Retrieved {len(docs)} transcriptions (skip={skip}, limit={limit})")
        
        return docs
        
    except Exception as e:
        logger.error(f"Error getting all transcriptions: {e}")
        raise


async def get_transcriptions_count() -> int:
    """
    Get total count of transcriptions
    
    Returns:
        Total number of transcriptions
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        count = await db[TRANSCRIPTIONS_COLLECTION].count_documents({})
        
        return count
        
    except Exception as e:
        logger.error(f"Error getting transcriptions count: {e}")
        raise

