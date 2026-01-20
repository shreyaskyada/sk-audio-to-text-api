import logging
from datetime import datetime
from typing import Dict, Optional, List
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

SOAP_NOTES_COLLECTION = 'soap_notes'

async def save_soap_note_to_db(soap_data: dict) -> Dict:
    """Save a generated SOAP note to MongoDB"""
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        
        soap_doc = {
            "transcription": soap_data.get("transcription", ""),
            "corrected_transcription": soap_data.get("corrected_transcription", ""),
            "transcription_id": soap_data.get("transcription_id"),
            "subjective": soap_data.get("subjective", ""),
            "objective": soap_data.get("objective", ""),
            "assessment": soap_data.get("assessment", ""),
            "plan": soap_data.get("plan", ""),
            "formatted_soap_note": soap_data.get("formatted_soap_note", ""),
            "patient_info": soap_data.get("patient_info"),
            "userId": soap_data.get("userId"),
            "date_of_service": soap_data.get("date_of_service"),
            "location": soap_data.get("location"),
            "reason_for_visit": soap_data.get("reason_for_visit"),
            "custom_prompts": {
                "system_prompt": soap_data.get("system_prompt"),
                "user_prompt_template": soap_data.get("user_prompt_template")
            } if soap_data.get("system_prompt") or soap_data.get("user_prompt_template") else None,
            "format": soap_data.get("format", "markdown"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db[SOAP_NOTES_COLLECTION].insert_one(soap_doc)
        soap_doc["_id"] = str(result.inserted_id)
        logger.info(f"✅ SOAP note saved: {soap_doc['_id']}")
        return soap_doc
    except Exception as e:
        logger.error(f"❌ Error saving SOAP note: {e}")
        raise

async def create_pending_soap_note(transcription_id: str, user_id: Optional[str] = None) -> Dict:
    """Create a placeholder SOAP note with pending status"""
    try:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
            
        doc = {
            "transcription_id": transcription_id,
            "userId": user_id,
            "status": "pending",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db[SOAP_NOTES_COLLECTION].insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        logger.info(f"⏳ Created pending SOAP note: {doc['_id']}")
        return doc
    except Exception as e:
        logger.error(f"Error creating pending SOAP note: {e}")
        raise

async def get_soap_note_by_id(soap_note_id: str) -> Optional[Dict]:
    """Retrieve a SOAP note by its ID"""
    try:
        db = get_database()
        if db is None: return None
        
        try:
            object_id = ObjectId(soap_note_id)
        except Exception:
            return None
        
        doc = await db[SOAP_NOTES_COLLECTION].find_one({"_id": object_id})
        if doc:
            doc["_id"] = str(doc["_id"])
            return doc
        return None
    except Exception as e:
        logger.error(f"Error retrieving SOAP note: {e}")
        raise

async def get_all_soap_notes(limit: int = 100, skip: int = 0) -> List[Dict]:
    """Get all SOAP notes with pagination"""
    try:
        db = get_database()
        if db is None: return []
        
        docs = await db[SOAP_NOTES_COLLECTION].find().sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return docs
    except Exception as e:
        logger.error(f"Error getting all SOAP notes: {e}")
        raise

async def update_soap_note_in_db(soap_note_id: str, update_data: dict) -> bool:
    """Update a SOAP note in database"""
    try:
        db = get_database()
        if db is None: return False
        
        try:
            object_id = ObjectId(soap_note_id)
        except Exception:
            return False
        
        update_data["updated_at"] = datetime.utcnow()
        result = await db[SOAP_NOTES_COLLECTION].update_one(
            {"_id": object_id},
            {"$set": update_data}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating SOAP note: {e}")
        raise

async def delete_soap_note_from_db(soap_note_id: str) -> bool:
    """Delete a SOAP note"""
    try:
        db = get_database()
        if db is None: return False
        
        try:
            object_id = ObjectId(soap_note_id)
        except Exception:
            return False
        
        result = await db[SOAP_NOTES_COLLECTION].delete_one({"_id": object_id})
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Error deleting SOAP note: {e}")
        raise

async def get_soap_stats() -> Dict:
    """Get statistics about SOAP notes"""
    try:
        db = get_database()
        if db is None: return {}
        
        total = await db[SOAP_NOTES_COLLECTION].count_documents({})
        custom_prompts_count = await db[SOAP_NOTES_COLLECTION].count_documents({
            "custom_prompts": {"$ne": None}
        })
        recent = await db[SOAP_NOTES_COLLECTION].find().sort("created_at", -1).limit(10).to_list(10)
        for doc in recent:
            doc["_id"] = str(doc["_id"])
        
        return {
            "total_soap_notes": total,
            "soap_notes_with_custom_prompts": custom_prompts_count,
            "soap_notes_with_default_prompts": total - custom_prompts_count,
            "recent_soap_notes": recent
        }
    except Exception as e:
        logger.error(f"Error getting SOAP notes statistics: {e}")
        raise

# ==========================================
# Legacy Helper Functions (Ported from Old Backend)
# ==========================================

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
