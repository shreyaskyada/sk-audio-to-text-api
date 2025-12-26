"""
Medical Transcription & SOAP Note API
Clean, modular FastAPI application with MongoDB feedback storage
"""

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
import os
import re
import jwt
import requests
import tempfile
import subprocess
import asyncio
from urllib.parse import urlencode, quote

from openai import OpenAI
import httpx

# Import schemas
from app.schemas import (
    LoginRequest,
    LoginResponse,
    TranscriptionResponse,
    TranscriptionListResponse,
    TranscriptionListItem,
    TranscriptionCreateRequest,
    TranscriptionUpdateRequest,
    SOAPRequest,
    SOAPResponse,
    PatientInfo
)

# Import MongoDB functions
from app.mongodb import connect_to_mongo, close_mongo_connection, get_database

# Import API routers
from app.api import feedback, soap_notes, intake_forms, followup_forms, pr1_generator, work_status_forms, pr2_forms
from app.api.soap_storage import (
    save_soap_note_to_db, 
    get_all_soap_notes_by_transcription_id,
    update_soap_note
)
from app.api.transcription_storage import (
    save_transcription_to_db,
    get_all_transcriptions,
    get_transcription_by_id,
    get_transcriptions_count,
    update_transcription_in_db,
    delete_transcription_in_db
)
from bson import ObjectId

# Import prompts
from app.prompts import (
    MEDICAL_TERMINOLOGY_CORRECTIONS,
    ORTHOPEDIC_SOAP_SYSTEM_PROMPT,
    ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE,
 )

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-secret-key')
AUTH_USERNAME = os.getenv('AUTH_USERNAME', 'admin')
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD', 'admin')
MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', 100))

# Audio file storage configuration
AUDIO_STORAGE_DIR = os.getenv('AUDIO_STORAGE_DIR', 'audio_storage')
os.makedirs(AUDIO_STORAGE_DIR, exist_ok=True)

# OpenAI Model Configuration - Use GPT-5.1 for comprehensive CPT code generation
# GPT-5.1 is used for all AI-generated CPT codes - NO static code lists
# Options: 'gpt-5.1' (recommended for CPT generation), 'gpt-4o' (fallback)
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.1')  # Default to GPT-5.1 for comprehensive code generation
logger.info(f"Using OpenAI model: {OPENAI_MODEL} (All CPT codes generated dynamically via AI)")

# Medical keyterms for Deepgram - Expanded list for better accuracy
MEDICAL_KEYTERMS = [
    # Critical anatomical and procedure terms ONLY (to avoid over-priming)
    "De Quervain's Tenosynovitis", "stenosing tenosynovitis", "MCL", "ACL", "PCL", "LCL", 
    "McMurray test", "Lachman test", "Finkelstein test", "Hoffman's test",
    "bicep", "tricep", "quadricep", "meniscectomy", "chondroplasty",
    "corticosteroid", "lidocaine", "bupivacaine", "Marcaine",
    "workers compensation", "work status", "modified duty", "full duty",
    "TTD", "temporary total disability", "P&S", "permanent and stationary",
    "abductor pollicis longus", "extensor pollicis brevis"
]

# Use medical terminology corrections from prompts module
MEDICAL_CORRECTIONS = MEDICAL_TERMINOLOGY_CORRECTIONS

# ============================================
# FASTAPI APP INITIALIZATION
# ============================================

app = FastAPI(
    title="Medical Transcription & SOAP Note API",
    description="Transcribe audio and generate SOAP notes with Deepgram + GPT-4 + MongoDB",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Exception Handling & Logging Middleware
import time
import traceback
from fastapi import Request

@app.middleware("http")
async def log_requests_and_exceptions(request: Request, call_next):
    start_time = time.time()
    
    # Prepare base log data
    log_entry = {
        "timestamp": datetime.utcnow(),
        "method": request.method,
        "url": str(request.url),
        "client_host": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
    
    try:
        response = await call_next(request)
        
        # Add response details
        process_time = (time.time() - start_time) * 1000
        log_entry["status_code"] = response.status_code
        log_entry["process_time_ms"] = round(process_time, 2)
        
        # Log to MongoDB if connected
        db = get_database()
        if db is not None:
            # Don't await this to avoid blocking the response? 
            # Ideally use background task, but for simplicity/reliability we await directly 
            # or ensure it's fast. MongoDB async inserts are usually fast.
            await db.api_logs.insert_one(log_entry)
            
        return response
        
    except Exception as e:
        # Calculate time
        process_time = (time.time() - start_time) * 1000
        
        # Capture error details
        error_msg = str(e)
        stack_trace = traceback.format_exc()
        
        # Update log entry
        log_entry["status_code"] = 500
        log_entry["process_time_ms"] = round(process_time, 2)
        log_entry["error_message"] = error_msg
        log_entry["stack_trace"] = stack_trace
        
        # Log to console
        logger.error(f"🔥 Global Exception: {error_msg}")
        logger.error(stack_trace)
        
        # Log to MongoDB
        db = get_database()
        if db is not None:
            await db.api_logs.insert_one(log_entry)
            
        # Return sanitized error response
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "message": "An unexpected error occurred. This event has been logged.",
                # In dev mode we might want to show more, but for safety hide trace
                "path": str(request.url.path)
            }
        )

# Security
security = HTTPBearer()

# ============================================
# AUTHENTICATION FUNCTIONS
# ============================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=30)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
    return encoded_jwt


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ============================================
# HELPER FUNCTIONS
# ============================================

