from fastapi import APIRouter, HTTPException, Form, Body
from typing import Optional, Dict, Any
from app.schemas.work_status_schema import WorkStatusForm
from app.services.work_status_service import WorkStatusService
import json

router = APIRouter(prefix="/api/v1", tags=["work-status"])

@router.post("/work-status-form/extract-from-soap")
async def extract_work_status_from_soap(
    soap_id: str = Form(...),
    use_latest_intake: bool = Form(False),
    use_latest_followup: bool = Form(False),
    flags: Optional[str] = Form(None)
):
    flags_dict = {}
    if flags:
        try:
            flags_dict = json.loads(flags)
        except:
            pass
            
    result = await WorkStatusService.process_extraction(soap_id, use_latest_intake, use_latest_followup, flags_dict)
    return result

@router.post("/work-status-form")
async def create_work_status_form(data: WorkStatusForm):
    form_id = await WorkStatusService.save_form(data.soap_id, data.model_dump())
    return {"status": "success", "message": "Saved successfully", "document_id": form_id}

@router.get("/work-status-form/latest")
async def get_latest_work_status_form():
    form = await WorkStatusService.get_latest()
    if not form:
        raise HTTPException(status_code=404, detail="Not found")
    return form

@router.get("/work-status-form/saved-ids")
async def get_all_saved_work_status_soap_ids():
    soap_ids = await WorkStatusService.get_all_saved_soap_ids()
    return {"status": "success", "soap_ids": soap_ids}

@router.get("/work-status-form/saved/{soap_id}")
async def get_saved_work_status_form(soap_id: str):
    form = await WorkStatusService.get_by_soap_id(soap_id)
    if not form:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "success", "data": form}
