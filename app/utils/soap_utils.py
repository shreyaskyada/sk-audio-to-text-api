import re
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def extract_soap_sections_from_formatted_note(formatted_soap_note: str) -> Dict[str, str]:
    """
    Extract S/O/A/P sections from the generated consolidated note.
    Supports both legacy markdown headings and current template headings.
    """
    sections = {"subjective": "", "objective": "", "assessment": "", "plan": ""}
    
    def _between(text, start_regex, end_regexes):
        start_match = re.search(start_regex, text, re.IGNORECASE | re.DOTALL)
        if not start_match:
            return ""
        start_pos = start_match.end()
        end_pos = len(text)
        for er in end_regexes:
            end_match = re.search(er, text[start_pos:], re.IGNORECASE | re.DOTALL)
            if end_match:
                end_pos = min(end_pos, start_pos + end_match.start())
        return text[start_pos:end_pos].strip()

    # Define standard start and end markers for each section
    subj_start = r'## S – SUBJECTIVE|SUBJECTIVE:|S: Subjective'
    obj_start = r'## O – OBJECTIVE|OBJECTIVE:|O: Objective'
    ass_start = r'## A – ASSESSMENT|ASSESSMENT:|A: Assessment'
    plan_start = r'## P – PLAN|PLAN:|P: Plan'
    
    end_markers = [r'## [SOAP] – ', r'---', r'\[', r'SUBJECTIVE:', r'OBJECTIVE:', r'ASSESSMENT:', r'PLAN:']

    sections["subjective"] = _between(formatted_soap_note, subj_start, [obj_start, ass_start, plan_start] + end_markers)
    sections["objective"] = _between(formatted_soap_note, obj_start, [ass_start, plan_start] + end_markers)
    sections["assessment"] = _between(formatted_soap_note, ass_start, [plan_start] + end_markers)
    sections["plan"] = _between(formatted_soap_note, plan_start, end_markers)
    
    return sections

def aggressive_validate_rfa_supportive_cpts(soap_note: str) -> str:
    """
    Aggressively validate RFA Supportive CPTs section to ensure 100% valid codes.
    This function specifically targets the RFA Supportive CPTs section and validates every code.
    """
    from app.cpt_mappings import is_valid_cpt_code
    
    if not soap_note:
        return soap_note
    
    result = soap_note
    invalid_codes_removed = []
    
    # Find RFA Supportive CPTs section
    rfa_supportive_pattern = r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\nJustification:|$)'
    match = re.search(rfa_supportive_pattern, result, re.DOTALL | re.IGNORECASE)
    
    if not match:
        return result
    
    supportive_section = match.group(2)
    original_section = supportive_section
    
    code_pattern = r'\b([A-Z][A-Z0-9]{3,4}|[0-9]{5})\b'
    all_codes = re.findall(code_pattern, supportive_section, re.IGNORECASE)
    
    invalid_codes_set = set()
    for code in all_codes:
        code_upper = code.upper()
        if not is_valid_cpt_code(code_upper):
            invalid_codes_set.add(code_upper)
            invalid_codes_removed.append(code_upper)
    
    lines = supportive_section.split('\n')
    validated_lines = []
    
    for line in lines:
        line_has_invalid = False
        line_codes = re.findall(code_pattern, line, re.IGNORECASE)
        for code in line_codes:
            if code.upper() in invalid_codes_set:
                line_has_invalid = True
                break
        
        if line_has_invalid:
            cleaned_line = line
            for invalid_code in invalid_codes_set:
                cleaned_line = re.sub(rf'\b{re.escape(invalid_code)}\b', '', cleaned_line, flags=re.IGNORECASE)
            cleaned_line = re.sub(r'\s+', ' ', cleaned_line).strip()
            if cleaned_line and (cleaned_line.startswith('•') or any(c.upper() not in invalid_codes_set for c in re.findall(code_pattern, cleaned_line, re.IGNORECASE))):
                validated_lines.append(cleaned_line)
        else:
            validated_lines.append(line)
    
    new_supportive_section = '\n'.join(validated_lines)
    if new_supportive_section != original_section:
        result = result[:match.start(2)] + new_supportive_section + result[match.end(2):]
    
    return result

def validate_all_cpt_codes_in_soap(soap_note: str) -> str:
    """
    Comprehensive validation of all CPT codes in SOAP note.
    Removes all invalid CPT codes and keeps only valid ones.
    """
    from app.cpt_mappings import is_valid_cpt_code
    
    if not soap_note:
        return soap_note
    
    result = soap_note
    
    cpt_sections = [
        (r'(Primary Procedure:\s*)(.*?)(?=\n|$)', 'Primary Procedure'),
        (r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\n[A-Z]|$)', 'Supportive CPTs'),
        (r'(Primary CPT:\s*)(.*?)(?=\n|$)', 'RFA Primary CPT'),
        (r'(Supportive CPTs:\s*)(.*?)(?=\n\n|\nJustification:|$)', 'RFA Supportive CPTs'),
        (r'(E/M Code:\s*)(.*?)(?=\n|$)', 'E/M Code'),
    ]
    
    for pattern, section_name in cpt_sections:
        matches = list(re.finditer(pattern, result, re.MULTILINE | re.DOTALL | re.IGNORECASE))
        for match in reversed(matches):
            section_content = match.group(2)
            original_content = section_content
            code_pattern = r'\b((?:[A-Z][A-Z0-9]{3,4})|(?:[0-9]{5}))\b'
            
            def validate_and_replace_code(code_match):
                code = code_match.group(1).upper()
                if is_valid_cpt_code(code):
                    return code_match.group(0)
                return ""
            
            validated_content = re.sub(code_pattern, validate_and_replace_code, section_content, flags=re.IGNORECASE)
            validated_content = re.sub(r'\n\s*\n+', '\n', validated_content)
            validated_content = re.sub(r'[ \t]+', ' ', validated_content)
            validated_content = validated_content.strip()
            
            if validated_content != original_content:
                result = result[:match.start(2)] + validated_content + result[match.end(2):]
    
    bullet_pattern = r'(•\s*)([A-Z0-9]{4,5})\s*[—\-:]\s*([^\n]+)'
    def validate_bullet_code(bullet_match):
        code = bullet_match.group(2).upper()
        if is_valid_cpt_code(code):
            return bullet_match.group(0)
        return ""
    
    result = re.sub(bullet_pattern, validate_bullet_code, result, flags=re.IGNORECASE | re.MULTILINE)
    result = re.sub(r'\n\s*\n\s*\n+', '\n\n', result)
    result = re.sub(r'[ \t]{2,}', ' ', result)
    
    return result