def check_ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def convert_audio_to_wav(audio_data: bytes, input_format: str = 'webm') -> bytes:
    """Convert audio (OPUS/WebM/MP3) to WAV format using ffmpeg"""
    # Note: This function assumes FFmpeg is available. Check before calling.
    if not check_ffmpeg_available():
        raise FileNotFoundError("FFmpeg not found. Please install FFmpeg to convert audio files.")
    
    try:
        # Create temporary files
        input_ext = f'.{input_format}' if input_format else '.webm'
        with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as input_file:
            input_file.write(audio_data)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
            wav_path = wav_file.name
        
        # Convert using ffmpeg - High-fidelity conversion
        # Preserves input sample rate and channels for best Deepgram accuracy
        # -acodec pcm_s16le: Standard WAV format (Lossless PCM)
        cmd = [
            'ffmpeg', '-i', input_path, 
            '-acodec', 'pcm_s16le', 
            '-y', wav_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg conversion failed: {result.stderr}")
            raise Exception(f"Audio conversion failed: {result.stderr}")
        
        # Read converted file
        with open(wav_path, 'rb') as f:
            wav_data = f.read()
        
        # Clean up temporary files
        try:
            os.unlink(input_path)
            os.unlink(wav_path)
        except:
            pass
        
        logger.info(f"Successfully converted {input_format} to WAV format ({len(wav_data)} bytes)")
        return wav_data
        
    except Exception as e:
        logger.error(f"Audio conversion error: {str(e)}")
        raise


def get_audio_content_type(filename: str) -> str:
    """Get appropriate Content-Type for audio file"""
    filename_lower = filename.lower()
    if filename_lower.endswith('.webm'):
        return 'audio/webm'
    elif filename_lower.endswith('.opus'):
        return 'audio/opus'
    elif filename_lower.endswith('.mp3'):
        return 'audio/mpeg'
    elif filename_lower.endswith('.m4a'):
        return 'audio/mp4'
    elif filename_lower.endswith('.wav'):
        return 'audio/wav'
    else:
        return 'audio/wav'  # Default to WAV


def convert_opus_to_wav(opus_data: bytes) -> bytes:
    """Convert OPUS audio to WAV format using ffmpeg (legacy function for backward compatibility)"""
    return convert_audio_to_wav(opus_data, 'opus')


def normalize_audio_bytes(audio_data: bytes) -> bytes:
    """Normalize audio bytes (for MP3/WAV files that don't need conversion)"""
    return audio_data


def fix_terms(text: str) -> str:
    """
    Fix common medical terminology errors in transcription using a single-pass regex replacement.
    This prevents double-replacements where a corrected term is further incorrectly modified.
    """
    if not text:
        return text
    
    # Sort keys by length (longest first) to ensure best match
    sorted_corrections = sorted(MEDICAL_CORRECTIONS.items(), key=lambda x: len(x[0]), reverse=True)
    
    # Escape keys and join with | for regex
    pattern_string = "|".join([r'\b' + re.escape(k) + r'\b' for k, _ in sorted_corrections])
    pattern = re.compile(pattern_string, flags=re.IGNORECASE)
    
    # Case-insensitive mapping for the replacement function
    # Note: We need a lower-case map to find the correct replacement while ignoring case
    mapping = {k.lower(): v for k, v in MEDICAL_CORRECTIONS.items()}
    
    def replace_func(match):
        match_text = match.group(0).lower()
        return mapping.get(match_text, match.group(0))
    
    return pattern.sub(replace_func, text)


def create_openai_client():
    """
    Create OpenAI client with explicit configuration to avoid proxy issues.
    """
    # Create httpx client without proxy
    http_client = httpx.Client(
        timeout=60.0,
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )
    
    # Create OpenAI client
    client = OpenAI(
        api_key=OPENAI_API_KEY,
        max_retries=2,
        timeout=60.0,
        http_client=http_client
    )
    
    return client


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
    except Exception as e:
        logger.error(f"Error fetching latest intake form: {e}")
        return None


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


def aggressive_validate_rfa_supportive_cpts(soap_note: str) -> str:
    """
    Aggressively validate RFA Supportive CPTs section to ensure 100% valid codes.
    This function specifically targets the RFA Supportive CPTs section and validates every code.
    """
    import re
    from app.cpt_mappings import is_valid_cpt_code
    
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
    import re
    from app.cpt_mappings import is_valid_cpt_code
    
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
    import re
    from app.cpt_mappings import is_valid_cpt_code
    
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
    from app.cpt_mappings import is_valid_cpt_code
    
    prefix = match.group(1) if match.lastindex >= 1 else ""
    code = match.group(2) if match.lastindex >= 2 else match.group(1)
    description = match.group(3) if match.lastindex >= 3 else ""
    
    if is_valid_cpt_code(code):
        return match.group(0)  # Keep original
    else:
        invalid_codes_list.append(code)
        return ""  # Remove invalid code line


def _validate_cpt_in_match_simple(match, invalid_codes_list):
    """Validate simple CPT code match"""
    from app.cpt_mappings import is_valid_cpt_code
    
    code = match.group(1)
    if is_valid_cpt_code(code):
        return match.group(0)  # Keep original
    else:
        invalid_codes_list.append(code)
        return ""  # Remove invalid code


def _validate_cpt_in_match_standalone(match, invalid_codes_list):
    """Validate standalone CPT code match"""
    from app.cpt_mappings import is_valid_cpt_code
    
    prefix = match.group(1) if match.lastindex >= 1 else ""
    code = match.group(2) if match.lastindex >= 2 else match.group(1)
    
    if is_valid_cpt_code(code):
        return match.group(0)  # Keep original
    else:
        invalid_codes_list.append(code)
        return ""  # Remove invalid code line


async def validate_and_correct_cpt_codes(soap_note: str, transcription: str, openai_client) -> str:
    """
    Post-process SOAP note to validate and correct CPT codes.
    Uses AI to generate CPT codes for procedures not in the mapping, making the system unlimited.
    Replaces "Not documented" with actual CPT codes when procedures are mentioned.
    Also validates and removes invalid CPT codes.
    """
    import re
    import asyncio
    
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
            # this works because we iterate in order
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
    from app.cpt_mappings import generate_cpt_with_ai
    
    # Try to extract procedure name from context
    procedure_text = _extract_procedure_from_transcription(transcription)
    
    if procedure_text and openai_client:
        primary, supportive = generate_cpt_with_ai(procedure_text, openai_client)
        if primary:
            return f"{match.group(1)}{primary}{match.group(3)}"
    
    return match.group(0)  # Return original if can't generate


def _generate_supportive_cpts(transcription: str, openai_client, match) -> str:
    """Generate supportive CPT codes - can be multiple codes - fully AI-driven"""
    from app.cpt_mappings import generate_cpt_with_ai
    
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
    
    import re
    for pattern in procedure_patterns:
        match = re.search(pattern, transcription_lower, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    # Return a snippet if no specific pattern found
    if len(transcription) > 200:
        return transcription[:200]  # Use first 200 chars for context
    return transcription


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


def extract_soap_sections_from_formatted_note(formatted_soap_note: str) -> dict:
    """
    Extract S/O/A/P sections from the generated consolidated note.

    The backend historically produced Markdown headings like:
      - "## S – SUBJECTIVE"
    But the current `ORTHOPEDIC_SOAP_SYSTEM_PROMPT` template uses plain headings like:
      - "SUBJECTIVE"
      - "O – OBJECTIVE/Physical Exam"
      - "ASSESSMENT"
      - "PLAN"

    The frontend UI renders `subjective/objective/assessment/plan`, so this extraction
    must support BOTH formats (backwards compatible).
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
        [r"^---\s*$", r"^\s*$"],
    )

    if any([subj, obj, assess, plan]):
        sections["subjective"] = subj
        sections["objective"] = obj
        sections["assessment"] = assess
        sections["plan"] = plan
        return sections

    # 2) Current plain-template headings (from prompts.py)
    dash = r"[\u2013\u2014-]"  # en-dash, em-dash, hyphen

    sections["subjective"] = _between(
        r"^SUBJECTIVE\s*$",
        [
            r"^---\s*$",
            rf"^O\s*{dash}\s*OBJECTIVE/Physical\s+Exam\s*$",
            r"^ASSESSMENT\s*$",
        ],
    )
    sections["objective"] = _between(
        rf"^O\s*{dash}\s*OBJECTIVE/Physical\s+Exam\s*$",
        [r"^---\s*$", r"^ASSESSMENT\s*$"],
    )
    sections["assessment"] = _between(
        r"^ASSESSMENT\s*$",
        [r"^---\s*$", r"^PLAN\s*$"],
    )
    sections["plan"] = _between(
        r"^PLAN\s*$",
        [
            r"^---\s*$",
            r"^CPT\s*/\s*BILLING\s*CODES\s*$",
            r"^REQUEST\s+FOR\s+AUTHORIZATION\s*\(RFA\)\s*$",
            r"^WORK\s+STATUS\s*$",
            r"^SIGNATURE\s*/\s*PROVIDER\s+INFORMATION\s*$",
        ],
    )

    return sections


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
    
    This helps debug differences between API-generated SOAP notes and ChatGPT-generated notes.
    
    **Why outputs differ even with same prompts:**
    1. Post-processing steps: CPT validation, intake data injection, markdown cleanup
    2. Multiple AI calls: Main SOAP generation + CPT code generation/correction
    3. API parameters: Exact temperature/top_p values that ChatGPT may not support
    4. Pre-processing: Medical terminology corrections applied before sending to AI
    
    **Solution:**
    - Use POST /api/v1/soap-prompts/debug to get exact prompts
    - Copy system_prompt and user_prompt to ChatGPT
    - Note: Outputs may still differ due to post-processing mentioned above
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


def generate_soap_note_from_transcription(text: str) -> str:
    """
    LEGACY: Generate a structured SOAP note from raw clinical transcription using GPT-4.
    This is kept for backward compatibility with the transcribe endpoint.
    For comprehensive SOAP generation, use generate_comprehensive_soap_note().
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
    Returns a dictionary with structured SOAP sections and formatted note.
    Supports dynamic custom prompts from frontend.
    
    Args:
        soap_request: SOAPRequest object with transcription and patient info
        intake_form_data: Optional formatted intake form data string for prompt inclusion
        intake_doc: Optional raw intake form document for post-processing
        model: Optional model override
    
    If skip_post_processing=True in soap_request:
    - Returns raw AI output without any post-processing (matches ChatGPT output)
    - No CPT validation, no intake injection, no markdown cleanup
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
        formatted_prompts_info = format_prompts_for_chatgpt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=selected_model,
            temperature=0.2,
            max_tokens=12000,
            top_p=0.95
        )
        logger.info("=" * 80)
        logger.info("📋 PROMPTS USED FOR SOAP GENERATION (for ChatGPT comparison)")
        logger.info("=" * 80)
        logger.info(f"Model: {selected_model}")
        logger.info(f"Temperature: 0.2 | Max Tokens: 12000 | Top P: 0.95")
        logger.info("-" * 80)
        logger.info("SYSTEM PROMPT (first {:.0f} chars): {}".format(
            len(system_prompt), 
            system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt
        ))
        logger.info("-" * 80)
        logger.info("USER PROMPT (first {:.0f} chars): {}".format(
            len(user_prompt),
            user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt
        ))
        logger.info("=" * 80)
        logger.info("💡 To get full prompts for ChatGPT, use: POST /api/v1/soap-prompts/debug with same request")
        logger.info("=" * 80)
        
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
        
        # Check if post-processing should be skipped (returns raw output like ChatGPT)
        # NOTE: Use getattr for backwards compatibility if SOAPRequest schema doesn't include this field.
        skip_post_processing = getattr(soap_request, "skip_post_processing", False) or False
        
        if skip_post_processing:
            logger.info("⏭️  Skipping post-processing - returning raw AI output (matches ChatGPT behavior)")
        else:
            # Post-process: Preserve ** markdown syntax for subheadings so they appear bold
            # Subheadings (like "Chief Complaint:", "Problem Complexity:", etc.) should remain bold
            # Main section headings (SUBJECTIVE, OBJECTIVE, etc.) are handled by frontend/PDF generator
            # No need to remove ** - keep bold formatting for all subheadings
            
            # Post-process: Inject intake form data directly if available
            if intake_doc:
                intake_values = extract_intake_form_values(intake_doc)
                if intake_values:
                    logger.info(f"🔧 Post-processing SOAP note to inject intake form data: {intake_values}")
                    formatted_soap_note = inject_intake_data_into_soap(formatted_soap_note, intake_values)
            
            # Post-process: Validate and correct CPT codes using AI-enhanced system (GPT-5.1)
            # All CPT codes are generated dynamically by AI - NO static code lists
            formatted_soap_note = await validate_and_correct_cpt_codes(formatted_soap_note, corrected_transcription, client)
            
            # Final validation: Remove any remaining invalid CPT codes (double-check)
            # Note: This only validates format, not content - all codes are AI-generated
            formatted_soap_note = validate_all_cpt_codes_in_soap(formatted_soap_note)
            
            # AGGRESSIVE FINAL VALIDATION: Ensure 100% valid codes in RFA Supportive CPTs section
            formatted_soap_note = aggressive_validate_rfa_supportive_cpts(formatted_soap_note)
        
        # Extract sections from the formatted note for the frontend UI.
        # Supports both legacy markdown headings and the current prompts.py template headings.
        sections = extract_soap_sections_from_formatted_note(formatted_soap_note)
        
        return {
            "transcription": soap_request.transcription,
            "corrected_transcription": corrected_transcription,
            "transcription_id": soap_request.transcription_id,  # Include transcription_id for database lookup
            "subjective": sections["subjective"],
            "objective": sections["objective"],
            "assessment": sections["assessment"],
            "plan": sections["plan"],
            "formatted_soap_note": formatted_soap_note,
            "raw_soap_note": raw_soap_note,  # Raw AI output before post-processing (for ChatGPT comparison)
            "created_at": datetime.utcnow().isoformat(),
            "patient_info": {
                "name": soap_request.patient.name if soap_request.patient else None,
                "age": soap_request.patient.age if soap_request.patient else None,
                "gender": soap_request.patient.gender if soap_request.patient else None,
            } if soap_request.patient else None,
            "date_of_service": soap_request.date_of_service,
            "location": soap_request.location,
            "reason_for_visit": soap_request.reason_for_visit,
            "system_prompt": soap_request.system_prompt,
            "user_prompt_template": soap_request.user_prompt_template,
            "format": "markdown"
        }
        
    except Exception as e:
        logger.error(f"❌ Comprehensive SOAP generation failed: {e}")
        raise


# ============================================
# API ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Medical Transcription & SOAP Note API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "auth": "/api/v1/auth/login",
            "transcribe": "/api/v1/transcribe",
            "transcriptions": {
                "create": "/api/v1/transcriptions",
                "get_all": "/api/v1/transcriptions",
                "get_by_id": "/api/v1/transcriptions/{id}",
                "update": "/api/v1/transcriptions/{id}",
                "delete": "/api/v1/transcriptions/{id}"
            },
            "soap": {
                "generate_json": "/api/v1/generate-soap",
                "generate_form": "/generate-soap",
                "get_default_prompts": "/api/v1/soap-prompts/default",
                "get_all": "/api/v1/soap-notes/all",
                "get_by_id": "/api/v1/soap-notes/{id}",
                "stats": "/api/v1/soap-notes/stats",
                "update": "/api/v1/soap-notes/{id}",
                "delete": "/api/v1/soap-notes/{id}"
            },
            "feedback": {
                "submit": "/api/v1/feedback/submit",
                "stats": "/api/v1/feedback/stats",
                "all": "/api/v1/feedback/all"
            },
            "intake_forms": {
                "create": "/api/v1/intake-form",
                "latest": "/api/v1/intake-form/latest"
            },
            "followup_forms": {
                "create": "/api/v1/followup-intake",
                "latest": "/api/v1/follow-up/latest"
            },
            "pr1_generator": {
                "generate": "/api/v1/pr1/generate",
                "generate_from_soap": "/api/v1/pr1/generate-from-soap"
            },
            "work_status_forms": {
                "create": "/api/v1/work-status-form",
                "latest": "/api/v1/work-status-form/latest",
                "get_by_id": "/api/v1/work-status-form/{id}"
            }
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    db = get_database()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "services": {
            "deepgram": "configured" if DEEPGRAM_API_KEY else "not configured",
            "openai": "configured" if OPENAI_API_KEY else "not configured",
            "mongodb": "connected" if db is not None else "disconnected"
        }
    }


