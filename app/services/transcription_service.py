import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.database import get_database
from app.config import settings
from app.utils.text_utils import fix_terms
import httpx
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

TRANSCRIPTION_COLLECTION = 'transcriptions'

class TranscriptionService:
    @staticmethod
    async def save_transcription(data: dict) -> dict:
        db = get_database()
        if db is None:
            raise Exception("Database not available")
        data["created_at"] = datetime.utcnow()
        result = await db[TRANSCRIPTION_COLLECTION].insert_one(data)
        data["_id"] = str(result.inserted_id)
        return data

    @staticmethod
    async def get_by_id(transcription_id: str) -> Optional[dict]:
        db = get_database()
        if db is None:
            return None
        doc = await db[TRANSCRIPTION_COLLECTION].find_one({"_id": ObjectId(transcription_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    @staticmethod
    async def get_all(limit: int = 100, skip: int = 0) -> List[dict]:
        db = get_database()
        if db is None:
            return []
        cursor = db[TRANSCRIPTION_COLLECTION].find().sort("created_at", -1).skip(skip).limit(limit)
        docs = await cursor.to_list(length=limit)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return docs

    @staticmethod
    async def get_count() -> int:
        db = get_database()
        if db is None:
            return 0
        return await db[TRANSCRIPTION_COLLECTION].count_documents({})

    @staticmethod
    async def update(transcription_id: str, data: dict) -> bool:
        db = get_database()
        if db is None:
            return False
        result = await db[TRANSCRIPTION_COLLECTION].update_one(
            {"_id": ObjectId(transcription_id)}, {"$set": data}
        )
        return result.modified_count > 0

    @staticmethod
    async def delete(transcription_id: str) -> bool:
        db = get_database()
        if db is None:
            return False
        result = await db[TRANSCRIPTION_COLLECTION].delete_one({"_id": ObjectId(transcription_id)})
        return result.deleted_count > 0

    @staticmethod
    async def get_user_ids() -> List[str]:
        db = get_database()
        if db is None:
            return []
        return await db[TRANSCRIPTION_COLLECTION].distinct("user_id")

    @staticmethod
    async def get_latest_by_user_id(user_id: str) -> Optional[dict]:
        db = get_database()
        if db is None:
            return None
        doc = await db[TRANSCRIPTION_COLLECTION].find_one(
            {"user_id": user_id}, sort=[("created_at", -1)]
        )
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
        
    @staticmethod
    async def run_transcription(file_content: bytes, content_type: str) -> Dict[str, Any]:
        """Call Deepgram API"""
        query_params = {
            "model": "nova-2",
            "smart_format": "true",
            "language": "en",
            "punctuate": "true",
            "keywords": "Quervain:1,Tenosynovitis:1"
        }
        url = f"https://api.deepgram.com/v1/listen?{urlencode(query_params)}"
        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}", 
            "Content-Type": content_type
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, content=file_content)
                response.raise_for_status()
                result = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Deepgram API error: {e.response.status_code} - {e.response.text}")
            raise Exception(f"Transcription service error: {e.response.text}")
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise Exception(f"Failed to process transcription: {str(e)}")
        
        alternative = result["results"]["channels"][0]["alternatives"][0]
        text = alternative["transcript"].strip()
        text = fix_terms(text)
        
        return {
            'text': text,
            'confidence': alternative.get("confidence", 0.0),
            'language': 'en-US',
            'duration': result.get("metadata", {}).get("duration", 0.0)
        }
