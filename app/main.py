
"""
Medical Transcription & SOAP Note API
Clean, modular FastAPI application with MongoDB feedback storage
"""

import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.mongodb import connect_to_mongo, close_mongo_connection, get_database
from app.services.appointment_storage import seed_mock_appointments, sync_appointments_with_transcriptions

# Import API routers
from app.api import (
    auth,
    transcriptions,
    soap_generation,
    soap_notes,
    intake_forms,
    followup_forms,
    feedback,
    pr1_generator,
    work_status_forms,
    pr2_forms,
    logs,
    appointments,
    patient_signatures,
    root
)
from app.api.middleware import log_requests_and_exceptions

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Configuration
DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-secret-key')
MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', 100))
AUTH_USERNAME = os.getenv('AUTH_USERNAME', 'Goldy@gmail.com')

# FastAPI App
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
    allow_origins=["*", "http://16.171.115.103:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom Middleware
app.middleware("http")(log_requests_and_exceptions)

# Include Routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(transcriptions.router, prefix="/api/v1")
app.include_router(soap_generation.router, prefix="/api/v1")
app.include_router(soap_notes.router, prefix="/api/v1/soap-notes", tags=["soap-notes"])
app.include_router(feedback.router, prefix="/api/v1/feedback", tags=["feedback"])
app.include_router(intake_forms.router, prefix="/api/v1", tags=["intake-forms"])
app.include_router(followup_forms.router, prefix="/api/v1", tags=["followup-forms"])
app.include_router(pr1_generator.router, prefix="/api/v1", tags=["pr1-generator"])
app.include_router(work_status_forms.router, prefix="/api/v1", tags=["work-status-forms"])
app.include_router(pr2_forms.router, prefix="/api/v1", tags=["pr2-forms"])
app.include_router(patient_signatures.router, prefix="/api/v1", tags=["Patient Signatures"])
app.include_router(logs.router, prefix="/api/v1", tags=["logs"])
app.include_router(appointments.router, prefix="/api/v1/appointments", tags=["appointments"])
app.include_router(root.router)



@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("Starting Medical Transcription & SOAP Note API v2.0.0")
    logger.info("=" * 60)
    
    await connect_to_mongo()
    await seed_mock_appointments()
    await sync_appointments_with_transcriptions()
    
    if not DEEPGRAM_API_KEY:
        logger.error("❌ DEEPGRAM_API_KEY not configured!")
    else:
        logger.info("✅ Deepgram API key configured")
    
    if not OPENAI_API_KEY:
        logger.warning("⚠️  OPENAI_API_KEY not configured")
    else:
        logger.info("✅ OpenAI API key configured")
        
    logger.info("🚀 API is ready!")
    
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 API shutting down...")
    await close_mongo_connection()
    logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
