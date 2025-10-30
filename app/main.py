"""
Medical Transcription & SOAP Note API
Clean, modular FastAPI application with MongoDB feedback storage
"""

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
import os
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
    SOAPRequest,
    SOAPResponse,
    PatientInfo
)

# Import MongoDB functions
from app.mongodb import connect_to_mongo, close_mongo_connection, get_database

# Import API routers
from app.api import feedback, soap_notes
from app.api.soap_storage import save_soap_note_to_db

# Import prompts
from app.prompts import (
    MEDICAL_TERMINOLOGY_CORRECTIONS,
    ORTHOPEDIC_SOAP_SYSTEM_PROMPT,
    ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
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

# Medical keyterms for Deepgram
MEDICAL_KEYTERMS = [
    "pes anserine", "antalgic gait", "corticosteroid injection",
    "intra-articular", "ligamentous", "osteoarthritis",
    "bursitis", "MCL", "ACL", "PCL", "LCL", "McMurray test",
    "contralateral", "neurovascularly intact",
    "range of motion", "joint line tenderness",
    "effusion", "crepitus", "meniscus", "patellofemoral"
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

def convert_opus_to_wav(opus_data: bytes) -> bytes:
    """Convert OPUS audio to WAV format using ffmpeg"""
    try:
        # Create temporary files
        with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as opus_file:
            opus_file.write(opus_data)
            opus_path = opus_file.name
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
            wav_path = wav_file.name
        
        # Convert using ffmpeg
        cmd = [
            'ffmpeg', '-i', opus_path, '-acodec', 'pcm_s16le', 
            '-ar', '16000', '-ac', '1', '-y', wav_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg conversion failed: {result.stderr}")
            raise Exception(f"Audio conversion failed: {result.stderr}")
        
        # Read converted file
        with open(wav_path, 'rb') as f:
            wav_data = f.read()
        
        # Clean up temporary files
        os.unlink(opus_path)
        os.unlink(wav_path)
        
        return wav_data
        
    except FileNotFoundError:
        logger.error("FFmpeg not found. Please install FFmpeg to convert OPUS files.")
        raise Exception("FFmpeg not found. Please install FFmpeg to convert OPUS files.")
    except Exception as e:
        logger.error(f"OPUS conversion error: {str(e)}")
        raise


def normalize_audio_bytes(audio_data: bytes) -> bytes:
    """Normalize audio bytes (for MP3/WAV files)"""
    return audio_data


def fix_terms(text: str) -> str:
    """Fix common medical terminology errors in transcription"""
    corrected_text = text
    for incorrect, correct in MEDICAL_CORRECTIONS.items():
        corrected_text = corrected_text.replace(incorrect, correct)
    return corrected_text


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
            model="gpt-4o",
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
            max_tokens=5000
        )

        structured_note = response.choices[0].message.content.strip()
        return structured_note

    except Exception as e:
        logger.error(f"❌ GPT SOAP generation failed: {e}")
        logger.error(f"Full error details: {type(e).__name__}: {str(e)}")
        return text


def generate_comprehensive_soap_note(soap_request: SOAPRequest) -> dict:
    """
    Generate a comprehensive orthopedic SOAP note from transcription with optional structured data.
    Returns a dictionary with structured SOAP sections and formatted note.
    Supports dynamic custom prompts from frontend.
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
        
        # Build the user prompt - use custom template if provided
        if soap_request.user_prompt_template:
            # Custom prompt template - replace placeholders
            user_prompt = soap_request.user_prompt_template.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section
            )
            logger.info("Using custom user prompt template provided by frontend")
        else:
            # Use default template
            user_prompt = ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE.format(
                transcription=corrected_transcription,
                patient_context=patient_context,
                header_section=header_section
            )
            logger.info("Using default orthopedic SOAP prompt template")
        
        # Log if custom system prompt is used
        if soap_request.system_prompt:
            logger.info("Using custom system prompt provided by frontend")
        
        # Call OpenAI GPT-4 with explicit configuration
        client = create_openai_client()
        
        response = client.chat.completions.create(
            model="gpt-4o",
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
            temperature=0.2,
            max_tokens=6000
        )
        
        formatted_soap_note = response.choices[0].message.content.strip()
        
        # Extract sections from the formatted note (simple parsing)
        # This is a best-effort extraction for structured access
        sections = {
            "subjective": "",
            "objective": "",
            "assessment": "",
            "plan": ""
        }
        
        # Try to extract sections from Markdown format
        import re
        
        # Extract Subjective section
        subjective_match = re.search(
            r'## S – SUBJECTIVE\s*\n(.*?)(?=## O – OBJECTIVE|---)',
            formatted_soap_note,
            re.DOTALL
        )
        if subjective_match:
            sections["subjective"] = subjective_match.group(1).strip()
        
        # Extract Objective section
        objective_match = re.search(
            r'## O – OBJECTIVE\s*\n(.*?)(?=## A – ASSESSMENT|---)',
            formatted_soap_note,
            re.DOTALL
        )
        if objective_match:
            sections["objective"] = objective_match.group(1).strip()
        
        # Extract Assessment section
        assessment_match = re.search(
            r'## A – ASSESSMENT\s*\n(.*?)(?=## P – PLAN|---)',
            formatted_soap_note,
            re.DOTALL
        )
        if assessment_match:
            sections["assessment"] = assessment_match.group(1).strip()
        
        # Extract Plan section
        plan_match = re.search(
            r'## P – PLAN\s*\n(.*?)(?=---|$)',
            formatted_soap_note,
            re.DOTALL
        )
        if plan_match:
            sections["plan"] = plan_match.group(1).strip()
        
        return {
            "transcription": soap_request.transcription,
            "corrected_transcription": corrected_transcription,
            "subjective": sections["subjective"],
            "objective": sections["objective"],
            "assessment": sections["assessment"],
            "plan": sections["plan"],
            "formatted_soap_note": formatted_soap_note,
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
    username: str = Depends(verify_token)
):
    """
    Transcribe audio file with optional SOAP note generation.
    
    **Parameters:**
    - file: Audio file (WAV, MP3, OPUS, etc.)
    - generate_soap: Set to true to generate SOAP note (default: false)
    
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
        
        soap_note = None
        
        # If SOAP generation is requested, use enhanced Deepgram API
        if generate_soap:
            logger.info(f"SOAP generation requested for file: {file.filename}")
            
            # Convert OPUS to WAV if needed
            filename_lower = file.filename.lower()
            if filename_lower.endswith(".opus"):
                file_content = convert_opus_to_wav(file_content)
            else:
                file_content = normalize_audio_bytes(file_content)
            
            # Build enhanced Deepgram API URL
            query_params = {
                "model": "nova-3-medical",
                "numerals": "true",
                "language": "en-US",
                "version": "latest",
                "smart_format": "true",
                "diarize": "true",
                "custom_intent": "orthopedic_patient_assessment",
                "custom_intent_mode": "extended",
                "sentiment": "false"
            }
            
            # Build URL with keyterms
            url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
            for term in MEDICAL_KEYTERMS:
                url += f"&keyterm={quote(term)}"
            
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "audio/wav"}
            
            # Get transcription from Deepgram
            response = requests.post(url, headers=headers, data=file_content)
            response.raise_for_status()
            result = response.json()
            
            raw_transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
            raw_transcript = fix_terms(raw_transcript)
            
            # Generate SOAP note
            logger.info("Generating SOAP note with GPT-4...")
            soap_note = generate_soap_note_from_transcription(raw_transcript)
            
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
            query_params = {
                "model": "nova-3-medical",
                "smart_format": "true",
                "language": "en-US"
            }
            
            url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "audio/wav"}
            
            response = requests.post(url, headers=headers, data=file_content)
            response.raise_for_status()
            result = response.json()
            
            text = result["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
            confidence = result["results"]["channels"][0]["alternatives"][0].get("confidence", 0.0)
            duration = result.get("metadata", {}).get("duration", 0.0)
            
            transcription_result = {
                'text': text,
                'confidence': confidence,
                'language': 'en-US',
                'duration': duration
            }
        
        logger.info(f"Transcription complete: {len(transcription_result['text'])} chars")
        
        return TranscriptionResponse(
            transcription_id=f"temp_{datetime.utcnow().timestamp()}",
            text=transcription_result['text'],
            confidence=transcription_result.get('confidence', 0.0),
            language=transcription_result.get('language', 'unknown'),
            duration=transcription_result.get('duration', 0.0),
            created_at=datetime.utcnow(),
            soap_note=soap_note
        )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during transcription: {str(e)}"
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
            "{header_section}"
        ],
        "description": "Default orthopedic SOAP note prompts following DWC, AMA, and HIPAA standards",
        "specialty": "orthopedics"
    })


