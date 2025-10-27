from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status, Header, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Optional
import jwt
from pydantic import BaseModel
import requests
from urllib.parse import urlencode, quote
from openai import OpenAI
from jiwer import wer
import tempfile
import subprocess

from app.schemas import TranscriptionResponse, TranscriptionRequest
from app.services.transcription_service import TranscriptionService
from app.services.hipaa_compliance import HIPAAComplianceService
from app.utils.encryption import EncryptionService
from app.api import feedback
from app.mongodb import connect_to_mongo, close_mongo_connection, get_database

# Load environment variables
load_dotenv()

# Configure logging for HIPAA compliance
# On Vercel, filesystem is read-only  except /tmp, so we adapt logging accordingly
is_vercel = os.getenv('VERCEL') is not None

if is_vercel:
    # On Vercel, use /tmp directory or just console logging
    log_path = '/tmp/audit.log'
    log_dir = '/tmp'
else:
    # Local development - use logs directory
    log_path = os.getenv('AUDIT_LOG_PATH', './logs/audit.log')
    log_dir = os.path.dirname(log_path)

# Only create directory if not on Vercel and it doesn't exist
if not is_vercel and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

# Configure handlers based on environment
handlers = [logging.StreamHandler()]
if not is_vercel or os.access('/tmp', os.W_OK):
    # Add file handler only if we can write to the directory
    try:
        handlers.append(logging.FileHandler(log_path))
    except (OSError, PermissionError):
        # If file logging fails, just use console logging
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="HIPAA-Compliant Audio Transcription API",
    description="Secure audio transcription service with HIPAA compliance",
    version="1.0.0",
    docs_url="/docs" if os.getenv('ENVIRONMENT') == 'development' else None,
    redoc_url="/redoc" if os.getenv('ENVIRONMENT') == 'development' else None
)

# CORS middleware - Allow all origins for now
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Authentication models
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str

# Services
encryption_service = EncryptionService()
transcription_service = TranscriptionService()
hipaa_service = HIPAAComplianceService()
security = HTTPBearer()

# API Keys
DG_KEY = os.getenv('DEEPGRAM_API_KEY')
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

# Authentication functions
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=int(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES', 30)))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, os.getenv('SECRET_KEY', 'test-secret-key-for-development'), algorithm=os.getenv('ALGORITHM', 'HS256'))
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, os.getenv('SECRET_KEY', 'test-secret-key-for-development'), algorithms=[os.getenv('ALGORITHM', 'HS256')])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return username
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("Starting HIPAA-Compliant Audio Transcription API with Deepgram Nova-3 Medical")
    
    # Connect to MongoDB
    try:
        await connect_to_mongo()
        logger.info("MongoDB connection established successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {str(e)}")
        logger.warning("Continuing without MongoDB - audit logs will be file-only")
        # Don't raise exception - allow app to run without MongoDB for development
    
    # Verify encryption keys
    if not encryption_service.verify_keys():
        logger.error("Encryption keys not properly configured")
        raise Exception("Encryption keys not configured")
    
    # Verify Deepgram API key
    deepgram_api_key = os.getenv('DEEPGRAM_API_KEY')
    if not deepgram_api_key:
        logger.error("Deepgram API key not configured")
        raise Exception("Deepgram API key not configured")
    
    # Test transcription service
    try:
        service_status = transcription_service.get_service_status()
        if service_status['available']:
            logger.info("Deepgram Nova-3 Medical service initialized successfully")
        else:
            logger.error(f"Transcription service error: {service_status['message']}")
            raise Exception("Transcription service not available")
    except Exception as e:
        logger.error(f"Failed to initialize transcription service: {str(e)}")
        raise Exception("Transcription service initialization failed")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    await close_mongo_connection()
    logger.info("Application shutdown complete")

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "HIPAA-Compliant Audio Transcription API", "status": "running"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow()}

