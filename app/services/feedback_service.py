
"""
Feedback Service functions for MongoDB
"""
import logging
from datetime import datetime
from typing import Dict, List
from bson import ObjectId

from app.mongodb import get_database
from app.schemas import FeedbackRequest, ErrorCorrection

logger = logging.getLogger(__name__)

# Configuration
FEEDBACK_COLLECTION = 'feedback'


# ============================================
# FEEDBACK STORAGE FUNCTIONS (MongoDB)
# ============================================

async def add_feedback_to_db(feedback_data: FeedbackRequest) -> Dict:
    """Add feedback entry to MongoDB"""
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Convert errors to proper format
        errors_list = []
        for error in feedback_data.errors_found:
            if isinstance(error, ErrorCorrection):
                errors_list.append({"wrong": error.wrong, "correct": error.correct})
            elif isinstance(error, dict):
                errors_list.append(error)
            else:
                errors_list.append(str(error))
        
        # Create feedback document
        feedback_doc = {
            "rating": feedback_data.rating,
            "rating_text": feedback_data.rating_text or (
                f"{feedback_data.rating} stars" if feedback_data.rating > 0 else "No rating given"
            ),
            "feedback": feedback_data.feedback,
            "transcription_id": feedback_data.transcription_id,
            "transcription_preview": feedback_data.transcription_preview,
            "errors_found": errors_list,
            "total_errors": feedback_data.total_errors,
            "feedback_type": feedback_data.feedback_type,
            "timestamp": datetime.utcnow()
        }
        
        # Insert into MongoDB
        result = await db[FEEDBACK_COLLECTION].insert_one(feedback_doc)
        feedback_doc["id"] = str(result.inserted_id)
        
        return feedback_doc
        
    except Exception as e:
        logger.error(f"Error adding feedback to MongoDB: {e}")
        raise


async def get_feedback_stats_from_db() -> Dict:
    """Get feedback statistics from MongoDB"""
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Get total count
        total = await db[FEEDBACK_COLLECTION].count_documents({})
        
        if total == 0:
            return {
                "total": 0,
                "average_rating": 0.0,
                "recent": []
            }
        
        # Calculate average rating (only ratings > 0)
        pipeline = [
            {"$match": {"rating": {"$gt": 0}}},
            {"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}}}
        ]
        
        avg_result = await db[FEEDBACK_COLLECTION].aggregate(pipeline).to_list(1)
        avg_rating = avg_result[0]["avg_rating"] if avg_result else 0.0
        
        # Get recent feedback (last 10)
        recent_docs = await db[FEEDBACK_COLLECTION].find().sort("timestamp", -1).limit(10).to_list(10)
        
        return {
            "total": total,
            "average_rating": round(avg_rating, 2),
            "recent": recent_docs
        }
        
    except Exception as e:
        logger.error(f"Error getting feedback stats: {e}")
        raise



async def get_feedback_count() -> int:
    """Get total number of feedback entries"""
    try:
        db = get_database()
        if db is None:
            return 0
        return await db[FEEDBACK_COLLECTION].count_documents({})
    except Exception:
        return 0

async def get_all_feedback_from_db() -> List[Dict]:
    """Get all feedback from MongoDB"""
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        # Get all feedback documents
        docs = await db[FEEDBACK_COLLECTION].find().sort("timestamp", -1).to_list(None)
        
        return docs
        
    except Exception as e:
        logger.error(f"Error getting all feedback: {e}")
        raise