@app.post("/api/v1/generate-soap", response_model=SOAPResponse)
async def generate_soap_comprehensive(soap_request: SOAPRequest):
    """
    Generate a comprehensive orthopedic SOAP note from clinical transcription.
    Supports custom prompts and automatically saves to database.
    
    **Parameters:**
    - transcription: Clinical transcription text (REQUIRED)
    - patient: Optional patient information (name, age, gender)
    - date_of_service: Optional date of service
    - location: Optional clinic/hospital location
    - reason_for_visit: Optional reason for visit
    - system_prompt: Optional custom system prompt for SOAP generation
    - user_prompt_template: Optional custom user prompt template (use {transcription}, {patient_context}, {header_section} placeholders)
    - Additional structured data for S/O/A/P sections (optional)
    
    **Returns:**
    - Original and corrected transcription
    - Structured SOAP sections (Subjective, Objective, Assessment, Plan)
    - Fully formatted SOAP note (ready for PDF export)
    - Patient information and metadata
    
    **Example Request:**
    ```json
    {
        "transcription": "78-year-old female with one week history...",
        "patient": {
            "name": "Jane Smith",
            "age": 78,
            "gender": "Female"
        },
        "date_of_service": "2025-10-27",
        "location": "Cresol Ortho Center",
        "reason_for_visit": "Left wrist pain after fall",
        "system_prompt": "Custom system prompt here (optional)",
        "user_prompt_template": "Custom template with {transcription} placeholder (optional)"
    }
    ```
    """
    try:
        logger.info("Generating comprehensive orthopedic SOAP note with GPT-4...")
        
        # Generate comprehensive SOAP note
        soap_result = generate_comprehensive_soap_note(soap_request)
        
        # Save to MongoDB
        try:
            saved_doc = await save_soap_note_to_db(soap_result)
            logger.info(f"✅ SOAP note saved with ID: {saved_doc.get('_id')}")
        except Exception as db_error:
            logger.error(f"⚠️ Failed to save SOAP note to database: {db_error}")
            logger.warning("Continuing without database storage...")
        
        # Return as SOAPResponse
        return SOAPResponse(**soap_result)
        
    except Exception as e:
        logger.error(f"Comprehensive SOAP generation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate SOAP note: {str(e)}"
        )