@app.post("/api/v1/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Authenticate user and return JWT access token"""
    if (request.username == AUTH_USERNAME and request.password == AUTH_PASSWORD):
        access_token_expires = timedelta(minutes=30)
        access_token = create_access_token(
            data={"sub": request.username},
            expires_delta=access_token_expires
        )
        
        logger.info(f"User {request.username} logged in successfully")
        return LoginResponse(access_token=access_token, token_type="bearer")
    else:
        logger.warning(f"Failed login attempt for username: {request.username}")
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password"
        )


@app.post("/api/v1/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    generate_soap: bool = Form(False),
    username: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None)
):
    """
    Transcribe audio file with optional SOAP note generation.
    
    **Parameters:**
    - file: Audio file (WAV, MP3, OPUS, etc.)
    - generate_soap: Set to true to generate SOAP note (default: false)
    - username: Username who created the transcription (optional)
    - user_id: User ID who created the transcription (optional)
    
    **Returns:**
    - Transcription with confidence, duration, language
    - SOAP note (if generate_soap=true)
    """
    try:
        # Validate file type
        if not file.content_type or not file.content_type.startswith('audio/'):
            raise HTTPException(
                status_code=400,
                detail="File must be an audio file"
            )
        
        # Validate file size
        max_size = MAX_FILE_SIZE_MB * 1024 * 1024
        file_content = await file.read()
        
        if len(file_content) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File size exceeds {MAX_FILE_SIZE_MB}MB limit"
            )
        
        logger.info(f"Processing file: {file.filename}, Size: {len(file_content)} bytes, SOAP: {generate_soap}")
        
        # Save original audio file to storage BEFORE any conversions
        saved_file_path = None
        original_file_content = file_content  # Keep original for saving
        try:
            # Generate unique filename with timestamp
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            original_filename = file.filename or "recording"
            # Get file extension from original filename or content type
            if '.' in original_filename:
                file_ext = os.path.splitext(original_filename)[1]
            else:
                # Determine extension from content type
                content_type = file.content_type or 'audio/webm'
                ext_map = {
                    'audio/webm': '.webm',
                    'audio/wav': '.wav',
                    'audio/mpeg': '.mp3',
                    'audio/mp4': '.m4a',
                    'audio/ogg': '.ogg',
                    'audio/opus': '.opus'
                }
                file_ext = ext_map.get(content_type, '.webm')
            
            safe_filename = f"{timestamp}_{original_filename.replace(' ', '_').replace('/', '_')}"
            if not safe_filename.endswith(file_ext):
                safe_filename += file_ext
            
            saved_file_path = os.path.join(AUDIO_STORAGE_DIR, safe_filename)
            
            # Write original file to disk
            with open(saved_file_path, 'wb') as f:
                f.write(original_file_content)
            
            logger.info(f"✅ Audio file saved to: {saved_file_path}")
        except Exception as save_error:
            logger.warning(f"⚠️ Failed to save audio file: {save_error}")
            logger.warning("Continuing without file storage...")
        
        soap_note = None
        
        # If SOAP generation is requested, use enhanced Deepgram API
        if generate_soap:
            logger.info(f"SOAP generation requested for file: {file.filename}")
            
            # Deepgram supports WebM directly, but we convert to WAV for better accuracy
            # Match Deepgram Web Playground behavior: send raw file, don't convert to WAV
            filename_lower = file.filename.lower()
            content_type = get_audio_content_type(file.filename)
            logger.info(f"Uploading raw file for SOAP transcription: {file.filename} as {content_type}")
            # Sending raw bytes directly to Deepgram for maximum fidelity
            
            # Build standard Deepgram API URL for best consistency with web results
            query_params = {
                "model": "nova-2",
                "smart_format": "true",
                "language": "en",
                "punctuate": "true",
                "keywords": "Quervain:1,Tenosynovitis:1"  # Smart keywords with low boost
            }
            # Re-enabled keywords with partial matches only to avoid "he"->"De" confusion
            # Using intensity=1 (low) to gently guide without forcing
            
            url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": content_type}
            
            # Get transcription from Deepgram
            response = requests.post(url, headers=headers, data=file_content)
            response.raise_for_status()
            result = response.json()
            
            raw_transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
            logger.info(f"Raw Deepgram Transcript: {raw_transcript}")
            raw_transcript = fix_terms(raw_transcript)
            
            # Generate SOAP note
            logger.info("Generating SOAP note with GPT-4...")
            # soap_note = generate_soap_note_from_transcription(raw_transcript)
            
            # Extract metadata
            confidence = result["results"]["channels"][0]["alternatives"][0].get("confidence", 0.0)
            duration = result.get("metadata", {}).get("duration", 0.0)
            detected_language = "en-US"
            
            transcription_result = {
                'text': raw_transcript,
                'confidence': confidence,
                'language': detected_language,
                'duration': duration
            }
        else:
            # Use standard Deepgram transcription without SOAP
            # Convert WebM to WAV for better accuracy (same as SOAP path)
            filename_lower = file.filename.lower()
            content_type = get_audio_content_type(file.filename)
            logger.info(f"Uploading raw file for standard transcription: {file.filename}")
            
            query_params = {
                "model": "nova-2",
                "smart_format": "true",
                "language": "en",
                "punctuate": "true",
                "keywords": "Quervain:1,Tenosynovitis:1"  # Smart keywords with low boost
            }
            # Re-enabled keywords with partial matches only
            
            url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": content_type}
            
            response = requests.post(url, headers=headers, data=file_content)
            response.raise_for_status()
            result = response.json()
            
            text = result["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
            logger.info(f"Raw Deepgram Transcript: {text}")
            # Apply medical terminology corrections
            text = fix_terms(text)
            confidence = result["results"]["channels"][0]["alternatives"][0].get("confidence", 0.0)
            duration = result.get("metadata", {}).get("duration", 0.0)
            
            transcription_result = {
                'text': text,
                'confidence': confidence,
                'language': 'en-US',
                'duration': duration
            }
        
        logger.info(f"Transcription complete: {len(transcription_result['text'])} chars")
        
        # Save transcription to database
        document_id = None
        try:
            transcription_data = {
                'text': transcription_result['text'],
                'confidence': transcription_result.get('confidence', 0.0),
                'language': transcription_result.get('language', 'unknown'),
                'duration': transcription_result.get('duration', 0.0),
                'filename': file.filename,
                'audio_file_path': saved_file_path,  # Store the saved audio file path
                'username': username,
                'user_id': user_id
            }
            saved_doc = await save_transcription_to_db(transcription_data)
            document_id = saved_doc.get('_id')
            logger.info(f"✅ Transcription saved to database with ID: {document_id}")
            
            # Delete audio file after successful transcription save (no longer needed)
            if saved_file_path and os.path.exists(saved_file_path):
                try:
                    os.remove(saved_file_path)
                    logger.info(f"🗑️ Audio file deleted: {saved_file_path}")
                except Exception as delete_error:
                    logger.warning(f"⚠️ Failed to delete audio file {saved_file_path}: {delete_error}")
                    # Continue even if deletion fails - transcription is already saved
        except Exception as db_error:
            logger.error(f"⚠️ Failed to save transcription to database: {db_error}")
            logger.warning("Continuing without database storage...")
            # Still try to delete audio file even if DB save failed
            if saved_file_path and os.path.exists(saved_file_path):
                try:
                    os.remove(saved_file_path)
                    logger.info(f"🗑️ Audio file deleted after transcription: {saved_file_path}")
                except Exception as delete_error:
                    logger.warning(f"⚠️ Failed to delete audio file: {delete_error}")
        
        return TranscriptionResponse(
            transcription_id=document_id or f"temp_{datetime.utcnow().timestamp()}",
            text=transcription_result['text'],
            confidence=transcription_result.get('confidence', 0.0),
            language=transcription_result.get('language', 'unknown'),
            duration=transcription_result.get('duration', 0.0),
            created_at=datetime.utcnow(),
            soap_note=soap_note,
            document_id=document_id
        )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during transcription: {str(e)}"
        )


