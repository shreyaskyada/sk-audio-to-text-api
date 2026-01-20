import logging
import re
import json
from typing import Optional, List, Dict, Any, Tuple
from app.services.cpt_service import create_openai_client, is_valid_cpt_code
from app.config import settings

logger = logging.getLogger(__name__)

OPENAI_MODEL = 'gpt-5.1'

def format_clinical_data(data: Any) -> Optional[str]:
    """Format clinical data into a standard display string."""
    if data is None: return None
    if isinstance(data, str): return data.strip()
    if isinstance(data, list):
        return " | ".join(str(item).strip() for item in data if item)
    if isinstance(data, dict):
        parts = []
        for key, value in data.items():
            if value:
                key_text = key.replace("_", " ").title()
                val_text = format_clinical_data(value)
                if val_text:
                    parts.append(f"{key_text}: {val_text}")
        return " | ".join(parts)
    return str(data)

def extract_cpt_codes_from_text(text: str) -> List[str]:
    """Extract all CPT/HCPCS codes mentioned in the text using regex patterns"""
    if not text or not isinstance(text, str): return []
    cpt_pattern = r'\b(\d{5})\b'
    hcpcs_pattern = r'\b([A-Z]\d{4,5})\b'
    found_codes = []
    cpt_matches = re.findall(cpt_pattern, text)
    found_codes.extend(cpt_matches)
    hcpcs_matches = re.findall(hcpcs_pattern, text)
    found_codes.extend(hcpcs_matches)
    return list(set(found_codes))

def filter_examination_findings_from_hpi(text: str) -> Tuple[str, str]:
    """Filter out examination findings, imaging results, and test results from HPI text"""
    if not text or not isinstance(text, str): return (text or "", "")
    sentences = re.split(r'[.|!?]\s+', text)
    examination_patterns = [
        r'examination\s+reveals', r'on\s+exam', r'physical\s+examination', r'clinical\s+examination',
        r'physical\s+exam', r'clinical\s+exam', r'exam\s+shows', r'exam\s+demonstrates',
        r'examination\s+shows', r'examination\s+demonstrates', r'on\s+examination',
        r'during\s+examination', r'upon\s+examination', r'exam\s+reveals', r'exam\s+today',
        r'today\s+on\s+exam', r'on\s+physical\s+exam', r'on\s+clinical\s+exam',
    ]
    imaging_patterns = [
        r'mri\s+confirms', r'mri\s+shows', r'mri\s+reveals', r'mri\s+demonstrates',
        r'on\s+mri', r'mri\s+review', r'on\s+mri\s+review', r'mri\s+indicates',
        r'x-ray\s+shows', r'x-ray\s+reveals', r'x-ray\s+demonstrates', r'ct\s+shows',
        r'ct\s+scan\s+shows', r'imaging\s+shows', r'imaging\s+reveals', r'imaging\s+demonstrates',
        r'imaging\s+confirms', r'radiograph\s+shows', r'study\s+shows', r'study\s+reveals',
        r'report\s+shows', r'report\s+reveals',
    ]
    test_patterns = [
        r'test\s+is\s+positive', r'test\s+positive', r'positive\s+test', r'test\s+negative',
        r'negative\s+test', r'test\s+reveals', r'test\s+shows', r'special\s+test',
        r'provocative\s+test', r'positive\s+\w+\s+test', r'positive\s+\w+\s+drawer',
        r'positive\s+\w+\s+maneuver',
    ]
    observation_patterns = [
        r'walks\s+with', r'gait\s+is', r'range\s+of\s+motion\s+is', r'rom\s+is',
        r'strength\s+is', r'neurovascular', r'pulses\s+are', r'sensation\s+is',
        r'reflexes\s+are', r'inspection\s+reveals', r'palpation\s+reveals', r'auscultation\s+reveals',
    ]
    all_patterns = examination_patterns + imaging_patterns + test_patterns + observation_patterns
    filtered_sentences, excluded_sentences = [], []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence: continue
        should_exclude = False
        sentence_lower = sentence.lower()
        for pattern in all_patterns:
            if re.search(pattern, sentence_lower, re.IGNORECASE):
                should_exclude = True
                break
        if should_exclude: excluded_sentences.append(sentence)
        else: filtered_sentences.append(sentence)
    filtered_text = ". ".join(filtered_sentences)
    excluded_text = ". ".join(excluded_sentences)
    filtered_text = re.sub(r'\s+', ' ', filtered_text)
    excluded_text = re.sub(r'\s+', ' ', excluded_text)
    return (filtered_text.strip(), excluded_text.strip())

