import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.models.intake_form import IntakeForm
from app.models.followup_form import FollowUpForm
from app.services import form_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/intake-form")
async def create_intake_form(data: IntakeForm):
    try:
        doc_id = await form_service.create_intake_form(data.model_dump())
        return JSONResponse({
            "status": "success",
            "message": "Intake form saved successfully.",
            "document_id": doc_id
        })
    except Exception as e:
        logger.error(f"Error saving intake form: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save intake form: {str(e)}")

@router.get("/intake-form/latest")
async def get_latest_intake_form():
    try:
        form = await form_service.get_latest_intake_form()
        if form is None:
            raise HTTPException(status_code=404, detail="No intake forms found")
        form["document_id"] = form.get("_id")
        return JSONResponse(form)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest intake form: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve latest intake form: {str(e)}")

@router.post("/followup-intake")
async def create_followup_intake(payload: FollowUpForm):
    try:
        doc_id = await form_service.create_followup_form(payload.model_dump())
        return JSONResponse({
            "status": "success",
            "message": "Follow-up intake form saved.",
            "document_id": doc_id
        })
    except Exception as e:
        logger.error(f"Error saving follow-up intake form: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save follow-up intake form: {str(e)}")

@router.get("/follow-up/latest")
async def get_latest_followup_form():
    try:
        form = await form_service.get_latest_followup_form()
        if form is None:
            raise HTTPException(status_code=404, detail="No follow-up forms found")
        form["document_id"] = form.get("_id")
        return JSONResponse(form)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest follow-up form: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve latest follow-up form: {str(e)}")
