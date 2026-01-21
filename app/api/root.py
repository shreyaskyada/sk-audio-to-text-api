
from fastapi import APIRouter
import os
from app.mongodb import get_database

router = APIRouter()

DEEPGRAM_API_KEY = os.getenv('DEEPGRAM_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

@router.get("/")
async def root():
    return {
        "message": "Medical Transcription & SOAP Note API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "auth": "/api/v1/auth/login",
            "transcribe": "/api/v1/transcribe",
            "soap": "/api/v1/generate-soap"
        }
    }

@router.get("/health")
async def health_check():
    db = get_database()
    return {
        "status": "healthy",
        "services": {
            "deepgram": "configured" if DEEPGRAM_API_KEY else "not configured",
            "openai": "configured" if OPENAI_API_KEY else "not configured",
            "mongodb": "connected" if db is not None else "disconnected"
        }
    }
