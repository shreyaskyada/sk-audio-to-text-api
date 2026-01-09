"""
Appointment Storage functions for MongoDB
"""
import logging
from datetime import datetime
from typing import Dict, Optional, List
from bson import ObjectId

from app.mongodb import get_database

logger = logging.getLogger(__name__)

# Configuration
APPOINTMENTS_COLLECTION = 'appointments'
TRANSCRIPTIONS_COLLECTION = 'transcriptions'
SOAP_NOTES_COLLECTION = 'soap_notes'
PR1_FORMS_COLLECTION = 'saved_pr1_forms'
WORK_STATUS_FORMS_COLLECTION = 'work_status_forms'

async def get_all_appointments() -> List[Dict]:
    """Retrieve all appointments from MongoDB"""
    try:
        db = get_database()
        if db is None:
            return []
        
        cursor = db[APPOINTMENTS_COLLECTION].find({"patient": {"$exists": True}})
        appointments = []
        async for doc in cursor:
            doc['_id'] = str(doc['_id'])
            appointments.append(doc)
        return appointments
    except Exception as e:
        logger.error(f"Error getting appointments: {e}")
        return []

async def get_appointment_stats():
    """
    Get appointment statistics.
    """
    try:
        db = get_database()
        if db is None:
            return {"total": 0, "completed": 0, "upcoming": 0, "next_appointment": "None"}
        
        total = await db[APPOINTMENTS_COLLECTION].count_documents({"patient": {"$exists": True}})
        completed = await db[APPOINTMENTS_COLLECTION].count_documents({"status": "completed"})
        upcoming = await db[APPOINTMENTS_COLLECTION].count_documents({"status": "scheduled"})
        
        # Get next appointment
        cursor = db[APPOINTMENTS_COLLECTION].find(
            {"status": "scheduled"}
        ).sort("time", 1).limit(1)
        
        next_apt = "None"
        async for doc in cursor:
            next_apt = doc.get("time", "None")
            
        return {
            "total": total,
            "completed": completed,
            "upcoming": upcoming,
            "next_appointment": next_apt
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return {"total": 0, "completed": 0, "upcoming": 0, "next_appointment": "None"}

async def update_appointment_status(appointment_id: str, status: str):
    """
    Update appointment status in MongoDB.
    """
    try:
        db = get_database()
        if db is None:
            return False
        
        result = await db[APPOINTMENTS_COLLECTION].update_one(
            {"appointment_id": appointment_id},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}},
            upsert=True
        )
        
        if result.modified_count > 0 or result.upserted_id:
            logger.info(f"✅ Appointment status updated: {appointment_id} -> {status}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating appointment status: {e}")
        return False

async def get_all_completed_appointment_ids() -> List[str]:
    """
    Get all IDs of appointments that are completed.
    Now uses the appointments collection 'status' as source of truth.
    """
    try:
        db = get_database()
        if db is None:
            return []
        
        # Get appointments where status is strictly 'completed'
        cursor = db[APPOINTMENTS_COLLECTION].find({"status": "completed"})
        completed_ids = []
        async for doc in cursor:
            aid = doc.get("appointment_id")
            if aid:
                completed_ids.append(str(aid))
        
        logger.info(f"Retrieved {len(completed_ids)} completed appointment IDs from DB")
        return completed_ids
    except Exception as e:
        logger.error(f"Error getting all completed appointment IDs: {e}")
        return []
    except Exception as e:
        logger.error(f"Error getting all completed appointment IDs: {e}")
        return []

