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
    feedback: Optional[str] = ""
    transcription_preview: str

@router.post("/submit")
async def submit_feedback(feedback_data: FeedbackRequest):
    """Save feedback to a file on the server"""
    try:
        # Validate rating
        if not (1 <= feedback_data.rating <= 5):
            raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
        
        # Create feedback entry
        feedback_entry = {
            "timestamp": datetime.now().isoformat(),
            "rating": feedback_data.rating,
            "feedback": feedback_data.feedback,
            "transcription_preview": feedback_data.transcription_preview
        }
        
        # Save to file in logs folder
        feedback_file = "logs/feedback.json"
        
        # Create logs directory if it doesn't exist
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
        logger.info(f"FEEDBACK_RECEIVED: Rating={feedback_data.rating}, Feedback='{feedback_data.feedback[:50]}...'")
        
        return {"message": "Feedback saved successfully", "total_feedback": len(existing_feedback)}
        
    except Exception as e:
        logger.error(f"Error saving feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

@router.get("/stats")
async def get_feedback_stats():
    """Get feedback statistics"""
    try:
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
        avg_rating = sum(f["rating"] for f in feedback) / total
        recent = feedback[-10:]  # Last 10 feedback entries
        
        return {
            "total_feedback": total,
            "average_rating": round(avg_rating, 2),
            "recent_feedback": recent
        }
        
    except Exception as e:
        logger.error(f"Error getting feedback stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get feedback stats")