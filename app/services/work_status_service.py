from datetime import datetime
from bson import ObjectId
from typing import Any, Dict, Optional, List
import json
import logging
import re
from fastapi import HTTPException

from app.models.work_status_form import WorkStatusForm
from app.models.pr1_models import PR1GenerateRequest
from app.mongodb import get_database
# Import service logic
from app.services.pr1_service import extract_work_status_orchestrator
from app.services.text_extraction import (
    extract_weight_from_text,
    extract_work_status_section, 
    extract_date_from_text,
    extract_body_parts_fallback
)

logger = logging.getLogger(__name__)

# Configuration
WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'


def serialize_mongodb_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert MongoDB document to JSON-serializable format
    Converts ObjectId and datetime objects to strings
    """
    if doc is None:
        return None
    
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            serialized[key] = str(value)
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, dict):
            serialized[key] = serialize_mongodb_doc(value)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_mongodb_doc(item) if isinstance(item, dict) else
                str(item) if isinstance(item, (ObjectId, datetime)) else item
                for item in value
            ]
        else:
            serialized[key] = value
    
    return serialized


# ============================================
# WORK STATUS FORMS API ENDPOINTS
# ============================================

async def extract_work_status_from_soap_service(
    soap_id: str,
    use_latest_intake: bool = False,
    use_latest_followup: bool = False,
    flags: Optional[Dict[str, Any]] = None
):
    """
    Service function to extract work status data from SOAP note.
    Wraps process_work_status_generation.
    """
    return await process_work_status_generation(
        soap_id=soap_id,
        use_latest_intake=use_latest_intake,
        use_latest_followup=use_latest_followup,
        flags=flags
    )


async def process_work_status_generation(
    soap_id: str,
    use_latest_intake: bool = False,
    use_latest_followup: bool = False,
    flags: Dict[str, Any] = None
):
    """
    Service function to extract work status data from SOAP note.
    """
    print(f"\n{'='*80}\n🔄 WORK STATUS GENERATION CALLED FOR SOAP: {soap_id}\n{'='*80}\n", flush=True)
    logger.info(f"🔄 Starting Work Status generation for SOAP ID: {soap_id}")
    logger.info(f"   Parameters: use_latest_intake={use_latest_intake}, use_latest_followup={use_latest_followup}")
    try:
        db = get_database()
        if db is not None:
            saved_form = await db['work_status_forms'].find_one({"soap_id": soap_id})
            if saved_form and saved_form.get("status") != "pending":
                logger.info(f"✅ Found saved Work Status form for SOAP ID: {soap_id}")
                return {"work_status_data": serialize_mongodb_doc(saved_form), "metadata": {"source": "cache"}}
            
            if saved_form:
                 await db['work_status_forms'].update_one(
                     {"_id": saved_form["_id"]},
                     {"$set": {"status": "pending", "updated_at": datetime.utcnow()}}
                 )
            else:
                 await db['work_status_forms'].insert_one({
                     "soap_id": soap_id,
                     "status": "pending",
                     "created_at": datetime.utcnow(),
                     "updated_at": datetime.utcnow()
                 })

        # Use existing extraction engine
        payload = PR1GenerateRequest(
            soap_id=soap_id,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            flags=flags
        )
        
        # Get base extraction result
        result = await extract_work_status_orchestrator(payload)
        
        # --- POST-PROCESSING FIX for Work Status Form ---

        if result and "work_status_data" in result:
            ws_data = result["work_status_data"]
            restrictions = ws_data.get("functionalRestrictions", {})
            lifting = restrictions.get("liftingPushingPulling", {})
            other_text = restrictions.get("otherRestrictions", "") or ""
            
            # 1. Fetch RAW SOAP note for aggressive validation
            full_raw_text = ""
            try:
                db = get_database()
                if db is not None:
                    soap_doc = await db['soap_notes'].find_one({"_id": ObjectId(soap_id)})
                    if soap_doc:
                        raw_texts = []
                        if soap_doc.get("formatted_soap_note"):
                            raw_texts.append(str(soap_doc.get("formatted_soap_note")))
                        if soap_doc.get("subjective"):
                            raw_texts.append(str(soap_doc.get("subjective")))
                        if soap_doc.get("history") or soap_doc.get("historyOfPresentIllness") or soap_doc.get("history_of_present_illness"):
                            raw_texts.append(str(soap_doc.get("history") or soap_doc.get("historyOfPresentIllness") or soap_doc.get("history_of_present_illness")))
                        if soap_doc.get("plan"):
                            raw_texts.append(str(soap_doc.get("plan")))
                        if soap_doc.get("work_status"): # Field user edits manually in UI
                            raw_texts.append(str(soap_doc.get("work_status")))
                        if soap_doc.get("workStatus"):
                            raw_texts.append(str(soap_doc.get("workStatus")))
                        if soap_doc.get("transcription"):
                            raw_texts.append(str(soap_doc.get("transcription")))
                        full_raw_text = "\n".join(raw_texts)
            except Exception as db_e:
                logger.error(f"Failed to fetch raw SOAP: {db_e}")

            # 2. Focus on Work Status / Restrictions Section
            # Prefer 'other_text' from AI, or extract from raw text
            target_text = other_text
            if full_raw_text:
                work_status_section = extract_work_status_section(full_raw_text)
                target_text = target_text + "\n" + work_status_section
            
            target_text_lower = target_text.lower()

            # ... (Existing selections code) ...
            
            # 4. FIX: Date of Injury (DOI) Aggressive Extraction
            # If dateOfInjury is missing, try to find it in the text
            emp_info = ws_data.get("employeeInfo", {})
            if not emp_info.get("dateOfInjury"):
                # Search primarily in the first chunk of text (Header/History) to avoid False Positives
                search_scope = full_raw_text[:3000] if len(full_raw_text) > 3000 else full_raw_text
                
                # Patterns to look for
                doi_patterns = [
                    r'(?:Date of Injury|DOI|Injury Date|Date of Acc|Injury|Date of Accident)[^0-9]*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                    r'(?:Date of Injury|DOI|Injury Date|Date of Acc|Injury|Date of Accident)[^0-9]*?((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})',
                    # Narrative: "injury on 01/14/2026", "accident on 1-1-26"
                    r'(?:injury|accident|incident|onset)\s+(?:occurred|sustained|happened)?\s*(?:on)?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
                ]
                
                for pat in doi_patterns:
                    match = re.search(pat, search_scope, re.IGNORECASE)
                    if match:
                        raw_date = match.group(1)
                        try:
                            # Numeric Parsing
                            if re.search(r'\d+[/-]\d+', raw_date):
                                parts = re.split(r'[/-]', raw_date)
                                if len(parts) == 3:
                                    p0, p1, year = parts[0], parts[1], parts[2]
                                    if len(year) == 2: year = "20" + year
                                    emp_info["dateOfInjury"] = f"{year}-{p0.zfill(2)}-{p1.zfill(2)}"
                                    logger.info(f"WorkStatusForm Fix: Extracted Date of Injury (Numeric): {emp_info['dateOfInjury']}")
                                    break
                            # Textual Parsing
                            else:
                                clean_date = raw_date.replace(".", "").replace(",", "")
                                for fmt in ["%b %d %Y", "%B %d %Y"]:
                                     try:
                                         dt_obj = datetime.strptime(clean_date, fmt)
                                         emp_info["dateOfInjury"] = dt_obj.strftime("%Y-%m-%d")
                                         break
                                     except:
                                         continue
                                if emp_info.get("dateOfInjury"): break
                        except:
                            continue

                # Fallback: Check for "injury ... today" or "sustained ... today"
                if not emp_info.get("dateOfInjury"):
                     today_match = re.search(r'(?:injury|accident|sustained).{0,50}\s+today', search_scope, re.IGNORECASE)
                     if today_match and emp_info.get("dateOfEvaluation"):
                             emp_info["dateOfInjury"] = emp_info.get("dateOfEvaluation")
                             logger.info(f"WorkStatusForm Fix: Inferred DOI from 'injury today' -> {emp_info['dateOfInjury']}")
                
                # Double Fallback: If "Acute" injury is mentioned, and no date found, assume DOI = Date of Evaluation
                if not emp_info.get("dateOfInjury"):
                     acute_match = re.search(r'(?:acute|new)\s+(?:injury|onset)', search_scope, re.IGNORECASE)
                     if acute_match and emp_info.get("dateOfEvaluation"):
                         emp_info["dateOfInjury"] = emp_info.get("dateOfEvaluation")
                         logger.info(f"WorkStatusForm Fix: Inferred DOI from 'Acute Injury' context -> {emp_info['dateOfInjury']}")

            # 5. FIX: Body Parts Injured Aggressive Extraction
            if not emp_info.get("bodyPartsInjured") or emp_info.get("bodyPartsInjured") == "N/A":
                # Try simple regex extraction from raw text
                body_part = extract_body_parts_fallback(full_raw_text)
                if body_part:
                    emp_info["bodyPartsInjured"] = body_part
                    logger.info(f"WorkStatusForm Fix: Extracted Body Part from clean fallback: {body_part}")
                else:
                    # Try finding common body parts in text if fallback specific format fails
                    common_parts = ["Lumbar Spine", "Lower Back", "Back", "Cervical Spine", "Neck", 
                                   "Left Shoulder", "Right Shoulder", "Shoulder", "Left Knee", "Right Knee", "Knee",
                                   "Left Wrist", "Right Wrist", "Wrist", "Left Ankle", "Right Ankle", "Ankle"]
                    
                    found_parts = []
                    # Limit search range for efficiency
                    search_scope = full_raw_text[:2000] if full_raw_text else ""
                    
                    for part in common_parts:
                        # Simple case-insensitive check
                        if re.search(r'\b' + re.escape(part) + r'\b', search_scope, re.IGNORECASE):
                             # Special handling to avoid "No Back Pain"
                             # Check negative assertion isn't immediately before
                             if not re.search(r'(?:no|not|denies)\s+' + re.escape(part), search_scope, re.IGNORECASE):
                                  found_parts.append(part)
                    
                    if found_parts:
                        # De-duplicate generic vs specific (e.g. if "Lumbar Spine" and "Back" both found, prefer L-Spine)
                        # Simple approach: If any specific found, ignore generic "Back"
                        final_parts = list(set(found_parts)) # dedupe
                        emp_info["bodyPartsInjured"] = ", ".join(final_parts)
                        logger.info(f"WorkStatusForm Fix: Inferred Body Parts from keywords: {emp_info['bodyPartsInjured']}")

            # 6. FIX: Employee Name Fallback
            if not emp_info.get("employeeName"):
                 # Try extract from raw text if SOAP structure failed
                 name_match = re.search(r'(?:Patient Name|Patient|Name):\s*([A-Za-z\s\.]+)', full_raw_text[:1000], re.IGNORECASE)
                 if name_match:
                     extracted_name = name_match.group(1).strip()
                     # Clean up
                     if len(extracted_name) < 40 and "Date" not in extracted_name:
                         emp_info["employeeName"] = extracted_name
                         logger.info(f"WorkStatusForm Fix: Extracted Employee Name from raw text: {extracted_name}")

            # 7. FIX: Date of Evaluation (DOE)
            if not emp_info.get("dateOfEvaluation"):
                # Try from SOAP metadata first
                if soap_doc and soap_doc.get("date_of_service"):
                     emp_info["dateOfEvaluation"] = soap_doc.get("date_of_service")
                     logger.info(f"WorkStatusForm Fix: Populated DOE from SOAP metadata: {emp_info['dateOfEvaluation']}")
                
                # Try extraction from text if still missing
                if not emp_info.get("dateOfEvaluation") and full_raw_text:
                     # Look for "Date of Visit: ..." or "Date: ..." in the first 1000 chars
                     visit_date_match = re.search(r'(?:Date of Visit|Date of Service|Visit Date|Date):\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', full_raw_text[:1000], re.IGNORECASE)
                     if visit_date_match:
                         raw_date = visit_date_match.group(1)
                         try:
                             parts = re.split(r'[/-]', raw_date)
                             if len(parts) == 3:
                                 p0, p1, year = parts[0], parts[1], parts[2]
                                 if len(year) == 2: year = "20" + year
                                 # Standardize to YYYY-MM-DD (Assume MM/DD/YYYY)
                                 emp_info["dateOfEvaluation"] = f"{year}-{p0.zfill(2)}-{p1.zfill(2)}"
                                 logger.info(f"WorkStatusForm Fix: Extracted DOE from text: {emp_info['dateOfEvaluation']}")
                         except Exception as e:
                             logger.warning(f"Failed to parse DOE from text: {e}")

                # Final fallback to today's date if we just generated it
                if not emp_info.get("dateOfEvaluation"):
                    emp_info["dateOfEvaluation"] = datetime.utcnow().strftime("%Y-%m-%d")
                    logger.info(f"WorkStatusForm Fix: Defaulted DOE to today: {emp_info['dateOfEvaluation']}")


        # Save Completed
        logger.info(f"📝 Work Status extraction result type: {type(result)}")
        logger.info(f"📝 Has work_status_data: {'work_status_data' in result if result else False}")
        
        if db is not None and result and "work_status_data" in result:
             logger.info(f"💾 Saving Work Status form to database for SOAP {soap_id}")
             form_data = result["work_status_data"]
             form_data["soap_id"] = soap_id
             form_data["status"] = "completed"
             form_data["updated_at"] = datetime.utcnow()
             
             await db['work_status_forms'].update_one(
                 {"soap_id": soap_id},
                 {"$set": form_data},
                 upsert=True
             )
             logger.info(f"✅ Work Status form saved successfully for SOAP {soap_id}")
        else:
             logger.warning(f"⚠️ Work Status form NOT saved - db={db is not None}, result={result is not None}, has_data={'work_status_data' in result if result else False}")

        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in work-status-form extraction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract date: {str(e)}")









async def create_work_status_form_service(data: WorkStatusForm):
    """
    Create a new work status form
    
    **Parameters:**
    - data: WorkStatusForm object with all sections:
        - employeeInfo: Employee information
        - workStatus: Work status selection and dates
        - functionalRestrictions: All functional restrictions
        - providerInfo: Provider information
    
    **Returns:**
    - status: Success status
    - message: Success message
    - document_id: MongoDB document ID of the saved work status form
    
    **Example Request:**
    ```json
    {
        "employeeInfo": {
            "employeeName": "John Doe",
            "claimNumber": "WC-12345",
            "dateOfInjury": "2024-01-15",
            "dateOfEvaluation": "2024-11-10",
            "bodyPartsInjured": "Left shoulder",
            "nextFollowUpAppointment": "2024-11-20"
        },
        "workStatus": {
            "status": "modifiedDuty",
            "modifiedDutyFrom": "2024-11-10",
            "modifiedDutyTo": "2024-12-10"
        },
        "functionalRestrictions": {
            "liftingPushingPulling": {
                "noLiftingOver": true,
                "weightLimit": "10"
            },
            "upperExtremity": {
                "noAboveShoulderReaching": true,
                "aboveShoulderRight": true
            },
            ...
        },
        "providerInfo": {
            "providerName": "Dr. Smith",
            "clinic": "Ortho Clinic",
            "phone": "555-1234",
            "signature": "Dr. Smith",
            "date": "2024-11-10"
        }
    }
    ```
    """
    try:
        # Get the database (using existing database connection)
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        # Convert Pydantic model to dict and add timestamp
        form_data = data.model_dump()
        now = datetime.utcnow()
        
        # Check if a form with this soap_id already exists
        soap_id = form_data.get("soap_id")
        if soap_id:
            existing = await collection.find_one({"soap_id": soap_id})
            if existing:
                form_data["updated_at"] = now
                form_data["created_at"] = existing.get("created_at", now)
                await collection.update_one(
                    {"soap_id": soap_id},
                    {"$set": form_data}
                )
                logger.info(f"✅ Updated work status form for SOAP ID: {soap_id}")
                return {
                    "status": "success",
                    "message": "Work status form updated successfully.",
                    "document_id": str(existing["_id"])
                }

        # Create new form
        form_data["created_at"] = now
        form_data["updated_at"] = now
        result = await collection.insert_one(form_data)
        
        logger.info(f"✅ New work status form saved with ID: {result.inserted_id}")
        
        return {
            "status": "success",
            "message": "Work status form saved successfully.",
            "document_id": str(result.inserted_id)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save work status form: {str(e)}"
        )


async def get_latest_work_status_form_service():
    """
    Get the most recently created work status form
    
    **Returns:**
    - Complete work status form document with all fields
    - Includes document_id and created_at timestamp
    - Returns 404 if no work status forms exist
    
    **Example Response:**
    ```json
    {
        "_id": "507f1f77bcf86cd799439011",
        "document_id": "507f1f77bcf86cd799439011",
        "employeeInfo": {
            "employeeName": "John Doe",
            "claimNumber": "WC-12345",
            ...
        },
        "workStatus": { ... },
        "functionalRestrictions": { ... },
        "providerInfo": { ... },
        "created_at": "2024-11-10T17:22:16.436963"
    }
    ```
    """
    try:
        # Get the database (using existing database connection)
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        # Find the latest work status form (sorted by created_at descending, limit 1)
        latest_form = await collection.find_one(
            sort=[("created_at", -1)]
        )
        
        if latest_form is None:
            raise HTTPException(
                status_code=404,
                detail="No work status forms found"
            )
        
        # Serialize MongoDB document (convert ObjectId and datetime to strings)
        serialized_form = serialize_mongodb_doc(latest_form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved latest work status form with ID: {serialized_form.get('_id')}")
        
        return serialized_form
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest work status form: {str(e)}"
        )


async def get_all_saved_work_status_soap_ids_service():
    """
    Retrieve all soap_ids that have a saved Work Status form.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        # Consistent collection name from the top of the file
        collection = db['work_status_forms']
        
        # Get all documents but only the soap_id field
        cursor = collection.find({}, {"soap_id": 1, "_id": 0})
        saved_forms = await cursor.to_list(length=1000)
        
        soap_ids = [str(doc["soap_id"]) for doc in saved_forms if doc.get("soap_id")]
        
        return {
            "status": "success",
            "soap_ids": soap_ids
        }
            
    except Exception as e:
        logger.error(f"Error retrieving saved Work Status soap_ids: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve saved Work Status soap_ids: {str(e)}")


