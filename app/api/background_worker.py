"""
Background Worker for SOAP Note Generation
Handles asynchronous SOAP note generation after transcription is saved
"""
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime

from app.api.transcription_storage import get_transcription_by_id
from app.api.soap_storage import (
    save_soap_note_to_db,
    get_soap_note_by_transcription_id
)
from app.schemas import SOAPRequest, PatientInfo

# Import from main to avoid circular dependency issues
# These functions are imported at runtime inside the function

logger = logging.getLogger(__name__)


async def generate_soap_note_in_background(transcription_id: str) -> Optional[str]:
    """
    Background worker to generate SOAP note from a transcription.
    This runs asynchronously without blocking the main request.
    
    Args:
        transcription_id: The MongoDB transcription document ID
        
    Returns:
        The SOAP note ID if successful, None otherwise
    """
    try:
        print(f"🔄 [BACKGROUND WORKER] Starting SOAP generation for transcription_id: {transcription_id}")
        logger.info(f"🔄 Background worker started for transcription_id: {transcription_id}")
        
        # Check if SOAP note already exists for this transcription
        print(f"🔍 [BACKGROUND WORKER] Checking if SOAP already exists for transcription_id: {transcription_id}")
        existing_soap = await get_soap_note_by_transcription_id(transcription_id)
        if existing_soap:
            soap_id = existing_soap.get("_id")
            print(f"✅ [BACKGROUND WORKER] SOAP note already exists! ID: {soap_id}, skipping generation")
            logger.info(f"✅ SOAP note already exists for transcription_id: {transcription_id}, skipping generation")
            return soap_id
        print(f"ℹ️ [BACKGROUND WORKER] No existing SOAP found, proceeding with generation")
        
        # Fetch transcription from database
        print(f"📥 [BACKGROUND WORKER] Fetching transcription from database: {transcription_id}")
        transcription_doc = await get_transcription_by_id(transcription_id)
        if not transcription_doc:
            print(f"❌ [BACKGROUND WORKER] ERROR: Transcription not found: {transcription_id}")
            logger.error(f"❌ Transcription not found: {transcription_id}")
            return None
        
        transcription_text = transcription_doc.get("text", "")
        print(f"📄 [BACKGROUND WORKER] Transcription text length: {len(transcription_text)} characters")
        if not transcription_text or len(transcription_text.strip()) < 10:
            print(f"⚠️ [BACKGROUND WORKER] WARNING: Transcription text too short or empty (length: {len(transcription_text.strip())})")
            logger.warning(f"⚠️ Transcription text too short or empty for {transcription_id}")
            return None
        
        print(f"📝 [BACKGROUND WORKER] Starting SOAP note generation from transcription ({len(transcription_text)} chars)")
        logger.info(f"📝 Generating SOAP note from transcription ({len(transcription_text)} chars)")
        
        # Fetch intake form data if available
        # Import here to avoid circular dependencies
        print(f"📋 [BACKGROUND WORKER] Fetching intake form data...")
        from app.main import fetch_latest_intake_form, format_intake_form_data_for_prompt
        intake_doc = await fetch_latest_intake_form()
        intake_form_data = None
        if intake_doc:
            intake_form_data = format_intake_form_data_for_prompt(intake_doc)
            print(f"✅ [BACKGROUND WORKER] Intake form data found and formatted")
            logger.info(f"✅ Using intake form data for SOAP generation")
        else:
            print(f"ℹ️ [BACKGROUND WORKER] No intake form data available")
        
        # Extract patient info from transcription document
        patient_info = None
        username = transcription_doc.get("username")
        if username:
            patient_info = PatientInfo(
                name=username,
                age=None,
                gender=None
            )
        
        # Create SOAP request
        soap_request = SOAPRequest(
            transcription_id=transcription_id,
            transcription=transcription_text,
            patient=patient_info,
            date_of_service=datetime.utcnow().strftime("%Y-%m-%d"),
            location=None,
            reason_for_visit=None,
            system_prompt=None,
            user_prompt_template=None,
            model=None
        )
        
        # Generate SOAP note (this may take some time)
        # Import here to avoid circular dependencies
        from app.main import generate_comprehensive_soap_note
        print(f"🤖 [BACKGROUND WORKER] Calling GPT API to generate SOAP note (this may take 30-60 seconds)...")
        logger.info(f"🤖 Calling GPT API to generate SOAP note...")
        soap_result = await generate_comprehensive_soap_note(
            soap_request,
            intake_form_data=intake_form_data,
            intake_doc=intake_doc,
            model=None  # Use default model
        )
        
        if not soap_result:
            print(f"❌ [BACKGROUND WORKER] ERROR: SOAP generation returned empty result")
            logger.error(f"❌ SOAP generation returned empty result for {transcription_id}")
            return None
        
        print(f"✅ [BACKGROUND WORKER] SOAP note generated successfully!")
        print(f"   - Subjective length: {len(soap_result.get('subjective', ''))} chars")
        print(f"   - Objective length: {len(soap_result.get('objective', ''))} chars")
        print(f"   - Assessment length: {len(soap_result.get('assessment', ''))} chars")
        print(f"   - Plan length: {len(soap_result.get('plan', ''))} chars")
        
        # Prepare SOAP note data for saving
        soap_data = {
            "transcription": transcription_text,
            "corrected_transcription": soap_result.get("corrected_transcription", transcription_text),
            "transcription_id": transcription_id,
            "subjective": soap_result.get("subjective", ""),
            "objective": soap_result.get("objective", ""),
            "assessment": soap_result.get("assessment", ""),
            "plan": soap_result.get("plan", ""),
            "formatted_soap_note": soap_result.get("formatted_soap_note", ""),
            "patient_info": {
                "name": username or "",
                "age": None,
                "gender": None
            } if username else None,
            "userId": transcription_doc.get("user_id"),
            "date_of_service": datetime.utcnow().strftime("%Y-%m-%d"),
            "location": None,
            "reason_for_visit": None,
            "custom_prompts": None,
            "format": "markdown"
        }
        
        # Save SOAP note to database
        print(f"💾 [BACKGROUND WORKER] Saving SOAP note to database...")
        saved_soap = await save_soap_note_to_db(soap_data)
        soap_note_id = saved_soap.get("_id")
        
        print(f"✅ [BACKGROUND WORKER] SUCCESS! SOAP note saved with ID: {soap_note_id}")
        print(f"🎉 [BACKGROUND WORKER] Background worker completed for transcription_id: {transcription_id}")
        logger.info(f"✅ Background worker completed: SOAP note saved with ID: {soap_note_id}")
        
        return soap_note_id
        
    except Exception as e:
        print(f"❌ [BACKGROUND WORKER] ERROR: Exception occurred for transcription_id {transcription_id}")
        print(f"❌ [BACKGROUND WORKER] Error message: {str(e)}")
        print(f"❌ [BACKGROUND WORKER] Error type: {type(e).__name__}")
        import traceback
        print(f"❌ [BACKGROUND WORKER] Traceback:\n{traceback.format_exc()}")
        logger.error(f"❌ Background worker error for transcription_id {transcription_id}: {str(e)}", exc_info=True)
        return None


async def start_background_worker(transcription_id: str):
    """
    Start a background task to generate SOAP note.
    This function schedules the async task without blocking.
    
    Args:
        transcription_id: The MongoDB transcription document ID
    """
    try:
        # Schedule the background task
        # Use create_task to run in background without awaiting
        task = asyncio.create_task(generate_soap_note_in_background(transcription_id))
        logger.info(f"🚀 Background worker task scheduled for transcription_id: {transcription_id}")
        # Don't await - let it run in background
        return task
    except Exception as e:
        logger.error(f"❌ Failed to start background worker for {transcription_id}: {str(e)}", exc_info=True)
        return None
