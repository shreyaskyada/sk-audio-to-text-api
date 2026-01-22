
import logging
import asyncio
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Form
from fastapi.responses import JSONResponse

from app.schemas import SOAPRequest, SOAPResponse, PatientInfo
from app.prompts import ORTHOPEDIC_SOAP_SYSTEM_PROMPT, ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
from app.services.transcription_storage import get_transcription_by_id
from app.services.soap_storage import (
    get_soap_note_by_transcription_id, 
    create_pending_soap_note, 
    update_soap_note, 
    get_soap_note_by_id,
    save_soap_note_to_db
)
from app.services.text_processing import fix_terms
from app.services.intake import (
    fetch_intake_form_by_id, 
    fetch_latest_intake_form, 
    format_intake_form_data_for_prompt
)
from app.services.soap import generate_comprehensive_soap_note, format_prompts_for_chatgpt
from app.services.workflow import run_downstream_workflows
import os

logger = logging.getLogger(__name__)

router = APIRouter()

OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.1')

@router.post("/soap-prompts/debug", tags=["SOAP Generation"])
async def get_formatted_prompts_for_chatgpt_endpoint(
    soap_request: SOAPRequest,
    intake_id: Optional[str] = Query(None, description="MongoDB intake form ID to use for PMH, Medications, and Social/Occupational History"),
    use_latest_intake: Optional[bool] = Query(False, description="Use latest intake form if True")
):
    """
    Get the exact formatted prompts used for SOAP generation, formatted for ChatGPT comparison.
    """
    try:
        if soap_request.transcription_id:
            transcription = await get_transcription_by_id(soap_request.transcription_id)
            if not transcription:
                raise HTTPException(
                    status_code=404,
                    detail=f"Transcription not found with ID: {soap_request.transcription_id}"
                )
            soap_request.transcription = transcription.get("text", "")
        
        if not soap_request.transcription:
            raise HTTPException(
                status_code=400,
                detail="Either transcription_id or transcription text must be provided"
            )
        
        corrected_transcription = fix_terms(soap_request.transcription)
        
        patient_context = ""
        header_section = ""
        
        if soap_request.patient:
            patient_info = []
            if soap_request.patient.name:
                patient_info.append(f"Name: {soap_request.patient.name}")
            if soap_request.patient.age:
                patient_info.append(f"Age: {soap_request.patient.age}")
            if soap_request.patient.gender:
                patient_info.append(f"Gender: {soap_request.patient.gender}")
            
            if patient_info:
                patient_context = "**PATIENT INFORMATION:**\n" + "\n".join(patient_info)
                header_section = f"Patient: {', '.join(patient_info)}\n"
        
        if soap_request.date_of_service:
            header_section += f"Date of Service: {soap_request.date_of_service}\n"
        
        if soap_request.location:
            header_section += f"Location: {soap_request.location}\n"
        
        if soap_request.reason_for_visit:
            header_section += f"Reason for Visit: {soap_request.reason_for_visit}\n"
        
        system_prompt = soap_request.system_prompt if soap_request.system_prompt else ORTHOPEDIC_SOAP_SYSTEM_PROMPT
        
        intake_form_data = ""
        if intake_id:
            intake_doc = await fetch_intake_form_by_id(intake_id)
            if intake_doc:
                intake_form_data = format_intake_form_data_for_prompt(intake_doc)
        elif use_latest_intake:
            intake_doc = await fetch_latest_intake_form()
            if intake_doc:
                intake_form_data = format_intake_form_data_for_prompt(intake_doc)
        
        if soap_request.user_prompt_template:
            user_prompt = soap_request.user_prompt_template.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_form_data
            )
        else:
            user_prompt = ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_form_data
            )
        
        selected_model = soap_request.model or OPENAI_MODEL
        
        formatted_prompts = format_prompts_for_chatgpt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=selected_model,
            temperature=0.2,
            max_tokens=12000,
            top_p=0.95
        )
        
        return formatted_prompts
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error formatting prompts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to format prompts: {str(e)}"
        )


