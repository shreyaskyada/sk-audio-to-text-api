import logging
from datetime import datetime
from typing import Dict, List
from app.database import get_database
from app.schemas.feedback_schema import FeedbackRequest, ErrorCorrection

logger = logging.getLogger(__name__)

FEEDBACK_COLLECTION = 'feedback'

async def add_feedback_to_db(feedback_data: FeedbackRequest) -> Dict:
    """Add feedback entry to MongoDB"""
    try:
        db = get_database()
        if db is None: raise Exception("Database not available")
        
        errors_list = []
        for error in feedback_data.errors_found:
            if isinstance(error, ErrorCorrection):
                errors_list.append({"wrong": error.wrong, "correct": error.correct})
            elif isinstance(error, dict):
                errors_list.append(error)
            else:
                errors_list.append(str(error))
        
        feedback_doc = {
            "rating": feedback_data.rating,
            "rating_text": feedback_data.rating_text or (f"{feedback_data.rating} stars" if feedback_data.rating > 0 else "No rating given"),
            "feedback": feedback_data.feedback,
            "transcription_id": feedback_data.transcription_id,
            "transcription_preview": feedback_data.transcription_preview,
            "errors_found": errors_list,
            "total_errors": feedback_data.total_errors,
            "feedback_type": feedback_data.feedback_type,
            "timestamp": datetime.utcnow()
        }
        
        result = await db[FEEDBACK_COLLECTION].insert_one(feedback_doc)
        feedback_doc["_id"] = str(result.inserted_id)
        return feedback_doc
    except Exception as e:
        logger.error(f"Error adding feedback: {e}")
        raise

async def get_feedback_stats() -> Dict:
    """Get feedback statistics from MongoDB"""
    try:
        db = get_database()
        if db is None: return {"total": 0, "average_rating": 0.0, "recent": []}
        
        total = await db[FEEDBACK_COLLECTION].count_documents({})
        if total == 0:
            return {"total": 0, "average_rating": 0.0, "recent": []}
        
        pipeline = [
            {"$match": {"rating": {"$gt": 0}}},
            {"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}}}
        ]
        avg_result = await db[FEEDBACK_COLLECTION].aggregate(pipeline).to_list(1)
        avg_rating = avg_result[0]["avg_rating"] if avg_result else 0.0
        
        recent_docs = await db[FEEDBACK_COLLECTION].find().sort("timestamp", -1).limit(10).to_list(10)
        return {"total": total, "average_rating": round(avg_rating, 2), "recent": recent_docs}
    except Exception as e:
        logger.error(f"Error getting feedback stats: {e}")
        raise

async def get_all_feedback() -> List[Dict]:
    """Get all feedback from MongoDB"""
    try:
        db = get_database()
        if db is None: return []
        return await db[FEEDBACK_COLLECTION].find().sort("timestamp", -1).to_list(None)
    except Exception as e:
        logger.error(f"Error getting all feedback: {e}")
        raise
