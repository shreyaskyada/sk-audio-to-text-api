from fastapi import APIRouter, Request
from app.schemas.log_schema import ClientLogRequest
from app.services import log_service

router = APIRouter()

@router.post("/client-logs", status_code=201)
async def log_client_error(log_data: ClientLogRequest, request: Request):
    data = log_data.model_dump()
    data["source"] = "client"
    data["ip_address"] = request.client.host if request.client else None
    status = await log_service.log_entry(data)
    return {"status": "logged" if status != "skipped" else "skipped"}
