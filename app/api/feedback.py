from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

class FeedbackRequest(BaseModel):
    rating: int
    rating_text: str = ""
    transcription_preview: str
    errors_found: Optional[list] = []
    total_errors: Optional[int] = 0
    feedback_type: Optional[str] = "simple"
    feedback: Optional[str] = ""  # Keep for backward compatibility

@router.post("/submit")
async def submit_feedback(feedback_data: FeedbackRequest):
    """Save feedback to a file on the server"""
    try:
        # Validate rating (allow 0 for no rating)
        if not (0 <= feedback_data.rating <= 5):
            raise HTTPException(status_code=400, detail="Rating must be between 0 and 5 (0 = no rating)")
        
        # Create feedback entry
        feedback_entry = {
            "timestamp": datetime.now().isoformat(),
            "rating": feedback_data.rating,
            "rating_text": feedback_data.rating_text or (f"{feedback_data.rating} stars" if feedback_data.rating > 0 else "No rating given"),
            "feedback": feedback_data.feedback,
            "transcription_preview": feedback_data.transcription_preview,
            "errors_found": feedback_data.errors_found,
            "total_errors": feedback_data.total_errors,
            "feedback_type": feedback_data.feedback_type
        }
        
        # Save to file - use /tmp on Vercel, logs locally
        if os.path.exists("/tmp"):
            feedback_file = "/tmp/feedback.json"
        else:
            feedback_file = "logs/feedback.json"
            # Create logs directory if it doesn't exist (local development)
            os.makedirs("logs", exist_ok=True)
        
        # Load existing feedback
        existing_feedback = []
        if os.path.exists(feedback_file):
            try:
                with open(feedback_file, 'r') as f:
                    existing_feedback = json.load(f)
            except:
                existing_feedback = []
        
        # Add new feedback
        existing_feedback.append(feedback_entry)
        
        # Save back to file
        with open(feedback_file, 'w') as f:
            json.dump(existing_feedback, f, indent=2)
        
        # Log to audit log as well
        error_summary = f"{feedback_data.total_errors} errors" if feedback_data.total_errors > 0 else "no errors"
        logger.info(f"FEEDBACK_RECEIVED: Rating={feedback_data.rating}, Type={feedback_data.feedback_type}, {error_summary}")
        
        return {"message": "Feedback saved successfully", "total_feedback": len(existing_feedback)}
        
    except Exception as e:
        logger.error(f"Error saving feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

@router.get("/stats")
async def get_feedback_stats():
    """Get feedback statistics"""
    try:
        # Use /tmp on Vercel, logs locally
        if os.path.exists("/tmp"):
            feedback_file = "/tmp/feedback.json"
        else:
            feedback_file = "logs/feedback.json"
        
        if not os.path.exists(feedback_file):
            return {
                "total_feedback": 0,
                "average_rating": 0,
                "recent_feedback": []
            }
        
        with open(feedback_file, 'r') as f:
            feedback = json.load(f)
        
        if not feedback:
            return {
                "total_feedback": 0,
                "average_rating": 0,
                "recent_feedback": []
            }
        
        # Calculate stats
        total = len(feedback)
        # Only count ratings > 0 for average calculation
        ratings = [f["rating"] for f in feedback if f["rating"] > 0]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0
        recent = feedback[-10:]  # Last 10 feedback entries
        
        return {
            "total_feedback": total,
            "average_rating": round(avg_rating, 2),
            "recent_feedback": recent
        }
        
    except Exception as e:
        logger.error(f"Error getting feedback stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get feedback stats")

@router.get("/all")
async def get_all_feedback():
    """Get all feedback data"""
    try:
        # Use /tmp on Vercel, logs locally
        if os.path.exists("/tmp"):
            feedback_file = "/tmp/feedback.json"
        else:
            feedback_file = "logs/feedback.json"
        
        if not os.path.exists(feedback_file):
            return {
                "message": "No feedback file found",
                "feedback": []
            }
        
        with open(feedback_file, 'r') as f:
            feedback = json.load(f)
        
        return {
            "total_feedback": len(feedback),
            "feedback": feedback
        }
        
    except Exception as e:
        logger.error(f"Error getting all feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get feedback data")