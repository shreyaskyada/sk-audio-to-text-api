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
        logger.info(f"✅ Connected to MongoDB: {MONGODB_DB_NAME}")
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

