"""
Work Status Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
from bson import ObjectId
from typing import Any, Dict

from app.models.work_status_form import WorkStatusForm
from app.mongodb import get_database

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'


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
# WORK STATUS FORMS API ENDPOINTS
# ============================================

@router.post("/work-status-form")
async def create_work_status_form(data: WorkStatusForm):
    """
    Create a new work status form
    
    **Parameters:**
    - data: WorkStatusForm object with all sections:
        - employeeInfo: Employee information
        - workStatus: Work status selection and dates
        - functionalRestrictions: All functional restrictions
        - providerInfo: Provider information
    
    **Returns:**
    - status: Success status
    - message: Success message
    - document_id: MongoDB document ID of the saved work status form
    
    **Example Request:**
    ```json
    {
        "employeeInfo": {
            "employeeName": "John Doe",
            "claimNumber": "WC-12345",
            "dateOfInjury": "2024-01-15",
            "dateOfEvaluation": "2024-11-10",
            "bodyPartsInjured": "Left shoulder",
            "nextFollowUpAppointment": "2024-11-20"
        },
        "workStatus": {
            "status": "modifiedDuty",
            "modifiedDutyFrom": "2024-11-10",
            "modifiedDutyTo": "2024-12-10"
        },
        "functionalRestrictions": {
            "liftingPushingPulling": {
                "noLiftingOver": true,
                "weightLimit": "10"
            },
            "upperExtremity": {
                "noAboveShoulderReaching": true,
                "aboveShoulderRight": true
            },
            ...
        },
        "providerInfo": {
            "providerName": "Dr. Smith",
            "clinic": "Ortho Clinic",
            "phone": "555-1234",
            "signature": "Dr. Smith",
            "date": "2024-11-10"
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
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        # Convert Pydantic model to dict and add timestamp
        form_data = data.model_dump()
        form_data["created_at"] = datetime.utcnow()
        
        # Insert into MongoDB
        result = await collection.insert_one(form_data)
        
        logger.info(f"✅ Work status form saved with ID: {result.inserted_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Work status form saved successfully.",
            "document_id": str(result.inserted_id)
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save work status form: {str(e)}"
        )


@router.get("/work-status-form/latest")
async def get_latest_work_status_form():
    """
    Get the most recently created work status form
    
    **Returns:**
    - Complete work status form document with all fields
    - Includes document_id and created_at timestamp
    - Returns 404 if no work status forms exist
    
    **Example Response:**
    ```json
    {
        "_id": "507f1f77bcf86cd799439011",
        "document_id": "507f1f77bcf86cd799439011",
        "employeeInfo": {
            "employeeName": "John Doe",
            "claimNumber": "WC-12345",
            ...
        },
        "workStatus": { ... },
        "functionalRestrictions": { ... },
        "providerInfo": { ... },
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
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        # Find the latest work status form (sorted by created_at descending, limit 1)
        latest_form = await collection.find_one(
            sort=[("created_at", -1)]
        )
        
        if latest_form is None:
            raise HTTPException(
                status_code=404,
                detail="No work status forms found"
            )
        
        # Serialize MongoDB document (convert ObjectId and datetime to strings)
        serialized_form = serialize_mongodb_doc(latest_form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved latest work status form with ID: {serialized_form.get('_id')}")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest work status form: {str(e)}"
        )


@router.get("/work-status-form/{form_id}")
async def get_work_status_form_by_id(form_id: str):
    """
    Get a specific work status form by ID
    
    **Parameters:**
    - form_id: MongoDB document ID of the work status form
    
    **Returns:**
    - Complete work status form document with all fields
    - Includes document_id and created_at timestamp
    - Returns 404 if form not found
    
    **Example Response:**
    ```json
    {
        "_id": "507f1f77bcf86cd799439011",
        "document_id": "507f1f77bcf86cd799439011",
        "employeeInfo": { ... },
        "workStatus": { ... },
        "functionalRestrictions": { ... },
        "providerInfo": { ... },
        "created_at": "2024-11-10T17:22:16.436963"
    }
    ```
    """
    try:
        # Get the database
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        # Validate ObjectId format
        try:
            object_id = ObjectId(form_id)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid form ID format: {form_id}"
            )
        
        # Find the form by ID
        form = await collection.find_one({"_id": object_id})
        
        if form is None:
            raise HTTPException(
                status_code=404,
                detail=f"Work status form not found with ID: {form_id}"
            )
        
        # Serialize MongoDB document
        serialized_form = serialize_mongodb_doc(form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved work status form with ID: {form_id}")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve work status form: {str(e)}"
        )

