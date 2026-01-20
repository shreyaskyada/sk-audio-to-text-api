import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.schemas.feedback_schema import FeedbackRequest, FeedbackResponse, FeedbackStatsResponse, FeedbackListResponse
from app.services import feedback_service, transcription_service
from app.database import get_database

logger = logging.getLogger(__name__)

router = APIRouter()

FEEDBACK_COLLECTION = 'feedback'

@router.post("/submit")
async def submit_feedback(feedback_data: FeedbackRequest):
    try:
        if not (0 <= feedback_data.rating <= 5):
            raise HTTPException(status_code=400, detail="Rating must be between 0 and 5")
        
        transcription = await transcription_service.get_transcription_by_id(feedback_data.transcription_id)
        if not transcription:
            raise HTTPException(status_code=404, detail=f"Transcription not found: {feedback_data.transcription_id}")
        
        if not feedback_data.transcription_preview:
            feedback_data.transcription_preview = transcription.get("text", "")[:200]
        
        feedback_entry = await feedback_service.add_feedback_to_db(feedback_data)
        
        db = get_database()
        total_count = await db[FEEDBACK_COLLECTION].count_documents({})
        
        return JSONResponse({
            "message": "Feedback saved successfully",
            "feedback_id": str(feedback_entry["_id"]),
            "total_feedback": total_count
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats():
    try:
        stats = await feedback_service.get_feedback_stats()
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
            ) for doc in stats["recent"]
        ]
        return FeedbackStatsResponse(
            total_feedback=stats["total"],
            average_rating=stats["average_rating"],
            recent_feedback=recent_feedback
        )
    except Exception as e:
        logger.error(f"Error getting feedback stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get feedback statistics")

@router.get("/all", response_model=FeedbackListResponse)
async def get_all_feedback():
    try:
        feedback_docs = await feedback_service.get_all_feedback()
        feedback_responses = [
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
            ) for doc in feedback_docs
        ]
        return FeedbackListResponse(
            total_feedback=len(feedback_responses),
            feedback=feedback_responses
        )
    except Exception as e:
        logger.error(f"Error getting all feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to get feedback data")
