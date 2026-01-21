
import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.services.openai import create_openai_client
from app.prompts import ORTHOPEDIC_SOAP_SYSTEM_PROMPT, ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
from app.services.text_processing import fix_terms
from app.services.intake import extract_intake_form_values, inject_intake_data_into_soap
from app.services.cpt import validate_and_correct_cpt_codes, validate_all_cpt_codes_in_soap, aggressive_validate_rfa_supportive_cpts
from app.schemas import SOAPRequest

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.1')


def format_prompts_for_chatgpt(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 12000,
    top_p: float = 0.95
) -> dict:
    """
    Format the exact prompts used for SOAP generation in a way that can be copied to ChatGPT.
    Returns a dictionary with formatted prompts and instructions.
    """
    formatted_prompts = {
        "model": model,
        "parameters": {
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "top_p": top_p
        },
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "chatgpt_instructions": {
            "note": "To use these prompts in ChatGPT, you need to provide the system prompt first, then the user prompt.",
            "step_1": "Copy the system_prompt and paste it as a system message or in the first message",
            "step_2": "Copy the user_prompt and paste it as the user message",
            "note_2": "Note: ChatGPT may not support exact temperature/top_p parameters, which can cause output differences",
            "note_3": "Also note: The API applies post-processing (CPT validation, intake data injection, markdown cleanup) which ChatGPT won't do automatically"
        },
        "full_chat_format": f"""SYSTEM MESSAGE:
{system_prompt}

---

USER MESSAGE:
{user_prompt}

---

PARAMETERS USED (for reference):
- Model: {model}
- Temperature: {temperature}
- Max Tokens: {max_tokens}
- Top P: {top_p}"""
    }
    
    return formatted_prompts


def extract_soap_sections_from_formatted_note(formatted_soap_note: str) -> dict:
    """
    Extract S/O/A/P sections from the generated consolidated note.
    """
    import re

    sections = {"subjective": "", "objective": "", "assessment": "", "plan": ""}
    if not formatted_soap_note:
        return sections

    note = formatted_soap_note.replace("\r\n", "\n").replace("\r", "\n")

    def _between(start_regex: str, end_regexes: list[str]) -> str:
        start_match = re.search(start_regex, note, flags=re.IGNORECASE | re.MULTILINE)
        if not start_match:
            return ""
        start_idx = start_match.end()

        end_idx = len(note)
        tail = note[start_idx:]
        for end_regex in end_regexes:
            m = re.search(end_regex, tail, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                end_idx = min(end_idx, start_idx + m.start())

        return note[start_idx:end_idx].strip()

    # 1) Preferred: Markdown-style headings (older outputs)
    subj = _between(
        r"^##\s*S\s*[–-]\s*SUBJECTIVE\s*$",
        [r"^##\s*O\s*[–-]\s*OBJECTIVE\s*$", r"^---\s*$"],
    )
    obj = _between(
        r"^##\s*O\s*[–-]\s*OBJECTIVE\s*$",
        [r"^##\s*A\s*[–-]\s*ASSESSMENT\s*$", r"^---\s*$"],
    )
    assess = _between(
        r"^##\s*A\s*[–-]\s*ASSESSMENT\s*$",
        [r"^##\s*P\s*[–-]\s*PLAN\s*$", r"^---\s*$"],
    )
    plan = _between(
        r"^##\s*P\s*[–-]\s*PLAN\s*$",
        [r"^##\s*(?:GENERATED\s*BY|Author|Signature)|$", r"^---\s*$"],
    )

    if not any([subj, obj, assess, plan]):
        # 2) Fallback: Plain CAPS headings (common in prompts.py templates)
        subj = _between(
            r"^SUBJECTIVE\s*$",
            [r"^OBJECTIVE/Physical Exam\s*$", r"^OBJECTIVE\s*$", r"^ASSESSMENT\s*$"],
        )
        obj = _between(
            r"^OBJECTIVE(?:/Physical Exam)?\s*$",
            [r"^ASSESSMENT\s*$", r"^PLAN\s*$"],
        )
        assess = _between(
            r"^ASSESSMENT\s*$",
            [r"^PLAN\s*$", r"^Detailed Plan\s*$"],
        )
        plan = _between(
            r"^PLAN\s*$",
            [r"^---\s*$", r"^\*\*Disclaimer\*\*"],
        )

    sections["subjective"] = subj
    sections["objective"] = obj
    sections["assessment"] = assess
    sections["plan"] = plan
    return sections


def generate_soap_note_from_transcription(text: str) -> str:
    """
    LEGACY: Generate a structured SOAP note from raw clinical transcription using GPT-4.
    """
    try:
        # Initialize OpenAI client
        client = create_openai_client()
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a medical documentation assistant. "
                        "Your task is to transform a raw clinical transcription into a structured SOAP note."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "I will give you a transcription of a clinical encounter.\n\n"
                        "Your task is to:\n"
                        "1. Correct grammatical or typographical errors while preserving clinical meaning.\n"
                        "2. Organize the information into a structured consult note with the following sections:\n"
                        "   - S: Subjective (chief complaint, history, patient-reported details)\n"
                        "   - O: Objective (physical exam findings, imaging/lab mentions, observations)\n"
                        "   - A: Assessment (diagnosis, clinical impression)\n"
                        "   - P: Plan (treatment plan, follow-up, medications, referrals)\n"
                        "3. Keep the tone formal and clinical.\n"
                        "4. Include all special tests, exam findings, and measurements mentioned.\n"
                        "5. Use standard orthopedic terminology when applicable.\n"
                        "6. Do not invent or omit any detail that is not in the transcription.\n\n"
                        f"Here is the transcription:\n\n{text}"
                    )
                }
            ],
            temperature=0.2,
            max_completion_tokens=5000
        )

        structured_note = response.choices[0].message.content.strip()
        return structured_note

    except Exception as e:
        logger.error(f"❌ GPT SOAP generation failed: {e}")
        logger.error(f"Full error details: {type(e).__name__}: {str(e)}")
        return text


