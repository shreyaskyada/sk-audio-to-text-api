
"""
PR2 Forms API endpoints
Routes request to PR2 Service.
"""
import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import JSONResponse

from app.models.pr2_form import PR2Form
from app.services.pr2_service import (
    create_pr2_form_service,
    generate_pr2_from_transcription_service,
    generate_pr2_from_soap_service,
    get_latest_pr2_form_service,
    get_latest_pr2_form_service,
    get_pr2_form_by_id_service,
    get_pr2_form_by_soap_id_service
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/pr2-form")
async def create_pr2_form(data: PR2Form):
    """
    Create a new PR2 form (save to database only, no transcription/SOAP creation)
    """
    result = await create_pr2_form_service(data)
    return JSONResponse(result)


@router.post("/pr2/generate-from-transcription")
async def generate_pr2_from_transcription(
    transcription_id: Optional[str] = Form(None, description="Transcription MongoDB ID"),
    transcription: Optional[str] = Form(None, description="Transcription text"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    pr1_id: Optional[str] = Form(None, description="PR1 form MongoDB ID (optional)")
):
    """
    Automatically generate PR2 form directly from transcription.
    """
    try:
        result = await generate_pr2_from_transcription_service(
            transcription_id=transcription_id,
            transcription=transcription,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            pr1_id=pr1_id
        )
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR2 form from transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pr2/generate-from-soap")
async def generate_pr2_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    pr1_id: Optional[str] = Form(None, description="PR1 form MongoDB ID (optional)")
):
    """
    Automatically generate PR2 form from SOAP note.
    """
    try:
        result = await generate_pr2_from_soap_service(
            soap_id=soap_id,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            pr1_id=pr1_id
        )
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR2 form: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pr2-form/latest")
async def get_latest_pr2_form():
    """
    Get the most recently created PR2 form
    """
    try:
        result = await get_latest_pr2_form_service()
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest PR2 form: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pr2-form/{form_id}")
async def get_pr2_form_by_id(form_id: str):
    """
    Get a specific PR2 form by ID
    """
    try:
        result = await get_pr2_form_by_id_service(form_id)
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving PR2 form: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
        
@router.get("/pr2/saved/{soap_id}")
async def get_pr2_form_by_soap_id(soap_id: str):
    """
    Get a PR2 form associated with a specific SOAP ID
    """
    try:
        result = await get_pr2_form_by_soap_id_service(soap_id)
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving PR2 form by SOAP ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))
