from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import json
from app.schemas.work_status_schema import WorkStatusForm
from app.services import work_status_service

router = APIRouter()

@router.post("/extract-from-soap")
async def extract_work_status_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags")
):
    try:
        await work_status_service.process_work_status_generation(soap_id)
        saved_form = await work_status_service.get_saved_form_by_soap_id(soap_id)
        return {
            "status": "success", 
            "message": "Work status extracted and saved", 
            "work_status_data": saved_form,
            "metadata": {
                "soap_source": "id",
                "soap_id": soap_id,
                "intake_source": "latest" if use_latest_intake else None,
                "followup_source": "latest" if use_latest_followup else None
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
async def create_work_status_form(data: WorkStatusForm):
    try:
        doc_id = await work_status_service.save_work_status_form(data.model_dump(exclude_none=True))
        return {"status": "success", "message": "Work status form saved successfully.", "document_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/latest")
async def get_latest_work_status_form():
    doc = await work_status_service.get_latest_work_status_form()
    if not doc: raise HTTPException(status_code=404, detail="No work status forms found")
    return doc

@router.get("/saved-ids")
async def get_all_saved_work_status_soap_ids():
    ids = await work_status_service.get_all_saved_soap_ids()
    return {"status": "success", "soap_ids": ids}

@router.get("/saved/{soap_id}")
async def get_saved_work_status_form(soap_id: str):
    doc = await work_status_service.get_saved_form_by_soap_id(soap_id)
    if not doc: raise HTTPException(status_code=404, detail="Saved Work Status form not found")
    return {"status": "success", "data": doc}