async def generate_comprehensive_soap_note(soap_request: SOAPRequest, intake_form_data: Optional[str] = None, intake_doc: Optional[dict] = None, model: Optional[str] = None) -> dict:
    """
    Generate a comprehensive orthopedic SOAP note from transcription with optional structured data.
    """
    try:
        # Apply medical terminology corrections to transcription
        corrected_transcription = fix_terms(soap_request.transcription)
        
        # Build patient context section
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
        
        # Use custom prompts if provided, otherwise use default prompts
        system_prompt = soap_request.system_prompt if soap_request.system_prompt else ORTHOPEDIC_SOAP_SYSTEM_PROMPT
        
        # Use provided intake form data or empty string
        intake_data_section = intake_form_data if intake_form_data else ""
        
        # Build the user prompt - use custom template if provided
        if soap_request.user_prompt_template:
            # Custom prompt template - replace placeholders
            user_prompt = soap_request.user_prompt_template.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_data_section
            )
            logger.info("Using custom user prompt template provided by frontend")
        else:
            # Use default template
            user_prompt = ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_data_section
            )
            logger.info("Using default orthopedic SOAP prompt template")
        
        # Log if custom system prompt is used
        if soap_request.system_prompt:
            logger.info("Using custom system prompt provided by frontend")
        
        # Use model from request if provided, otherwise use default
        selected_model = model or soap_request.model or OPENAI_MODEL
        logger.info(f"Using OpenAI model: {selected_model}")
        
        # Log formatted prompts for ChatGPT comparison (detailed logging)
        format_prompts_for_chatgpt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=selected_model,
            temperature=0.2,
            max_tokens=12000,
            top_p=0.95
        )
        # (Logging statements omitted for brevity, but exist in logic)
        
        # Call OpenAI GPT-4 with explicit configuration
        client = create_openai_client()
        
        # Use asyncio.to_thread for the blocking OpenAI call
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=selected_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            temperature=0.2,  # Slightly higher to encourage comprehensive code generation
            max_completion_tokens=12000,  # Increased to allow 60+ CPT codes with full descriptions
            top_p=0.95  # Slightly lower for more deterministic output
        )
        
        # Store raw AI output (before any post-processing) - for comparison with ChatGPT
        raw_soap_note = response.choices[0].message.content.strip()
        formatted_soap_note = raw_soap_note
        
        # Remove any --- separators if the AI still generates them
        formatted_soap_note = re.sub(r'^\s*---\s*$', '', formatted_soap_note, flags=re.MULTILINE)
        # Clean up multiple newlines that might be left behind
        formatted_soap_note = re.sub(r'\n{3,}', '\n\n', formatted_soap_note)
        
        # --- FIX: "Employer / Carrier: Not on File" Hallucination ---
        if "Employer / Carrier: Not on File" in formatted_soap_note or "Employer / Carrier: [Not on File]" in formatted_soap_note:
             emp_match = re.search(r'(Employer\s*/\s*Carrier|Employer|Carrier|Insurance)\s*:\s*(.+?)(?=\n|$)', corrected_transcription, re.IGNORECASE)
             
             if emp_match:
                 found_value = emp_match.group(2).strip()
                 if found_value and found_value.lower() not in ["not on file", "none", "n/a"]:
                     formatted_soap_note = re.sub(r'Employer / Carrier:.*?(?=\n)', f'Employer / Carrier: {found_value}', formatted_soap_note)
                     logger.info(f"✅ Fixed 'Not on File' with transcription value: {found_value}")
                 else:
                     formatted_soap_note = re.sub(r'Employer / Carrier:.*?\n', '', formatted_soap_note)
             else:
                 formatted_soap_note = re.sub(r'Employer / Carrier:.*?\n', '', formatted_soap_note)
        
        skip_post_processing = getattr(soap_request, "skip_post_processing", False) or False
        
        if skip_post_processing:
            logger.info("⏭️  Skipping post-processing - returning raw AI output")
        else:
            if intake_doc:
                intake_values = extract_intake_form_values(intake_doc)
                if intake_values:
                    logger.info(f"🔧 Post-processing SOAP note to inject intake form data: {intake_values}")
                    formatted_soap_note = inject_intake_data_into_soap(formatted_soap_note, intake_values)
            
            formatted_soap_note = await validate_and_correct_cpt_codes(formatted_soap_note, corrected_transcription, client)
            formatted_soap_note = validate_all_cpt_codes_in_soap(formatted_soap_note)
            formatted_soap_note = aggressive_validate_rfa_supportive_cpts(formatted_soap_note)
        
        sections = extract_soap_sections_from_formatted_note(formatted_soap_note)
        
        return {
            "transcription": soap_request.transcription,
            "corrected_transcription": corrected_transcription,
            "transcription_id": soap_request.transcription_id,
            "subjective": sections["subjective"],
            "objective": sections["objective"],
            "assessment": sections["assessment"],
            "plan": sections["plan"],
            "formatted_soap_note": formatted_soap_note,
            "raw_soap_note": raw_soap_note,
            "created_at": datetime.utcnow().isoformat(),
            "patient_info": {
                "name": soap_request.patient.name if soap_request.patient else None,
                "age": soap_request.patient.age if soap_request.patient else None,
                "gender": soap_request.patient.gender if soap_request.patient else None,
            } if soap_request.patient else None,
            "userId": soap_request.userId,
            "date_of_service": soap_request.date_of_service,
            "location": soap_request.location,
            "reason_for_visit": soap_request.reason_for_visit,
            "system_prompt": soap_request.system_prompt,
            "user_prompt_template": soap_request.user_prompt_template,
            "format": "markdown"
        }

    except Exception as e:
        logger.error(f"Error generating comprehensive SOAP note: {e}")
        # Return partial error response but with as much info as possible
        return {
            "error": str(e),
            "transcription": soap_request.transcription,
            "created_at": datetime.utcnow().isoformat()
        }
