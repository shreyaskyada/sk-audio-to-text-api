
import re
import asyncio
import logging
from app.cpt_mappings import is_valid_cpt_code, generate_cpt_with_ai

logger = logging.getLogger(__name__)

def aggressive_validate_rfa_supportive_cpts(soap_note: str) -> str:
    """
    Aggressively validate RFA Supportive CPTs section to ensure 100% valid codes.
    This function specifically targets the RFA Supportive CPTs section and validates every code.
    """
    if not soap_note:
        return soap_note
    
    result = soap_note
    invalid_codes_removed = []
    valid_codes_kept = []
    
    # Find RFA Supportive CPTs section
    rfa_supportive_pattern = r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\nJustification:|$)'
    match = re.search(rfa_supportive_pattern, result, re.DOTALL | re.IGNORECASE)
    
    if not match:
        return result  # No RFA section found
    
    supportive_section = match.group(2)
    original_section = supportive_section
    
    # Find all potential CPT codes in the section (comprehensive pattern)
    # Match codes in formats: "CODE — Description", "• CODE — Description", "CODE, CODE", standalone
    code_pattern = r'\b([A-Z][A-Z0-9]{3,4}|[0-9]{5})\b'
    
    # Extract all codes from the section
    all_codes = re.findall(code_pattern, supportive_section, re.IGNORECASE)
    
    # Validate each code and track invalid ones
    invalid_codes_set = set()
    for code in all_codes:
        code_upper = code.upper()
        if not is_valid_cpt_code(code_upper):
            invalid_codes_set.add(code_upper)
            invalid_codes_removed.append(code_upper)
            logger.warning(f"🔴 Removed invalid CPT code from RFA Supportive CPTs: '{code_upper}'")
        else:
            valid_codes_kept.append(code_upper)
    
    # Remove lines containing invalid codes
    lines = supportive_section.split('\n')
    validated_lines = []
    
    for line in lines:
        # Check if line contains any invalid codes
        line_has_invalid = False
        line_codes = re.findall(code_pattern, line, re.IGNORECASE)
        
        for code in line_codes:
            if code.upper() in invalid_codes_set:
                line_has_invalid = True
                break
        
        if line_has_invalid:
            # Try to clean the line - remove invalid codes but keep the line structure
            cleaned_line = line
            for invalid_code in invalid_codes_set:
                # Remove invalid code from line (case insensitive)
                cleaned_line = re.sub(rf'\b{re.escape(invalid_code)}\b', '', cleaned_line, flags=re.IGNORECASE)
            # Clean up extra spaces
            cleaned_line = re.sub(r'\s+', ' ', cleaned_line).strip()
            # Only keep line if it still has content (might have valid codes or description)
            if cleaned_line and (cleaned_line.startswith('•') or any(c.upper() not in invalid_codes_set for c in re.findall(code_pattern, cleaned_line, re.IGNORECASE))):
                validated_lines.append(cleaned_line)
            # Otherwise, skip the line entirely
        else:
            # Line is valid, keep it
            validated_lines.append(line)
    
    # Rebuild the section
    new_supportive_section = '\n'.join(validated_lines)
    
    if new_supportive_section != original_section:
        result = result[:match.start(2)] + new_supportive_section + result[match.end(2):]
        logger.info(f"✅ Aggressive RFA Validation: Kept {len(valid_codes_kept)} valid codes, removed {len(invalid_codes_removed)} invalid codes")
        if invalid_codes_removed:
            logger.warning(f"   Invalid codes removed: {invalid_codes_removed[:20]}")
    
    return result