def extract_hpi_from_intake(intake_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract HPI (History of Present Illness) from intake form Sections G and H"""
    if not intake_doc: return None
    section_g = intake_doc.get("section_g") or {}
    section_h = intake_doc.get("section_h") or {}
    hpi_parts = []
    if section_g:
        symptoms = section_g.get("symptoms") or []
        if symptoms:
            if isinstance(symptoms, list): hpi_parts.append(f"Symptoms: {', '.join(str(s) for s in symptoms if s)}")
            else: hpi_parts.append(f"Symptoms: {symptoms}")
        pain_at_rest = section_g.get("pain_at_rest")
        pain_with_activity = section_g.get("pain_with_activity")
        pain_worst = section_g.get("pain_worst")
        pain_info = []
        if pain_at_rest: pain_info.append(f"Pain at rest: {pain_at_rest}/10")
        if pain_with_activity: pain_info.append(f"Pain with activity: {pain_with_activity}/10")
        if pain_worst: pain_info.append(f"Worst pain: {pain_worst}/10")
        if pain_info: hpi_parts.append(" ".join(pain_info))
        pain_description = section_g.get("pain_description") or []
        if pain_description:
            if isinstance(pain_description, list): hpi_parts.append(f"Pain description: {', '.join(str(p) for p in pain_description if p)}")
            else: hpi_parts.append(f"Pain description: {pain_description}")
        aggravating = section_g.get("aggravating_factors")
        if aggravating: hpi_parts.append(f"Aggravating factors: {aggravating}")
        relieving = section_g.get("relieving_factors")
        if relieving: hpi_parts.append(f"Relieving factors: {relieving}")
    if section_h:
        limited_activities = section_h.get("limited_activities") or []
        if limited_activities:
            if isinstance(limited_activities, list): hpi_parts.append(f"Limited activities: {', '.join(str(a) for a in limited_activities if a)}")
            else: hpi_parts.append(f"Limited activities: {limited_activities}")
        adl_limitations = section_h.get("adl_limitations") or []
        if adl_limitations:
            if isinstance(adl_limitations, list): hpi_parts.append(f"ADL limitations: {', '.join(str(a) for a in adl_limitations if a)}")
            else: hpi_parts.append(f"ADL limitations: {adl_limitations}")
    return " | ".join(hpi_parts) if hpi_parts else None

def extract_objective_findings_from_intake(intake_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract Objective Findings (Vitals) from intake form Section I (ClinicalInputs)"""
    if not intake_doc: return None
    section_i = intake_doc.get("section_i") or {}
    if not section_i: return None
    findings_parts = []
    bp, pulse, temp, weight, height = section_i.get("bp"), section_i.get("pulse"), section_i.get("temp"), section_i.get("weight"), section_i.get("height")
    vital_signs = []
    if bp: vital_signs.append(f"BP: {bp}")
    if pulse: vital_signs.append(f"Pulse: {pulse}")
    if temp: vital_signs.append(f"Temp: {temp}")
    if weight: vital_signs.append(f"Weight: {weight}")
    if height: vital_signs.append(f"Height: {height}")
    if vital_signs: findings_parts.append("Vital Signs: " + ", ".join(vital_signs))
    rom, strength, pain_chart_notes = section_i.get("rom"), section_i.get("strength"), section_i.get("pain_chart_notes")
    if rom: findings_parts.append(f"Range of Motion: {rom}")
    if strength: findings_parts.append(f"Strength: {strength}")
    if pain_chart_notes: findings_parts.append(f"Pain Chart Notes: {pain_chart_notes}")
    return " | ".join(findings_parts) if findings_parts else None

def extract_objective_findings_from_text(text: str) -> Optional[str]:
    """Extract objective findings (physical examination) from transcription or SOAP text using GPT"""
    if not text or not str(text).strip(): return None
    try:
        client = create_openai_client()
        system_prompt = "You are a medical documentation assistant specializing in extracting objective findings (physical examination). Extract specific physical examination findings, vital signs, and clinical observations from the provided text. Return ONLY the extracted text summary. Do not include headers."
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Extract objective findings: {text[:5000]}"}],
            temperature=0.1
        )
        content = response.choices[0].message.content
        if content and content.lower() not in ["null", "none", "not found"]: return content.strip()
    except Exception as e:
        logger.error(f"Error extracting objective findings: {e}")
    return None

