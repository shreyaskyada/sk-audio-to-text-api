from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from app.services.intake_service import IntakeService

router = APIRouter(prefix="/api/v1", tags=["intake"])

@router.post("/intake-form")
async def create_intake_form(data: Dict[str, Any]):
    return await IntakeService.save_form(data)

@router.get("/intake-form/latest")
async def get_latest_intake():
    doc = await IntakeService.get_latest()
    if not doc:
        raise HTTPException(status_code=404, detail="No intake form found")
    return doc