def validate_all_cpt_codes_in_soap(soap_note: str) -> str:
    """
    Comprehensive validation of all CPT codes in SOAP note.
    Removes all invalid CPT codes and keeps only valid ones.
    Focuses on CPT code sections: Primary Procedure, Supportive CPTs, RFA sections.
    
    NOTE: This function only removes codes that are INVALID (not properly formatted).
    It does NOT remove valid codes. If codes are being removed, they are likely invalid format.
    For maximum code inclusion, ensure the AI generates properly formatted CPT/HCPCS codes.
    """
    if not soap_note:
        return soap_note
    
    result = soap_note
    invalid_codes_removed = []
    total_codes_checked = 0
    valid_codes_kept = 0
    
    # Target specific CPT code sections
    cpt_sections = [
        (r'(Primary Procedure:\s*)(.*?)(?=\n|$)', 'Primary Procedure'),
        (r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\n[A-Z]|$)', 'Supportive CPTs'),
        (r'(Primary CPT:\s*)(.*?)(?=\n|$)', 'RFA Primary CPT'),
        (r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\nJustification:|$)', 'RFA Supportive CPTs'),
        (r'(E/M Code:\s*)(.*?)(?=\n|$)', 'E/M Code'),
    ]
    
    for pattern, section_name in cpt_sections:
        matches = list(re.finditer(pattern, result, re.MULTILINE | re.DOTALL | re.IGNORECASE))
        for match in reversed(matches):  # Reverse to maintain positions
            section_content = match.group(2)
            original_content = section_content
            
            # Find all potential CPT codes in this section
            # STRICT Pattern: Only match valid CPT/HCPCS formats
            # CPT: Exactly 5 digits | HCPCS: Letter(s) + digits, total 4-5 chars
            code_pattern = r'\b((?:[A-Z][A-Z0-9]{3,4})|(?:[0-9]{5}))\b'
            
            def validate_and_replace_code(code_match):
                nonlocal total_codes_checked, valid_codes_kept, invalid_codes_removed
                code = code_match.group(1).upper()
                total_codes_checked += 1
                
                if is_valid_cpt_code(code):
                    valid_codes_kept += 1
                    return code_match.group(0)  # Keep valid code
                else:
                    invalid_codes_removed.append(code)
                    logger.warning(f"Removed invalid CPT code '{code}' from {section_name} section")
                    return ""  # Remove invalid code
            
            # Validate all codes in this section
            validated_content = re.sub(code_pattern, validate_and_replace_code, section_content, flags=re.IGNORECASE)
            
            # Clean up: remove empty lines, extra spaces
            validated_content = re.sub(r'\n\s*\n+', '\n', validated_content)
            validated_content = re.sub(r'[ \t]+', ' ', validated_content)
            validated_content = validated_content.strip()
            
            # If content changed, update the result
            if validated_content != original_content:
                result = result[:match.start(2)] + validated_content + result[match.end(2):]
    
    # Also validate codes in bulleted lists (common format: "• CODE — Description")
    bullet_pattern = r'(•\s*)([A-Z0-9]{4,5})\s*[—\-:]\s*([^\n]+)'
    def validate_bullet_code(bullet_match):
        nonlocal total_codes_checked, valid_codes_kept, invalid_codes_removed
        code = bullet_match.group(2).upper()
        total_codes_checked += 1
        
        if is_valid_cpt_code(code):
            valid_codes_kept += 1
            return bullet_match.group(0)  # Keep valid code
        else:
            invalid_codes_removed.append(code)
            logger.warning(f"Removed invalid CPT code '{code}' from bulleted list")
            return ""  # Remove invalid code line
    
    result = re.sub(bullet_pattern, validate_bullet_code, result, flags=re.IGNORECASE | re.MULTILINE)
    
    # Clean up any double spaces or empty lines created by removals
    result = re.sub(r'\n\s*\n\s*\n+', '\n\n', result)  # Remove multiple blank lines
    result = re.sub(r'[ \t]{2,}', ' ', result)  # Normalize multiple spaces
    
    if invalid_codes_removed:
        logger.info(f"✅ CPT Code Validation Complete: Checked {total_codes_checked} codes, kept {valid_codes_kept} valid, removed {len(invalid_codes_removed)} invalid")
        if len(invalid_codes_removed) <= 50:
            logger.info(f"   Invalid codes removed: {invalid_codes_removed}")
        else:
            logger.info(f"   Invalid codes removed (first 50): {invalid_codes_removed[:50]}...")
    
    return result