@app.get("/api/v1/transcriptions", response_model=TranscriptionListResponse)
async def get_transcriptions(
    limit: int = 100,
    skip: int = 0
):
    """
    Get all transcriptions with pagination.
    
    **Parameters:**
    - limit: Maximum number of transcriptions to return (default: 100, max: 1000)
    - skip: Number of transcriptions to skip for pagination (default: 0)
    
    **Returns:**
    - List of transcriptions with metadata
    """
    try:
        # Validate limit
        if limit > 1000:
            limit = 1000
        if limit < 1:
            limit = 100
        if skip < 0:
            skip = 0
        
        # Get transcriptions from database
        transcriptions = await get_all_transcriptions(limit=limit, skip=skip)
        total = await get_transcriptions_count()
        
        # Convert to response format
        transcription_items = []
        for trans in transcriptions:
            transcription_items.append(
                TranscriptionListItem(
                    id=trans.get("_id", ""),
                    text=trans.get("text", ""),
                    confidence=trans.get("confidence", 0.0),
                    language=trans.get("language", "unknown"),
                    duration=trans.get("duration", 0.0),
                    filename=trans.get("filename"),
                    username=trans.get("username"),
                    user_id=trans.get("user_id"),
                    created_at=trans.get("created_at", datetime.utcnow())
                )
            )
        
        return TranscriptionListResponse(
            total=total,
            limit=limit,
            skip=skip,
            transcriptions=transcription_items
        )
        
    except Exception as e:
        logger.error(f"Error retrieving transcriptions: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error retrieving transcriptions: {str(e)}"
        )


