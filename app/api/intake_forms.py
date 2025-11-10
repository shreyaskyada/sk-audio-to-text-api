"""
Intake Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime

from app.models.intake_form import IntakeForm
from app.mongodb import get_database

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
INTAKE_FORMS_COLLECTION = 'intake_forms'


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