def extract_and_validate_cpt_codes_from_soap(soap_note: str) -> str:
    """
    Extract all CPT codes from SOAP note and remove invalid ones.
    Only keeps valid CPT/HCPCS codes in the output.
    """
    if not soap_note:
        return soap_note
    
    result = soap_note
    invalid_codes_removed = []
    
    # Pattern to find CPT codes in various formats:
    # - "CODE — Description"
    # - "CODE - Description"
    # - "CODE: Description"
    # - "• CODE — Description"
    # - "CODE, CODE, CODE" (comma-separated)
    # - Just "CODE" on a line
    
    # Find all potential CPT code patterns
    cpt_patterns = [
        # Pattern: "CODE — Description" or "CODE - Description" or "• CODE — Description"
        (r'([•\-\s]*)([A-Z0-9]{4,5})\s*[—\-:]\s*([^\n]+)', 
         lambda m: _validate_cpt_in_match(m, invalid_codes_removed)),
        # Pattern: Comma-separated codes "CODE1, CODE2, CODE3"
        (r'\b([A-Z0-9]{4,5})\b(?=\s*[,;])',
         lambda m: _validate_cpt_in_match_simple(m, invalid_codes_removed)),
        # Pattern: Standalone codes on lines "CODE"
        (r'^([•\-\s]*)([A-Z0-9]{4,5})(?:\s*[—\-:]|\s*$)', 
         lambda m: _validate_cpt_in_match_standalone(m, invalid_codes_removed)),
    ]
    
    # Process each pattern
    for pattern, validation_func in cpt_patterns:
        matches = list(re.finditer(pattern, result, re.MULTILINE | re.IGNORECASE))
        for match in reversed(matches):  # Reverse to maintain positions
            try:
                validated_text = validation_func(match)
                if validated_text and validated_text != match.group(0):
                    result = result[:match.start()] + validated_text + result[match.end():]
            except Exception as e:
                logger.warning(f"Error validating CPT code in pattern: {e}")
                continue
    
    # Also validate codes in RFA Supportive CPTs section more aggressively
    # Find the Supportive CPTs section and validate all codes there
    supportive_cpts_pattern = r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\nJustification:|$)'
    match = re.search(supportive_cpts_pattern, result, re.DOTALL | re.IGNORECASE)
    if match:
        supportive_section = match.group(2)
        # Extract all codes from this section
        codes_in_section = re.findall(r'\b([A-Z0-9]{4,5})\b', supportive_section, re.IGNORECASE)
        valid_codes = []
        for code in codes_in_section:
            if is_valid_cpt_code(code):
                valid_codes.append(code)
            else:
                invalid_codes_removed.append(code)
                logger.warning(f"Removed invalid CPT code: {code}")
        
        # Rebuild the supportive CPTs section with only valid codes
        # Keep the original format but filter codes
        lines = supportive_section.split('\n')
        filtered_lines = []
        for line in lines:
            # Check if line contains a CPT code
            line_codes = re.findall(r'\b([A-Z0-9]{4,5})\b', line, re.IGNORECASE)
            if line_codes:
                # Check if all codes in this line are valid
                all_valid = all(is_valid_cpt_code(code) for code in line_codes)
                if all_valid:
                    filtered_lines.append(line)
                else:
                    # Try to keep line but remove invalid codes
                    for code in line_codes:
                        if not is_valid_cpt_code(code):
                            line = re.sub(rf'\b{re.escape(code)}\b', '', line, flags=re.IGNORECASE)
                            line = re.sub(r'\s+', ' ', line).strip()
                    if line.strip():
                        filtered_lines.append(line)
            else:
                # Line doesn't contain codes, keep it
                filtered_lines.append(line)
        
        new_supportive_section = '\n'.join(filtered_lines)
        if new_supportive_section != supportive_section:
            result = result[:match.start(2)] + new_supportive_section + result[match.end(2):]
    
    if invalid_codes_removed:
        logger.info(f"✅ Removed {len(invalid_codes_removed)} invalid CPT codes: {invalid_codes_removed[:10]}...")
    
    return result


def _validate_cpt_in_match(match, invalid_codes_list):
    """Validate CPT code in a match and return validated text or empty string"""
    prefix = match.group(1) if match.lastindex >= 1 else ""
    code = match.group(2) if match.lastindex >= 2 else match.group(1)
    
    if is_valid_cpt_code(code):
        return match.group(0)  # Keep original
    else:
        invalid_codes_list.append(code)
        return ""  # Remove invalid code line


def _validate_cpt_in_match_simple(match, invalid_codes_list):
    """Validate simple CPT code match"""
    code = match.group(1)
    if is_valid_cpt_code(code):
        return match.group(0)  # Keep original
    else:
        invalid_codes_list.append(code)
        return ""  # Remove invalid code


def _validate_cpt_in_match_standalone(match, invalid_codes_list):
    """Validate standalone CPT code match"""
    code = match.group(2) if match.lastindex >= 2 else match.group(1)
    
    if is_valid_cpt_code(code):
        return match.group(0)  # Keep original
    else:
        invalid_codes_list.append(code)
        return ""  # Remove invalid code


