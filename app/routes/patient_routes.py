from fastapi import APIRouter, HTTPException, Body
from typing import Dict, Any
from app.schemas.patient_schema import PatientSignatureRequest, PatientSignatureResponse
from app.services.patient_service import PatientService

router = APIRouter(prefix="/api/v1", tags=["patient"])

@router.post("/patient-signatures", response_model=PatientSignatureResponse)
async def save_patient_signature(request: PatientSignatureRequest = Body(...)):
    try:
        signature_id = await PatientService.save_signature(request.model_dump())
        return {
            "status": "success",
            "message": "Patient signature saved successfully",
            "signature_id": signature_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/patient-signatures/{soap_id}", response_model=Dict[str, Any])
async def get_patient_signature(soap_id: str):
    signature = await PatientService.get_signature_by_soap_id(soap_id)
    if not signature:
        return {"status": "not_found", "message": "No signature found"}
    return {"status": "success", "data": signature}