@app.post("/api/v1/transcriptions", response_model=TranscriptionListItem)
async def create_transcription_endpoint(
    create_request: TranscriptionCreateRequest
):
    """
    Create a new transcription.
    
    **Note:** This endpoint allows you to manually create a transcription without audio transcription.
    For audio transcription, use the `/api/v1/transcribe` endpoint instead.
    
    **Parameters:**
    - create_request: JSON body with transcription data:
        - text: Transcription text (required)
        - confidence: Confidence score (optional, default: 0.0)
        - language: Language code (optional, default: "unknown")
        - duration: Audio duration in seconds (optional, default: 0.0)
        - filename: Original filename (optional)
        - username: Username who created the transcription (optional)
    
    **Returns:**
    - Created transcription details with ID
    
    **Example Request:**
    ```json
    {
        "text": "Patient presents with left shoulder pain...",
        "confidence": 0.95,
        "language": "en-US",
        "duration": 120.5,
        "filename": "patient_recording.wav",
        "username": "john_doe"
    }
    ```
    """
    try:
        # Validate required field
        if not create_request.text or not create_request.text.strip():
            raise HTTPException(
                status_code=400,
                detail="Text field is required and cannot be empty"
            )
        
        # Prepare transcription data
        transcription_data = {
            'text': create_request.text.strip(),
            'confidence': create_request.confidence if create_request.confidence is not None else 0.0,
            'language': create_request.language if create_request.language else "unknown",
            'duration': create_request.duration if create_request.duration is not None else 0.0,
            'filename': create_request.filename,
            'username': create_request.username,
            'user_id': create_request.user_id
        }
        
        # Save transcription to database
        saved_doc = await save_transcription_to_db(transcription_data)
        document_id = saved_doc.get('_id')
        
        logger.info(f"✅ Transcription created with ID: {document_id}")
        
        # Return created transcription
        return TranscriptionListItem(
            id=document_id,
            text=saved_doc.get("text", ""),
            confidence=saved_doc.get("confidence", 0.0),
            language=saved_doc.get("language", "unknown"),
            duration=saved_doc.get("duration", 0.0),
            filename=saved_doc.get("filename"),
            username=saved_doc.get("username"),
            user_id=saved_doc.get("user_id"),
            created_at=saved_doc.get("created_at", datetime.utcnow())
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error creating transcription: {str(e)}"
        )


@app.get("/api/v1/transcriptions/{transcription_id}", response_model=TranscriptionListItem)
async def get_transcription_by_id_endpoint(
    transcription_id: str
):
    """
    Get a specific transcription by ID.
    
    **Parameters:**
    - transcription_id: MongoDB document ID of the transcription
    
    **Returns:**
    - Transcription details
    """
    try:
        transcription = await get_transcription_by_id(transcription_id)
        
        if not transcription:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found: {transcription_id}"
            )
        
        return TranscriptionListItem(
            id=transcription.get("_id", ""),
            text=transcription.get("text", ""),
            confidence=transcription.get("confidence", 0.0),
            language=transcription.get("language", "unknown"),
            duration=transcription.get("duration", 0.0),
            filename=transcription.get("filename"),
            username=transcription.get("username"),
            user_id=transcription.get("user_id"),
            created_at=transcription.get("created_at", datetime.utcnow())
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error retrieving transcription: {str(e)}"
        )


