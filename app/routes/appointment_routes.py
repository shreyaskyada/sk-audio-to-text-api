from fastapi import APIRouter, HTTPException, Body
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])

@router.get("/")
async def get_all():
    return await AppointmentService.get_all()

@router.get("/stats")
async def get_stats():
    return await AppointmentService.get_stats()

@router.get("/completed-ids")
async def get_completed_ids():
    return await AppointmentService.get_completed_ids()

@router.put("/{appointment_id}")
async def update_status(appointment_id: str, payload: dict = Body(...)):
    status = payload.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status is required")
    
    success = await AppointmentService.update_status(appointment_id, status)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update appointment status")
    
    return {"message": "Status updated successfully"}

@router.post("/{appointment_id}/reset-reports")
async def reset_reports(appointment_id: str):
    success = await AppointmentService.reset_reports(appointment_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset appointment reports")
    return {"message": "Appointment reports reset successfully"}

@router.post("/sync")
async def sync_appointments():
    try:
        await AppointmentService.sync_with_transcriptions()
        return {"message": "Appointments synchronized successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sync appointments: {str(e)}")
