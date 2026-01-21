import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.schemas.feedback_schema import (
    FeedbackRequest, FeedbackResponse, 
    FeedbackStatsResponse, FeedbackListResponse
)
from app.services.feedback_service import FeedbackService
from app.services.transcription_service import TranscriptionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

@router.post("/submit")
async def submit_feedback(feedback_data: FeedbackRequest):
    """Submit feedback about transcription quality"""
    try:
        if not (0 <= feedback_data.rating <= 5):
            raise HTTPException(status_code=400, detail="Rating must be between 0 and 5")
        
        transcription = await TranscriptionService.get_by_id(feedback_data.transcription_id)
        if not transcription:
            raise HTTPException(status_code=404, detail="Transcription not found")
        
        if not feedback_data.transcription_preview:
            feedback_data.transcription_preview = transcription.get("text", "")[:200]
        
        feedback_entry = await FeedbackService.add_feedback(feedback_data)
        stats = await FeedbackService.get_stats()
        
        return JSONResponse({
            "message": "Feedback saved successfully",
            "feedback_id": feedback_entry["id"],
            "total_feedback": stats["total"]
        })
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats():
    """Get feedback statistics"""
    stats = await FeedbackService.get_stats()
    recent = [FeedbackResponse(id=str(d["_id"]), **d) for d in stats["recent"]]
    return FeedbackStatsResponse(
        total_feedback=stats["total"],
        average_rating=stats["average_rating"],
        recent_feedback=recent
    )

@router.get("/all", response_model=FeedbackListResponse)
async def get_all_feedback():
    """Get all feedback entries"""
    docs = await FeedbackService.get_all()
    feedback = [FeedbackResponse(id=str(d["_id"]), **d) for d in docs]
    return FeedbackListResponse(total_feedback=len(feedback), feedback=feedback)
