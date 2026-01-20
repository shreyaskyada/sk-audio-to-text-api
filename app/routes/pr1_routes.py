from fastapi import APIRouter, HTTPException, BackgroundTasks, Form, Body
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from typing import List, Optional, Dict, Any, Union
import json
from app.schemas.pr1_schema import PR1GenerateRequest, SavedPR1Form
from app.services import pr1_service, soap_service
from app.utils.pr1_utils import fetch_if_needed, fetch_latest_document

router = APIRouter()

COLL_INTAKE = "intake_forms"
COLL_FOLLOWUP = "followup_intake_forms"
COLL_SOAP = "soap_notes"

@router.post("/generate-from-soap")
async def generate_pr1(
    payload: Optional[PR1GenerateRequest] = None,
    soap_id: Optional[str] = Form(None),
    use_latest_intake: bool = Form(False),
    use_latest_followup: bool = Form(False),
    flags: Optional[str] = Form(None)
):
    """
    Generate PR1 data structures from provided SOAP ID.
    Supports BOTH JSON Body (Legacy/Standard) and Form Data.
    """
    try:
        # Determine source of valid data
        final_soap_id = None
        final_use_latest_intake = False
        final_use_latest_followup = False
        final_flags = {}

        if payload:
            # JSON Payload was provided
            final_soap_id = payload.soap_id
            final_use_latest_intake = payload.use_latest_intake
            final_use_latest_followup = payload.use_latest_followup
            final_flags = payload.flags or {}
        else:
            # Fallback to Form Data
            final_soap_id = soap_id
            final_use_latest_intake = use_latest_intake
            final_use_latest_followup = use_latest_followup
            if flags:
                try:
                    final_flags = json.loads(flags)
                except:
                    pass
        
        if not final_soap_id:
             raise HTTPException(status_code=400, detail="soap_id is required (in JSON body or form data)")

        # Use the Orchestrator service (Handles GPT enrichment, fetching, saving)
        saved_id = await pr1_service.process_pr1_generation(
            soap_id=final_soap_id,
            use_latest_intake=final_use_latest_intake,
            use_latest_followup=final_use_latest_followup,
            flags=final_flags
        )
        
        # Fetch the generated form to return to frontend
        generated_doc = await pr1_service.get_saved_pr1(final_soap_id)
        if not generated_doc:
             raise HTTPException(status_code=500, detail="Failed to retrieve generated PR1")

        # Fetch SOAP doc for metadata/context if needed by frontend (Legacy compatibility)
        soap_doc = await soap_service.get_soap_note_by_id(final_soap_id) or {}
        
        # Construct simplified soap_data similar to legacy
        soap_data_dict = {
            "subjective": soap_doc.get("subjective", ""),
            "objective": soap_doc.get("objective", ""),
            "assessment": soap_doc.get("assessment", ""),
            "plan": soap_doc.get("plan", ""),
            "date_of_service": soap_doc.get("date_of_service", ""),
            "patient_info": soap_doc.get("patient_info", {})
        }
        
        # Pr1 Data handling
        # Generated doc is now nested { form_data: {...}, soap_data: {...} }
        pr1_values = generated_doc.get("form_data", {})
        soap_data_response = generated_doc.get("soap_data") or soap_data_dict
        
        # Return response matching legacy structure
        # Use jsonable_encoder to handle ObjectId serialization automatically
        return JSONResponse(jsonable_encoder({
            "status": "success",
            "pr1_values": pr1_values, # The actual form data (checkboxes, sections)
            "pr1_data": pr1_values,   # Alias for safety
            "metadata": {
                "soap_used": True,
                "soap_source": "id",
                "soap_id": final_soap_id,
                "source": "generated",
            },
            "soap_data": soap_data_response
        }))
        
    except Exception as e:
        import traceback
        print(traceback.format_exc()) # Print to console for debugging
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save")
async def save_pr1(payload: SavedPR1Form):
    """Save or update a PR1 form"""
    try:
        form_id = await pr1_service.save_pr1_form(payload.model_dump(exclude_none=True))
        return {"status": "success", "id": form_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/saved/{soap_id}")
async def get_saved_pr1(soap_id: str):
    """Get a saved PR1 form by SOAP ID"""
    doc = await pr1_service.get_saved_pr1(soap_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Saved PR1 form not found")
    return doc

@router.get("/saved-ids")
async def get_saved_pr1_ids():
    """List all SOAP IDs that have a saved PR1 form"""
    docs = await pr1_service.get_all_saved_pr1s()
    # Extract only soap_ids from the saved docs
    ids = [doc.get("soap_id") for doc in docs if doc.get("soap_id")]
    return {"status": "success", "soap_ids": ids}

@router.get("/saved")
async def list_saved_pr1s():
    """List all saved PR1 forms"""
    return await pr1_service.get_all_saved_pr1s()
