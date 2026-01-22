"""
SOAP Note Storage functions for MongoDB
"""
import logging
from datetime import datetime
from typing import Dict, Optional, List
from bson import ObjectId

from app.mongodb import get_database

logger = logging.getLogger(__name__)

# Configuration
SOAP_NOTES_COLLECTION = 'soap_notes'


# ============================================
# SOAP NOTE STORAGE FUNCTIONS (MongoDB)
# ============================================

async def save_soap_note_to_db(soap_data: dict) -> Dict:
    """
    Save a generated SOAP note with transcription to MongoDB
    
    Args:
        soap_data: Dictionary containing SOAP note data including:
            - transcription: Original transcription text
            - corrected_transcription: Corrected transcription
            - subjective, objective, assessment, plan: SOAP sections
            - formatted_soap_note: Full formatted note
            - patient_info: Patient information
            - custom_prompts: Custom prompts used (if any)
            
    Returns:
        Dictionary with saved document including _id
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Create SOAP note document
        soap_doc = {
            "transcription": soap_data.get("transcription", ""),
            "corrected_transcription": soap_data.get("corrected_transcription", ""),
            "transcription_id": soap_data.get("transcription_id"),  # Store transcription_id for lookup
            "subjective": soap_data.get("subjective", ""),
            "objective": soap_data.get("objective", ""),
            "assessment": soap_data.get("assessment", ""),
            "plan": soap_data.get("plan", ""),
            "formatted_soap_note": soap_data.get("formatted_soap_note", ""),
            "patient_info": soap_data.get("patient_info"),
            "userId": soap_data.get("userId"),  # Store patient ID
            "date_of_service": soap_data.get("date_of_service"),
            "location": soap_data.get("location"),
            "reason_for_visit": soap_data.get("reason_for_visit"),
            "custom_prompts": {
                "system_prompt": soap_data.get("system_prompt"),
                "user_prompt_template": soap_data.get("user_prompt_template")
            } if soap_data.get("system_prompt") or soap_data.get("user_prompt_template") else None,
            "format": soap_data.get("format", "markdown"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        # Insert into MongoDB
        result = await db[SOAP_NOTES_COLLECTION].insert_one(soap_doc)
        soap_doc["_id"] = str(result.inserted_id)
        
        logger.info(f"✅ SOAP note saved to database with ID: {soap_doc['_id']}")
        
        return soap_doc
        
    except Exception as e:
        logger.error(f"❌ Error saving SOAP note to MongoDB: {e}")
        raise


async def create_pending_soap_note(transcription_id: str, user_id: Optional[str] = None) -> Dict:
    """
    Create a placeholder SOAP note with pending status.
    Returns the created document with _id.
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
            
        doc = {
            "transcription_id": transcription_id,
            "userId": user_id,
            "status": "pending",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db[SOAP_NOTES_COLLECTION].insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        
        logger.info(f"⏳ Created pending SOAP note: {doc['_id']} for transcription {transcription_id}")
        return doc
        
    except Exception as e:
        logger.error(f"Error creating pending SOAP note: {e}")
        raise

async def get_soap_note_by_id(soap_note_id: str) -> Optional[Dict]:
    """
    Retrieve a SOAP note by its ID
    
    Args:
        soap_note_id: The MongoDB ObjectId as string
        
    Returns:
        Dictionary with SOAP note data or None if not found
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(soap_note_id)
        except Exception:
            logger.error(f"Invalid ObjectId format: {soap_note_id}")
            return None
        
        # Find document
        doc = await db[SOAP_NOTES_COLLECTION].find_one({"_id": object_id})
        
        if doc:
            doc["_id"] = str(doc["_id"])
            logger.info(f"Retrieved SOAP note: {soap_note_id}")
            return doc
        else:
            logger.warning(f"SOAP note not found: {soap_note_id}")
            return None
        
    except Exception as e:
        logger.error(f"Error retrieving SOAP note: {e}")
        raise


async def get_soap_note_by_transcription_id(transcription_id: str) -> Optional[Dict]:
    """
    Retrieve a SOAP note by transcription_id
    
    Args:
        transcription_id: The MongoDB transcription document ID as string
        
    Returns:
        Dictionary with SOAP note data or None if not found
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Find document by transcription_id (most recent first)
        cursor = db[SOAP_NOTES_COLLECTION].find(
            {"transcription_id": transcription_id}
        ).sort("created_at", -1).limit(1)
        doc = await cursor.to_list(1)
        doc = doc[0] if doc else None
        
        if doc:
            doc["_id"] = str(doc["_id"])
            logger.info(f"Retrieved SOAP note by transcription_id: {transcription_id}")
            return doc
        else:
            logger.info(f"No SOAP note found for transcription_id: {transcription_id}")
            return None
        
    except Exception as e:
        logger.error(f"Error retrieving SOAP note by transcription_id: {e}")
        raise


async def get_all_soap_notes_by_transcription_id(transcription_id: str) -> List[Dict]:
    """
    Retrieve all SOAP notes by transcription_id
    
    Args:
        transcription_id: The MongoDB transcription document ID as string
        
    Returns:
        List of dictionaries with SOAP note data (sorted by created_at descending)
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Find all documents by transcription_id (most recent first)
        cursor = db[SOAP_NOTES_COLLECTION].find(
            {"transcription_id": transcription_id}
        ).sort("created_at", -1)
        docs = await cursor.to_list(None)
        
        # Convert ObjectId to string
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        
        logger.info(f"Retrieved {len(docs)} SOAP note(s) for transcription_id: {transcription_id}")
        return docs
        
    except Exception as e:
        logger.error(f"Error retrieving SOAP notes by transcription_id: {e}")
        raise


async def get_all_soap_notes(limit: int = 100, skip: int = 0) -> List[Dict]:
    """
    Get all SOAP notes with pagination
    
    Args:
        limit: Maximum number of documents to return
        skip: Number of documents to skip
        
    Returns:
        List of SOAP note documents
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Get documents sorted by created_at descending
        docs = await db[SOAP_NOTES_COLLECTION].find().sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        
        # Convert ObjectId to string
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        
        logger.info(f"Retrieved {len(docs)} SOAP notes (skip={skip}, limit={limit})")
        
        return docs
        
    except Exception as e:
        logger.error(f"Error getting all SOAP notes: {e}")
        raise


async def update_soap_note(soap_note_id: str, update_data: dict) -> bool:
    """
    Update a SOAP note
    
    Args:
        soap_note_id: The MongoDB ObjectId as string
        update_data: Dictionary with fields to update
        
    Returns:
        True if updated successfully, False otherwise
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(soap_note_id)
        except Exception:
            logger.error(f"Invalid ObjectId format: {soap_note_id}")
            return False
        
        # Add updated_at timestamp
        update_data["updated_at"] = datetime.utcnow()
        
        # Update document
        result = await db[SOAP_NOTES_COLLECTION].update_one(
            {"_id": object_id},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            logger.info(f"✅ SOAP note updated: {soap_note_id}")
            return True
        else:
            logger.warning(f"SOAP note not modified or not found: {soap_note_id}")
            return False
        
    except Exception as e:
        logger.error(f"Error updating SOAP note: {e}")
        raise


async def delete_soap_note(soap_note_id: str) -> bool:
    """
    Delete a SOAP note
    
    Args:
        soap_note_id: The MongoDB ObjectId as string
        
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(soap_note_id)
        except Exception:
            logger.error(f"Invalid ObjectId format: {soap_note_id}")
            return False
        
        # Delete document
        result = await db[SOAP_NOTES_COLLECTION].delete_one({"_id": object_id})
        
        if result.deleted_count > 0:
            logger.info(f"✅ SOAP note deleted: {soap_note_id}")
            return True
        else:
            logger.warning(f"SOAP note not found: {soap_note_id}")
            return False
        
    except Exception as e:
        logger.error(f"Error deleting SOAP note: {e}")
        raise


async def get_soap_notes_stats() -> Dict:
    """
    Get statistics about SOAP notes
    
    Returns:
        Dictionary with statistics
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Get total count
        total = await db[SOAP_NOTES_COLLECTION].count_documents({})
        
        # Get count with custom prompts
        custom_prompts_count = await db[SOAP_NOTES_COLLECTION].count_documents({
            "custom_prompts": {"$ne": None}
        })
        
        # Get recent SOAP notes (last 10)
        recent = await db[SOAP_NOTES_COLLECTION].find().sort("created_at", -1).limit(10).to_list(10)
        
        # Convert ObjectId to string
        for doc in recent:
            doc["_id"] = str(doc["_id"])
        
        stats = {
            "total_soap_notes": total,
            "soap_notes_with_custom_prompts": custom_prompts_count,
            "soap_notes_with_default_prompts": total - custom_prompts_count,
            "recent_soap_notes": recent
        }
        
        logger.info(f"Retrieved SOAP notes statistics: {total} total")
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting SOAP notes statistics: {e}")
        raise

