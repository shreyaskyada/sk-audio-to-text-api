from fastapi import APIRouter, Request, HTTPException
from app.schemas import ClientLogRequest
from app.services.log_service import log_client_error_service

router = APIRouter()

@router.post("/client-logs", status_code=201)
async def log_client_error(log_data: ClientLogRequest, request: Request):
    """
    Log an error from the frontend/client to the database.
    """
    log_dict = log_data.model_dump()
    ip = request.client.host if request.client else None
    
    success = await log_client_error_service(log_dict, ip_address=ip)
    
    if not success:
         return {"status": "skipped", "message": "Logging failed or database unavailable"}
    
    return {"status": "logged"}