async def sync_appointments_with_transcriptions():
    """
    Sync appointments with transcriptions and SOAP notes:
    1. If a SOAP note exists -> completed
    2. If only a transcription exists -> soap pending
    3. If neither exists -> scheduled
    """
    try:
        db = get_database()
        if db is None:
            return
        
        # Get IDs from transcriptions
        transcription_user_ids = await db[TRANSCRIPTIONS_COLLECTION].distinct("user_id")
        transcription_ids = [str(uid) for uid in transcription_user_ids if uid]
        
        # Get IDs from SOAP notes
        soap_user_ids = await db[SOAP_NOTES_COLLECTION].distinct("userId")
        soap_ids = [str(uid) for uid in soap_user_ids if uid]
        
        # 1. Set 'completed' for those with SOAP notes
        if soap_ids:
            await db[APPOINTMENTS_COLLECTION].update_many(
                {"appointment_id": {"$in": soap_ids}},
                {"$set": {"status": "completed", "updated_at": datetime.utcnow()}}
            )
            
        # 2. Set 'soap pending' for those with transcriptions but NO SOAP notes
        pending_ids = list(set(transcription_ids) - set(soap_ids))
        if pending_ids:
            await db[APPOINTMENTS_COLLECTION].update_many(
                {"appointment_id": {"$in": pending_ids}},
                {"$set": {"status": "soap pending", "updated_at": datetime.utcnow()}}
            )
            
        # 3. Set 'scheduled' for those WITHOUT transcriptions
        all_done_ids = list(set(transcription_ids) | set(soap_ids))
        await db[APPOINTMENTS_COLLECTION].update_many(
            {"appointment_id": {"$nin": all_done_ids}, "patient": {"$exists": True}},
            {"$set": {"status": "scheduled", "updated_at": datetime.utcnow()}}
        )
            
        logger.info(f"✅ Synced appointments: {len(soap_ids)} completed, {len(pending_ids)} soap pending")
    except Exception as e:
        logger.error(f"Error syncing appointments: {e}")

async def seed_mock_appointments():
    """
    Seed mockup appointments if collection is empty.
    """
    try:
        db = get_database()
        if db is None:
            return
        
        # Cleanup invalid records (those missing patient names, created by previous buggy syncs)
        deleted_invalid = await db[APPOINTMENTS_COLLECTION].delete_many({"patient": {"$exists": False}})
        if deleted_invalid.deleted_count > 0:
            logger.info(f"🗑️ Deleted {deleted_invalid.deleted_count} invalid appointment records")
        
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
        
        seeded_count = 0
        for apt in mock_appointments:
            # Only seed if ID doesn't exist
            existing = await db[APPOINTMENTS_COLLECTION].find_one({"appointment_id": apt["appointment_id"]})
            if not existing:
                await db[APPOINTMENTS_COLLECTION].insert_one(apt)
                seeded_count += 1
        
        if seeded_count > 0:
            logger.info(f"✅ Seeded {seeded_count} new mock appointments")
        
        final_count = await db[APPOINTMENTS_COLLECTION].count_documents({"patient": {"$exists": True}})
        logger.info(f"Database currently contains {final_count} valid appointments")
    except Exception as e:
        logger.error(f"Error seeding mock appointments: {e}")

async def reset_appointment_reports(appointment_id: str) -> bool:
    """
    Reset reports for a given appointment/patient.
    Deletes SOAP note, PR1 form, and Work Status form associated with this appointment ID (user_id).
    This allows the workflow to restart from "Pending Reports" in Patient Cases.
    """
    try:
        db = get_database()
        if db is None:
            return False
            
        logger.info(f"🔄 Resetting reports for appointment_id (userId): {appointment_id}")
        
        # 1. Find the SOAP note(s) for this user to get their IDs
        # We need SOAP IDs because PR1 and Work Status are linked by soap_id
        soap_cursor = db[SOAP_NOTES_COLLECTION].find({"userId": appointment_id})
        soap_ids = []
        async for doc in soap_cursor:
            soap_ids.append(str(doc["_id"]))
            
        if not soap_ids:
            logger.info(f"ℹ️ No SOAP notes found for userId {appointment_id}, nothing to reset.")
            return True
            
        logger.info(f"Found {len(soap_ids)} SOAP notes to delete: {soap_ids}")
        
        # 2. Delete PR1 forms linked to these SOAP IDs
        pr1_result = await db[PR1_FORMS_COLLECTION].delete_many({"soap_id": {"$in": soap_ids}})
        logger.info(f"🗑️ Deleted {pr1_result.deleted_count} PR1 forms")
        
        # 3. Delete Work Status forms linked to these SOAP IDs
        ws_result = await db[WORK_STATUS_FORMS_COLLECTION].delete_many({"soap_id": {"$in": soap_ids}})
        logger.info(f"🗑️ Deleted {ws_result.deleted_count} Work Status forms")
        
        # 4. Delete the SOAP notes themselves
        soap_result = await db[SOAP_NOTES_COLLECTION].delete_many({"userId": appointment_id})
        logger.info(f"🗑️ Deleted {soap_result.deleted_count} SOAP notes")
        
        return True
    except Exception as e:
        logger.error(f"Error resetting appointment reports: {e}")
        return False
