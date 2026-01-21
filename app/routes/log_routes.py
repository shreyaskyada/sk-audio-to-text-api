import logging
from fastapi import APIRouter
from app.schemas.log_schema import ClientLogRequest

logger = logging.getLogger("client_logs")
router = APIRouter(prefix="/api/v1", tags=["logs"])

@router.post("/logs")
async def receive_client_logs(log_data: ClientLogRequest):
    """Receive and log messages from the client"""
    log_msg = f"CLIENT_{log_data.level.upper()}: {log_data.message}"
    if log_data.context:
        log_msg += f" | Context: {log_data.context}"
    
    if log_data.level.lower() == "error":
        logger.error(log_msg)
    elif log_data.level.lower() == "warning":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)
    
    return {"status": "success"}