async def get_saved_work_status_form_service(soap_id: str):
    """
    Retrieve a saved Work Status form by its associated SOAP ID.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        form = await collection.find_one({"soap_id": soap_id})
        
        if not form:
            raise HTTPException(
                status_code=404,
                detail=f"No saved Work Status form found for SOAP ID: {soap_id}"
            )
        
        # Serialize MongoDB document
        serialized_form = serialize_mongodb_doc(form)
        
        return {
            "status": "success",
            "data": serialized_form
        }
            
    except Exception as e:
        logger.error(f"Error retrieving saved Work Status form: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve saved Work Status form: {str(e)}")


async def get_work_status_form_by_id(form_id: str):
    """
    Get a specific work status form by ID
    
    **Parameters:**
    - form_id: MongoDB document ID of the work status form
    
    **Returns:**
    - Complete work status form document with all fields
    - Includes document_id and created_at timestamp
    - Returns 404 if form not found
    
    **Example Response:**
    ```json
    {
        "_id": "507f1f77bcf86cd799439011",
        "document_id": "507f1f77bcf86cd799439011",
        "employeeInfo": { ... },
        "workStatus": { ... },
        "functionalRestrictions": { ... },
        "providerInfo": { ... },
        "created_at": "2024-11-10T17:22:16.436963"
    }
    ```
    """
    try:
        # Get the database
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[WORK_STATUS_FORMS_COLLECTION]
        
        # Validate ObjectId format
        try:
            object_id = ObjectId(form_id)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid form ID format: {form_id}"
            )
        
        # Find the form by ID
        form = await collection.find_one({"_id": object_id})
        
        if form is None:
            raise HTTPException(
                status_code=404,
                detail=f"Work status form not found with ID: {form_id}"
            )
        
        # Serialize MongoDB document
        serialized_form = serialize_mongodb_doc(form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved work status form with ID: {form_id}")
        
        return serialized_form
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve work status form: {str(e)}"
        )

