from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import JSONResponse
from datetime import datetime
from bson import ObjectId
from typing import Any, Dict, Optional
import json
import logging
import re

from app.models.work_status_form import WorkStatusForm
from app.mongodb import get_database
# Import extraction logic from pr1_generator to use as engine
from app.api.pr1_generator import extract_work_status_from_pr1, PR1GenerateRequest

logger = logging.getLogger(__name__)

router = APIRouter()

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

@router.post("/work-status-form/extract-from-soap")
async def extract_work_status_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags")
):
    """
    Extract work status data from SOAP note directly for the Work Status Form.
    Uses PHI data engine but applies specific Work Status Form post-processing.
    """
    try:
        # Parse flags
        flags_dict = {}
        if flags:
            try:
                flags_dict = json.loads(flags)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in flags parameter: {flags}")
        
        # Use existing extraction engine
        payload = PR1GenerateRequest(
            soap_id=soap_id,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            flags=flags_dict
        )
        
        # Get base extraction result
        result = await extract_work_status_from_pr1(payload)
        
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
                        if soap_doc.get("plan"):
                            raw_texts.append(str(soap_doc.get("plan")))
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

            # 3. AGGRESSIVE AUTO-CHECK: If keywords exist, check the box!
            # User requirement: "lifting pushing pushing aa 3 mathi 1 pan work hoy ne to te chechk box auto chechk thay"
            keywords_present = any(k in target_text_lower for k in ["lift", "push", "pull"])
            
            # Exclude explicit "no restrictions" context if possible, but favor checking
            is_negative = "no lifting restrictions" in target_text_lower or "no work restrictions" in target_text_lower
            
            if keywords_present and not is_negative:
                lifting["noLiftingOver"] = True
                logger.info("WorkStatusForm Fix: Auto-checked 'noLiftingOver' due to keywords")
                
                # 4. Extract weight if possible
                weight_found = extract_weight_from_text(target_text)
                if weight_found:
                    logger.info(f"WorkStatusForm Fix: Extracted weight {weight_found}")
                    if weight_found in ["5", "10", "15", "25"]:
                        lifting["weightLimit"] = weight_found
                    else:
                        lifting["weightLimit"] = "custom"
                        lifting["customWeight"] = weight_found
            
            # If AI already extracted it, ensure we keep it valid
            elif lifting.get("noLiftingOver") and not lifting.get("weightLimit"):
                 weight_found = extract_weight_from_text(target_text)
                 if weight_found:
                     if weight_found in ["5", "10", "15", "25"]:
                        lifting["weightLimit"] = weight_found
                     else:
                        lifting["weightLimit"] = "custom"
                        lifting["customWeight"] = weight_found

            # --- BODY PARTS Fix ---
            emp_info = ws_data.get("employeeInfo", {})
            
            # 1. First, explicitly CLEAN the existing value of junk like "**"
            raw_bp = str(emp_info.get("bodyPartsInjured", ""))
            cleaned_bp = raw_bp.replace("*", "").replace("-", "").strip()
            
            # Update with cleaned value (so at worst we have empty string, not "**")
            emp_info["bodyPartsInjured"] = cleaned_bp
            
            # 2. If valid part found after cleaning, keep it. If not, fallback.
            if not cleaned_bp and full_raw_text:
                 parts_found = extract_body_parts_fallback(full_raw_text)
                 if parts_found:
                      emp_info["bodyPartsInjured"] = parts_found
                      logger.info(f"WorkStatusForm Fix: Extracted body parts: {parts_found}")

        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in work-status-form extraction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract date: {str(e)}")


def extract_work_status_section(text: str) -> str:
    """Isolate work status section to avoid false positives from history"""
    if not text: return ""
    # Look for headers
    match = re.search(r'(?:work status|restrictions|functional limitations|plan)(.*)', text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    return text # Fallback to all text


def extract_body_parts_fallback(text: str) -> Optional[str]:
    """Fallback extraction for body parts from raw text"""
    if not text: return None
    
    flags = re.IGNORECASE | re.DOTALL
    
    # 1. Look for explicit Labels (Diagnosis/Injury)
    # Matches: "Diagnosis: Left Knee" or "Diagnosis:\nLeft Knee"
    # Capture up to newline or full stop
    match = re.search(r'(?:Diagnosis|Assessment|Body\s*Part|Injury\s*Location).*?:\s*([^\n\.]+)', text, flags)
    if match:
        val = match.group(1).replace("*", "").strip()
        if val and len(val) < 100: 
            return val
    
    # 2. Look for Chief Complaint
    match = re.search(r'(?:CC|Chief\s*Complaint).*?:\s*([^\n\.]+)', text, flags)
    if match:
        val = match.group(1).replace("*", "").strip()
        if val and len(val) < 100: 
            return val

    return None


def extract_weight_from_text(text: str) -> Optional[str]:
    """Helper to extract weight (lbs) from text using various patterns"""
    if not text:
        return None
        
    flags = re.IGNORECASE | re.DOTALL
    
    # Pattern 1: Explicit "limited to" or symbols <= / < / ≤
    # Matches: "lifting ... limited to <= 10 lbs", "lifting < 10 lbs"
    match = re.search(r'(?:lift|push|pull).*?(?:limit.*?to|<=|<|≤|max|maximum)\s*(?:<=|<|≤)?\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    # Pattern 2: "no lifting over 10 lbs" or "no lifting > 10 lbs"
    match = re.search(r'no (?:lift|push|pull).*?(?:over|>)\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)

    # Pattern 3: "lifting restriction 10 lbs"
    match = re.search(r'(?:lift|push|pull).*?restriction.*?\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    # Pattern 4: Broad fallback - "lifting ... 10 lbs" within reasonable distance
    match = re.search(r'(?:lift|push|pull).{0,50}?\s(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    return None



@router.post("/work-status-form")
async def create_work_status_form(data: WorkStatusForm):
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
                return JSONResponse({
                    "status": "success",
                    "message": "Work status form updated successfully.",
                    "document_id": str(existing["_id"])
                })

        # Create new form
        form_data["created_at"] = now
        form_data["updated_at"] = now
        result = await collection.insert_one(form_data)
        
        logger.info(f"✅ New work status form saved with ID: {result.inserted_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "Work status form saved successfully.",
            "document_id": str(result.inserted_id)
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save work status form: {str(e)}"
        )


@router.get("/work-status-form/latest")
async def get_latest_work_status_form():
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
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest work status form: {str(e)}"
        )


@router.get("/work-status-form/saved-ids")
async def get_all_saved_work_status_soap_ids():
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


@router.get("/work-status-form/saved/{soap_id}")
async def get_saved_work_status_form(soap_id: str):
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
        
        return JSONResponse({
            "status": "success",
            "data": serialized_form
        })
            
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
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving work status form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve work status form: {str(e)}"
        )

