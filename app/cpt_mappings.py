"""
CPT/HCPCS Code Mappings for Orthopedic Procedures
This ensures consistent and accurate CPT code generation in SOAP notes
"""
import os

# Static CPT mappings removed - now using AI-only extraction from transcription
# All CPT codes are extracted dynamically from transcription using AI


def get_e_m_code(visit_type: str, complexity: str) -> tuple:
    """
    Get E/M code based on visit type and complexity.
    Static mappings removed - E/M codes should be extracted from transcription using AI.
    Returns (code, description) or (None, None).
    """
    # Static mappings removed - E/M codes should be extracted from transcription
    return (None, None)


def is_valid_cpt_code(code: str) -> bool:
    """
    Validate if a code is a valid CPT or HCPCS code.
    
    CPT codes: 5 digits (e.g., 29881, 20610)
    HCPCS codes: Letter(s) followed by digits (e.g., L1833, E0114, A4566, C1713)
    
    Returns:
        True if valid, False otherwise
    """
    if not code:
        return False
    
    code_str = str(code).strip()
    
    # Remove any dashes or spaces
    code_str = code_str.replace("-", "").replace(" ", "").replace(".", "")
    
    # Empty after cleaning
    if not code_str:
        return False
    
    # CPT codes: Exactly 5 digits
    if len(code_str) == 5 and code_str.isdigit():
        return True
    
    # HCPCS codes: Letter(s) followed by digits, total length 5
    # Examples: L1833, E0114, A4566, C1713, Q4001
    if len(code_str) == 5:
        # Check if starts with letter(s) and rest are digits
        if code_str[0].isalpha():
            # Single letter + 4 digits (most common: L1833, E0114, A4566)
            if code_str[1:].isdigit():
                return True
            # Two letters + 3 digits (e.g., Q4001)
            if len(code_str) >= 2 and code_str[0:2].isalpha() and code_str[2:].isdigit():
                return True
    
    # Some HCPCS codes might be 4 characters (less common but valid)
    if len(code_str) == 4:
        if code_str[0].isalpha() and code_str[1:].isdigit():
            return True
    
    return False


