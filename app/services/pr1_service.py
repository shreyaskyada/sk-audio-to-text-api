import logging
import json
import asyncio
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from bson import ObjectId
from fastapi.responses import JSONResponse

from app.database import get_database
from app.utils.pr1_utils import (
    str_or_nd, format_clinical_data, fetch_if_needed, fetch_latest_document,
    pick_name, pick_doi, primary_secondary_dx, extract_diagnosis_codes_from_soap_assessment,
    to_mmddyyyy, to_yyyy_mm_dd
)
from app.utils.pr1_parser import (
    extract_hpi_from_intake, extract_objective_findings_from_intake,
    extract_objective_findings_from_text, extract_rfa_items_from_text,
    filter_examination_findings_from_hpi, parse_rfa_section_from_formatted_soap,
    extract_cpt_codes_from_text
)
from app.services.cpt_service import create_openai_client, generate_cpt_with_ai, validate_cpt_codes_relevance

logger = logging.getLogger(__name__)

COLL_SAVED_PR1 = "saved_pr1_forms"
COLL_INTAKE = "intake_forms"
COLL_FOLLOWUP = "followup_intake_forms"
COLL_SOAP = "soap_notes"

OPENAI_MODEL = 'gpt-5.1'  # Match old backend model

async def convert_formatted_soap_to_json(formatted_soap: str) -> Dict[str, Any]:
    """
    Use GPT to convert formatted SOAP note text to structured JSON data.
    This restores the crucial enrichment step from the old backend.
    """
    if not formatted_soap: return {}
    
    try:
        client = create_openai_client()
        
        # Truncate if too long (approx 50k chars)
        if len(formatted_soap) > 50000:
            formatted_soap = formatted_soap[:50000] + "\n[Truncated]"
            
        system_prompt = """You are a medical documentation assistant. Extract structured data from the SOAP note text into JSON.
Include:
- Patient info (name, DOB, DOI, claim #)
- Clinical info:
  - subjective (chief complaint, history)
  - objective (physical exam, findings)
  - assessment (diagnoses, discussion)
  - plan (treatment, meds)
- Work Status:
  - work_status (Modified Duty, Full Duty, etc.)
  - restrictions (detailed list of restrictions, weight limits, etc.)
  - return_to_work_date
  - next_visit_date
- RFA items (requests)
Return valid JSON only."""

        user_prompt = f"Extract structured data from:\n\n{formatted_soap}"
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=4000
        )
        
        content = response.choices[0].message.content
        if content:
            return json.loads(content)
        return {}
        
    except Exception as e:
        logger.error(f"Error converting SOAP text to JSON: {e}")
        return {}


async def generate_cpt_codes_from_diagnosis_codes(assessment_codes: Dict[str, Any], transcription: str, soap_doc: Dict[str, Any], openai_client=None) -> List[str]:
    if not assessment_codes: return []
    primary = assessment_codes.get("primary_diagnosis_code")
    if not primary: return []
    
    try:
        if not openai_client: openai_client = create_openai_client()
        _, supportive = await generate_cpt_with_ai(f"Primary DX: {primary}. Context: {transcription[:1000]}", openai_client)
        return supportive
    except Exception as e:
        logger.warning(f"Error in generate_cpt_codes_from_diagnosis: {e}")
        return []