async def extract_rfa_items_from_text(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract RFA items from text using GPT"""
    if not text or not str(text).strip(): return None
    try:
        client = create_openai_client()
        system_prompt = """You are a medical documentation assistant specializing in extracting Request for Authorization (RFA) items from clinical transcriptions and SOAP notes.

Your task is to analyze the provided medical text and extract ONLY those treatment and drug requests that are EXPLICITLY ORDERED or REQUESTED today for future action. This includes:
- Medical treatments EXPLICITLY ORDERED (physical therapy, injections, imaging, surgery, DME, etc.)
- Medications/drugs EXPLICITLY prescribed
- Services or goods EXPLICITLY requested

For each RFA item, extract:
- Type: "treatment" or "drug"
- Diagnosis: The diagnosis/condition this treatment/drug is for
- ICD-10 Code: If mentioned
- Treatment Requested / Drug Requested: Name of the treatment or drug
- Primary CPT/HCPCS Code: The main procedure code (for treatments)
- Supportive CPTs: CRITICAL - Extract ALL supportive CPT/HCPCS codes that are typically required with the primary procedure. This includes:
  * Fluoroscopic guidance codes (77003, 77002, etc.) for injections
  * Ultrasound guidance codes (76942, etc.) for procedures
  * DME/Supplies codes (L-codes for braces, boots, crutches, etc.)
  * Anesthesia codes if mentioned
  * Any other supportive services, devices, or supplies mentioned
  Supportive CPTs should be an array of codes. If multiple supportive codes are mentioned or typically required, include ALL of them.
- Strength & Form: For drugs (e.g., "500mg tablet", "10mg/ml injection")
- Frequency/Duration: For treatments (e.g., "3x/week for 6 weeks", "1 session")
- Quantity: For drugs (e.g., "30 tablets", "1 vial")
- Justification: Any medical justification mentioned

Return a JSON object with an "rfa_items" array containing all extracted items. Use EXACT field names as shown:
{
  "rfa_items": [
    {
      "type": "treatment",
      "diagnosis": "Lower back pain",
      "diagnosisCode": "M54.5",
      "serviceRequested": "Epidural steroid injection",
      "cpt": "62311",
      "supportiveCpts": ["77003"],
      "frequencyDuration": "1 injection"
    },
    {
      "type": "drug",
      "diagnosis": "Lower back pain",
      "diagnosisCode": "M54.5",
      "drug": "Ibuprofen",
      "doseForm": "600mg tablet",
      "quantity": "90 tablets"
    }
  ]
}

CRITICAL RULES:
1. **MANDATORY - INCLUDE ALL CODES MENTIONED IN DICTATION:** If ANY CPT/HCPCS code is mentioned in the text (e.g., "29881", "29882", "20924", "L1833", "E0114", etc.), you MUST include it in the supportiveCpts array. 
2. DO NOT add codes "that might be needed" unless they are standard guidance codes for the procedure.
3. If no RFA items found, return {"rfa_items": []}."""
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Extract RFA items from: {text[:5000]}"}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        rfa_items = data.get("rfa_items") or []
        mentioned_codes = extract_cpt_codes_from_text(text[:5000])
        for item in rfa_items:
            if item.get("type") == "treatment":
                sc = item.get("supportiveCpts", [])
                pc = item.get("cpt", "").strip()
                for c in mentioned_codes:
                    if c != pc and c not in sc and not c.startswith("99") and not c.upper().startswith("WC"):
                        sc.append(c)
                item["supportiveCpts"] = sc
        return rfa_items
    except Exception as e:
        logger.error(f"Error extracting RFA items: {e}")
    return None

def parse_rfa_section_from_formatted_soap(formatted_soap_note: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parse RFA section directly from formatted SOAP note text.
    Extracts requested service, primary CPT, and all supportive CPTs from the RFA section.
    
    This function looks for the "REQUEST FOR AUTHORIZATION (RFA)" section and extracts:
    - Requested Service description
    - Primary CPT code
    - All Supportive CPT codes (grouped by category)
    
    Returns a list of RFA items in the standard format.
    """
    if not formatted_soap_note or not isinstance(formatted_soap_note, str):
        return None
    
    try:
        # Look for RFA section in the formatted SOAP note
        rfa_match = re.search(
            r'\*\*REQUEST FOR AUTHORIZATION \(RFA\)\*\*(.*?)(?=\n\*\*[A-Z]|\Z)',
            formatted_soap_note,
            re.IGNORECASE | re.DOTALL
        )
        
        if not rfa_match:
            return None
        
        rfa_section = rfa_match.group(1)
        logger.info(f"Found RFA section in formatted SOAP note ({len(rfa_section)} characters)")
        
        # Extract Requested Service
        requested_service = ""
        service_match = re.search(r'\*\*Requested Service:\*\*\s*\n\s*-\s*(.+?)(?=\n\n|\n\*\*|$)', rfa_section, re.DOTALL)
        if not service_match:
            # Fallback for different formatting
            service_match = re.search(r'\*\*Requested Service:\*\*\s*(.+?)(?=\n\n|\n\*\*|$)', rfa_section, re.DOTALL)
            
        if service_match:
            requested_service = service_match.group(1).strip()
        
        # Extract Primary CPT
        primary_cpt = ""
        primary_cpt_match = re.search(r'\*\*Primary CPT:\*\*\s*\n\s*(.+?)(?=\n\n|\n\*\*|$)', rfa_section, re.DOTALL)
        if not primary_cpt_match:
            primary_cpt_match = re.search(r'\*\*Primary CPT:\*\*\s*(.+?)(?=\n\n|\n\*\*|$)', rfa_section, re.DOTALL)

        if primary_cpt_match:
            primary_cpt_text = primary_cpt_match.group(1).strip()
            # Extract just the code (first 5-6 characters before the dash)
            code_match = re.match(r'([A-Z]?\d{4,5})', primary_cpt_text)
            if code_match:
                primary_cpt = code_match.group(1)
        
        # Extract ALL CPT codes from the entire RFA section using regex
        all_cpt_codes = extract_cpt_codes_from_text(rfa_section)
        
        # Remove primary CPT from supportive list
        supportive_cpts = [code for code in all_cpt_codes if code != primary_cpt]
        
        logger.info(f"Parsed RFA section: Service='{requested_service[:50]}...', Primary CPT={primary_cpt}, Supportive CPTs={len(supportive_cpts)} codes")
        
        # Create a single RFA item with all the codes
        if requested_service or primary_cpt:
            rfa_item = {
                "type": "treatment",
                "serviceRequested": requested_service if requested_service else "Medical procedure",
                "cpt": primary_cpt if primary_cpt else "",
                "supportiveCpts": supportive_cpts,
                "diagnosis": "",  # Will be filled from SOAP assessment
                "diagnosisCode": "",  # Will be filled from SOAP assessment
                "frequencyDuration": "1"
            }
            
            return [rfa_item]
        
        return None
        
    except Exception as e:
        logger.warning(f"Error parsing RFA section from formatted SOAP: {e}")
        return None
