
"""
Work Status Form API endpoints
Routes request to Work Status Service.
"""
import logging
import json
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import JSONResponse

from app.models.work_status_form import WorkStatusForm
from app.services.work_status_service import (
    extract_work_status_from_soap_service,
    create_work_status_form_service,
    get_latest_work_status_form_service,
    get_all_saved_work_status_soap_ids_service,
    get_saved_work_status_form_service,
    get_work_status_form_by_id
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/work-status-form/extract-from-soap")
async def extract_work_status_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags")
):
    """
    Extract work status data from SOAP note directly for the Work Status Form.
    """
    try:
        flags_dict = {}
        if flags:
            try:
                flags_dict = json.loads(flags)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in flags parameter: {flags}")
                
        return await extract_work_status_from_soap_service(
            soap_id=soap_id,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            flags=flags_dict
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in work-status-form extraction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/work-status-form")
async def create_work_status_form(data: WorkStatusForm):
    """
    Create a new work status form
    """
    result = await create_work_status_form_service(data)
    return JSONResponse(result)


@router.get("/work-status-form/latest")
async def get_latest_work_status_form():
    """
    Get the most recently created work status form
    """
    result = await get_latest_work_status_form_service()
    return JSONResponse(result)


@router.get("/work-status-form/saved-ids")
async def get_all_saved_work_status_soap_ids():
    """
    Retrieve all soap_ids that have a saved Work Status form.
    """
    return await get_all_saved_work_status_soap_ids_service()


@router.get("/work-status-form/saved/{soap_id}")
async def get_saved_work_status_form(soap_id: str):
    """
    Retrieve a saved Work Status form by its associated SOAP ID.
    """
    result = await get_saved_work_status_form_service(soap_id)
    return JSONResponse(result)


@router.get("/work-status-form/{form_id}")
async def get_work_status_form(form_id: str):
    """
    Get a specific work status form by ID
    """
    result = await get_work_status_form_by_id(form_id)
    return JSONResponse(result)
