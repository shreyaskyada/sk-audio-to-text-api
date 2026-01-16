"""
Transcription Storage functions for MongoDB
"""
import logging
import asyncio
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
        
        # Insert into MongoDB
        result = await db[TRANSCRIPTIONS_COLLECTION].insert_one(transcription_doc)
        transcription_doc["_id"] = str(result.inserted_id)
        
        transcription_id = transcription_doc["_id"]
        print(f"✅ [TRANSCRIPTION SAVE] Transcription saved to database with ID: {transcription_id}")
        logger.info(f"✅ Transcription saved to database with ID: {transcription_id}")
        
        # Start background worker to generate SOAP note
        # Import here to avoid circular dependencies
        try:
            print(f"🚀 [TRANSCRIPTION SAVE] Starting background SOAP worker for transcription_id: {transcription_id}")
            from app.api.background_worker import generate_soap_note_in_background
            
            # Schedule background task (non-blocking)
            # Use asyncio.create_task to run in background
            try:
                # Create task without awaiting - runs in background
                task = asyncio.create_task(generate_soap_note_in_background(transcription_id))
                print(f"✅ [TRANSCRIPTION SAVE] Background SOAP worker task created and scheduled")
                print(f"   Task object: {task}")
                logger.info(f"🚀 Background SOAP generation scheduled for transcription_id: {transcription_id}")
            except Exception as task_error:
                print(f"⚠️ [TRANSCRIPTION SAVE] ERROR: Failed to schedule background task")
                print(f"   Error: {str(task_error)}")
                logger.warning(f"⚠️ Failed to schedule background task: {str(task_error)}")
        except Exception as e:
            # Don't fail the transcription save if background worker fails to start
            print(f"⚠️ [TRANSCRIPTION SAVE] WARNING: Failed to start background SOAP worker")
            print(f"   Error: {str(e)}")
            logger.warning(f"⚠️ Failed to start background SOAP worker: {str(e)}")
        
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
            doc["id"] = str(doc["_id"])
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
            doc["id"] = str(doc["_id"])
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
        
        # Check if transcription text is being updated (to trigger SOAP regeneration)
        text_updated = 'text' in update_data
        
        # Update document
        result = await db[TRANSCRIPTIONS_COLLECTION].update_one(
            {"_id": object_id},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            print(f"✅ [TRANSCRIPTION UPDATE] Transcription updated: {transcription_id}")
            logger.info(f"✅ Transcription updated: {transcription_id}")
            
            # If transcription text was updated, trigger background worker to regenerate SOAP note
            if text_updated:
                try:
                    print(f"🔄 [TRANSCRIPTION UPDATE] Transcription text updated, triggering background SOAP regeneration")
                    from app.api.background_worker import generate_soap_note_in_background
                    
                    # Delete existing SOAP notes for this transcription to regenerate fresh
                    try:
                        from app.api.soap_storage import get_all_soap_notes_by_transcription_id, delete_soap_note
                        existing_soaps = await get_all_soap_notes_by_transcription_id(transcription_id)
                        for soap in existing_soaps:
                            await delete_soap_note(soap.get("_id"))
                            print(f"🗑️ [TRANSCRIPTION UPDATE] Deleted old SOAP note: {soap.get('_id')}")
                        if existing_soaps:
                            logger.info(f"🗑️ Deleted {len(existing_soaps)} old SOAP note(s) for regeneration")
                    except Exception as delete_error:
                        print(f"⚠️ [TRANSCRIPTION UPDATE] Warning: Could not delete old SOAP notes: {str(delete_error)}")
                        logger.warning(f"⚠️ Could not delete old SOAP notes: {str(delete_error)}")
                    
                    # Schedule background task to regenerate SOAP note
                    try:
                        task = asyncio.create_task(generate_soap_note_in_background(transcription_id))
                        print(f"✅ [TRANSCRIPTION UPDATE] Background SOAP regeneration task scheduled")
                        logger.info(f"🚀 Background SOAP regeneration scheduled for updated transcription_id: {transcription_id}")
                    except Exception as task_error:
                        print(f"⚠️ [TRANSCRIPTION UPDATE] ERROR: Failed to schedule background task: {str(task_error)}")
                        logger.warning(f"⚠️ Failed to schedule background task for updated transcription: {str(task_error)}")
                except Exception as e:
                    # Don't fail the transcription update if background worker fails to start
                    print(f"⚠️ [TRANSCRIPTION UPDATE] WARNING: Failed to start background SOAP worker: {str(e)}")
                    logger.warning(f"⚠️ Failed to start background SOAP worker for updated transcription: {str(e)}")
            
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


async def get_user_ids_with_transcriptions() -> List[str]:
    """
    Get a list of distinct user_ids that have transcriptions.
    
    Returns:
        List of user_id strings
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Get distinct user_ids
        user_ids = await db[TRANSCRIPTIONS_COLLECTION].distinct("user_id")
        
        # Filter out None values and ensure strings
        valid_user_ids = [str(uid) for uid in user_ids if uid]
        
        logger.info(f"Retrieved {len(valid_user_ids)} user_ids with transcriptions")
        
        return valid_user_ids
        
    except Exception as e:
        logger.error(f"Error getting user_ids with transcriptions: {e}")
        return []


async def get_latest_transcription_by_user_id(user_id: str) -> Optional[Dict]:
    """
    Retrieve the latest transcription for a specific user ID.
    
    Args:
        user_id: The user_id string to filter by
        
    Returns:
        Dictionary with transcription data or None if not found
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Find latest document for this user_id
        doc = await db[TRANSCRIPTIONS_COLLECTION].find_one(
            {"user_id": user_id},
            sort=[("created_at", -1)]
        )
        
        if doc:
            doc["id"] = str(doc["_id"])
            doc["_id"] = str(doc["_id"])
            logger.info(f"Retrieved latest transcription for user {user_id}")
            return doc
        else:
            logger.warning(f"No transcription found for user {user_id}")
            return None
            
    except Exception as e:
        logger.error(f"Error retrieving latest transcription for user {user_id}: {e}")
        raise