@app.post("/generate-soap", response_model=SOAPResponse)
async def generate_soap_simple(
    transcription: str = Form(...),
    patient_name: Optional[str] = Form(None),
    patient_age: Optional[int] = Form(None),
    patient_gender: Optional[str] = Form(None),
    date_of_service: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    reason_for_visit: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    user_prompt_template: Optional[str] = Form(None)
):
    """
    Generate comprehensive orthopedic SOAP note from transcription text.
    Supports custom prompts and automatically saves to database.
    
    **Accepts Form Data or JSON:**
    - Use this endpoint if sending form-data
    - Use /api/v1/generate-soap if sending JSON payload
    
    **Required Parameters:**
    - transcription: Clinical transcription text
    
    **Optional Parameters:**
    - patient_name: Patient's full name
    - patient_age: Patient's age
    - patient_gender: Patient's gender
    - date_of_service: Date of visit (YYYY-MM-DD)
    - location: Clinic/hospital location
    - reason_for_visit: Chief complaint
    - system_prompt: Custom system prompt for SOAP generation
    - user_prompt_template: Custom user prompt template (use {transcription}, {patient_context}, {header_section} placeholders)
    
    **Returns:**
    - Comprehensive SOAP note with structured sections
    - Formatted note ready for PDF export
    - ICD-10 codes in assessment section
    
    **Example (form-data):**
    ```
    transcription: "78-year-old female with left wrist fracture..."
    patient_name: "Jane Smith"
    patient_age: 78
    patient_gender: "Female"
    system_prompt: "Custom system prompt (optional)"
    user_prompt_template: "Custom template (optional)"
    ```
    """
    try:
        logger.info("Generating comprehensive orthopedic SOAP note...")
        
        # Build SOAPRequest object from form data
        patient_info = None
        if patient_name or patient_age or patient_gender:
            patient_info = PatientInfo(
                name=patient_name,
                age=patient_age,
                gender=patient_gender
            )
        
        soap_request = SOAPRequest(
            transcription=transcription,
            patient=patient_info,
            date_of_service=date_of_service,
            location=location,
            reason_for_visit=reason_for_visit,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template
        )
        
        # Generate comprehensive SOAP note
        soap_result = generate_comprehensive_soap_note(soap_request)
        
        # Save to MongoDB
        try:
            saved_doc = await save_soap_note_to_db(soap_result)
            logger.info(f"✅ SOAP note saved with ID: {saved_doc.get('_id')}")
        except Exception as db_error:
            logger.error(f"⚠️ Failed to save SOAP note to database: {db_error}")
            logger.warning("Continuing without database storage...")
        
        # Return as SOAPResponse
        return SOAPResponse(**soap_result)
        
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
