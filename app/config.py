import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "Audio to Text API"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    
    # MongoDB Settings
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "audio-to-text-db"
    MONGODB_DATABASE: Optional[str] = None  # Alias for MONGODB_DB_NAME
    
    # OpenAI Settings
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"
    
    # Deepgram Settings
    DEEPGRAM_API_KEY: Optional[str] = None
    
    # Storage Settings
    AUDIO_STORAGE_PATH: str = "audio_storage"
    UPLOAD_PATH: str = "./uploads"
    MAX_FILE_SIZE_MB: str = "100"
    
    # Database (SQLite fallback)
    DATABASE_URL: Optional[str] = None
    
    # Security & Auth
    SECRET_KEY: str = "your_secret_key_here"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: str = "30"
    ENCRYPTION_KEY: str = "your_32_byte_encryption_key_here"
    AUTH_USERNAME: str = "admin"
    AUTH_PASSWORD: str = "password"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # આ મહત્વપૂર્ણ છે - extra fields ને ignore કરશે
    )

settings = Settings()
