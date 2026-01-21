
import logging
from typing import Optional
from bson import ObjectId
from app.mongodb import get_database

logger = logging.getLogger(__name__)

async def fetch_intake_form_by_id(intake_id: str) -> Optional[dict]:
    """Fetch intake form from MongoDB by ID"""
    try:
        db = get_database()
        if db is None:
            logger.warning("Database connection not available")
            return None
        
        collection = db['intake_forms']
        intake_doc = await collection.find_one({"_id": ObjectId(intake_id)})
        
        if intake_doc:
            intake_doc["_id"] = str(intake_doc["_id"])
            logger.info(f"✅ Fetched intake form with ID: {intake_doc['_id']}")
            return intake_doc
        return None
    except Exception as e:
        logger.error(f"Error fetching intake form by ID: {e}")
        return None


async def fetch_latest_intake_form() -> Optional[dict]:
    """Fetch the latest intake form from MongoDB"""
    try:
        db = get_database()
        if db is None:
            logger.warning("Database connection not available")
            return None
        
        collection = db['intake_forms']
        latest_doc = await collection.find_one(sort=[("created_at", -1)])
        
        if latest_doc:
            latest_doc["_id"] = str(latest_doc["_id"])
            logger.info(f"✅ Fetched latest intake form with ID: {latest_doc['_id']}")
            return latest_doc
        return None
        return None
    except Exception as e:
        logger.error(f"Error fetching latest intake form: {e}")
        return None


async def save_intake_form(form_data: dict) -> str:
    """
    Save a new intake form to MongoDB.
    Returns the document ID as a string.
    """
    try:
        db = get_database()
        if db is None:
            raise Exception("Database connection not available")
        
        collection = db['intake_forms']
        
        # Ensure timestamp is set
        if "created_at" not in form_data:
            from datetime import datetime
            form_data["created_at"] = datetime.utcnow()
        
        result = await collection.insert_one(form_data)
        logger.info(f"✅ Intake form saved with ID: {result.inserted_id}")
        
        return str(result.inserted_id)
        
    except Exception as e:
        logger.error(f"Error saving intake form: {e}")
        raise


def extract_intake_form_values(intake_doc: Optional[dict]) -> dict:
    """Extract intake form values for direct injection into SOAP note"""
    if not intake_doc:
        return {}
    
    values = {}
    
    # Extract Past Medical History (section_e.comorbidities)
    section_e = intake_doc.get("section_e", {})
    comorbidities = section_e.get("comorbidities", [])
    if comorbidities and len(comorbidities) > 0:
        comorbidities = [c for c in comorbidities if c and str(c).strip()]
        if comorbidities:
            values["pmh"] = ", ".join(comorbidities)
    
    # Extract Medications (section_e.current_medications)
    current_medications = section_e.get("current_medications", "")
    logger.info(f"🔍 Extracting medications from intake form. Raw value: {repr(current_medications)}")
    if current_medications and current_medications.strip():
        meds_text = current_medications.strip()
        # Remove quotes
        meds_text = meds_text.replace('"', '').replace("'", '').replace('"', '').replace("'", '')
        meds_text = meds_text.strip('"').strip("'").strip('"').strip("'")
        if meds_text:
            values["medications"] = meds_text
            logger.info(f"✅ Extracted medications: {meds_text}")
        else:
            logger.warning(f"⚠️ Medications text became empty after cleaning")
    else:
        logger.warning(f"⚠️ No medications found in intake form (section_e.current_medications)")
    
    # Extract Social/Occupational History (section_b)
    section_b = intake_doc.get("section_b", {})
    if section_b:
        soc_hist_parts = []
        employer_name = section_b.get("employer_name", "").strip()
        occupation = section_b.get("occupation", "").strip()
        work_description = section_b.get("work_description", [])
        
        if employer_name:
            soc_hist_parts.append(f"Employer: {employer_name}")
        if occupation:
            soc_hist_parts.append(f"Occupation: {occupation}")
        if work_description and isinstance(work_description, list) and len(work_description) > 0:
            work_desc_text = ", ".join([str(w) for w in work_description if w])
            if work_desc_text:
                soc_hist_parts.append(f"Work Description: {work_desc_text}")
        
        if soc_hist_parts:
            values["social_history"] = "; ".join(soc_hist_parts)
    
    return values


