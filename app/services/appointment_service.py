import logging
from datetime import datetime
from typing import Dict, Optional, List, Any
from bson import ObjectId
from app.database import get_database

logger = logging.getLogger(__name__)

APPOINTMENTS_COLLECTION = 'appointments'
TRANSCRIPTIONS_COLLECTION = 'transcriptions'
SOAP_NOTES_COLLECTION = 'soap_notes'
PR1_FORMS_COLLECTION = 'saved_pr1_forms'
WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'

class AppointmentService:
    @staticmethod
    async def get_all() -> List[Dict]:
        """Retrieve all appointments from MongoDB"""
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

    @staticmethod
    async def get_stats() -> Dict:
        """Get appointment statistics."""
        db = get_database()
        if db is None:
            return {"total": 0, "completed": 0, "upcoming": 0, "next_appointment": "None"}
        
        total = await db[APPOINTMENTS_COLLECTION].count_documents({"patient": {"$exists": True}})
        completed = await db[APPOINTMENTS_COLLECTION].count_documents({"status": "completed"})
        upcoming = await db[APPOINTMENTS_COLLECTION].count_documents({"status": "scheduled"})
        
        cursor = db[APPOINTMENTS_COLLECTION].find({"status": "scheduled"}).sort("time", 1).limit(1)
        next_apt = "None"
        async for doc in cursor:
            next_apt = doc.get("time", "None")
            
        return {
            "total": total,
            "completed": completed,
            "upcoming": upcoming,
            "next_appointment": next_apt
        }

    @staticmethod
    async def update_status(appointment_id: str, status: str) -> bool:
        """Update appointment status in MongoDB."""
        db = get_database()
        if db is None: return False
        result = await db[APPOINTMENTS_COLLECTION].update_one(
            {"appointment_id": appointment_id},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None

    @staticmethod
    async def get_completed_ids() -> List[str]:
        """Get all IDs of appointments that are completed."""
        db = get_database()
        if db is None: return []
        cursor = db[APPOINTMENTS_COLLECTION].find({"status": "completed"})
        completed_ids = []
        async for doc in cursor:
            aid = doc.get("appointment_id")
            if aid:
                completed_ids.append(str(aid))
        return completed_ids

    @staticmethod
    async def sync_with_transcriptions():
        """Sync appointments with transcriptions and SOAP notes."""
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
            
        pending_ids = list(set(transcription_ids) - set(soap_ids))
        pending_ids = list(set(pending_ids) | set(pending_soap_ids))
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

    @staticmethod
    async def reset_reports(appointment_id: str) -> bool:
        """Reset reports associated with this appointment."""
        db = get_database()
        if db is None: return False
        
        soap_cursor = db[SOAP_NOTES_COLLECTION].find({"userId": appointment_id})
        soap_ids = [str(doc["_id"]) async for doc in soap_cursor]
            
        if not soap_ids: return True
        
        await db[PR1_FORMS_COLLECTION].delete_many({"soap_id": {"$in": soap_ids}})
        await db[WORK_STATUS_FORMS_COLLECTION].delete_many({"soap_id": {"$in": soap_ids}})
        await db[SOAP_NOTES_COLLECTION].delete_many({"userId": appointment_id})
        return True

    @staticmethod
    async def seed_mock_data():
        """Seed mockup appointments if collection is empty."""
        db = get_database()
        if db is None: return
        
        mock_appointments = [
            {"appointment_id": "1", "patient": "John Martinez", "status": "scheduled", "time": "09:00 AM"},
            {"appointment_id": "2", "patient": "Sarah Chen", "status": "scheduled", "time": "10:30 AM"},
            {"appointment_id": "3", "patient": "Michael Johnson", "status": "scheduled", "time": "02:00 PM"},
            {"appointment_id": "4", "patient": "Emily Davis", "status": "scheduled", "time": "03:30 PM"},
            {"appointment_id": "5", "patient": "David Lee", "status": "scheduled", "time": "08:30 AM"},
            {"appointment_id": "6", "patient": "Lisa Wilson", "status": "scheduled", "time": "11:15 AM"},
            {"appointment_id": "7", "patient": "Robert Brown", "status": "scheduled", "time": "01:00 PM"},
            {"appointment_id": "8", "patient": "Jennifer Taylor", "status": "scheduled", "time": "04:00 PM"},
            {"appointment_id": "9", "patient": "Mark Anderson", "status": "scheduled", "time": "08:00 AM"},
            {"appointment_id": "10", "patient": "Amanda Garcia", "status": "scheduled", "time": "12:30 PM"},
        ]
        
        for apt in mock_appointments:
            existing = await db[APPOINTMENTS_COLLECTION].find_one({"appointment_id": apt["appointment_id"]})
            if not existing:
                await db[APPOINTMENTS_COLLECTION].insert_one(apt)
