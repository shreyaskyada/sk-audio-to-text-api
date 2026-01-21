from fastapi import APIRouter, HTTPException, Form, Body
from typing import Optional, Dict, Any
from app.schemas.pr2_schema import PR2Form
from app.services.pr2_service import PR2Service

router = APIRouter(prefix="/api/v1", tags=["pr2"])

@router.post("/pr2-form")
async def create_pr2_form(data: PR2Form):
    doc_id = await PR2Service.save_form(data.model_dump())
    return {"status": "success", "message": "Saved successfully", "document_id": doc_id}

@router.get("/pr2-form/latest")
async def get_latest_pr2_form():
    form = await PR2Service.get_latest()
    if not form:
        raise HTTPException(status_code=404, detail="Not found")
    return form

@router.get("/pr2-form/saved-ids")
async def get_all_saved_pr2_soap_ids():
    soap_ids = await PR2Service.get_all_saved_soap_ids()
    return {"status": "success", "soap_ids": soap_ids}

@router.get("/pr2-form/saved/{soap_id}")
async def get_saved_pr2_form(soap_id: str):
    form = await PR2Service.get_by_soap_id(soap_id)
    if not form:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "success", "data": form}
