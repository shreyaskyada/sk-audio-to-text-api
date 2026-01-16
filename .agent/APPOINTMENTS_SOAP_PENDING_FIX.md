# Appointments API - SOAP Pending Status Fix

## Issue
The `/api/v1/appointments/` endpoint was returning an empty array even though there were SOAP notes with `status: "pending"` in the database.

## Root Cause
The `sync_appointments_with_transcriptions()` function was marking appointments as "completed" for **ANY** SOAP note, regardless of whether the SOAP status was "pending" or "completed". This caused:

1. Appointments with pending SOAP notes were marked as "completed"
2. The sync logic didn't differentiate between pending and completed SOAP notes
3. Appointments appeared in the wrong status category

## Solution Applied

### 1. Updated Sync Logic (`app/api/appointment_storage.py`)

**Before:**
```python
# Get IDs from SOAP notes (any status)
soap_user_ids = await db[SOAP_NOTES_COLLECTION].distinct("userId")
soap_ids = [str(uid) for uid in soap_user_ids if uid]

# Set 'completed' for those with SOAP notes
if soap_ids:
    await db[APPOINTMENTS_COLLECTION].update_many(
        {"appointment_id": {"$in": soap_ids}},
        {"$set": {"status": "completed", ...}}
    )
```

**After:**
```python
# Get IDs from COMPLETED SOAP notes only
soap_user_ids = await db[SOAP_NOTES_COLLECTION].distinct("userId", {"status": "completed"})
soap_ids = [str(uid) for uid in soap_user_ids if uid]

# Get IDs from PENDING SOAP notes
pending_soap_user_ids = await db[SOAP_NOTES_COLLECTION].distinct("userId", {"status": "pending"})
pending_soap_ids = [str(uid) for uid in pending_soap_user_ids if uid]

# Set 'completed' for those with COMPLETED SOAP notes
if soap_ids:
    await db[APPOINTMENTS_COLLECTION].update_many(
        {"appointment_id": {"$in": soap_ids}},
        {"$set": {"status": "completed", ...}}
    )

# Set 'soap pending' for those with PENDING SOAP notes
pending_ids = list(set(transcription_ids) - set(soap_ids))
pending_ids = list(set(pending_ids) | set(pending_soap_ids))
if pending_ids:
    await db[APPOINTMENTS_COLLECTION].update_many(
        {"appointment_id": {"$in": pending_ids}},
        {"$set": {"status": "soap pending", ...}}
    )
```

### 2. Added Manual Sync Endpoint (`app/api/appointments.py`)

Created a new endpoint to manually trigger synchronization:

```python
@router.post("/sync")
async def sync_appointments():
    """
    Manually trigger synchronization of appointments with transcriptions and SOAP notes.
    """
    await sync_appointments_with_transcriptions()
    return {"message": "Appointments synchronized successfully"}
```

## New Appointment Status Logic

1. **"completed"** - Has a SOAP note with `status: "completed"`
2. **"soap pending"** - Has:
   - A SOAP note with `status: "pending"`, OR
   - A transcription but no completed SOAP note
3. **"scheduled"** - Has no transcription or SOAP note

## Usage

### Automatic Sync
The sync runs automatically on server startup.

### Manual Sync
Trigger sync manually when needed:
```bash
curl -X POST http://localhost:8000/api/v1/appointments/sync
```

### Get Appointments
```bash
curl http://localhost:8000/api/v1/appointments/
```

## Test Results

**Before Fix:**
```bash
curl http://localhost:8000/api/v1/appointments/
# Returns: []
```

**After Fix:**
```bash
curl http://localhost:8000/api/v1/appointments/
# Returns: [
#   {"appointment_id": "1", "patient": "John Martinez", "status": "scheduled"},
#   {"appointment_id": "2", "patient": "Sarah Chen", "status": "scheduled"},
#   {"appointment_id": "3", "patient": "Michael Johnson", "status": "soap pending"},
#   ...
# ]
```

## Benefits

✅ **Correct Status Tracking** - Appointments reflect actual SOAP note status  
✅ **Pending SOAP Detection** - Properly identifies SOAP notes being generated  
✅ **Manual Sync Option** - Can refresh appointment statuses on demand  
✅ **Better Visibility** - Frontend can show accurate appointment states  

## Related Files
- `app/api/appointment_storage.py` (lines 119-163) - Sync logic
- `app/api/appointments.py` (lines 54-70) - Manual sync endpoint

## Status: ✅ FIXED

Appointments now correctly show "soap pending" status when SOAP notes are pending, and the API returns all appointments with their correct statuses.