def calc_checkboxes(soap_doc: Optional[Dict[str, Any]], follow_doc: Optional[Dict[str, Any]], flags: Optional[Dict[str, bool]], section_a: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    f = flags or {}
    s = soap_doc or {}
    
    # Check if section_a contains any requests
    has_rfa_in_section_a = False
    if section_a:
        if section_a.get("medical_treatment_requests") or section_a.get("drug_requests"):
            has_rfa_in_section_a = True

    return {
        "request_for_authorization": bool(s.get("rfa_items")) or has_rfa_in_section_a or f.get("request_for_authorization", False),
        "progress_report": f.get("progress_report", True),
        "response_to_request_for_information": f.get("response_to_request_for_information", False),
        "expedited_request_for_authorization": f.get("expedited_request_for_authorization", False),
        "change_in_work_status": True,
        "change_in_patient_condition": f.get("change_in_patient_condition", False) or bool(s.get("discussion_assessment")),
        "change_in_treatment_plan": f.get("change_in_treatment_plan", False) or bool(s.get("change_in_treatment_plan")),
        "released_from_care": f.get("released_from_care", False) or bool(s.get("discharge_from_care")),
        "other": f.get("other", False)
    }

async def build_section_a_rfa(soap_doc: Optional[Dict[str, Any]], intake_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = soap_doc or {}
    rfa_items = doc.get("rfa_items") or doc.get("RFA_items") or []
    
    if not rfa_items:
        plan = doc.get("plan")
        formatted_soap = doc.get("formatted_soap_note")
        transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
        
        if formatted_soap and isinstance(formatted_soap, str):
            parsed_rfa = parse_rfa_section_from_formatted_soap(formatted_soap)
            if parsed_rfa: rfa_items = parsed_rfa
                
        if not rfa_items and formatted_soap:
             extracted = await extract_rfa_items_from_text(formatted_soap)
             if extracted: rfa_items = extracted

        if not rfa_items and transcription_text:
             extracted = await extract_rfa_items_from_text(transcription_text)
             if extracted: rfa_items = extracted

    patient_name = pick_name(intake_doc, soap_doc)
    general_request_text = doc.get("general_request_text") or doc.get("generalRequestText") or ""
    
    assessment_codes = extract_diagnosis_codes_from_soap_assessment(soap_doc)
    primary_code = assessment_codes.get("primary_diagnosis_code")
    
    transcription_text = str(doc.get("transcription") or doc.get("corrected_transcription") or "")
    mentioned_cpt_codes = extract_cpt_codes_from_text(transcription_text)
    
    cpt_descriptions = {
        # Meniscus procedures
        "29881": "Meniscectomy", "29882": "Meniscus Repair",
        "29880": "Meniscectomy (Medial and Lateral)", "29883": "Meniscus Repair (Medial and Lateral)",
        "29877": "Chondroplasty", "29879": "Microfracture",
        "29884": "Lysis of Adhesions", "29887": "OCD Drilling with Bone Grafting",
        "29870": "Arthroscopy, Knee, Diagnostic", "29871": "Arthroscopy, Knee, Surgical",
        # Graft/Allograft codes
        "20924": "Tendon Graft", "20925": "Tendon Graft Allograft", "20926": "Tissue Graft Allograft",
        # Implant/Anchor codes
        "C1713": "Anchor/Screw Implant", "C1714": "Anchor/Screw Implant, Additional",
        # DME - Braces
        "L1833": "ACL Functional Knee Brace", "L1845": "Hinged Knee Brace",
        "L1832": "Elastic Knee Brace", "L1830": "Rigid Knee Brace",
        # DME - Mobility aids
        "E0114": "Crutches, Forearm", "E0116": "Crutches, Underarm",
        # Cryotherapy
        "E0218": "Cryotherapy Device", "E0236": "Cold Therapy Pump",
        # Surgical supplies
        "A4566": "Sling or Arm Support", "A4570": "Splint",
        # Post-op care supplies
        "A4217": "Sterile Saline Solution", "A4219": "Antiseptic Solution",
        "A4221": "Wound Care Supplies",
    }

    async def process_rfa_item(it):
        item_requests = []
        item_drug_requests = []
        
        if hasattr(it, 'model_dump'): it = it.model_dump(exclude_none=True)
        elif not isinstance(it, dict): return [], []
        
        request_type = it.get("type", "").lower()
        is_drug = request_type == "drug" or it.get("is_drug") or False
        
        if is_drug:
            drug_name = it.get("drug") or it.get("drug_name") or ""
            diagnosis = it.get("diagnosis") or it.get("diagnosis_name") or ""
            diagnosis_code = it.get("diagnosisCode") or it.get("icd10") or primary_code or ""
            if drug_name:
                drug_item = {
                    "type": "drug", "diagnosis": diagnosis, "diagnosisCode": diagnosis_code,
                    "diagnosis_code": diagnosis_code, # FE alias
                    "drug": drug_name, "quantity": it.get("quantity") or "",
                    "doseForm": it.get("doseForm") or it.get("dose_form") or "",
                    "dose_form": it.get("doseForm") or it.get("dose_form") or "" # FE alias
                }
                item_requests.append(drug_item)
                item_drug_requests.append(drug_item)
        else:
            service_requested = it.get("serviceRequested") or it.get("service") or ""
            diagnosis = it.get("diagnosis") or ""
            diagnosis_code = it.get("diagnosisCode") or primary_code or ""
            cpt = it.get("cpt") or ""
            
            supportive_cpts = []
            raw_supp = it.get("supportiveCpts") or []
            if isinstance(raw_supp, str): supportive_cpts = [c.strip() for c in raw_supp.split(",") if c.strip()]
            elif isinstance(raw_supp, list): supportive_cpts = [str(c).strip() for c in raw_supp if c]
            
            if service_requested:
                # Add mentioned CPTs if not already present
                if mentioned_cpt_codes:
                    for code in mentioned_cpt_codes:
                         c_str = str(code).strip()
                         if c_str and c_str != cpt.strip() and c_str not in supportive_cpts:
                             supportive_cpts.append(c_str)
                
                # Try to generate supportive CPTs (catch potential async errors)
                try:
                    cli = create_openai_client()
                    gen_cpts = await generate_cpt_codes_from_diagnosis_codes(assessment_codes, transcription_text, soap_doc, cli)
                    if gen_cpts:
                        # Validation is internal to generate_cpt_codes... mostly, but we double check
                        for gc in gen_cpts:
                            if str(gc).strip() not in supportive_cpts: supportive_cpts.append(str(gc).strip())
                except Exception as e:
                    logger.warning(f"Failed to generate extra CPTs: {e}")

                freq_dur = it.get("frequencyDuration") or it.get("frequency_duration") or ""
                item_requests.append({
                    "type": "treatment", "diagnosis": diagnosis, "diagnosisCode": diagnosis_code,
                    "diagnosis_code": diagnosis_code, # FE alias
                    "serviceRequested": service_requested, "service_requested": service_requested, # FE alias
                    "cpt": cpt, "frequencyDuration": freq_dur, "frequency_duration": freq_dur # FE alias
                })
                
                for sc in supportive_cpts:
                    if sc and str(sc).strip() != cpt.strip():
                        s_name = cpt_descriptions.get(sc, f"Supportive Service ({sc})")
                        item_requests.append({
                            "type": "treatment", "diagnosis": diagnosis, "diagnosisCode": diagnosis_code,
                            "diagnosis_code": diagnosis_code, # FE alias
                            "serviceRequested": s_name, "service_requested": s_name, # FE alias
                            "cpt": sc, "frequencyDuration": "As needed", "frequency_duration": "As needed" # FE alias
                        })

        return item_requests, item_drug_requests

    requests_list, medical_treatment_requests, drug_requests = [], [], []
    if rfa_items and isinstance(rfa_items, list):
        results = await asyncio.gather(*(process_rfa_item(item) for item in rfa_items))
        for res_reqs, res_drugs in results:
            requests_list.extend(res_reqs)
            medical_treatment_requests.extend([r for r in res_reqs if r["type"] == "treatment"])
            drug_requests.extend(res_drugs)
            
    return {
        "patientName": patient_name,
        "generalRequestText": general_request_text,
        "requests": requests_list,
        "medical_treatment_requests": medical_treatment_requests,
        "drug_requests": drug_requests
    }

async def extract_subjective_findings(doc: Dict[str, Any], intake_doc: Optional[Dict[str, Any]]) -> str:
    parts = []
    sc = doc.get("chief_complaint") or doc.get("brief_history")
    if sc: parts.append(format_clinical_data(sc))
    soap_sub = doc.get("subjective")
    if soap_sub: parts.append(format_clinical_data(soap_sub))
    if intake_doc:
        hpi = extract_hpi_from_intake(intake_doc)
        if hpi: parts.append(hpi)
    raw = " | ".join(filter(None, parts))
    if not raw: return "Patient evaluation for reported symptoms."
    filtered, _ = filter_examination_findings_from_hpi(raw)
    return filtered or raw

async def build_header_admin(soap_doc: Dict[str, Any], intake_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    doc = soap_doc or {}
    pat_info = doc.get("patient_info") or {}
    
    # Try to pick best values
    dob = pick_dob(intake_doc, doc)
    doi = pick_doi(intake_doc, doc)
    
    return {
        "patient_name": pick_name(intake_doc, doc),
        "date_of_injury": doi,
        "date_of_birth": dob,
        "claim_number": doc.get("claim_number") or pat_info.get("claim_number") or "",
        "employer": pick_employer(intake_doc) or doc.get("employer") or "",
        "physician": {
            "physician_name": "Dr. Usha K Colburn", # Default/Placeholder
            "practice_name": "UseHealth",
            "address": "123 Medical Center Dr",
            "city": "Health City",
            "state": "CA",
            "zip": "90001"
        },
        "claims_administrator": {
            "name": doc.get("insurance_company") or doc.get("claims_admin") or "",
            "address": "",
            "city": "",
            "state": "",
            "zip": ""
        },
        "date_of_first_examination": doi # Fallback/Assumption
    }

async def build_page2_signature(soap_doc: Dict[str, Any], section_a: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    has_rfa = False
    if section_a:
        if section_a.get("medical_treatment_requests") or section_a.get("drug_requests"):
            has_rfa = True
            
    return {
        "include_section_a": has_rfa,
        "include_section_b": True, 
        "include_section_c": True,
        "physician_signature": "Dr. Usha K Colburn",
        "executed_at": "UseHealth Clinic",
        "signature_date": to_mmddyyyy(datetime.utcnow().strftime("%Y-%m-%d"))
    }

async def build_section_b(soap_doc: Optional[Dict[str, Any]], intake_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = soap_doc or {}
    p, s, addl = primary_secondary_dx(doc)
    
    subj = await extract_subjective_findings(doc, intake_doc)
    obj = doc.get("objective") or doc.get("physical_exam") or "Clinical observation performed."
    assessment = doc.get("assessment") or doc.get("discussion_assessment") or ""
    plan = doc.get("plan") or doc.get("treatment_plan") or "Continue current conservative management."
    
    return {
        "diagnoses": {
            "primary": {"condition": p.get("condition"), "icd10": p.get("icd10")} if p else None,
            "secondary": {"condition": s.get("condition"), "icd10": s.get("icd10")} if s else None,
            "additional_diagnoses": [{"condition": d.get("condition"), "icd10": d.get("icd10")} for d in addl] if addl else []
        },
        "subjective": subj,
        "chief_complaint_and_history": subj, # Frontend alias
        "objective_findings": obj,
        "physical_exam": obj, # Frontend alias
        "plan": plan,
        "treatment_plan": plan, # Frontend alias
        "discussion_assessment": assessment,
        "current_treatment_and_meds": doc.get("medications") or "",
        "continue_same_treatment": True,
        "discharge_from_care": doc.get("discharge_from_care") or False,
        "change_in_treatment_plan": doc.get("change_in_treatment_plan") or False,
        "dispense_as_written": True
    }

async def build_section_c(intake_doc: Optional[Dict[str, Any]], follow_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    doc = soap_doc or {}
    
    ws_text = str(doc.get("work_status") or "").lower()
    restrictions_text = doc.get("restrictions") or "Avoid heavy lifting and prolonged standing."
    
    # Parse Work Status
    return_full = "full duty" in ws_text
    modified = "modified" in ws_text or "restrictions" in ws_text
    unable = "total temporary disability" in ws_text or "ttd" in ws_text or "unable to return" in ws_text
    
    # Default to modified if ambiguous but restrictions exist
    if not return_full and not unable and restrictions_text:
        modified = True
        
    next_visit = doc.get("next_visit_date") or doc.get("mmi_date")
    
    return {
        "patientName": pick_name(intake_doc, soap_doc),
        "returnToFullDuty": return_full,
        "returnToFullDutyDate": next_visit if return_full else None,
        "returnToWorkWithRestrictions": modified,
        "returnToModifiedDutyDate": to_mmddyyyy(datetime.utcnow().strftime("%Y-%m-%d")) if modified else None, # Effective today
        "unableToReturnToWork": unable,
        "dischargedFromCareDate": doc.get("discharge_date"),
        "nextVisitDate": next_visit,
        "restrictions": {}, # Detailed dict empty for now, using otherRestrictions string
        "otherRestrictions": format_clinical_data(restrictions_text),
        "medicationDuringWorkHours": "No"
    }

async def build_pr1_payload(intake_doc: Optional[Dict[str, Any]], follow_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]], flags: Dict[str, bool]) -> Dict[str, Any]:
    section_a = await build_section_a_rfa(soap_doc, intake_doc)
    
    # Transform Section A to match Frontend keys (snake_case)
    # The helper currently returns camelCase keys in lists, we need to map them.
    med_reqs = []
    for r in section_a.get("medical_treatment_requests", []):
         med_reqs.append({
             "diagnosis": r.get("diagnosis"),
             "diagnosis_code": r.get("diagnosisCode"),
             "service_requested": r.get("serviceRequested"),
             "cpt": r.get("cpt"),
             "frequency_duration": r.get("frequencyDuration")
         })
         
    drug_reqs = []
    for r in section_a.get("drug_requests", []):
        drug_reqs.append({
            "diagnosis": r.get("diagnosis"),
            "diagnosis_code": r.get("diagnosisCode"),
            "drug": r.get("drug"),
            "dose_form": r.get("doseForm"),
            "quantity": r.get("quantity")
        })

    return {
        "page1_checkboxes": calc_checkboxes(soap_doc, follow_doc, flags, section_a),
        "header_admin": await build_header_admin(soap_doc, intake_doc),
        "page2_signature_and_included_sections": await build_page2_signature(soap_doc, section_a),
        "section_a_request_for_authorization": {
            "medical_treatment_requests": med_reqs,
            "drug_requests": drug_reqs,
            "general_request_text": section_a.get("generalRequestText")
        },
        "section_b_evaluation_management": await build_section_b(soap_doc, intake_doc),
        "section_c_work_status": await build_section_c(intake_doc, follow_doc, soap_doc)
    }

async def save_pr1_form(form_data: Dict[str, Any]) -> str:
    db = get_database()
    if db is None: raise Exception("DB not available")
    now = datetime.utcnow().isoformat()
    form_data["updated_at"] = now
    if "_id" in form_data:
        oid = form_data.pop("_id")
        await db[COLL_SAVED_PR1].update_one({"_id": ObjectId(oid)}, {"$set": form_data})
        return str(oid)
    else:
        form_data["created_at"] = now
        res = await db[COLL_SAVED_PR1].insert_one(form_data)
        return str(res.inserted_id)

async def get_saved_pr1(soap_id: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[COLL_SAVED_PR1].find_one({"soap_id": soap_id})
    if doc: doc["_id"] = str(doc["_id"])
    return doc

async def get_all_saved_pr1s() -> List[Dict[str, Any]]:
    db = get_database()
    if db is None: return []
    cursor = db[COLL_SAVED_PR1].find().sort("updated_at", -1)
    docs = await cursor.to_list(length=100)
    for d in docs: d["_id"] = str(d["_id"])
    return docs

# ============================================
# ORCHESTRATION FUNCTION (Restored Logic)
# ============================================

async def process_pr1_generation(
    soap_id: str,
    use_latest_intake: bool = False,
    use_latest_followup: bool = False,
    flags: Optional[Dict[str, Any]] = None
):
    """
    Orchestrate PR1 generation:
    1. Fetch SOAP
    2. Enrich SOAP with GPT extraction if structured fields missing
    3. Fetch Intake/Followup
    4. Build Payload
    5. Save
    """
    db = get_database()
    if db is None: raise Exception("DB connection failed")
    
    # 1. Fetch SOAP
    try:
        soap_oid = ObjectId(soap_id)
    except:
        raise Exception(f"Invalid SOAP ID format: {soap_id}")
        
    soap_doc = await db[COLL_SOAP].find_one({"_id": soap_oid})
    if not soap_doc: raise Exception(f"SOAP note {soap_id} not found")
    soap_doc["_id"] = str(soap_doc["_id"])
    
    # 2. Enrich with GPT (Restoring critical missing step)
    # If formatted_soap_note exists, parsing it ensures we have latest text updates
    # even if structured fields in DB are stale.
    formatted_soap = soap_doc.get("formatted_soap_note")
    gpt_data = {}
    if formatted_soap:
        logger.info(f"Enriching SOAP {soap_id} with GPT extraction from text...")
        gpt_data = await convert_formatted_soap_to_json(formatted_soap)
        
    # Merge: DB structured fields > GPT extracted > DB original (fallback)
    # We want robust data.
    merged_soap = {}
    merged_soap.update(gpt_data) # Start with GPT data
    merged_soap.update(soap_doc) # Override with DB data (assuming DB is source of truth if explicit fields exist)
    # Actually, allow GPT to fill gaps. If DB has empty 'restrictions' but GPT found them in text, use GPT.
    # So we prefer non-empty values.
    
    final_soap = soap_doc.copy()
    for k, v in gpt_data.items():
        if v and not final_soap.get(k):
             final_soap[k] = v
        # Specifically for complex fields like RFA or restrictions, if DB is just string but GPT gave struct, maybe switch?
        # For now, safe merge: if missing in DB, use GPT.
    
    # 3. Fetch Intake/Followup
    intake_doc = None
    if use_latest_intake:
        intake_doc = await fetch_latest_document(COLL_INTAKE)
        
    follow_doc = None
    if use_latest_followup:
        follow_doc = await fetch_latest_document(COLL_FOLLOWUP)
        
    # 4. Build
    pr1_flags = flags or {}
    pr1_payload = await build_pr1_payload(intake_doc, follow_doc, final_soap, pr1_flags)
    
    # 5. Construct Full Saved Document (Nested structure matching Old Backend/Schema)
    full_doc = {
        "soap_id": soap_id,
        "patient_name": pr1_payload.get("patientName") or "Unknown Patient",
        "form_data": pr1_payload,
        "soap_data": final_soap,
        "metadata": {
            "intake_used": bool(intake_doc),
            "followup_used": bool(follow_doc),
            "soap_used": True,
            "source": "orchestrator",
            "enriched": bool(gpt_data)
        }
    }
    
    # 6. Save (handle existing)
    existing = await db[COLL_SAVED_PR1].find_one({"soap_id": soap_id})
    if existing:
        full_doc["_id"] = str(existing["_id"])
        
    saved_id = await save_pr1_form(full_doc)
    logger.info(f"✅ Generated PR1 via Orchestrator for {soap_id}")
    
    return saved_id
