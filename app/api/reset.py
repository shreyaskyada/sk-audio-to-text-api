from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import logging
from app.mongodb import reset_database_on_login

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/reset-database")
async def reset_database_endpoint():
    """
    Reset database by clearing all collections.
    This endpoint is called when a user logs in to clear all previous data.
    """
    try:
        logger.info("🔄 Database reset requested via router...")
        result = await reset_database_on_login()
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "Database reset successfully",
                "result": result
            }
        )
    except Exception as e:
        logger.error(f"❌ Database reset failed: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Database reset failed",
                "error": str(e)
            }
        )
