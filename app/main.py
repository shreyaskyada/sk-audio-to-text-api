from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Optional
import jwt
from pydantic import BaseModel

from app.database import get_db, engine
from app.models import Base
from app.schemas import TranscriptionResponse, TranscriptionRequest
from app.services.transcription_service import TranscriptionService
from app.services.hipaa_compliance import HIPAAComplianceService
from app.utils.encryption import EncryptionService
from app.api import feedback

# Load environment variables
load_dotenv()

# Create database tables
Base.metadata.create_all(bind=engine)

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
    db: Session = Depends(get_db),
    username: str = Depends(verify_token)
):
    """
    Transcribe audio file with HIPAA compliance
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
            details=f"File: {file.filename}, Size: {file.size}",
            db=db
        )
        
        # Read file content
        file_content = await file.read()
        
        # Perform transcription directly
        transcription_result = await transcription_service.transcribe_audio(
            file_content,
            file.filename
        )
        
        # Log successful transcription
        await hipaa_service.log_access(
            user_id=username,
            action="transcription_complete",
            details=f"File: {file.filename} transcribed successfully",
            db=db
        )
        
        return TranscriptionResponse(
            transcription_id=f"temp_{datetime.utcnow().timestamp()}",
            text=transcription_result['text'],
            confidence=transcription_result.get('confidence', 0.0),
            language=transcription_result.get('language', 'unknown'),
            duration=transcription_result.get('duration', 0.0),
            created_at=datetime.utcnow()
        )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during transcription"
        )

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
