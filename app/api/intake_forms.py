"""
Intake Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
from bson import ObjectId
from typing import Any, Dict

from app.models.intake_form import IntakeForm
from app.services.intake import save_intake_form, fetch_latest_intake_form

logger = logging.getLogger(__name__)

router = APIRouter()

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
# INTAKE FORMS API ENDPOINTS
# ============================================

@router.post("/intake-form")
async def create_intake_form(data: IntakeForm):
    """
    Create a new intake form
    """
    try:
        # Convert Pydantic model to dict
        form_data = data.model_dump()
        
        # Save using service
        document_id = await save_intake_form(form_data)
        
        return JSONResponse({
            "status": "success",
            "message": "Intake form saved successfully.",
            "document_id": document_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving intake form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save intake form: {str(e)}"
        )


@router.get("/intake-form/latest")
async def get_latest_intake_form_endpoint():
    """
    Get the most recently created intake form
    """
    try:
        latest_form = await fetch_latest_intake_form()
        
        if latest_form is None:
            raise HTTPException(
                status_code=404,
                detail="No intake forms found"
            )
        
        # Serialize MongoDB document
        serialized_form = serialize_mongodb_doc(latest_form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest intake form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest intake form: {str(e)}"
        )


