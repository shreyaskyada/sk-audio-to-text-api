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
            - user_id: User ID who created the transcription (optional)
            
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
            "audio_file_path": transcription_data.get("audio_file_path"),  # Store audio file path
            "username": transcription_data.get("username"),
            "user_id": transcription_data.get("user_id"),
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


async def update_transcription_in_db(transcription_id: str, update_data: dict) -> bool:
    """
    Update a transcription in MongoDB
    
    Args:
        transcription_id: The MongoDB ObjectId as string
        update_data: Dictionary with fields to update. Can include:
            - text: Updated transcription text
            - confidence: Updated confidence score
            - language: Updated language
            - duration: Updated duration
            - filename: Updated filename
            - username: Updated username
            - user_id: Updated user ID
            
    Returns:
        True if updated successfully, False otherwise
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
            return False
        
        # Prevent updating protected fields
        protected_fields = ['_id', 'created_at']
        update_data = {k: v for k, v in update_data.items() if k not in protected_fields}
        
        # Add updated_at timestamp
        update_data["updated_at"] = datetime.utcnow()
        
        # Update document
        result = await db[TRANSCRIPTIONS_COLLECTION].update_one(
            {"_id": object_id},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            logger.info(f"✅ Transcription updated: {transcription_id}")
            return True
        elif result.matched_count > 0:
            logger.warning(f"Transcription found but not modified: {transcription_id}")
            return False
        else:
            logger.warning(f"Transcription not found: {transcription_id}")
            return False
        
    except Exception as e:
        logger.error(f"Error updating transcription: {e}")
        raise


async def delete_transcription_in_db(transcription_id: str) -> bool:
    """
    Delete a transcription from MongoDB
    
    Args:
        transcription_id: The MongoDB ObjectId as string
        
    Returns:
        True if deleted successfully, False otherwise
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
            return False
        
        # Delete document
        result = await db[TRANSCRIPTIONS_COLLECTION].delete_one({"_id": object_id})
        
        if result.deleted_count > 0:
            logger.info(f"✅ Transcription deleted: {transcription_id}")
            return True
        else:
            logger.warning(f"Transcription not found: {transcription_id}")
            return False
        
    except Exception as e:
        logger.error(f"Error deleting transcription: {e}")
        raise