async def validate_and_correct_cpt_codes(soap_note: str, transcription: str, openai_client) -> str:
    """
    Post-process SOAP note to validate and correct CPT codes.
    Uses AI to generate CPT codes for procedures not in the mapping, making the system unlimited.
    Replaces "Not documented" with actual CPT codes when procedures are mentioned.
    Also validates and removes invalid CPT codes.
    """
    if not soap_note or not transcription:
        return soap_note
    
    result = soap_note
    
    # Extract procedures/treatments from transcription
    transcription_lower = transcription.lower()
    
    # Check for common procedure keywords
    procedure_keywords = [
        "injection", "physical therapy", "pt", "mri", "x-ray", "xray", "ct scan",
        "walking boot", "cam boot", "brace", "crutches", "walker", "cane",
        "surgery", "arthroscopy", "epidural", "facet", "trigger point",
        "therapy", "imaging", "procedure", "surgery"
    ]
    
    has_procedures = any(keyword in transcription_lower for keyword in procedure_keywords)
    
    if not has_procedures:
        return soap_note  # No procedures mentioned, no need to correct
    
    # Pattern to find CPT sections with "Not documented"
    cpt_patterns = [
        # Primary Procedure
        (r'(Primary Procedure:\s*\[)(Not documented|\[Not documented\])(\]\s*—\s*\[Procedure Name\])', 
         _generate_cpt_for_procedure),
        # Supportive CPTs
        (r'(Supportive CPTs:\s*\[)(Not documented|\[Not documented\])(\])',
         _generate_supportive_cpts),
        # RFA Primary CPT
        (r'(Primary CPT:\s*\[)(Not documented|\[Not documented\]|Code)(\])',
         _generate_cpt_for_rfa),
        # RFA Supportive CPTs
        (r'(Supportive CPTs:\s*\[)(Not documented|\[Not documented\]|Codes)(\])',
         _generate_supportive_cpts_rfa),
    ]
    
    # Collect all matches and their corresponding generator functions
    tasks_to_run = []
    
    # We use a unique marker for each replacement to avoid issues with .replace()
    # when multiple placeholders have the same text
    placeholder_map = {}
    
    for pattern, generator_func in cpt_patterns:
        matches = list(re.finditer(pattern, result, re.IGNORECASE | re.MULTILINE))
        for i, match in enumerate(matches):
            match_text = match.group(0)
            # Create a unique marker for this specific match instance
            marker = f"__CPT_MARKER_{len(tasks_to_run)}__"
            # Replace only the FIRST occurrence of match_text with marker
            result = result.replace(match_text, marker, 1)
            
            # Prepare the parallel task
            tasks_to_run.append(
                asyncio.to_thread(generator_func, transcription, openai_client, match)
            )
            placeholder_map[marker] = None
    
    if tasks_to_run:
        logger.info(f"🚀 Starting parallel CPT generation for {len(tasks_to_run)} items...")
        replacements = await asyncio.gather(*tasks_to_run)
        
        # Map markers back to their generated results
        for i, marker in enumerate(placeholder_map.keys()):
            replacement = replacements[i]
            # Replace the marker with the actual generated CPT text
            result = result.replace(marker, replacement)
            
        logger.info(f"✅ Parallel CPT generation complete")
    
    # CRITICAL: Validate and remove ALL invalid CPT codes before returning
    result = validate_all_cpt_codes_in_soap(result)
    
    return result


def _generate_cpt_for_procedure(transcription: str, openai_client, match) -> str:
    """Generate CPT code for a procedure mentioned in transcription - fully AI-driven"""
    
    # Try to extract procedure name from context
    procedure_text = _extract_procedure_from_transcription(transcription)
    
    if procedure_text and openai_client:
        primary, supportive = generate_cpt_with_ai(procedure_text, openai_client)
        if primary:
            return f"{match.group(1)}{primary}{match.group(3)}"
    
    return match.group(0)  # Return original if can't generate


def _generate_supportive_cpts(transcription: str, openai_client, match) -> str:
    """Generate supportive CPT codes - can be multiple codes - fully AI-driven"""
    
    procedure_text = _extract_procedure_from_transcription(transcription)
    
    if procedure_text and openai_client:
        primary, supportive = generate_cpt_with_ai(procedure_text, openai_client)
        if supportive and len(supportive) > 0:
            # Format multiple codes as comma-separated string
            codes_str = ", ".join(supportive) if isinstance(supportive, list) else str(supportive)
            return f"{match.group(1)}{codes_str}{match.group(3)}"
    
    return match.group(0)


def _generate_cpt_for_rfa(transcription: str, openai_client, match) -> str:
    """Generate CPT code for RFA section"""
    return _generate_cpt_for_procedure(transcription, openai_client, match)


def _generate_supportive_cpts_rfa(transcription: str, openai_client, match) -> str:
    """Generate supportive CPT codes for RFA section"""
    return _generate_supportive_cpts(transcription, openai_client, match)


def _extract_procedure_from_transcription(transcription: str) -> str:
    """Extract procedure name from transcription using simple heuristics"""
    transcription_lower = transcription.lower()
    
    # Look for common procedure patterns
    procedure_patterns = [
        r"(epidural[^.]*)",
        r"(injection[^.]*)",
        r"(physical therapy[^.]*)",
        r"(\bpt\b[^.]*)",
        r"(mri[^.]*)",
        r"(x-ray[^.]*)",
        r"(walking boot[^.]*)",
        r"(cam boot[^.]*)",
        r"(brace[^.]*)",
        r"(surgery[^.]*)",
        r"(arthroscopy[^.]*)",
    ]
    
    for pattern in procedure_patterns:
        match = re.search(pattern, transcription_lower, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    # Return a snippet if no specific pattern found
    if len(transcription) > 200:
        return transcription[:200]  # Use first 200 chars for context
    return transcription