@router.get("/soap-prompts/default", tags=["SOAP Generation"])
async def get_default_soap_prompts():
    """
    Get the default orthopedic SOAP note prompts.
    """
    return JSONResponse({
        "system_prompt": ORTHOPEDIC_SOAP_SYSTEM_PROMPT,
        "user_prompt_template": ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE,
        "placeholders": [
            "{transcription}",
            "{patient_context}",
            "{header_section}",
            "{intake_form_data}"
        ],
        "description": "Default orthopedic SOAP note prompts following DWC, AMA, and HIPAA standards",
        "specialty": "orthopedics"
    })


@router.post("/generate-soap", response_model=SOAPResponse, tags=["SOAP Generation"])
async def generate_soap_comprehensive_endpoint(
    soap_request: SOAPRequest,
    background_tasks: BackgroundTasks,
    intake_id: Optional[str] = Query(None, description="MongoDB intake form ID to use for PMH, Medications, and Social/Occupational History"),
    use_latest_intake: Optional[bool] = Query(False, description="Use latest intake form if True")
):
    """
    Generate a comprehensive orthopedic SOAP note from clinical transcription.
    """
    try:
        logger.info("Generating comprehensive orthopedic SOAP note with GPT-4...")
        
        transcription_doc = None
        if soap_request.transcription_id:
            # Check for existing SOAP note only if force is False
            if not soap_request.force:
                existing_soap = await get_soap_note_by_transcription_id(soap_request.transcription_id)
                if existing_soap:
                    logger.info(f"✅ Found existing SOAP note for transcription {soap_request.transcription_id}, returning cached version.")
                    
                    if isinstance(existing_soap.get("created_at"), datetime):
                        existing_soap["created_at"] = existing_soap["created_at"].isoformat()
                    
                    existing_soap["transcription"] = existing_soap.get("transcription") or ""
                    existing_soap["corrected_transcription"] = existing_soap.get("corrected_transcription") or ""
                    existing_soap["subjective"] = existing_soap.get("subjective") or ""
                    existing_soap["objective"] = existing_soap.get("objective") or ""
                    existing_soap["assessment"] = existing_soap.get("assessment") or ""
                    existing_soap["plan"] = existing_soap.get("plan") or ""
                    existing_soap["formatted_soap_note"] = existing_soap.get("formatted_soap_note") or ""
                    
                    soap_response = SOAPResponse(**existing_soap)
                    soap_response.document_id = existing_soap.get("_id")
                    soap_response.status = existing_soap.get("status", "completed")
                    return soap_response
            else:
                logger.info(f"🔄 Force regeneration requested for transcription {soap_request.transcription_id}. Bypassing cache.")

            transcription_doc = await get_transcription_by_id(soap_request.transcription_id)
            if not transcription_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Transcription not found with ID: {soap_request.transcription_id}"
                )
            soap_request.transcription = transcription_doc.get("text", "")
        elif not soap_request.transcription:
            raise HTTPException(
                status_code=400,
                detail="Either transcription_id or transcription text must be provided"
            )
        
        intake_doc = None
        if intake_id:
            intake_doc = await fetch_intake_form_by_id(intake_id)
            if intake_doc:
                logger.info(f"Using intake form with ID: {intake_id}")
        elif use_latest_intake:
            intake_doc = await fetch_latest_intake_form()
            if intake_doc:
                logger.info(f"Using latest intake form with ID: {intake_doc.get('_id')}")
        
        intake_form_data = format_intake_form_data_for_prompt(intake_doc) if intake_doc else None
        
        if intake_form_data:
            logger.info(f"✅ Intake form data formatted and will be included in prompt.")
        else:
            logger.warning("⚠️ No intake form data available - will use 'As per chart'")
        
        pending_soap = await create_pending_soap_note(soap_request.transcription_id, soap_request.userId)
        logger.info(f"⏳ Generated pending SOAP note ID: {pending_soap['_id']}")

        soap_result = await generate_comprehensive_soap_note(soap_request, intake_form_data, intake_doc, model=soap_request.model)
        
        if transcription_doc:
            try:
                modified_content = soap_result.get("formatted_soap_note", "")
                changes_made = False
                
                if transcription_doc.get("needs_rfa") and transcription_doc.get("rfa_name"):
                    rfa_val = transcription_doc.get("rfa_name")
                    if rfa_val not in modified_content:
                        logger.info(f"Injecting missing RFA '{rfa_val}' into generated SOAP note")
                        rfa_header_pattern = r"(\*\*REQUEST FOR AUTHORIZATION.*?\*\*)"
                        match = re.search(rfa_header_pattern, modified_content, re.IGNORECASE | re.DOTALL)
                        
                        if match:
                            start_index = match.start()
                            remaining_content = modified_content[match.end():]
                            next_header_match = re.search(r"\n\s*\*\*[A-Z0-9\s\/–-]+\*\*", remaining_content)
                            
                            end_relative_index = next_header_match.start() if next_header_match else len(remaining_content)
                            end_index = match.end() + end_relative_index
                            
                            existing_body = remaining_content[:end_relative_index]
                            cleaned_body = re.sub(r"^\s*\*\*Requested Service:?(\(s\))?\*\*\s*", "", existing_body, count=1, flags=re.IGNORECASE | re.MULTILINE).strip()
                            
                            new_rfa_section = f"{match.group(1)}\n\n**Requested Service:**\n- {rfa_val}\n{cleaned_body}"
                            modified_content = modified_content[:start_index] + new_rfa_section + modified_content[end_index:]
                        else:
                             modified_content += f"\n\n**REQUEST FOR AUTHORIZATION (RFA)**\n\n**Requested Service:**\n- {rfa_val}"
                        changes_made = True

                if transcription_doc.get("needs_work_status") and transcription_doc.get("work_status"):
                    ws_val = transcription_doc.get("work_status")
                    if ws_val not in modified_content:
                        logger.info(f"Injecting missing Work Status '{ws_val}' into generated SOAP note")
                        
                        ws_header_pattern = r"(\*\*WORK STATUS.*?\*\*)"
                        match = re.search(ws_header_pattern, modified_content, re.IGNORECASE | re.DOTALL)
                        
                        if match:
                            start_index = match.start()
                            remaining_content = modified_content[match.end():]
                            next_header_match = re.search(r"\n\s*\*\*[A-Z0-9\s\/–-]+\*\*", remaining_content)
                            end_relative_index = next_header_match.start() if next_header_match else len(remaining_content)
                            end_index = match.end() + end_relative_index
                            
                            existing_body = remaining_content[:end_relative_index]
                            new_ws_section = f"{match.group(1)}\n\n{ws_val}\n{existing_body}"
                            modified_content = modified_content[:start_index] + new_ws_section + modified_content[end_index:]
                        else:
                            modified_content += f"\n\n**WORK STATUS**\n\n{ws_val}"
                        changes_made = True
                
                if changes_made:
                    soap_result["formatted_soap_note"] = modified_content
                    if "soap_note" in soap_result:
                        soap_result["soap_note"] = modified_content
                        
            except Exception as e:
                logger.error(f"Error injecting metadata into generated SOAP note: {e}")

        document_id = pending_soap["_id"]
        try:
            soap_result["status"] = "completed"
            await update_soap_note(document_id, soap_result)
            logger.info(f"✅ SOAP note completed with ID: {document_id}")
            
            # Verify SOAP is saved
            saved_soap = await get_soap_note_by_id(document_id)
            if not saved_soap:
                await asyncio.sleep(0.5)
                saved_soap = await get_soap_note_by_id(document_id)
            
            if saved_soap:
                trans_id = soap_request.transcription_id
                if not trans_id:
                    trans_id = saved_soap.get("transcription_id")
                
                if document_id:
                    logger.info(f"🔄 Triggering downstream workflows")
                    async def delayed_workflow():
                        await asyncio.sleep(0.5)
                        await run_downstream_workflows(soap_id=document_id, transcription_id=trans_id)
                    
                    background_tasks.add_task(delayed_workflow)
            else:
                 logger.error("Failed to verify soap note saved")
                
        except Exception as db_error:
            logger.error(f"⚠️ Failed to update SOAP note status: {db_error}")

        soap_response = SOAPResponse(**soap_result)
        if document_id:
            soap_response.document_id = document_id
        return soap_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating SOAP note: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error generating SOAP note: {str(e)}"
        )

