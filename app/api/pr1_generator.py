
"""
PR-1 Generator API endpoints
Routes request to PR1 Service.
"""
import logging
import json
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse

from app.models.pr1_models import (
    PR1GenerateRequest,
    SavedPR1Form
)
from app.services.pr1_service import (
    generate_pr1_orchestrator,
    process_pr1_generation_service,
    extract_work_status_orchestrator,
    save_pr1_service,
    get_saved_pr1_service,
    extract_work_status_from_soap_service,
    get_all_saved_pr1_soap_ids_service
)
from app.services.pdf_processing import (
    extract_text_from_pdf,
    convert_pdf_text_to_soap_json
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/pr1/generate")
async def generate_pr1(payload: PR1GenerateRequest):
    """
    Generate PR-1 form data structure from intake, follow-up, and/or SOAP note data
    """
    return await generate_pr1_orchestrator(payload)


@router.post("/pr1/upload-pdf")
async def upload_pdf_to_soap(file: UploadFile = File(...)):
    """
    Upload a PDF medical record/SOAP note, extract text, and convert to structured SOAP Note JSON for PR-1 generation.
    """
    try:
        # 1. Extract text from PDF
        pdf_text = await extract_text_from_pdf(file)
        
        # 2. Convert text to structured JSON using GPT
        soap_data = convert_pdf_text_to_soap_json(pdf_text)
        
        return JSONResponse({
            "status": "success",
            "soap_data": soap_data,
            "text_preview": pdf_text[:500] + "..." if pdf_text else ""
        })
    # Note: Exceptions are already handled in service functions returns
    except Exception as e:
        logger.error(f"Error in upload-pdf endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pr1/generate-from-soap")
async def generate_pr1_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags")
):
    """Generate PR-1 form from SOAP note by ID"""
    try:
        flags_dict = None
        if flags:
            try:
                flags_dict = json.loads(flags)
            except Exception as e:
                logger.warning(f"Invalid JSON in flags parameter: {flags}, error: {e}")
        
        return await process_pr1_generation_service(
            soap_id=soap_id,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            flags=flags_dict
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in PR1 generation endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pr1/extract-work-status")
async def extract_work_status_from_pr1(payload: PR1GenerateRequest):
    """
    Extract work status data from PR1 in the Work Status Form format.
    """
    return await extract_work_status_orchestrator(payload)


@router.post("/pr1/save")
async def save_pr1(payload: SavedPR1Form):
    """Save a generated PR1 form to the database"""
    result = await save_pr1_service(payload)
    return JSONResponse(result)


@router.get("/pr1/saved/{soap_id}")
async def get_saved_pr1(soap_id: str):
    """
    Retrieve a saved PR1 form by its associated SOAP ID.
    """
    result = await get_saved_pr1_service(soap_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    return JSONResponse(result)


@router.post("/pr1/extract-work-status-from-soap")
async def extract_work_status_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags")
):
    """
    Extract work status data from SOAP note directly for the Work Status Form.
    """
    return await extract_work_status_from_soap_service(
        soap_id=soap_id,
        use_latest_intake=use_latest_intake,
        use_latest_followup=use_latest_followup,
        flags=flags
    )


@router.get("/pr1/saved-ids")
async def get_all_saved_pr1_soap_ids():
    """
    Retrieve all soap_ids that have a saved PR1 form.
    """
    return await get_all_saved_pr1_soap_ids_service()
