from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel
import logging
from app.mongodb import get_database
from bson import ObjectId

router = APIRouter()
logger = logging.getLogger(__name__)

class PatientSignatureRequest(BaseModel):
    soap_id: str
    signature_data: str  # Base64 encoded signature image
    patient_name: Optional[str] = None
    form_type: Optional[str] = "PR1"  # "PR1" or "WorkStatus"

class PatientSignatureResponse(BaseModel):
    status: str
    message: str
    signature_id: str

@router.post("/patient-signatures", response_model=PatientSignatureResponse)
async def save_patient_signature(request: PatientSignatureRequest = Body(...)):
    """
    Save a patient's digital signature.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")
            
        collection = db['patient_signatures']
        
        signature_doc = {
            "soap_id": request.soap_id,
            "signature_data": request.signature_data,
            "patient_name": request.patient_name,
            "form_type": request.form_type,
            "created_at": datetime.utcnow()
        }
        
        result = await collection.insert_one(signature_doc)
        
        return {
            "status": "success",
            "message": "Patient signature saved successfully",
            "signature_id": str(result.inserted_id)
        }
        
    except Exception as e:
        logger.error(f"Error saving patient signature: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save signature: {str(e)}")

@router.get("/patient-signatures/{soap_id}", response_model=Dict[str, Any])
async def get_patient_signature(soap_id: str):
    """
    Get a patient's digital signature for a given SOAP ID.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")
            
        collection = db['patient_signatures']
        
        # Sort by created_at desc to get latest
        signature = await collection.find_one(
            {"soap_id": soap_id},
            sort=[("created_at", -1)]
        )
        
        if not signature:
            return {"status": "not_found", "message": "No signature found"}
        
        signature["_id"] = str(signature["_id"])
        
        return {
            "status": "success",
            "data": signature
        }
        
    except Exception as e:
        logger.error(f"Error fetching patient signature: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch signature: {str(e)}")
