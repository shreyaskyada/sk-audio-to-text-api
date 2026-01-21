import logging
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)

class Database:
    mongo_client: Optional[AsyncIOMotorClient] = None
    mongo_db = None

db = Database()

async def connect_to_mongo():
    """Connect to MongoDB"""
    try:
        db.mongo_client = AsyncIOMotorClient(settings.MONGODB_URL)
        db.mongo_db = db.mongo_client[settings.MONGODB_DB_NAME]
        
        # Test connection
        await db.mongo_client.admin.command('ping')
        
        logger.info(f"✅ Connected to MongoDB: {settings.MONGODB_DB_NAME}")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return False

async def close_mongo_connection():
    """Close MongoDB connection"""
    if db.mongo_client:
        db.mongo_client.close()
        logger.info("MongoDB connection closed")

def get_database():
    """Get MongoDB database instance"""
    return db.mongo_db

def get_database_by_name(db_name: str):
    """Get MongoDB database instance by name"""
    if db.mongo_client is None:
        return None
    return db.mongo_client[db_name]
