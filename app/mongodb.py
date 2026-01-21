"""
MongoDB connection and database management
"""
import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional

logger = logging.getLogger(__name__)

# MongoDB client
mongo_client: Optional[AsyncIOMotorClient] = None
mongo_db = None

# Configuration
MONGODB_URL = os.getenv('MONGODB_URL', 'mongodb://localhost:27017')
MONGODB_DB_NAME = os.getenv('MONGODB_DB_NAME', 'audio-to-text-db')


async def connect_to_mongo():
    """Connect to MongoDB"""
    global mongo_client, mongo_db
    try:
        mongo_client = AsyncIOMotorClient(MONGODB_URL)
        mongo_db = mongo_client[MONGODB_DB_NAME]
        
        # Test connection
        await mongo_client.admin.command('ping')
        
        # Log which URL is being used (masked)
        masked_url = MONGODB_URL
        if "@" in MONGODB_URL:
            # Mask credentials: mongodb+srv://user:pass@cluster... -> mongodb+srv://***@cluster...
            try:
                part1 = MONGODB_URL.split("@")[1]
                proto = MONGODB_URL.split("://")[0]
                masked_url = f"{proto}://***@{part1}"
            except:
                pass
        
        logger.info(f"✅ Connected to MongoDB: {MONGODB_DB_NAME} at {masked_url}")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        logger.warning("⚠️  Continuing without MongoDB - feedback storage will be limited")
        return False


async def close_mongo_connection():
    """Close MongoDB connection"""
    global mongo_client
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB connection closed")


def get_database():
    """Get MongoDB database instance"""
    if mongo_db is None:
        return None
    return mongo_db


def get_database_by_name(db_name: str):
    """Get MongoDB database instance by name"""
    if mongo_client is None:
        return None
    return mongo_client[db_name]


async def reset_database_on_login():
    """
    Clear all data from database when user logs in.
    This deletes all documents from all collections.
    """
    try:
        db = get_database()
        if db is None:
            logger.warning("Database connection not available for reset")
            return {"status": "skipped", "message": "No database connection"}
        
        # List of collections to clear
        collections_to_clear = [
            'transcriptions',
            'soap_notes',
            'appointments',
            'intake_forms',
            'feedback',
            'followup_forms',
            'pr1_forms',
            'pr2_forms',
            'work_status_forms',
            'patient_signatures',
            'api_logs'
        ]
        
        cleared_collections = []
        total_deleted = 0
        
        for collection_name in collections_to_clear:
            try:
                collection = db[collection_name]
                result = await collection.delete_many({})
                deleted_count = result.deleted_count
                total_deleted += deleted_count
                if deleted_count > 0:
                    cleared_collections.append({
                        "collection": collection_name,
                        "deleted_count": deleted_count
                    })
                    logger.info(f"🗑️ Cleared {deleted_count} documents from {collection_name}")
            except Exception as e:
                logger.error(f"❌ Error clearing {collection_name}: {e}")
        
        logger.info(f"✅ Database reset complete. Total {total_deleted} documents deleted from {len(cleared_collections)} collections")
        
        return {
            "status": "success",
            "total_deleted": total_deleted,
            "cleared_collections": cleared_collections
        }
        
    except Exception as e:
        logger.error(f"❌ Database reset failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

