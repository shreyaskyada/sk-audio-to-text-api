import logging
from datetime import datetime
from typing import Dict, Optional, List
from app.database import get_database

logger = logging.getLogger(__name__)

APPOINTMENTS_COLLECTION = 'appointments'
TRANSCRIPTIONS_COLLECTION = 'transcriptions'
SOAP_NOTES_COLLECTION = 'soap_notes'
PR1_FORMS_COLLECTION = 'saved_pr1_forms'
WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'

async def get_all_appointments() -> List[Dict]:
    """Retrieve all appointments from MongoDB"""
    try:
        db = get_database()
        if db is None: return []
        cursor = db[APPOINTMENTS_COLLECTION].find({})
        appointments = []
        async for doc in cursor:
            doc['_id'] = str(doc['_id'])
            if "patient" not in doc:
                doc["patient"] = "Unknown Patient"
            appointments.append(doc)
        return appointments
    except Exception as e:
        logger.error(f"Error getting appointments: {e}")
        return []

async def get_appointment_stats():
    """Get appointment statistics"""
    try:
        db = get_database()
        if db is None: return {"total": 0, "completed": 0, "upcoming": 0, "next_appointment": "None"}
        
        total = await db[APPOINTMENTS_COLLECTION].count_documents({"patient": {"$exists": True}})
        completed = await db[APPOINTMENTS_COLLECTION].count_documents({"status": "completed"})
        upcoming = await db[APPOINTMENTS_COLLECTION].count_documents({"status": "scheduled"})
        
        cursor = db[APPOINTMENTS_COLLECTION].find({"status": "scheduled"}).sort("time", 1).limit(1)
        next_apt = "None"
        async for doc in cursor:
            next_apt = doc.get("time", "None")
            
        return {"total": total, "completed": completed, "upcoming": upcoming, "next_appointment": next_apt}
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return {"total": 0, "completed": 0, "upcoming": 0, "next_appointment": "None"}

async def update_appointment_status(appointment_id: str, status: str):
    """Update appointment status in MongoDB"""
    try:
        db = get_database()
        if db is None: return False
        result = await db[APPOINTMENTS_COLLECTION].update_one(
            {"appointment_id": appointment_id},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None
    except Exception as e:
        logger.error(f"Error updating appointment status: {e}")
        return False

async def get_all_completed_ids() -> List[str]:
    """Get all IDs of appointments that are completed"""
    try:
        db = get_database()
        if db is None: return []
        cursor = db[APPOINTMENTS_COLLECTION].find({"status": "completed"})
        return [str(doc.get("appointment_id")) async for doc in cursor if doc.get("appointment_id")]
    except Exception as e:
        logger.error(f"Error getting completed appointment IDs: {e}")
        return []

async def sync_appointments():
    """Sync appointments with transcriptions and SOAP notes"""
    try:
        db = get_database()
        if db is None: return
        
        transcription_user_ids = await db[TRANSCRIPTIONS_COLLECTION].distinct("user_id")
        transcription_ids = [str(uid) for uid in transcription_user_ids if uid]
        
        soap_user_ids = await db[SOAP_NOTES_COLLECTION].distinct("userId", {"status": "completed"})
        soap_ids = [str(uid) for uid in soap_user_ids if uid]
        
        pending_soap_user_ids = await db[SOAP_NOTES_COLLECTION].distinct("userId", {"status": "pending"})
        pending_soap_ids = [str(uid) for uid in pending_soap_user_ids if uid]
        
        if soap_ids:
            await db[APPOINTMENTS_COLLECTION].update_many(
                {"appointment_id": {"$in": soap_ids}},
                {"$set": {"status": "completed", "updated_at": datetime.utcnow()}}
            )
            
        pending_ids = list((set(transcription_ids) | set(pending_soap_ids)) - set(soap_ids))
        if pending_ids:
            await db[APPOINTMENTS_COLLECTION].update_many(
                {"appointment_id": {"$in": pending_ids}},
                {"$set": {"status": "soap pending", "updated_at": datetime.utcnow()}}
            )
            
        all_done_ids = list(set(transcription_ids) | set(soap_ids) | set(pending_soap_ids))
        await db[APPOINTMENTS_COLLECTION].update_many(
            {"appointment_id": {"$nin": all_done_ids}, "patient": {"$exists": True}},
            {"$set": {"status": "scheduled", "updated_at": datetime.utcnow()}}
        )
    except Exception as e:
        logger.error(f"Error syncing appointments: {e}")

async def reset_reports(appointment_id: str) -> bool:
    """Reset reports for a given appointment/patient"""
    try:
        db = get_database()
        if db is None: return False
        
        soap_ids = [str(doc["_id"]) async for doc in db[SOAP_NOTES_COLLECTION].find({"userId": appointment_id})]
        if not soap_ids: return True
        
        await db[PR1_FORMS_COLLECTION].delete_many({"soap_id": {"$in": soap_ids}})
        await db[WORK_STATUS_FORMS_COLLECTION].delete_many({"soap_id": {"$in": soap_ids}})
        await db[SOAP_NOTES_COLLECTION].delete_many({"userId": appointment_id})
        
        return True
    except Exception as e:
        logger.error(f"Error resetting reports: {e}")
        return False