def inject_intake_data_into_soap(soap_note: str, intake_values: dict) -> str:
    """Post-process SOAP note to inject intake form data, replacing 'As per chart'"""
    if not intake_values:
        return soap_note
    
    import re
    result = soap_note
    changes_made = []
    
    # Replace Past Medical History
    if "pmh" in intake_values:
        pmh_before = result
        # More flexible patterns - match with or without newlines, various spacing
        pmh_patterns = [
            (r'(Past Medical History:\s*\n?\s*=\s*)(As per chart)', intake_values["pmh"]),
            (r'(Past Medical History:\s*\n?\s*=\s*)(\[As per chart\])', intake_values["pmh"]),
            (r'(Past Medical History:\s*\n?\s*=\s*)(As per chart\.)', intake_values["pmh"]),
            (r'(Past Medical History:\s*\n?\s*=\s*)(\[As per chart\.\])', intake_values["pmh"]),
            (r'(Past Medical History:\s*\n?\s*=\s*)(As\s+per\s+chart)', intake_values["pmh"]),
        ]
        for pattern, replacement_text in pmh_patterns:
            result = re.sub(pattern, lambda m: m.group(1) + replacement_text, result, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if pmh_before != result:
            changes_made.append("PMH")
            logger.info(f"✅ Replaced PMH 'As per chart' with: {intake_values['pmh']}")
    
    # Replace Medications - more aggressive pattern matching
    if "medications" in intake_values:
        med_before = result
        # More flexible patterns - match with or without newlines, various spacing
        med_patterns = [
            (r'(Medications:\s*\n?\s*=\s*)(As per chart)', intake_values["medications"]),
            (r'(Medications:\s*\n?\s*=\s*)(\[As per chart\])', intake_values["medications"]),
            (r'(Medications:\s*\n?\s*=\s*)(As per chart\.)', intake_values["medications"]),
            (r'(Medications:\s*\n?\s*=\s*)(\[As per chart\.\])', intake_values["medications"]),
            (r'(Medications:\s*\n?\s*=\s*)(As\s+per\s+chart)', intake_values["medications"]),
        ]
        for pattern, replacement_text in med_patterns:
            # Use lambda to properly insert replacement text without escaping issues
            result = re.sub(pattern, lambda m: m.group(1) + replacement_text, result, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if med_before != result:
            changes_made.append("Medications")
            logger.info(f"✅ Replaced Medications 'As per chart' with: {intake_values['medications']}")
        else:
            # Log what we're looking for to help debug
            logger.warning(f"⚠️ Could not find 'Medications: ... = As per chart' pattern to replace")
            logger.warning(f"   Medication text to inject: {intake_values['medications']}")
            # Try to find what the actual format is
            med_match = re.search(r'Medications:.*?=\s*(.*?)(?=\n|$)', result, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if med_match:
                logger.warning(f"   Found Medications section with: '{med_match.group(1).strip()}'")
            # Also try a simpler direct replacement as fallback
            if "As per chart" in result and "Medications:" in result:
                # Find the Medications section and replace directly
                med_section = re.search(r'(Medications:\s*\n?\s*=\s*)(As per chart)', result, re.IGNORECASE | re.MULTILINE)
                if med_section:
                    result = result[:med_section.start()] + med_section.group(1) + intake_values["medications"] + result[med_section.end():]
                    changes_made.append("Medications (fallback)")
                    logger.info(f"✅ Replaced Medications using fallback method: {intake_values['medications']}")
    
    # Replace Social/Occupational History
    if "social_history" in intake_values:
        soc_before = result
        soc_patterns = [
            (r'(Social\s*/\s*Occupational\s*History:\s*\n?\s*=\s*)(As per chart)', intake_values["social_history"]),
            (r'(Social\s*/\s*Occupational\s*History:\s*\n?\s*=\s*)(\[As per chart\])', intake_values["social_history"]),
            (r'(Social\s*/\s*Occupational\s*History:\s*\n?\s*=\s*)(As per chart\.)', intake_values["social_history"]),
            (r'(Social\s*/\s*Occupational\s*History:\s*\n?\s*=\s*)(\[As per chart\.\])', intake_values["social_history"]),
            (r'(Social\s*/\s*Occupational\s*History:\s*\n?\s*=\s*)(As\s+per\s+chart)', intake_values["social_history"]),
        ]
        for pattern, replacement_text in soc_patterns:
            result = re.sub(pattern, lambda m: m.group(1) + replacement_text, result, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if soc_before != result:
            changes_made.append("Social/Occupational History")
            logger.info(f"✅ Replaced Social/Occupational History 'As per chart' with: {intake_values['social_history']}")
    
    if changes_made:
        logger.info(f"🔧 Post-processing complete. Changes made to: {', '.join(changes_made)}")
    else:
        logger.warning("⚠️ Post-processing found no 'As per chart' patterns to replace")
    
    return result


def format_intake_form_data_for_prompt(intake_doc: Optional[dict]) -> str:
    """Format intake form data for inclusion in SOAP prompt"""
    if not intake_doc:
        return ""

    logger.info(f"🔍 Formatting intake form data for prompt: {intake_doc}")
    
    sections = []
    
    # Extract Past Medical History (section_e.comorbidities)
    section_e = intake_doc.get("section_e", {})
    comorbidities = section_e.get("comorbidities", [])
    if comorbidities and len(comorbidities) > 0:
        # Filter out empty strings
        comorbidities = [c for c in comorbidities if c and str(c).strip()]
        if comorbidities:
            pmh_text = ", ".join(comorbidities) if isinstance(comorbidities, list) else str(comorbidities)
            sections.append(f"**Past Medical History (from intake form):** {pmh_text}")
    
    # Extract Medications (section_e.current_medications)
    current_medications = section_e.get("current_medications", "")
    if current_medications and current_medications.strip():
        # Remove quotes if present (handles both straight and curly quotes)
        meds_text = current_medications.strip()
        # Remove leading/trailing quotes (straight and curly)
        meds_text = meds_text.strip('"').strip("'").strip('"').strip("'")
        meds_text = meds_text.strip('"').strip('"').strip('"').strip('"')
        # Remove any remaining quote characters
        meds_text = meds_text.replace('"', '').replace("'", '').replace('"', '').replace("'", '')
        if meds_text:
            sections.append(f"**Current Medications (from intake form):** {meds_text}")
    
    # Extract Social/Occupational History (section_b)
    section_b = intake_doc.get("section_b", {})
    if section_b:
        soc_hist_parts = []
        employer_name = section_b.get("employer_name", "").strip()
        occupation = section_b.get("occupation", "").strip()
        work_description = section_b.get("work_description", [])
        
        if employer_name:
            soc_hist_parts.append(f"Employer: {employer_name}")
        if occupation:
            soc_hist_parts.append(f"Occupation: {occupation}")
        if work_description and isinstance(work_description, list) and len(work_description) > 0:
            work_desc_text = ", ".join([str(w) for w in work_description if w])
            if work_desc_text:
                soc_hist_parts.append(f"Work Description: {work_desc_text}")
        
        if soc_hist_parts:
            sections.append(f"**Social/Occupational History (from intake form):** {'; '.join(soc_hist_parts)}")
    
    if sections:
        return "\n\n=== PATIENT INTAKE FORM DATA (USE THIS DATA - DO NOT USE 'AS PER CHART') ===\n" + "\n".join(sections) + "\n=== END INTAKE FORM DATA ===\n"
    return ""
