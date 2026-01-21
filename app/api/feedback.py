"""
Feedback API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
from typing import Dict, List

from app.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    FeedbackStatsResponse,
    FeedbackListResponse,
    ErrorCorrection
)
from app.mongodb import get_database
from app.services.transcription_storage import get_transcription_by_id

logger = logging.getLogger(__name__)

router = APIRouter()

from app.services.feedback_service import (
    add_feedback_to_db,
    get_feedback_stats_from_db,
    get_all_feedback_from_db,
    get_feedback_count
)


# ============================================
# FEEDBACK API ENDPOINTS
# ============================================

@router.post("/submit")
async def submit_feedback(feedback_data: FeedbackRequest):
    """
    Submit feedback about transcription quality
    
    **Parameters:**
    - rating: Rating from 0-5 (0 = no rating)
    - rating_text: Description of the rating
    - transcription_id: MongoDB transcription document ID (required)
    - transcription_preview: Optional preview of the transcription
    - errors_found: List of errors found in transcription
    - total_errors: Total number of errors
    - feedback_type: Type of feedback (simple/detailed)
    - feedback: Additional feedback text
    
    **Returns:**
    - message: Success message
    - feedback_id: ID of the submitted feedback
    - total_feedback: Total number of feedback entries
    """
    try:
        # Validate rating
        if not (0 <= feedback_data.rating <= 5):
            raise HTTPException(
                status_code=400,
                detail="Rating must be between 0 and 5 (0 = no rating)"
            )
        
        # Validate transcription_id exists
        transcription = await get_transcription_by_id(feedback_data.transcription_id)
        if not transcription:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found with ID: {feedback_data.transcription_id}"
            )
        
        # If transcription_preview is not provided, use the transcription text
        if not feedback_data.transcription_preview:
            feedback_data.transcription_preview = transcription.get("text", "")[:200]  # First 200 chars as preview
        
        # Add feedback entry to MongoDB
        feedback_entry = await add_feedback_to_db(feedback_data)
        
        # Log feedback
        error_summary = (
            f"{feedback_data.total_errors} errors" 
            if feedback_data.total_errors > 0 
            else "no errors"
        )
        logger.info(
            f"FEEDBACK_RECEIVED: Rating={feedback_data.rating}, "
            f"Type={feedback_data.feedback_type}, {error_summary}"
        )
        
        # Get total count
        total_count = await get_feedback_count()
        
        return JSONResponse({
            "message": "Feedback saved successfully",
            "feedback_id": feedback_entry["id"],
            "total_feedback": total_count
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save feedback"
        )


@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats():
    """
    Get feedback statistics
    
    **Returns:**
    - total_feedback: Total number of feedback entries
    - average_rating: Average rating (excluding 0 ratings)
    - recent_feedback: List of recent feedback entries (last 10)
    
    **Note:**
    - This endpoint does not require authentication
    - Useful for displaying feedback analytics
    """
    try:
        # Get stats from MongoDB
        stats = await get_feedback_stats_from_db()
        
        # Convert recent feedback to response model
        recent_feedback = []
        for doc in stats["recent"]:
            recent_feedback.append(FeedbackResponse(
                id=str(doc["_id"]),
                rating=doc["rating"],
                rating_text=doc["rating_text"],
                transcription_preview=doc["transcription_preview"],
                errors_found=doc.get("errors_found", []),
                total_errors=doc.get("total_errors", 0),
                feedback_type=doc.get("feedback_type", "simple"),
                feedback=doc.get("feedback", ""),
                timestamp=doc["timestamp"]
            ))
        
        return FeedbackStatsResponse(
            total_feedback=stats["total"],
            average_rating=stats["average_rating"],
            recent_feedback=recent_feedback
        )
        
    except Exception as e:
        logger.error(f"Error getting feedback stats: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get feedback statistics"
        )


@router.get("/all", response_model=FeedbackListResponse)
async def get_all_feedback():
    """
    Get all feedback entries
    
    **Returns:**
    - total_feedback: Total number of feedback entries
    - feedback: List of all feedback entries (sorted by timestamp, newest first)
    
    **Note:**
    - This endpoint does not require authentication
    - Returns complete feedback history
    - Useful for reviewing all submitted feedback
    """
    try:
        # Get all feedback from MongoDB
        feedback_docs = await get_all_feedback_from_db()
        
        # Convert to response model
        feedback_responses = []
        for doc in feedback_docs:
            feedback_responses.append(FeedbackResponse(
                id=str(doc["_id"]),
                rating=doc["rating"],
                rating_text=doc["rating_text"],
                transcription_preview=doc["transcription_preview"],
                errors_found=doc.get("errors_found", []),
                total_errors=doc.get("total_errors", 0),
                feedback_type=doc.get("feedback_type", "simple"),
                feedback=doc.get("feedback", ""),
                timestamp=doc["timestamp"]
            ))
        
        return FeedbackListResponse(
            total_feedback=len(feedback_responses),
            feedback=feedback_responses
        )
        
    except Exception as e:
        logger.error(f"Error getting all feedback: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get feedback data"
        )

