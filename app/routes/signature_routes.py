from fastapi import APIRouter, HTTPException, Body
from typing import Dict, Any
from app.schemas.signature_schema import PatientSignatureRequest, PatientSignatureResponse
from app.services import signature_service

router = APIRouter()

@router.post("/", response_model=PatientSignatureResponse)
async def save_patient_signature(request: PatientSignatureRequest = Body(...)):
    try:
        sig_id = await signature_service.save_signature(
            request.soap_id, request.signature_data, request.patient_name, request.form_type
        )
        return {"status": "success", "message": "Patient signature saved successfully", "signature_id": sig_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{soap_id}", response_model=Dict[str, Any])
async def get_patient_signature(soap_id: str):
    doc = await signature_service.get_latest_signature_by_soap_id(soap_id)
    if not doc: return {"status": "not_found", "message": "No signature found"}
    return {"status": "success", "data": doc}
