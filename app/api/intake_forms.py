"""
Intake Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime

from app.models.intake_form import IntakeForm
from app.mongodb import get_database
from bson import ObjectId
from typing import Any, Dict

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
INTAKE_FORMS_COLLECTION = 'intake_forms'


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
    
    **Parameters:**
    - data: IntakeForm object with all sections (A through J)
    
    **Returns:**
    - status: Success status
    - message: Success message
    - document_id: MongoDB document ID of the saved intake form
    
    **Example Request:**
    ```json
    {
        "section_a": {
            "full_name": "John Doe",
            "date_of_birth": "1980-01-01",
            "age": "43",
            "gender": "Male",
            ...
        },
        "section_b": { ... },
        "section_c": { ... },
        ...
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
        
        collection = db[INTAKE_FORMS_COLLECTION]
        
        # Convert Pydantic model to dict and add timestamp
        form_data = data.model_dump()
        form_data["created_at"] = datetime.utcnow()
        
        # Insert into MongoDB
        result = await collection.insert_one(form_data)
        
        logger.info(f"✅ Intake form saved with ID: {result.inserted_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Intake form saved successfully.",
            "document_id": str(result.inserted_id)
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
async def get_latest_intake_form():
    """
    Get the most recently created intake form
    
    **Returns:**
    - Complete intake form document with all fields
    - Includes document_id and created_at timestamp
    - Returns 404 if no intake forms exist
    
    **Example Response:**
    ```json
    {
        "_id": "507f1f77bcf86cd799439011",
        "section_a": { ... },
        "section_b": { ... },
        "section_c": { ... },
        ...
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
        
        collection = db[INTAKE_FORMS_COLLECTION]
        
        # Find the latest intake form (sorted by created_at descending, limit 1)
        latest_form = await collection.find_one(
            sort=[("created_at", -1)]
        )
        
        if latest_form is None:
            raise HTTPException(
                status_code=404,
                detail="No intake forms found"
            )
        
        # Serialize MongoDB document (convert ObjectId and datetime to strings)
        serialized_form = serialize_mongodb_doc(latest_form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved latest intake form with ID: {serialized_form.get('_id')}")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest intake form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest intake form: {str(e)}"
        )

