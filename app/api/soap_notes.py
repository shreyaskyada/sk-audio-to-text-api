"""
SOAP Notes API endpoints for retrieving stored notes
"""
import logging
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Optional

from app.api.soap_storage import (
    get_soap_note_by_id,
    get_all_soap_notes,
    get_soap_notes_stats,
    update_soap_note,
    delete_soap_note
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# SOAP NOTES API ENDPOINTS
# ============================================

from fastapi.encoders import jsonable_encoder

@router.get("/all")
async def get_all_stored_soap_notes(
    limit: int = Query(100, ge=1, le=500, description="Maximum number of notes to return"),
    skip: int = Query(0, ge=0, description="Number of notes to skip")
):
    """
    Get all stored SOAP notes with pagination
    
    **Parameters:**
    - limit: Maximum number of notes to return (default: 100, max: 500)
    - skip: Number of notes to skip for pagination (default: 0)
    
    **Returns:**
    - List of SOAP notes sorted by creation date (newest first)
    - Each note includes transcription, SOAP sections, and metadata
    
    **Example:**
    - Get first 100 notes: `/api/v1/soap-notes/all?limit=100&skip=0`
    - Get next 100 notes: `/api/v1/soap-notes/all?limit=100&skip=100`
    """
    try:
        soap_notes = await get_all_soap_notes(limit=limit, skip=skip)
        
        return JSONResponse(jsonable_encoder({
            "total_returned": len(soap_notes),
            "limit": limit,
            "skip": skip,
            "soap_notes": soap_notes
        }))
        
    except Exception as e:
        logger.error(f"Error retrieving SOAP notes: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve SOAP notes"
        )


@router.get("/stats")
async def get_soap_statistics():
    """
    Get statistics about stored SOAP notes
    
    **Returns:**
    - total_soap_notes: Total number of SOAP notes in database
    - soap_notes_with_custom_prompts: Number of notes generated with custom prompts
    - soap_notes_with_default_prompts: Number of notes generated with default prompts
    - recent_soap_notes: List of 10 most recent SOAP notes
    
    **Example:**
    ```json
    {
        "total_soap_notes": 150,
        "soap_notes_with_custom_prompts": 25,
        "soap_notes_with_default_prompts": 125,
        "recent_soap_notes": [...]
    }
    ```
    """
    try:
        stats = await get_soap_notes_stats()
        
        return JSONResponse(jsonable_encoder(stats))
        
    except Exception as e:
        logger.error(f"Error getting SOAP notes statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get SOAP notes statistics"
        )


@router.get("/{soap_note_id}")
async def get_soap_note(soap_note_id: str):
    """
    Get a specific SOAP note by ID
    
    **Parameters:**
    - soap_note_id: MongoDB ObjectId of the SOAP note
    
    **Returns:**
    - Complete SOAP note document with all fields
    
    **Example:**
    - `/api/v1/soap-notes/507f1f77bcf86cd799439011`
    """
    try:
        soap_note = await get_soap_note_by_id(soap_note_id)
        
        if soap_note is None:
            raise HTTPException(
                status_code=404,
                detail=f"SOAP note not found: {soap_note_id}"
            )
        
        return JSONResponse(jsonable_encoder(soap_note))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving SOAP note {soap_note_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve SOAP note"
        )


@router.put("/{soap_note_id}")
async def update_stored_soap_note(
    soap_note_id: str, 
    update_data: dict,
    background_tasks: BackgroundTasks = None  # Add BackgroundTasks optional
):
    """
    Update a stored SOAP note and trigger downstream workflows (PR1/Work Status)
    
    **Parameters:**
    - soap_note_id: MongoDB ObjectId of the SOAP note
    - update_data: Dictionary with fields to update
    - background_tasks: FastAPI BackgroundTasks for async processing
    
    **Returns:**
    - Success message
    """
    try:
        success = await update_soap_note(soap_note_id, update_data)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"SOAP note not found or not modified: {soap_note_id}"
            )
            
        # Trigger downstream workflows (PR1 & Work Status)
        # Import dynamically to avoid circular dependency if possible, or refactor later.
        # Since we can't easily import from main.py due to circular deps, 
        # and checking if we can move logic. 
        # For now, we will assume we need to trigger it.
        # But wait, without refactoring I can't call main.py's function.
        # I will refrain from refactoring in this single turn if possible to keep it safe.
        # I will inject the logic here directly.
        
        # Helper to run workflows
        async def run_update_workflows():
             from app.api import pr1_generator, work_status_forms
             from app.api.transcription_storage import update_transcription_in_db
             from app.api.soap_storage import get_soap_note_by_id
             import re
             
             logger.info(f"🔄 Running update workflows for SOAP {soap_note_id}...")
             
             # 1. Fetch updated note
             soap_doc = await get_soap_note_by_id(soap_note_id)
             if not soap_doc: return
             
             note_content = soap_doc.get("formatted_soap_note") or soap_doc.get("soap_note") or ""
             transcription_id = soap_doc.get("transcription_id")
             
             # 2. Update status flags in transcription
             if transcription_id:
                 status_updates = {}
                 if "**REQUEST FOR AUTHORIZATION (RFA)**" in note_content or "**Requested Service:**" in note_content:
                     status_updates["needs_rfa"] = True
                     rfa_match = re.search(r'\*\*Requested Service:\*\*[\s\n]+(.*?)(?=\n\*\*|$)', note_content, re.IGNORECASE | re.DOTALL)
                     if rfa_match:
                         status_updates["rfa_name"] = rfa_match.group(1).strip().split('\n')[0][:100]
                         
                 if "**WORK STATUS**" in note_content:
                     status_updates["needs_work_status"] = True
                     ws_match = re.search(r'\*\*WORK STATUS\*\*[\s\n]+(.*?)(?=\n\*\*|$)', note_content, re.IGNORECASE | re.DOTALL)
                     if ws_match:
                         ws_val = ws_match.group(1).strip().split('\n')[0]
                         status_updates["work_status"] = ws_val[:100]
                         if "Total Disability" in ws_val or "TTD" in ws_val: status_updates["work_status_code"] = "TTD"
                         elif "Modified" in ws_val: status_updates["work_status_code"] = "MODIFIED"
                         elif "Full Duty" in ws_val: status_updates["work_status_code"] = "FULL"
                         
                 if status_updates:
                     await update_transcription_in_db(transcription_id, status_updates)
                     
             # 3. Generate PR1 and Work Status (Always generate on update as per user request)
             # Detect RFA for PR1 flag
             has_rfa = "**REQUEST FOR AUTHORIZATION (RFA)**" in note_content or "**Requested Service:**" in note_content
             pr1_flags = {
                "progress_report": True,
                "request_for_authorization": has_rfa,
                "change_in_patient_condition": False
             }
             
             logger.info(f"🚀 Triggering PR1 & Work Status generation for updated SOAP {soap_note_id}")
             
             try:
                await pr1_generator.process_pr1_generation_service(
                    soap_id=soap_note_id,
                    use_latest_intake=True,
                    use_latest_followup=False,
                    flags=pr1_flags
                )
             except Exception as e:
                 logger.error(f"Failed to generate PR1: {e}")
                 
             try:
                await work_status_forms.process_work_status_generation(
                    soap_id=soap_note_id,
                    use_latest_intake=True,
                    use_latest_followup=False
                )
             except Exception as e:
                 logger.error(f"Failed to generate Work Status: {e}")

        # Execute workflow
        if background_tasks:
            background_tasks.add_task(run_update_workflows)
        else:
            # Fallback if background_tasks not provided (shouldn't happen if updated correctly)
            # But since endpoint is async, we can just await it or fire-and-forget?
            # Better to await if we can't use background tasks
            await run_update_workflows()

        return JSONResponse({
            "message": "SOAP note updated successfully and pipelines triggered",
            "soap_note_id": soap_note_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating SOAP note {soap_note_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update SOAP note"
        )


@router.delete("/{soap_note_id}")
async def delete_stored_soap_note(soap_note_id: str):
    """
    Delete a stored SOAP note
    
    **Parameters:**
    - soap_note_id: MongoDB ObjectId of the SOAP note
    
    **Returns:**
    - Success message
    
    **Example:**
    - DELETE `/api/v1/soap-notes/507f1f77bcf86cd799439011`
    """
    try:
        success = await delete_soap_note(soap_note_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"SOAP note not found: {soap_note_id}"
            )
        
        return JSONResponse({
            "message": "SOAP note deleted successfully",
            "soap_note_id": soap_note_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting SOAP note {soap_note_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete SOAP note"
        )