def generate_cpt_with_ai(procedure_description: str, openai_client=None) -> tuple:
    """
    Use AI to EXTRACT CPT codes ONLY from what is mentioned in the transcription.
    Returns (primary_cpt, supportive_cpts_list) or (None, []) if unable to extract.
    """
    if not procedure_description or not procedure_description.strip():
        return (None, [])
    
    if not openai_client:
        try:
            # Try to import from main or pr1_generator
            try:
                from app.main import create_openai_client
            except ImportError:
                from app.api.pr1_generator import create_openai_client
            openai_client = create_openai_client()
        except Exception:
            return (None, [])
    
    try:
        system_prompt = """You are a medical coding expert specializing in CPT/HCPCS codes for orthopedic procedures, surgeries, injections, imaging, therapy, and DME.

Your task is to EXTRACT CPT/HCPCS codes that are ACTUALLY MENTIONED in the transcription text provided AND are RELATED TO THE ILLNESS/DIAGNOSIS mentioned.

CRITICAL RULES - EXTRACT ONLY WHAT IS MENTIONED AND RELATED TO ILLNESS:
1. FIRST: Identify the PRIMARY ILLNESS/DIAGNOSIS mentioned in the transcription (e.g., "knee pain", "ACL tear", "shoulder injury", "back pain", "fracture", etc.)
2. Extract the PRIMARY CPT/HCPCS code ONLY if:
   - It is explicitly mentioned in the transcription OR
   - A specific procedure is mentioned AND it is directly related to the identified illness/diagnosis
3. Extract SUPPORTIVE CPT codes ONLY if:
   - They are explicitly mentioned in the transcription AND
   - They are directly related to the illness/diagnosis mentioned (same body part, same condition, same treatment)
4. DO NOT generate or add codes that are NOT mentioned in the transcription
5. DO NOT add codes "that might be needed" or "typically required" - ONLY extract what is actually stated
6. DO NOT include codes for different body parts than the illness/diagnosis (e.g., if illness is "knee pain", do NOT include shoulder codes)
7. DO NOT include codes for different conditions than the illness/diagnosis (e.g., if illness is "ACL tear", do NOT include rotator cuff codes)
8. If a code is mentioned (e.g., "29881", "29882", "20924", "L1833", "E0114"), extract it ONLY if it relates to the illness/diagnosis
9. If a procedure is mentioned but no code is given, you may infer the primary code for that specific procedure ONLY if it relates to the illness/diagnosis
10. Return ONLY valid CPT/HCPCS codes (5-digit numeric codes or HCPCS codes starting with letters) that are RELATED TO THE ILLNESS
11. DO NOT generate 70+ codes - only extract what is actually present in the transcription and related to the illness

Return a JSON object with:
{
  "primary_cpt": "CPT_CODE" or null,
  "supportive_cpts": ["CODE1", "CODE2"] (only codes mentioned in transcription),
  "description": "Brief description"
}

If no codes are mentioned, return:
{
  "primary_cpt": null,
  "supportive_cpts": [],
  "description": null
}"""

        user_prompt = f"""Extract CPT/HCPCS codes that are ACTUALLY MENTIONED in this transcription AND are RELATED TO THE ILLNESS/DIAGNOSIS:

{procedure_description}

**CRITICAL - EXTRACT ONLY WHAT IS MENTIONED AND RELATED TO ILLNESS:**
- FIRST: Identify the PRIMARY ILLNESS/DIAGNOSIS from the transcription (e.g., "knee pain", "ACL tear", "shoulder injury", "back pain", "fracture", etc.)
- Extract ONLY codes that are:
  1. Explicitly stated in the transcription AND
  2. Directly related to the identified illness/diagnosis (same body part, same condition, same treatment)
- DO NOT add codes that are not mentioned
- DO NOT add codes for different body parts than the illness (e.g., if illness is "knee pain", do NOT include shoulder codes)
- DO NOT add codes for different conditions than the illness (e.g., if illness is "ACL tear", do NOT include rotator cuff codes)
- DO NOT generate 70+ codes - only extract what is actually present and related to the illness
- If a procedure is mentioned without a code, you may infer the primary code ONLY if it relates to the illness/diagnosis
- DO NOT add supportive codes unless they are explicitly mentioned AND related to the illness
- If transcription mentions "29881" for a knee procedure, extract "29881" ONLY if it relates to the illness - do NOT add 50+ other codes that are not mentioned or not related

**ILLNESS RELEVANCE CHECK:**
Before including any code, verify:
- Is this code for the same body part as the illness? (e.g., knee illness → knee codes only)
- Is this code for the same condition as the illness? (e.g., ACL tear → ACL-related codes only)
- Is this code for treating the illness mentioned? (e.g., knee pain → knee treatment codes only)
- If ANY answer is NO, DO NOT include that code

Return the JSON object with only the codes that are actually mentioned AND related to the illness/diagnosis."""

        response = openai_client.chat.completions.create(
            model='gpt-5.1',  # Latest GPT-5.1 model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=5000  # Increased to allow 70+ codes with descriptions in response
        )
        
        result = response.choices[0].message.content
        import json
        import logging
        logger = logging.getLogger(__name__)
        data = json.loads(result)
        
        primary = data.get("primary_cpt")
        supportive = data.get("supportive_cpts", [])
        
        # Validate and filter codes
        validated_primary = None
        if primary:
            primary_str = str(primary).strip()
            if is_valid_cpt_code(primary_str):
                validated_primary = primary_str
            else:
                logger.warning(f"Invalid primary CPT code filtered out: {primary_str}")
        
        # Validate supportive codes
        validated_supportive = []
        if isinstance(supportive, list):
            for code in supportive:
                if code:
                    code_str = str(code).strip()
                    if is_valid_cpt_code(code_str):
                        # Avoid duplicates
                        if code_str not in validated_supportive:
                            validated_supportive.append(code_str)
                    else:
                        logger.warning(f"Invalid supportive CPT code filtered out: {code_str}")
        
        if validated_primary:
            logger.info(f"Generated {len(validated_supportive)} valid supportive CPT codes")
            return (validated_primary, validated_supportive)
        
        return (None, [])
        
    except Exception as e:
        # Log error but don't fail
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error generating CPT code with AI: {e}")
        return (None, [])


