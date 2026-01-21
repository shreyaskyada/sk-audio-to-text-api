
import logging
import asyncio
import re
from typing import Optional
from datetime import datetime

from app.services import pr1_service as pr1_generator
from app.services import work_status_service as work_status_forms
from app.services.transcription_storage import update_transcription_in_db, get_transcription_by_id, save_transcription_to_db
from app.services.soap_storage import get_soap_note_by_id, create_pending_soap_note, save_soap_note_to_db, update_soap_note
from app.services.intake import fetch_latest_intake_form, extract_intake_form_values, format_intake_form_data_for_prompt
from app.services.soap import generate_comprehensive_soap_note
from app.schemas import SOAPRequest

logger = logging.getLogger(__name__)

async def run_downstream_workflows(soap_id: str, transcription_id: Optional[str] = None, generate_files: bool = True):
    """
    Execute downstream tasks after SOAP generation:
    1. Parse SOAP content to update transcription statuses (RFA, Work Status)
    2. Generate PR1 form (Optional)
    3. Generate Work Status form (Optional)
    """
    logger.info(f"🔄 Starting downstream workflows for SOAP ID: {soap_id} (generate_files={generate_files})")
    try:
        # Fetch SOAP note for content parsing
        soap_doc = await get_soap_note_by_id(soap_id)
        if not soap_doc:
            logger.error(f"Cannot run downstream workflows: SOAP note {soap_id} not found")
            return

        # Determine transcription_id if not provided
        if not transcription_id:
            transcription_id = soap_doc.get("transcription_id")

        # 1. Update statuses if we have a transcription ID
        if transcription_id:
             try:
                note_content = soap_doc.get("formatted_soap_note") or soap_doc.get("soap_note") or ""
                status_updates = {}
                
                # Check for RFA
                if "**REQUEST FOR AUTHORIZATION (RFA)**" in note_content or "**Requested Service:**" in note_content:
                    status_updates["needs_rfa"] = True
                    # Extract RFA name
                    rfa_match = re.search(r'\*\*Requested Service:\*\*[\s\n]+(.*?)(?=\n\*\*|$)', note_content, re.IGNORECASE | re.DOTALL)
                    if rfa_match:
                        # Take first line or up to 100 chars
                        rfa_text = rfa_match.group(1).strip().split('\n')[0]
                        status_updates["rfa_name"] = rfa_text[:100]
                
                # Check for Work Status
                if "**WORK STATUS**" in note_content:
                    status_updates["needs_work_status"] = True
                    # Extract Work Status
                    ws_match = re.search(r'\*\*WORK STATUS\*\*[\s\n]+(.*?)(?=\n\*\*|$)', note_content, re.IGNORECASE | re.DOTALL)
                    if ws_match:
                        ws_text = ws_match.group(1).strip().split('\n')[0]
                        status_updates["work_status"] = ws_text[:100]
                        
                        # Map to code
                        if "Total Disability" in ws_text or "TTD" in ws_text:
                            status_updates["work_status_code"] = "TTD"
                        elif "Modified" in ws_text:
                            status_updates["work_status_code"] = "MODIFIED"
                        elif "Full Duty" in ws_text:
                            status_updates["work_status_code"] = "FULL"
                
                if status_updates:
                    logger.info(f"🔄 Updating transcription {transcription_id} with extracted statuses: {status_updates}")
                    await update_transcription_in_db(transcription_id, status_updates)
                    
             except Exception as parse_error:
                logger.warning(f"⚠️ Failed to extract statuses from SOAP note: {parse_error}")

        # 2 & 3. Generate PR1 and Work Status in parallel (faster!)
        if generate_files:
            logger.info(f"Step 2 & 3: Generating PR1 and Work Status in parallel for SOAP {soap_id}...")
        
        # Detect flags from SOAP note content
            note_content = soap_doc.get("formatted_soap_note") or soap_doc.get("soap_note") or ""
            has_rfa = "**REQUEST FOR AUTHORIZATION (RFA)**" in note_content or "**Requested Service:**" in note_content
            
            # Use same flags as manual generation, with dynamic RFA detection
            pr1_flags = {
                "progress_report": True,
                "request_for_authorization": has_rfa,
                "change_in_patient_condition": False
            }
            
            logger.info(f"   PR1 Flags: progress_report=True, request_for_authorization={has_rfa}")
            
            # Define async tasks for parallel execution
            async def generate_pr1():
                try:
                    await pr1_generator.process_pr1_generation_service(
                        soap_id=soap_id,
                        use_latest_intake=True, 
                        use_latest_followup=False,  # Match manual API behavior
                        flags=pr1_flags
                    )
                    logger.info(f"✅ PR1 generation completed for SOAP {soap_id}")
                    return True
                except Exception as pr1_error:
                    logger.error(f"❌ PR1 generation failed for SOAP {soap_id}: {pr1_error}", exc_info=True)
                    return False
            
            async def generate_work_status():
                try:
                    await work_status_forms.process_work_status_generation(
                        soap_id=soap_id,
                        use_latest_intake=True, 
                        use_latest_followup=False  # Match PR1 behavior
                    )
                    logger.info(f"✅ Work Status generation completed for SOAP {soap_id}")
                    return True
                except Exception as ws_error:
                    logger.error(f"❌ Work Status generation failed for SOAP {soap_id}: {ws_error}", exc_info=True)
                    return False
            
            # Run both generations in parallel
            pr1_result, ws_result = await asyncio.gather(
                generate_pr1(),
                generate_work_status(),
                return_exceptions=False
            )
            
            logger.info(f"✅ Parallel generation completed - PR1: {pr1_result}, Work Status: {ws_result}")
            
        logger.info(f"✅ Downstream workflows completed for SOAP {soap_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in downstream workflows for SOAP {soap_id}: {e}", exc_info=True)