@router.post("/generate-soap-form", response_model=SOAPResponse, tags=["SOAP Generation"])
async def generate_soap_simple_endpoint(
    background_tasks: BackgroundTasks,
    transcription_id: Optional[str] = Form(None),
    
    transcription: Optional[str] = Form(None),
    patient_name: Optional[str] = Form(None),
    patient_age: Optional[int] = Form(None),
    patient_gender: Optional[str] = Form(None),
    date_of_service: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    reason_for_visit: Optional[str] = Form(None),
    userId: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    user_prompt_template: Optional[str] = Form(None),
    model: Optional[str] = Form(None, description="OpenAI model to use: 'gpt-4o' or 'gpt-5.1'"),
    skip_post_processing: Optional[bool] = Form(False, description="If True, returns raw AI output without post-processing"),
    intake_id: Optional[str] = Form(None),
    use_latest_intake: Optional[bool] = Form(True)
):
    """
    Generate comprehensive orthopedic SOAP note from transcription text.
    Accepts Form Data (matches the /generate-soap endpoint logic but for Form input).
    Note: original route was /generate-soap, conflicting with JSON one? 
    In main.py, JSON one was /api/v1/generate-soap and Form one was /generate-soap (no prefix, or maybe root?).
    The docs showed: "generate_json": "/api/v1/generate-soap", "generate_form": "/generate-soap".
    So I should keep the path "/generate-soap" relative to router, but router usually has prefix.
    """
    try:
        logger.info("Generating comprehensive orthopedic SOAP note (Form)...")
        
        if transcription_id:
            transcription_doc = await get_transcription_by_id(transcription_id)
            if not transcription_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Transcription not found with ID: {transcription_id}"
                )
            transcription = transcription_doc.get("text", "")
        elif not transcription:
            raise HTTPException(
                status_code=400,
                detail="Either transcription_id or transcription text must be provided"
            )
        
        patient_info = None
        if patient_name or patient_age or patient_gender:
            patient_info = PatientInfo(
                name=patient_name,
                age=patient_age,
                gender=patient_gender
            )
        
        if model:
            allowed_models = ['gpt-4o', 'gpt-5.1']
            if model not in allowed_models:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{model}' is not allowed. Only {allowed_models} are permitted."
                )
        
        soap_request = SOAPRequest(
            transcription=transcription,
            transcription_id=transcription_id,
            patient=patient_info,
            date_of_service=date_of_service,
            location=location,
            reason_for_visit=reason_for_visit,
            userId=userId,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            model=model,
            skip_post_processing=skip_post_processing
        )
        
        intake_doc = None
        if intake_id:
            intake_doc = await fetch_intake_form_by_id(intake_id)
        elif use_latest_intake:
            intake_doc = await fetch_latest_intake_form()
        
        intake_form_data = format_intake_form_data_for_prompt(intake_doc) if intake_doc else None
        
        if intake_form_data:
             logger.info(f"✅ Intake form data formatted and will be included in prompt.")
        else:
             logger.warning("⚠️ No intake form data available - will use 'As per chart'")
        
        soap_result = await generate_comprehensive_soap_note(soap_request, intake_form_data, intake_doc, model=model)
        
        document_id = None
        try:
            saved_doc = await save_soap_note_to_db(soap_result)
            document_id = saved_doc.get('_id')
            logger.info(f"✅ SOAP note saved with ID: {document_id}")
            
            # Trigger downstream workflows
            if document_id:
                trans_id = soap_request.transcription_id
                logger.info(f"🔄 Triggering downstream workflows for form-generated SOAP {document_id}")
                background_tasks.add_task(run_downstream_workflows, soap_id=str(document_id), transcription_id=trans_id)
                
        except Exception as db_error:
            logger.error(f"⚠️ Failed to save SOAP note to database/workflow: {db_error}")
        
        soap_response = SOAPResponse(**soap_result)
        if document_id:
            soap_response.document_id = document_id
        return soap_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SOAP generation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate SOAP note: {str(e)}"
        )