def validate_cpt_codes_relevance(cpt_codes: list, transcription: str, openai_client=None) -> list:
    """
    Validate that CPT codes are actually relevant to the transcription.
    Filters out codes that are not related to procedures/services mentioned in transcription.
    
    Args:
        cpt_codes: List of CPT codes to validate
        transcription: The transcription text to check relevance against
        openai_client: Optional OpenAI client
    
    Returns:
        List of CPT codes that are relevant to the transcription
    """
    if not cpt_codes or not transcription:
        return []
    
    if not openai_client:
        try:
            try:
                from app.main import create_openai_client
            except ImportError:
                from app.api.pr1_generator import create_openai_client
            openai_client = create_openai_client()
        except Exception:
            return cpt_codes  # Return as-is if can't validate
    
    try:
        import json
        import logging
        logger = logging.getLogger(__name__)
        
        # Limit transcription length
        transcription_snippet = str(transcription)[:3000] if len(str(transcription)) > 3000 else str(transcription)
        
        system_prompt = """You are a medical coding validation expert. Your task is to validate if CPT/HCPCS codes are actually relevant to the ILLNESS/DIAGNOSIS and procedures mentioned in a medical transcription.

CRITICAL VALIDATION RULES - BE VERY STRICT - ILLNESS RELEVANCE REQUIRED:
1. FIRST: Identify the PRIMARY ILLNESS/DIAGNOSIS from the transcription (e.g., "knee pain", "ACL tear", "shoulder injury", "back pain", "fracture", etc.)

2. A code is RELEVANT ONLY if ALL of the following are true:
   - The procedure/service it represents is explicitly mentioned in the transcription
   - The code matches the EXACT body part of the illness/diagnosis (e.g., if illness is "right knee pain", only right knee codes are relevant, NOT left knee codes, NOT other body parts)
   - The code matches the EXACT condition/illness mentioned (e.g., if illness is "ACL tear", ACL-related codes are relevant, NOT rotator cuff codes unless rotator cuff is also the illness)
   - The code is for treating the identified illness/diagnosis (e.g., if illness is "knee pain", knee treatment codes are relevant, NOT shoulder treatment codes)
   - The code is for DME/supplies that are mentioned AND match the illness body part (e.g., if illness is "knee pain" and "knee brace" is mentioned, knee brace codes are relevant, NOT ankle brace codes)
   - The code is for imaging that is mentioned AND matches the illness body part and imaging type (e.g., if illness is "knee pain" and "knee X-ray" is mentioned, knee X-ray codes are relevant, NOT shoulder MRI codes)

3. A code is NOT RELEVANT if ANY of the following are true:
   - The procedure/service it represents is NOT mentioned in the transcription
   - The code is for a different body part than the illness/diagnosis (e.g., if illness is "right knee pain", left knee codes are NOT relevant, shoulder codes are NOT relevant)
   - The code is for a different condition than the illness/diagnosis (e.g., if illness is "ACL tear", rotator cuff codes are NOT relevant)
   - The code is for treating a different condition than the illness (e.g., if illness is "knee pain", shoulder treatment codes are NOT relevant)
   - The code is for a procedure that might be "typically needed" but is NOT mentioned or NOT related to the illness
   - The code is for a procedure that is not related to the illness/diagnosis described

4. Be VERY STRICT - only mark codes as relevant if they:
   - EXACTLY match what is mentioned in the transcription (procedure type, body part, and service type)
   - Are DIRECTLY related to the identified illness/diagnosis
   - Are for treating the same condition/body part as the illness

Return a JSON object with:
{
  "relevant_codes": ["CODE1", "CODE2", ...] (only codes that are actually relevant),
  "irrelevant_codes": ["CODE3", "CODE4", ...] (codes that are not relevant)
}"""

        user_prompt = f"""Validate if these CPT/HCPCS codes are relevant to the ILLNESS/DIAGNOSIS and procedures mentioned in this transcription:

CPT Codes to validate:
{', '.join(cpt_codes[:50])}  # Limit to first 50 codes

Transcription:
{transcription_snippet}

**CRITICAL - BE VERY STRICT - CHECK EACH CODE FOR ILLNESS RELEVANCE:**
FIRST: Identify the PRIMARY ILLNESS/DIAGNOSIS from the transcription (e.g., "knee pain", "ACL tear", "shoulder injury", "back pain", "fracture", etc.)

For EACH code, verify:
1. Is this code for a procedure/service that is EXPLICITLY mentioned in the transcription?
2. Does this code match the EXACT body part of the illness/diagnosis? (e.g., if illness is "right knee pain", do NOT mark left knee codes or shoulder codes as relevant)
3. Does this code match the EXACT condition/illness mentioned? (e.g., if illness is "ACL tear", do NOT mark rotator cuff codes as relevant)
4. Is this code for treating the identified illness/diagnosis? (e.g., if illness is "knee pain", do NOT mark shoulder treatment codes as relevant)
5. If ANY answer is NO, mark the code as IRRELEVANT

- Only mark codes as relevant if they:
  * EXACTLY match what is mentioned (procedure type, body part, service type)
  * Are DIRECTLY related to the identified illness/diagnosis
  * Are for treating the same condition/body part as the illness
- Filter out codes for procedures that are NOT mentioned
- Filter out codes for different body parts than the illness
- Filter out codes for different conditions than the illness
- Filter out codes that are not related to treating the illness
- Return ONLY codes that are actually relevant to the illness/diagnosis in this specific transcription

Return the JSON object with relevant and irrelevant codes."""

        response = openai_client.chat.completions.create(
            model='gpt-4o',  # Use gpt-4o for validation
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=2000
        )
        
        result = response.choices[0].message.content
        data = json.loads(result)
        
        relevant_codes = data.get("relevant_codes", [])
        
        # Validate codes are actually valid CPT codes
        validated_relevant = []
        for code in relevant_codes:
            code_str = str(code).strip()
            if is_valid_cpt_code(code_str) and code_str in cpt_codes:
                validated_relevant.append(code_str)
        
        filtered_count = len(cpt_codes) - len(validated_relevant)
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} irrelevant CPT codes. Kept {len(validated_relevant)} relevant codes.")
        
        return validated_relevant
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error validating CPT codes relevance: {e}")
        return cpt_codes  # Return as-is if validation fails


def get_cpt_for_procedure_enhanced(procedure_text: str, openai_client=None) -> tuple:
    """
    Extract CPT codes using AI only (static mappings removed).
    This makes the system fully dynamic - extracts codes from transcription.
    Returns (primary_cpt, supportive_cpts_list)
    """
    # Use AI to extract codes from transcription (no static mappings)
    return generate_cpt_with_ai(procedure_text, openai_client)
