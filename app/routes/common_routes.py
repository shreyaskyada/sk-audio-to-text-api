from fastapi import APIRouter, Depends
from datetime import datetime
from app.database import get_database
from app.config import settings

router = APIRouter(tags=["common"])

@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": settings.TITLE,
        "version": settings.VERSION,
        "status": "running"
    }

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    db = get_database()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "services": {
            "deepgram": "configured" if settings.DEEPGRAM_API_KEY else "not configured",
            "openai": "configured" if settings.OPENAI_API_KEY else "not configured",
            "mongodb": "connected" if db is not None else "disconnected"
        }
    }