@app.post("/api/v1/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Authenticate user and return access token"""
    # Simple authentication against environment variables
    if (request.username == os.getenv('AUTH_USERNAME', 'admin') and 
        request.password == os.getenv('AUTH_PASSWORD', 'admin')):
        
        access_token_expires = timedelta(minutes=int(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES', 30)))
        access_token = create_access_token(
            data={"sub": request.username}, expires_delta=access_token_expires
        )
        
        logger.info(f"User {request.username} logged in successfully")
        return LoginResponse(access_token=access_token, token_type="bearer")
    else:
        logger.warning(f"Failed login attempt for username: {request.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

@app.post("/api/v1/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    generate_soap: bool = Form(False),
    username: str = Depends(verify_token),
    db=Depends(get_database)
):

    """
    Transcribe audio file with HIPAA compliance.
    Optionally generate SOAP note by setting generate_soap=true
    """
    try:
        # Validate file type and size
        if not file.content_type.startswith('audio/'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an audio file"
            )
        
        max_size = int(os.getenv('MAX_FILE_SIZE_MB', 100)) * 1024 * 1024
        if file.size and file.size > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size exceeds {os.getenv('MAX_FILE_SIZE_MB', 100)}MB limit"
            )
        
        # Log access for HIPAA compliance
        await hipaa_service.log_access(
            user_id=username,
            action="audio_upload",
            details=f"File: {file.filename}, Size: {file.size}, Generate SOAP: {generate_soap}",
            db=db
        )
        
        # Read file content
        file_content = await file.read()
        
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
            keyterms = [
                "pes anserine", "antalgic gait", "corticosteroid injection",
                "intra-articular", "ligamentous", "osteoarthritis",
                "bursitis", "MCL", "ACL", "PCL", "LCL", "McMurray test",
                "contralateral", "neurovascularly intact",
                "range of motion", "joint line tenderness",
                "effusion", "crepitus", "meniscus", "patellofemoral"
            ]
            
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
            for term in keyterms:
                url += f"&keyterm={quote(term)}"
            
            headers = {"Authorization": f"Token {DG_KEY}", "Content-Type": "audio/wav"}
            
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
            detected_language = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("languages", ["en-US"])[0] if "languages" in result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0] else "en-US"
            
            transcription_result = {
                'text': raw_transcript,
                'confidence': confidence,
                'language': detected_language,
                'duration': duration
            }
        else:
            # Use standard transcription service (run in thread to avoid blocking)
            import asyncio
            transcription_result = await asyncio.to_thread(
                transcription_service.transcribe_audio,
                file_content,
                file.filename
            )
        
        # Log successful transcription
        await hipaa_service.log_access(
            user_id=username,
            action="transcription_complete",
            details=f"File: {file.filename} transcribed successfully. SOAP: {generate_soap}",
            db=db
        )
        
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during transcription"
        )

# ----------------------------
# 🔧 Helper Functions for SOAP Generation
# ----------------------------

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
    # For now, just return the audio data as-is
    # Can add normalization logic if needed
    return audio_data


def fix_terms(text: str) -> str:
    """Fix common medical terminology errors in transcription"""
    from app.prompts import MEDICAL_TERMINOLOGY_CORRECTIONS
    corrected_text = text
    for incorrect, correct in MEDICAL_TERMINOLOGY_CORRECTIONS.items():
        corrected_text = corrected_text.replace(incorrect, correct)
    return corrected_text


def generate_soap_note_from_transcription(text: str) -> str:
    """
    Generate a structured SOAP note from raw clinical transcription using GPT.
    It corrects grammar and organizes information into Subjective, Objective, Assessment, and Plan sections.
    """
    try:
        client = OpenAI(api_key=OPENAI_KEY)
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
        return text

# ----------------------------
# 🚀 SOAP Note Generation API
# ----------------------------

@app.post("/generate-soap")
async def generate_soap(text: str = Form(...)):
    """
    Generate a structured SOAP note from clinical transcription text.
    Simply provide the transcription text and get back a formatted SOAP note.
    """
    try:
        # Apply medical terminology corrections
        corrected_text = fix_terms(text)
        
        # Generate SOAP note
        logger.info("Generating SOAP note with GPT-4...")
        soap_note = generate_soap_note_from_transcription(corrected_text)

        return JSONResponse({
            "text": corrected_text,
            "soap_note": soap_note,
            "created_at": datetime.utcnow().isoformat()
        })

    except Exception as e:
        logger.error(f"SOAP generation error: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=500)


# Include feedback router
app.include_router(feedback.router, prefix="/api/v1/feedback", tags=["feedback"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True if os.getenv('ENVIRONMENT') == 'development' else False
    )
