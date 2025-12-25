from fastapi import APIRouter, Request, HTTPException
from app.schemas import ClientLogRequest
from app.mongodb import get_database
from datetime import datetime

router = APIRouter()

@router.post("/client-logs", status_code=201)
async def log_client_error(log_data: ClientLogRequest, request: Request):
    """
    Log an error from the frontend/client to the database.
    """
    db = get_database()
    if db is None:
        return {"status": "skipped", "message": "Database not available"}
        
    log_entry = log_data.model_dump()
    log_entry["timestamp"] = datetime.utcnow()
    log_entry["source"] = "client"
    log_entry["ip_address"] = request.client.host if request.client else None
    
    # Insert into api_logs (or separate client_logs if preferred, but keeping centralized is often good)
    # Let's use api_logs but tag source=client
    await db.api_logs.insert_one(log_entry)
    
    return {"status": "logged"}