@app.put("/api/v1/transcriptions/{transcription_id}", response_model=TranscriptionListItem)
async def update_transcription_endpoint(
    transcription_id: str,
    update_request: TranscriptionUpdateRequest
):
    """
    Update a transcription by ID.
    
    **Note:** If the transcription text is updated, all linked SOAP notes will be automatically regenerated
    with the new transcription text while preserving patient information, custom prompts, and other metadata.
    
    **Parameters:**
    - transcription_id: MongoDB document ID of the transcription
    - update_request: JSON body with fields to update (all fields are optional):
        - text: Updated transcription text (will trigger SOAP note regeneration if changed)
        - confidence: Updated confidence score
        - language: Updated language
        - duration: Updated duration
        - filename: Updated filename
        - username: Updated username
    
    **Returns:**
    - Updated transcription details
    
    **Example Request:**
    ```json
    {
        "text": "Updated transcription text...",
        "confidence": 0.95
    }
    ```
    
    **Behavior:**
    - If `text` field is updated and the text actually changed, all SOAP notes linked to this transcription
      will be automatically regenerated using the updated transcription text
    - Patient information, custom prompts, date of service, location, and other metadata from existing
      SOAP notes are preserved during regeneration
    - The transcription update will succeed even if SOAP regeneration fails (errors are logged)
    """
    try:
        # Convert Pydantic model to dict, excluding None values
        update_data = update_request.model_dump(exclude_none=True)
        
        # Check if there's anything to update
        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No fields provided to update"
            )
        
        # Check if transcription text is being updated
        transcription_text_changed = "text" in update_data
        
        # Get original transcription to compare text if needed
        original_transcription = None
        if transcription_text_changed:
            original_transcription = await get_transcription_by_id(transcription_id)
            if original_transcription:
                original_text = original_transcription.get("text", "")
                new_text = update_data.get("text", "")
                # Only regenerate if text actually changed
                transcription_text_changed = original_text != new_text
        
        # Update transcription in database
        success = await update_transcription_in_db(transcription_id, update_data)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found or not modified: {transcription_id}"
            )
        
        # If transcription text changed, regenerate all linked SOAP notes
        if transcription_text_changed:
            try:
                logger.info(f"Transcription text changed for ID: {transcription_id}. Regenerating linked SOAP notes...")
                
                # Get all SOAP notes linked to this transcription
                linked_soap_notes = await get_all_soap_notes_by_transcription_id(transcription_id)
                
                if linked_soap_notes:
                    logger.info(f"Found {len(linked_soap_notes)} SOAP note(s) to regenerate")
                    
                    # Get updated transcription text
                    updated_transcription = await get_transcription_by_id(transcription_id)
                    updated_text = updated_transcription.get("text", "") if updated_transcription else update_data.get("text", "")
                    
                    # Regenerate each SOAP note
                    for soap_note in linked_soap_notes:
                        try:
                            soap_note_id = soap_note.get("_id")
                            
                            # Create SOAPRequest with updated transcription and preserved metadata
                            soap_request = SOAPRequest(
                                transcription_id=transcription_id,
                                transcription=updated_text,
                                patient=PatientInfo(
                                    name=soap_note.get("patient_info", {}).get("name") if soap_note.get("patient_info") else None,
                                    age=soap_note.get("patient_info", {}).get("age") if soap_note.get("patient_info") else None,
                                    gender=soap_note.get("patient_info", {}).get("gender") if soap_note.get("patient_info") else None
                                ) if soap_note.get("patient_info") else None,
                                date_of_service=soap_note.get("date_of_service"),
                                location=soap_note.get("location"),
                                reason_for_visit=soap_note.get("reason_for_visit"),
                                system_prompt=soap_note.get("custom_prompts", {}).get("system_prompt") if soap_note.get("custom_prompts") else None,
                                user_prompt_template=soap_note.get("custom_prompts", {}).get("user_prompt_template") if soap_note.get("custom_prompts") else None
                            )
                            
                            # Regenerate SOAP note
                            regenerated_soap = generate_comprehensive_soap_note(soap_request)
                            
                            # Update SOAP note in database (preserve transcription_id and created_at)
                            update_soap_data = {
                                "transcription": regenerated_soap.get("transcription", ""),
                                "corrected_transcription": regenerated_soap.get("corrected_transcription", ""),
                                "subjective": regenerated_soap.get("subjective", ""),
                                "objective": regenerated_soap.get("objective", ""),
                                "assessment": regenerated_soap.get("assessment", ""),
                                "plan": regenerated_soap.get("plan", ""),
                                "formatted_soap_note": regenerated_soap.get("formatted_soap_note", "")
                            }
                            
                            await update_soap_note(soap_note_id, update_soap_data)
                            logger.info(f"✅ Regenerated and updated SOAP note: {soap_note_id}")
                            
                        except Exception as soap_error:
                            logger.error(f"⚠️ Failed to regenerate SOAP note {soap_note.get('_id')}: {soap_error}")
                            # Continue with other SOAP notes even if one fails
                            continue
                    
                    logger.info(f"✅ Completed regeneration of {len(linked_soap_notes)} SOAP note(s)")
                else:
                    logger.info(f"No SOAP notes found linked to transcription_id: {transcription_id}")
                    
            except Exception as regenerate_error:
                logger.error(f"⚠️ Error during SOAP note regeneration: {regenerate_error}")
                # Don't fail the transcription update if SOAP regeneration fails
                logger.warning("Continuing with transcription update despite SOAP regeneration error...")
        
        # Retrieve updated transcription
        updated_transcription = await get_transcription_by_id(transcription_id)
        
        if not updated_transcription:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found after update: {transcription_id}"
            )
        
        return TranscriptionListItem(
            id=updated_transcription.get("_id", ""),
            text=updated_transcription.get("text", ""),
            confidence=updated_transcription.get("confidence", 0.0),
            language=updated_transcription.get("language", "unknown"),
            duration=updated_transcription.get("duration", 0.0),
            filename=updated_transcription.get("filename"),
            username=updated_transcription.get("username"),
            created_at=updated_transcription.get("created_at", datetime.utcnow())
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error updating transcription: {str(e)}"
        )


@app.delete("/api/v1/transcriptions/{transcription_id}")
async def delete_transcription_endpoint(transcription_id: str):
    """
    Delete a transcription by ID.
    
    **Note:** If there are SOAP notes linked to this transcription, they will remain in the database
    but will have a dangling reference. Consider deleting linked SOAP notes separately if needed.
    
    **Parameters:**
    - transcription_id: MongoDB document ID of the transcription
    
    **Returns:**
    - Success message with information about linked SOAP notes (if any)
    
    **Example:**
    - DELETE `/api/v1/transcriptions/507f1f77bcf86cd799439011`
    """
    try:
        # Check if there are linked SOAP notes
        linked_soap_notes = await get_all_soap_notes_by_transcription_id(transcription_id)
        linked_soap_count = len(linked_soap_notes) if linked_soap_notes else 0
        
        # Delete transcription
        success = await delete_transcription_in_db(transcription_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Transcription not found: {transcription_id}"
            )
        
        # Prepare response message
        response_message = "Transcription deleted successfully"
        if linked_soap_count > 0:
            response_message += f". Warning: {linked_soap_count} SOAP note(s) are still linked to this transcription. Consider deleting them separately if needed."
        
        return JSONResponse({
            "message": response_message,
            "transcription_id": transcription_id,
            "linked_soap_notes_count": linked_soap_count,
            "linked_soap_note_ids": [soap.get("_id") for soap in linked_soap_notes] if linked_soap_notes else []
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting transcription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error deleting transcription: {str(e)}"
        )


@app.post("/api/v1/soap-prompts/debug")
async def get_formatted_prompts_for_chatgpt(
    soap_request: SOAPRequest,
    intake_id: Optional[str] = Query(None, description="MongoDB intake form ID to use for PMH, Medications, and Social/Occupational History"),
    use_latest_intake: Optional[bool] = Query(False, description="Use latest intake form if True")
):
    """
    Get the exact formatted prompts used for SOAP generation, formatted for ChatGPT comparison.
    
    This endpoint helps debug differences between API-generated SOAP notes and ChatGPT-generated notes.
    It returns the exact system prompt, user prompt, and all parameters that were/would be used.
    
    **Use Case:**
    - Copy the prompts to ChatGPT to test if you get the same output
    - Debug why outputs differ between API and ChatGPT
    - Understand what prompts are actually being sent to the AI
    
    **Returns:**
    - system_prompt: Exact system prompt used
    - user_prompt: Exact user prompt used (with transcription and context)
    - model: Model name used
    - parameters: API parameters (temperature, max_tokens, top_p)
    - chatgpt_instructions: Instructions on how to use these prompts in ChatGPT
    - full_chat_format: Complete formatted text ready to copy-paste
    
    **Note:** The API applies post-processing (CPT validation, intake data injection, markdown cleanup)
    which ChatGPT won't do automatically, so outputs may still differ even with same prompts.
    """
    try:
        # Fetch transcription from database if transcription_id is provided
        if soap_request.transcription_id:
            transcription = await get_transcription_by_id(soap_request.transcription_id)
            if not transcription:
                raise HTTPException(
                    status_code=404,
                    detail=f"Transcription not found with ID: {soap_request.transcription_id}"
                )
            soap_request.transcription = transcription.get("text", "")
        
        if not soap_request.transcription:
            raise HTTPException(
                status_code=400,
                detail="Either transcription_id or transcription text must be provided"
            )
        
        # Apply medical terminology corrections (same as in generate_comprehensive_soap_note)
        corrected_transcription = fix_terms(soap_request.transcription)
        
        # Build patient context section (same logic as generate_comprehensive_soap_note)
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
        
        # Fetch and format intake form data if requested
        intake_form_data = ""
        if intake_id:
            intake_doc = await fetch_intake_form_by_id(intake_id)
            if intake_doc:
                intake_form_data = format_intake_form_data_for_prompt(intake_doc)
        elif use_latest_intake:
            intake_doc = await fetch_latest_intake_form()
            if intake_doc:
                intake_form_data = format_intake_form_data_for_prompt(intake_doc)
        
        # Build the user prompt (same logic as generate_comprehensive_soap_note)
        if soap_request.user_prompt_template:
            user_prompt = soap_request.user_prompt_template.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_form_data
            )
        else:
            user_prompt = ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section,
                intake_form_data=intake_form_data
            )
        
        # Get model
        selected_model = soap_request.model or OPENAI_MODEL
        
        # Format prompts for ChatGPT
        formatted_prompts = format_prompts_for_chatgpt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=selected_model,
            temperature=0.2,
            max_tokens=12000,
            top_p=0.95
        )
        
        return formatted_prompts
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error formatting prompts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to format prompts: {str(e)}"
        )


