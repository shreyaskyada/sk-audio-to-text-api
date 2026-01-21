from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any
from app.schemas.pr1_schema import PR1GenerateRequest
from app.services.pr1_service import PR1Service
from app.services.intake_service import IntakeService
from app.services.followup_service import FollowupService
from app.services.soap_service import SOAPService

router = APIRouter(prefix="/api/v1", tags=["pr1"])

@router.post("/pr1/generate")
async def generate_pr1(request: PR1GenerateRequest):
    # 1. Fetch data
    intake = None
    if request.intake_id:
        intake = await IntakeService.get_by_id(request.intake_id)
    elif request.use_latest_intake:
        intake = await IntakeService.get_latest()
    elif request.intake:
        intake = request.intake.model_dump()

    followup = None
    if request.followup_id:
        followup = await FollowupService.get_by_id(request.followup_id)
    elif request.use_latest_followup:
        followup = await FollowupService.get_latest()
    elif request.followup:
        followup = request.followup.model_dump()

    soap = None
    if request.soap_id:
        soap = await SOAPService.get_by_id(request.soap_id)
    elif request.soap:
        soap = request.soap.model_dump()

    if not soap:
        raise HTTPException(status_code=400, detail="SOAP note is required for PR1 generation")

    # 2. Build Payload
    payload = PR1Service.build_pr1_payload(intake, followup, soap, request.flags)
    
    return {"status": "success", "data": payload}

@router.post("/pr1/save")
async def save_pr1(soap_id: str = Body(..., embed=True), patient_name: str = Body(..., embed=True), form_data: dict = Body(...)):
    doc_id = await PR1Service.save_pr1(soap_id, patient_name, form_data)
    return {"status": "success", "message": "Saved successfully", "soap_id": doc_id}

@router.get("/pr1/saved/{soap_id}")
async def get_saved_pr1(soap_id: str):
    doc = await PR1Service.get_by_soap_id(soap_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Saved PR1 not found")
    return {"status": "success", "data": doc}
