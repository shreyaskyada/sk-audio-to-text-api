"""
Follow-up Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime

from app.models.followup_form import FollowUpForm
from app.mongodb import get_database

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
FOLLOWUP_FORMS_COLLECTION = 'followup_intake_forms'


# ============================================
# FOLLOW-UP FORMS API ENDPOINTS
# ============================================

@router.post("/followup-intake")
async def create_followup_intake(payload: FollowUpForm):
    """
    Create a new follow-up intake form
    
    **Parameters:**
    - payload: FollowUpForm object with all sections (A through D)
    
    **Returns:**
    - status: Success status
    - message: Success message
    - document_id: MongoDB document ID of the saved follow-up form
    
    **Example Request:**
    ```json
    {
        "section_a": {
            "name": "John Doe",
            "dob": "02/10/1985",
            "case_or_claim_no": "WC-998877",
            "visit_no_or_version": "Visit 4"
        },
        "section_b": {
            "pain_better_since_last": true,
            "pain_score_0_10": "3",
            "pain_location": "Shoulder",
            ...
        },
        "section_c": {
            "bp": "120/78",
            "pulse": "72",
            ...
        },
        "section_d": {
            "patient_signature": "John Doe",
            "date": "02/10/2025",
            "staff_clinician_name": "M. Rivera, PA-C"
        }
    }
    ```
    """
    try:
        # Get the database (using existing database connection)
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[FOLLOWUP_FORMS_COLLECTION]
        
        # Convert Pydantic model to dict and add timestamp
        form_data = payload.model_dump()
        form_data["created_at"] = datetime.utcnow()
        
        # Insert into MongoDB
        result = await collection.insert_one(form_data)
        
        logger.info(f"✅ Follow-up intake form saved with ID: {result.inserted_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Follow-up intake form saved.",
            "document_id": str(result.inserted_id)
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving follow-up intake form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save follow-up intake form: {str(e)}"
        )

