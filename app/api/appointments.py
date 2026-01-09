"""
Appointments API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException, Body
from app.api.appointment_storage import get_appointment_stats, update_appointment_status, reset_appointment_reports

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/")
async def get_all():
    """Get all appointments"""
    from app.api.appointment_storage import get_all_appointments
    return await get_all_appointments()

@router.get("/stats")
async def get_stats():
    """Get appointment statistics"""
    return await get_appointment_stats()

@router.get("/completed-ids")
async def get_completed_ids():
    """Get all completed appointment IDs from DB"""
    from app.api.appointment_storage import get_all_completed_appointment_ids
    return await get_all_completed_appointment_ids()

@router.put("/{appointment_id}")
async def update_status(appointment_id: str, payload: dict = Body(...)):
    """Update appointment status"""
    status = payload.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status is required")
    
    success = await update_appointment_status(appointment_id, status)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update appointment status")
    
    return {"message": "Status updated successfully"}

@router.post("/{appointment_id}/reset-reports")
async def reset_reports(appointment_id: str):
    """
    Reset all reports (SOAP, PR1, Work Status) for a given appointment/patient.
    This is used when a transcription is updated, invalidating previous reports.
    """
    success = await reset_appointment_reports(appointment_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset appointment reports")
    
    return {"message": "Appointment reports reset successfully"}