@app.get("/api/v1/soap-prompts/default")
async def get_default_soap_prompts():
    """
    Get the default orthopedic SOAP note prompts.
    
    **Returns:**
    - system_prompt: Default system prompt for orthopedic SOAP notes
    - user_prompt_template: Default user prompt template with placeholders
    - placeholders: List of available placeholders for the template
    - description: Description of the default prompts
    
    **Usage:**
    Frontend can fetch these default prompts to:
    - Show users the default template
    - Use as a starting point for customization
    - Understand available placeholders
    
    **Example Response:**
    ```json
    {
        "system_prompt": "You are an expert medical documentation AI...",
        "user_prompt_template": "Transform the following clinical transcription...",
        "placeholders": ["{transcription}", "{patient_context}", "{header_section}"],
        "description": "Default orthopedic SOAP note prompts"
    }
    ```
    """
    return JSONResponse({
        "system_prompt": ORTHOPEDIC_SOAP_SYSTEM_PROMPT,
        "user_prompt_template": ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE,
        "placeholders": [
            "{transcription}",
            "{patient_context}",
            "{header_section}",
            "{intake_form_data}"
        ],
        "description": "Default orthopedic SOAP note prompts following DWC, AMA, and HIPAA standards",
        "specialty": "orthopedics"
    })


@app.post("/api/v1/generate-soap", response_model=SOAPResponse)
async def generate_soap_comprehensive(
    soap_request: SOAPRequest,
    intake_id: Optional[str] = Query(None, description="MongoDB intake form ID to use for PMH, Medications, and Social/Occupational History"),
    use_latest_intake: Optional[bool] = Query(False, description="Use latest intake form if True")
):
    """
    Generate a comprehensive orthopedic SOAP note from clinical transcription.
    Supports custom prompts and automatically saves to database.
    
    **Parameters:**
    - transcription_id: MongoDB transcription document ID (required if transcription not provided)
    - transcription: Clinical transcription text (required if transcription_id not provided)
    - patient: Optional patient information (name, age, gender)
    - date_of_service: Optional date of service
    - location: Optional clinic/hospital location
    - reason_for_visit: Optional reason for visit
    - system_prompt: Optional custom system prompt for SOAP generation
    - user_prompt_template: Optional custom user prompt template (use {transcription}, {patient_context}, {header_section}, {intake_form_data} placeholders)
    - model: Optional model selection ('gpt-4o' or 'gpt-5.1')
    - skip_post_processing: Optional boolean (default: False). If True, returns raw AI output without post-processing (matches ChatGPT output)
    - intake_id: Optional query parameter - MongoDB intake form ID to use for PMH, Medications, and Social/Occupational History
    - use_latest_intake: Optional query parameter - Use latest intake form if True (default: False)
    - Additional structured data for S/O/A/P sections (optional)
    
    **Note on skip_post_processing:**
    - If True: Returns raw AI output (no CPT validation, no intake injection, no markdown cleanup) - matches ChatGPT behavior
    - If False (default): Applies all post-processing steps (validated, cleaned, and processed output)
    
    **Returns:**
    - Original and corrected transcription
    - Structured SOAP sections (Subjective, Objective, Assessment, Plan)
    - Fully formatted SOAP note (ready for PDF export)
    - Patient information and metadata
    
    **Example Request:**
    ```json
    {
        "transcription_id": "507f1f77bcf86cd799439011",
        "patient": {
            "name": "Jane Smith",
            "age": 78,
            "gender": "Female"
        },
        "date_of_service": "2025-10-27",
        "location": "Cresol Ortho Center",
        "reason_for_visit": "Left wrist pain after fall"
    }
    ```
    """
    try:
        logger.info("Generating comprehensive orthopedic SOAP note with GPT-4...")
        
        # Fetch transcription from database if transcription_id is provided
        if soap_request.transcription_id:
            # Always generate new SOAP note (removed cache check to allow regeneration)
            transcription = await get_transcription_by_id(soap_request.transcription_id)
            if not transcription:
                raise HTTPException(
                    status_code=404,
                    detail=f"Transcription not found with ID: {soap_request.transcription_id}"
                )
            # Set transcription text from database
            soap_request.transcription = transcription.get("text", "")
        elif not soap_request.transcription:
            raise HTTPException(
                status_code=400,
                detail="Either transcription_id or transcription text must be provided"
            )
        
        # Fetch intake form data if requested
        intake_doc = None
        # if intake_id:
        #     intake_doc = await fetch_intake_form_by_id(intake_id)
        #     if intake_doc:
        #         logger.info(f"Using intake form with ID: {intake_id}")
        # elif use_latest_intake:
        intake_doc = await fetch_latest_intake_form()
        if intake_doc:
            logger.info(f"Using latest intake form with ID: {intake_doc.get('_id')}")
        
        # Format intake form data for prompt
        intake_form_data = format_intake_form_data_for_prompt(intake_doc) if intake_doc else None
        
        # Log intake form data for debugging
        if intake_form_data:
            logger.info(f"✅ Intake form data formatted and will be included in prompt:")
            logger.info(f"   {intake_form_data}")
        else:
            logger.warning("⚠️ No intake form data available - will use 'As per chart'")
        
        # Generate comprehensive SOAP note (pass both formatted data and raw doc for post-processing)
        soap_result = await generate_comprehensive_soap_note(soap_request, intake_form_data, intake_doc, model=soap_request.model)
        
        # Save to MongoDB
        document_id = None
        try:
            saved_doc = await save_soap_note_to_db(soap_result)
            document_id = saved_doc.get('_id')
            logger.info(f"✅ SOAP note saved with ID: {document_id}")
        except Exception as db_error:
            logger.error(f"⚠️ Failed to save SOAP note to database: {db_error}")
            logger.warning("Continuing without database storage...")
        
        # Return as SOAPResponse with document ID
        soap_response = SOAPResponse(**soap_result)
        if document_id:
            soap_response.document_id = document_id
        return soap_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comprehensive SOAP generation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate SOAP note: {str(e)}"
        )


