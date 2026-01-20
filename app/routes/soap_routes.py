import logging
import re
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from app.services import soap_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/all")
async def get_all_stored_soap_notes(
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0)
):
    try:
        soap_notes = await soap_service.get_all_soap_notes(limit=limit, skip=skip)
        return JSONResponse(jsonable_encoder({
            "total_returned": len(soap_notes),
            "limit": limit,
            "skip": skip,
            "soap_notes": soap_notes
        }))
    except Exception as e:
        logger.error(f"Error retrieving SOAP notes: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve SOAP notes")

@router.get("/stats")
async def get_soap_statistics():
    try:
        stats = await soap_service.get_soap_stats()
        return JSONResponse(jsonable_encoder(stats))
    except Exception as e:
        logger.error(f"Error getting SOAP notes statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get SOAP notes statistics")

@router.get("/{soap_note_id}")
async def get_soap_note(soap_note_id: str):
    try:
        soap_note = await soap_service.get_soap_note_by_id(soap_note_id)
        if soap_note is None:
            raise HTTPException(status_code=404, detail=f"SOAP note not found: {soap_note_id}")
        return JSONResponse(jsonable_encoder(soap_note))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving SOAP note {soap_note_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve SOAP note")

@router.put("/{soap_note_id}")
async def update_stored_soap_note(
    soap_note_id: str, 
    update_data: dict,
    background_tasks: BackgroundTasks
):
    try:
        success = await soap_service.update_soap_note_in_db(soap_note_id, update_data)
        if not success:
            raise HTTPException(status_code=404, detail=f"SOAP note not found or not modified: {soap_note_id}")
            
        # Helper to run workflows (keeping business logic identical)
        async def run_update_workflows():
             try:
                 # Imports inside parsing logic to avoid circular dependencies if any
                 from app.services import pr1_service, work_status_service
                 from app.services.transcription_service import update_transcription_in_db
                 from app.utils.pr1_utils import fetch_latest_document, COLL_INTAKE, COLL_FOLLOWUP
                 
                 logger.info(f"🔄 Running update workflows for SOAP {soap_note_id}...")
                 
                 soap_doc = await soap_service.get_soap_note_by_id(soap_note_id)
                 if not soap_doc: return
                 
                 note_content = soap_doc.get("formatted_soap_note") or soap_doc.get("soap_note") or ""
                 transcription_id = soap_doc.get("transcription_id")
                 
                 # 1. Update Transcription Status
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
                         
                 # 2. Trigger PR1 Generation (Using Service Orchestrator)
                 try:
                    has_rfa = "**REQUEST FOR AUTHORIZATION (RFA)**" in note_content or "**Requested Service:**" in note_content
                    pr1_flags = {"progress_report": True, "request_for_authorization": has_rfa, "change_in_patient_condition": False}
                    
                    logger.info(f"🚀 Generating PR1 for SOAP {soap_note_id}")
                    # Use the restored orchestration function that handles enrichment and fetching
                    await pr1_service.process_pr1_generation(
                        soap_id=soap_note_id,
                        use_latest_intake=True,
                        use_latest_followup=False, # Matches old backend default for updates
                        flags=pr1_flags
                    )
                 except Exception as e:
                     logger.error(f"Failed to generate PR1: {e}")
                     
                 # 3. Trigger Work Status Generation (Using Service)
                 try:
                    logger.info(f"🚀 Generating Work Status for SOAP {soap_note_id}")
                    await work_status_service.process_work_status_generation(soap_note_id)
                 except Exception as e:
                     logger.error(f"Failed to generate Work Status: {e}")
                     
             except Exception as e:
                 logger.error(f"Error in background workflow: {e}")
        
        background_tasks.add_task(run_update_workflows)

        return JSONResponse({
            "message": "SOAP note updated successfully and pipelines triggered",
            "soap_note_id": soap_note_id
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating SOAP note {soap_note_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update SOAP note")

@router.delete("/{soap_note_id}")
async def delete_stored_soap_note(soap_note_id: str):
    try:
        success = await soap_service.delete_soap_note_from_db(soap_note_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"SOAP note not found: {soap_note_id}")
        return JSONResponse({"message": "SOAP note deleted successfully", "soap_note_id": soap_note_id})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting SOAP note {soap_note_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete SOAP note")
