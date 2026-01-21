from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from app.services.followup_service import FollowupService

router = APIRouter(prefix="/api/v1", tags=["followup"])

@router.post("/followup-intake")
async def create_followup_form(data: Dict[str, Any]):
    return await FollowupService.save_form(data)

@router.get("/follow-up/latest")
async def get_latest_followup():
    doc = await FollowupService.get_latest()
    if not doc:
        raise HTTPException(status_code=404, detail="No followup form found")
    return doc