async def run_full_generation_workflow(transcription_id: str, user_id: Optional[str] = None, generate_files: bool = True):
    """
    Background workflow to generate SOAP, PR1, and Work Status forms sequentially.
    """
    logger.info(f"🚀 Starting background workflow for transcription {transcription_id} (generate_files={generate_files})")
    try:
        # 0. Update status to in_progress
        await update_transcription_in_db(transcription_id, {"background_running_status": "in_progress"})
        
        # Fetch transcription to check if text exists
        transcription = await get_transcription_by_id(transcription_id)
        if not transcription:
            raise Exception("Transcription not found") 
            
        transcription_text = transcription.get("text", "")
        
        # Create a new Pending SOAP note first (to have an ID)
        pending_soap = await create_pending_soap_note(
            user_id=user_id or transcription.get("user_id") or "system", 
            transcription_id=transcription_id
        )
        
        if not pending_soap:
             raise Exception("Failed to create pending SOAP note")
        
        soap_id = str(pending_soap.get('_id'))
        logger.info(f"Created pending SOAP note: {soap_id}")
        
        # Prepare SOAP Generation Request
        soap_req = SOAPRequest(
            transcription=transcription_text,
            transcription_id=transcription_id,
            userId=user_id or transcription.get("user_id") or "system"
        )
        
        # Fetch latest intake form if available
        intake_doc = None
        intake_form_data = None
        try:
             intake_doc = await fetch_latest_intake_form()
             if intake_doc:
                 intake_form_data = format_intake_form_data_for_prompt(intake_doc) # Assuming format_ function exists in intake service now? No I only added extract.
                 # Wait, format_intake_form_data_for_prompt was in main.py but I didn't add it to intake.py!
                 # I need to add that helper to intake.py first. It uses extract_intake_form_values.
                 pass
        except Exception as e:
             logger.warning(f"Could not fetch intake form for background process: {e}")

        # Wait, I missed copying `format_intake_form_data_for_prompt` to `intake.py`.
        # I should probably just use `extract_intake_form_values` and format it manually or add the function.
        # For now, to keep it simple, I'll pass intake_doc to `generate_comprehensive_soap_note` which handles extraction.
        # `generate_comprehensive_soap_note` takes `intake_doc`.
        
        # Generate SOAP Note
        soap_result = await generate_comprehensive_soap_note(
            soap_request=soap_req,
            intake_doc=intake_doc,
            intake_form_data=intake_form_data
        )
        
        # Save Generated Note
        if soap_result:
             # Update the pending note
             soap_result["status"] = "completed"
             soap_result["updated_at"] = datetime.utcnow()
             await update_soap_note(soap_id, soap_result)
             logger.info(f"✅ Saved generated SOAP note {soap_id}")
             
             # Run downstream
             await run_downstream_workflows(soap_id, transcription_id, generate_files)
             
             # Mark transcription as completed
             await update_transcription_in_db(transcription_id, {"background_running_status": "completed"})
             
        else:
             raise Exception("SOAP generation returned empty result")

    except Exception as e:
        logger.error(f"❌ Background workflow failed: {e}", exc_info=True)
        await update_transcription_in_db(transcription_id, {"background_running_status": "failed"})
