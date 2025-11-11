"""
Follow-up Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
from bson import ObjectId
from typing import Any, Dict

from app.models.followup_form import FollowUpForm
from app.mongodb import get_database

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
FOLLOWUP_FORMS_COLLECTION = 'followup_intake_forms'


def serialize_mongodb_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert MongoDB document to JSON-serializable format
    Converts ObjectId and datetime objects to strings
    """
    if doc is None:
        return None
    
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            serialized[key] = str(value)
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, dict):
            serialized[key] = serialize_mongodb_doc(value)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_mongodb_doc(item) if isinstance(item, dict) else
                str(item) if isinstance(item, (ObjectId, datetime)) else item
                for item in value
            ]
        else:
            serialized[key] = value
    
    return serialized


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


@router.get("/follow-up/latest")
async def get_latest_followup_form():
    """
    Get the most recently created follow-up intake form
    
    **Returns:**
    - Complete follow-up form document with all fields
    - Includes document_id and created_at timestamp
    - Returns 404 if no follow-up forms exist
    
    **Example Response:**
    ```json
    {
        "_id": "507f1f77bcf86cd799439011",
        "document_id": "507f1f77bcf86cd799439011",
        "section_a": {
            "name": "John Doe",
            "dob": "02/10/1985",
            "case_or_claim_no": "WC-998877",
            "visit_no_or_version": "Visit 4"
        },
        "section_b": { ... },
        "section_c": { ... },
        "section_d": { ... },
        "created_at": "2024-11-10T17:22:16.436963"
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
        
        # Find the latest follow-up form (sorted by created_at descending, limit 1)
        latest_form = await collection.find_one(
            sort=[("created_at", -1)]
        )
        
        if latest_form is None:
            raise HTTPException(
                status_code=404,
                detail="No follow-up forms found"
            )
        
        # Serialize MongoDB document (convert ObjectId and datetime to strings)
        serialized_form = serialize_mongodb_doc(latest_form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved latest follow-up form with ID: {serialized_form.get('_id')}")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest follow-up form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest follow-up form: {str(e)}"
        )

