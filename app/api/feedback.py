from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Union, Dict, Any
from datetime import datetime
import logging
from bson import ObjectId

from app.mongodb import get_database, FEEDBACK_COLLECTION

logger = logging.getLogger(__name__)
router = APIRouter()

class ErrorCorrection(BaseModel):
    """Model for medical terminology corrections"""
    wrong: str
    correct: str

class FeedbackRequest(BaseModel):
    rating: int
    rating_text: str = ""
    transcription_preview: str
    errors_found: Optional[List[Union[str, ErrorCorrection, Dict[str, Any]]]] = []
    total_errors: Optional[int] = 0
    feedback_type: Optional[str] = "simple"
    feedback: Optional[str] = ""  # Keep for backward compatibility

class FeedbackResponse(BaseModel):
    id: str
    rating: int
    rating_text: str
    transcription_preview: str
    errors_found: List[Union[str, Dict[str, Any]]]
    total_errors: int
    feedback_type: str
    feedback: str
    timestamp: datetime
    
    model_config = ConfigDict(from_attributes=True)

class FeedbackStatsResponse(BaseModel):
    total_feedback: int
    average_rating: float
    recent_feedback: List[FeedbackResponse]

class FeedbackListResponse(BaseModel):
    total_feedback: int
    feedback: List[FeedbackResponse]

@router.post("/submit", response_model=dict)
async def submit_feedback(feedback_data: FeedbackRequest, db=Depends(get_database)):
    """Save feedback to MongoDB"""
    try:
        # Validate rating (allow 0 for no rating)
        if not (0 <= feedback_data.rating <= 5):
            raise HTTPException(status_code=400, detail="Rating must be between 0 and 5 (0 = no rating)")
        
        # Convert errors_found to proper format for MongoDB
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
            "rating_text": feedback_data.rating_text or (f"{feedback_data.rating} stars" if feedback_data.rating > 0 else "No rating given"),
            "feedback": feedback_data.feedback,
            "transcription_preview": feedback_data.transcription_preview,
            "errors_found": errors_list,
            "total_errors": feedback_data.total_errors,
            "feedback_type": feedback_data.feedback_type,
            "timestamp": datetime.utcnow()
        }
        
        # Insert into MongoDB
        result = await db[FEEDBACK_COLLECTION].insert_one(feedback_doc)
        
        # Log to audit log as well
        error_summary = f"{feedback_data.total_errors} errors" if feedback_data.total_errors > 0 else "no errors"
        logger.info(f"FEEDBACK_RECEIVED: Rating={feedback_data.rating}, Type={feedback_data.feedback_type}, {error_summary}")
        
        # Get total count
        total_count = await db[FEEDBACK_COLLECTION].count_documents({})
        
        return {
            "message": "Feedback saved successfully", 
            "feedback_id": str(result.inserted_id),
            "total_feedback": total_count
        }
        
    except Exception as e:
        logger.error(f"Error saving feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats(db=Depends(get_database)):
    """Get feedback statistics from MongoDB"""
    try:
        # Get total count
        total = await db[FEEDBACK_COLLECTION].count_documents({})
        
        if total == 0:
            return FeedbackStatsResponse(
                total_feedback=0,
                average_rating=0,
                recent_feedback=[]
            )
        
        # Calculate average rating (only count ratings > 0)
        pipeline = [
            {"$match": {"rating": {"$gt": 0}}},
            {"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}}}
        ]
        
        avg_result = await db[FEEDBACK_COLLECTION].aggregate(pipeline).to_list(1)
        avg_rating = avg_result[0]["avg_rating"] if avg_result else 0
        
        # Get recent feedback (last 10)
        recent_docs = await db[FEEDBACK_COLLECTION].find().sort("timestamp", -1).limit(10).to_list(10)
        recent_feedback = [
            FeedbackResponse(
                id=str(doc["_id"]),
                rating=doc["rating"],
                rating_text=doc["rating_text"],
                transcription_preview=doc["transcription_preview"],
                errors_found=doc.get("errors_found", []),
                total_errors=doc.get("total_errors", 0),
                feedback_type=doc.get("feedback_type", "simple"),
                feedback=doc.get("feedback", ""),
                timestamp=doc["timestamp"]
            ) for doc in recent_docs
        ]
        
        return FeedbackStatsResponse(
            total_feedback=total,
            average_rating=round(avg_rating, 2),
            recent_feedback=recent_feedback
        )
        
    except Exception as e:
        logger.error(f"Error getting feedback stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get feedback stats")

@router.get("/all", response_model=FeedbackListResponse)
async def get_all_feedback(db=Depends(get_database)):
    """Get all feedback data from MongoDB"""
    try:
        # Get all feedback documents
        docs = await db[FEEDBACK_COLLECTION].find().sort("timestamp", -1).to_list(None)
        
        feedback_list = [
            FeedbackResponse(
                id=str(doc["_id"]),
                rating=doc["rating"],
                rating_text=doc["rating_text"],
                transcription_preview=doc["transcription_preview"],
                errors_found=doc.get("errors_found", []),
                total_errors=doc.get("total_errors", 0),
                feedback_type=doc.get("feedback_type", "simple"),
                feedback=doc.get("feedback", ""),
                timestamp=doc["timestamp"]
            ) for doc in docs
        ]
        
        return FeedbackListResponse(
            total_feedback=len(feedback_list),
            feedback=feedback_list
        )
        
    except Exception as e:
        logger.error(f"Error getting all feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get feedback data")