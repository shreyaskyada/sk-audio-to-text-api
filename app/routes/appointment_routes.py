import logging
from fastapi import APIRouter, HTTPException, Body
from app.services import appointment_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/")
async def get_all_appointments():
    return await appointment_service.get_all_appointments()

@router.get("/stats")
async def get_appointment_stats():
    return await appointment_service.get_appointment_stats()

@router.get("/completed-ids")
async def get_completed_ids():
    return await appointment_service.get_all_completed_ids()

@router.put("/{appointment_id}")
async def update_appointment_status(appointment_id: str, payload: dict = Body(...)):
    status = payload.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status is required")
    
    success = await appointment_service.update_appointment_status(appointment_id, status)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update appointment status")
    
    return {"message": "Status updated successfully"}

@router.post("/{appointment_id}/reset-reports")
async def reset_appointment_reports(appointment_id: str):
    success = await appointment_service.reset_reports(appointment_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset appointment reports")
    
    return {"message": "Appointment reports reset successfully"}

@router.post("/sync")
async def sync_appointments():
    try:
        await appointment_service.sync_appointments()
        return {"message": "Appointments synchronized successfully"}
    except Exception as e:
        logger.error(f"Error syncing appointments: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to sync appointments: {str(e)}")