@app.post("/generate-soap", response_model=SOAPResponse)
async def generate_soap_simple(
    transcription_id: Optional[str] = Form(None),
    transcription: Optional[str] = Form(None),
    patient_name: Optional[str] = Form(None),
    patient_age: Optional[int] = Form(None),
    patient_gender: Optional[str] = Form(None),
    date_of_service: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    reason_for_visit: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    user_prompt_template: Optional[str] = Form(None),
    model: Optional[str] = Form(None, description="OpenAI model to use: 'gpt-4o' or 'gpt-5.1'"),
    skip_post_processing: Optional[bool] = Form(False, description="If True, returns raw AI output without post-processing (matches ChatGPT output)"),
    intake_id: Optional[str] = Form(None),
    use_latest_intake: Optional[bool] = Form(True)
):
    """
    Generate comprehensive orthopedic SOAP note from transcription text.
    Supports custom prompts and automatically saves to database.
    
    **Accepts Form Data or JSON:**
    - Use this endpoint if sending form-data
    - Use /api/v1/generate-soap if sending JSON payload
    
    **Required Parameters (one of):**
    - transcription_id: MongoDB transcription document ID
    - transcription: Clinical transcription text
    
    **Optional Parameters:**
    - patient_name: Patient's full name
    - patient_age: Patient's age
    - patient_gender: Patient's gender
    - date_of_service: Date of visit (YYYY-MM-DD)
    - location: Clinic/hospital location
    - reason_for_visit: Chief complaint
    - system_prompt: Custom system prompt for SOAP generation
    - user_prompt_template: Custom user prompt template (use {transcription}, {patient_context}, {header_section}, {intake_form_data} placeholders)
    - model: OpenAI model to use ('gpt-4o' or 'gpt-5.1')
    - skip_post_processing: If True, returns raw AI output without post-processing (matches ChatGPT output). Default: False
    - intake_id: Optional MongoDB intake form ID to use for PMH, Medications, and Social/Occupational History (if provided, this takes precedence over use_latest_intake)
    - use_latest_intake: Optional boolean to use latest intake form (default: True - automatically uses latest intake form unless intake_id is provided)
    
    **Returns:**
    - Comprehensive SOAP note with structured sections
    - Formatted note ready for PDF export (or raw note if skip_post_processing=True)
    - raw_soap_note: Raw AI output before post-processing (for ChatGPT comparison)
    - ICD-10 codes in assessment section
    - document_id: MongoDB document ID of the saved SOAP note (if successfully saved)
    
    **Example (form-data):**
    ```
    transcription_id: "507f1f77bcf86cd799439011"
    patient_name: "Jane Smith"
    patient_age: 78
    patient_gender: "Female"
    system_prompt: "Custom system prompt (optional)"
    user_prompt_template: "Custom template (optional)"
    ```
    """
    try:
        logger.info("Generating comprehensive orthopedic SOAP note...")
        
        # Fetch transcription from database if transcription_id is provided
        if transcription_id:
            # Always generate new SOAP note (removed cache check to allow regeneration)
            transcription_doc = await get_transcription_by_id(transcription_id)
            if not transcription_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Transcription not found with ID: {transcription_id}"
                )
            transcription = transcription_doc.get("text", "")
        elif not transcription:
            raise HTTPException(
                status_code=400,
                detail="Either transcription_id or transcription text must be provided"
            )
        
        # Build SOAPRequest object from form data
        patient_info = None
        if patient_name or patient_age or patient_gender:
            patient_info = PatientInfo(
                name=patient_name,
                age=patient_age,
                gender=patient_gender
            )
        
        # Validate model if provided
        if model:
            allowed_models = ['gpt-4o', 'gpt-5.1']
            if model not in allowed_models:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{model}' is not allowed. Only {allowed_models} are permitted."
                )
        
        soap_request = SOAPRequest(
            transcription=transcription,
            transcription_id=transcription_id,
            patient=patient_info,
            date_of_service=date_of_service,
            location=location,
            reason_for_visit=reason_for_visit,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            model=model,
            skip_post_processing=skip_post_processing
        )
        
        # Fetch intake form data if requested
        intake_doc = None
        if intake_id:
            intake_doc = await fetch_intake_form_by_id(intake_id)
            if intake_doc:
                logger.info(f"Using intake form with ID: {intake_id}")
        elif use_latest_intake:
            intake_doc = await fetch_latest_intake_form()
            if intake_doc:
                logger.info(f"Using latest intake form with ID: {intake_doc.get('_id')}")
        
        # Format intake form data for prompt
        intake_form_data = format_intake_form_data_for_prompt(intake_doc) if intake_doc else None
        
        # Log intake form data for debugging
        if intake_form_data:
            logger.info(f"✅ Intake form data formatted and will be included in prompt:")
            logger.info(f"   {intake_form_data}")
        else:
            logger.warning("⚠️ No intake form data available - will use 'As per chart'")
        
        # Generate comprehensive SOAP note (pass both formatted data and raw doc for post-processing)
        soap_result = await generate_comprehensive_soap_note(soap_request, intake_form_data, intake_doc, model=model)
        
        # Save to MongoDB
        document_id = None
        try:
            saved_doc = await save_soap_note_to_db(soap_result)
            document_id = saved_doc.get('_id')
            logger.info(f"✅ SOAP note saved with ID: {document_id}")
        except Exception as db_error:
            logger.error(f"⚠️ Failed to save SOAP note to database: {db_error}")
            logger.warning("Continuing without database storage...")
        
        # Return as SOAPResponse with document ID
        soap_response = SOAPResponse(**soap_result)
        if document_id:
            soap_response.document_id = document_id
        return soap_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SOAP generation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate SOAP note: {str(e)}"
        )


# ============================================
# INCLUDE API ROUTERS
# ============================================

# Include feedback router
app.include_router(feedback.router, prefix="/api/v1/feedback", tags=["feedback"])

# Include SOAP notes router
app.include_router(soap_notes.router, prefix="/api/v1/soap-notes", tags=["soap-notes"])

# Include intake forms router
app.include_router(intake_forms.router, prefix="/api/v1", tags=["intake-forms"])

# Include follow-up forms router
app.include_router(followup_forms.router, prefix="/api/v1", tags=["followup-forms"])

# Include PR-1 generator router
app.include_router(pr1_generator.router, prefix="/api/v1", tags=["pr1-generator"])

# Include work status forms router
app.include_router(work_status_forms.router, prefix="/api/v1", tags=["work-status-forms"])

# Include PR2 forms router
app.include_router(pr2_forms.router, prefix="/api/v1", tags=["pr2-forms"])

# Include logs router
from app.api import logs
app.include_router(logs.router, prefix="/api/v1", tags=["logs"])


# ============================================
# STARTUP & SHUTDOWN EVENTS
# ============================================

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("=" * 60)
    logger.info("Starting Medical Transcription & SOAP Note API v2.0.0")
    logger.info("=" * 60)
    
    # Connect to MongoDB
    await connect_to_mongo()
    
    # Validate Deepgram API key
    if not DEEPGRAM_API_KEY:
        logger.error("❌ DEEPGRAM_API_KEY not configured!")
        raise Exception("Missing required environment variable: DEEPGRAM_API_KEY")
    logger.info("✅ Deepgram API key configured")
    
    # Validate OpenAI API key (warning only)
    if not OPENAI_API_KEY:
        logger.warning("⚠️  OPENAI_API_KEY not configured - SOAP generation will be limited")
    else:
        logger.info("✅ OpenAI API key configured")
    
    # Validate Secret Key
    if SECRET_KEY == 'change-this-secret-key':
        logger.warning("⚠️  Using default SECRET_KEY - please change in production!")
    
    logger.info(f"✅ Max file size: {MAX_FILE_SIZE_MB}MB")
    logger.info(f"✅ Authentication configured (username: {AUTH_USERNAME})")
    logger.info("=" * 60)
    logger.info("🚀 API is ready!")
    logger.info(f"📖 Documentation: http://localhost:8000/docs")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("🛑 API shutting down...")
    await close_mongo_connection()
    logger.info("✅ Shutdown complete")


# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
