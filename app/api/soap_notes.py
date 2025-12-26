"""
SOAP Notes API endpoints for retrieving stored notes
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional

from app.api.soap_storage import (
    get_soap_note_by_id,
    get_all_soap_notes,
    get_soap_notes_stats,
    update_soap_note,
    delete_soap_note
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# SOAP NOTES API ENDPOINTS
# ============================================

from fastapi.encoders import jsonable_encoder

@router.get("/all")
async def get_all_stored_soap_notes(
    limit: int = Query(100, ge=1, le=500, description="Maximum number of notes to return"),
    skip: int = Query(0, ge=0, description="Number of notes to skip")
):
    """
    Get all stored SOAP notes with pagination
    
    **Parameters:**
    - limit: Maximum number of notes to return (default: 100, max: 500)
    - skip: Number of notes to skip for pagination (default: 0)
    
    **Returns:**
    - List of SOAP notes sorted by creation date (newest first)
    - Each note includes transcription, SOAP sections, and metadata
    
    **Example:**
    - Get first 100 notes: `/api/v1/soap-notes/all?limit=100&skip=0`
    - Get next 100 notes: `/api/v1/soap-notes/all?limit=100&skip=100`
    """
    try:
        soap_notes = await get_all_soap_notes(limit=limit, skip=skip)
        
        return JSONResponse(jsonable_encoder({
            "total_returned": len(soap_notes),
            "limit": limit,
            "skip": skip,
            "soap_notes": soap_notes
        }))
        
    except Exception as e:
        logger.error(f"Error retrieving SOAP notes: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve SOAP notes"
        )


@router.get("/stats")
async def get_soap_statistics():
    """
    Get statistics about stored SOAP notes
    
    **Returns:**
    - total_soap_notes: Total number of SOAP notes in database
    - soap_notes_with_custom_prompts: Number of notes generated with custom prompts
    - soap_notes_with_default_prompts: Number of notes generated with default prompts
    - recent_soap_notes: List of 10 most recent SOAP notes
    
    **Example:**
    ```json
    {
        "total_soap_notes": 150,
        "soap_notes_with_custom_prompts": 25,
        "soap_notes_with_default_prompts": 125,
        "recent_soap_notes": [...]
    }
    ```
    """
    try:
        stats = await get_soap_notes_stats()
        
        return JSONResponse(jsonable_encoder(stats))
        
    except Exception as e:
        logger.error(f"Error getting SOAP notes statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get SOAP notes statistics"
        )


@router.get("/{soap_note_id}")
async def get_soap_note(soap_note_id: str):
    """
    Get a specific SOAP note by ID
    
    **Parameters:**
    - soap_note_id: MongoDB ObjectId of the SOAP note
    
    **Returns:**
    - Complete SOAP note document with all fields
    
    **Example:**
    - `/api/v1/soap-notes/507f1f77bcf86cd799439011`
    """
    try:
        soap_note = await get_soap_note_by_id(soap_note_id)
        
        if soap_note is None:
            raise HTTPException(
                status_code=404,
                detail=f"SOAP note not found: {soap_note_id}"
            )
        
        return JSONResponse(jsonable_encoder(soap_note))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving SOAP note {soap_note_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve SOAP note"
        )


@router.put("/{soap_note_id}")
async def update_stored_soap_note(soap_note_id: str, update_data: dict):
    """
    Update a stored SOAP note
    
    **Parameters:**
    - soap_note_id: MongoDB ObjectId of the SOAP note
    - update_data: Dictionary with fields to update
    
    **Returns:**
    - Success message
    
    **Example Request:**
    ```json
    {
        "formatted_soap_note": "Updated SOAP note content...",
        "subjective": "Updated subjective section..."
    }
    ```
    """
    try:
        success = await update_soap_note(soap_note_id, update_data)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"SOAP note not found or not modified: {soap_note_id}"
            )
        
        return JSONResponse({
            "message": "SOAP note updated successfully",
            "soap_note_id": soap_note_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating SOAP note {soap_note_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update SOAP note"
        )


@router.delete("/{soap_note_id}")
async def delete_stored_soap_note(soap_note_id: str):
    """
    Delete a stored SOAP note
    
    **Parameters:**
    - soap_note_id: MongoDB ObjectId of the SOAP note
    
    **Returns:**
    - Success message
    
    **Example:**
    - DELETE `/api/v1/soap-notes/507f1f77bcf86cd799439011`
    """
    try:
        success = await delete_soap_note(soap_note_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"SOAP note not found: {soap_note_id}"
            )
        
        return JSONResponse({
            "message": "SOAP note deleted successfully",
            "soap_note_id": soap_note_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting SOAP note {soap_note_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete SOAP note"
        )

