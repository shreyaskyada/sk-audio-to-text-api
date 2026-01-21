import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from dotenv import load_dotenv

# Load .env file explicitly
load_dotenv()

class Settings(BaseSettings):
    # App Settings
    TITLE: str = "Medical Transcription & SOAP Note API"
    VERSION: str = "1.0.0"
    
    # Auth Settings
    SECRET_KEY: str = os.getenv('SECRET_KEY', 'change-this-secret-key')
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200  # 30 days
    AUTH_USERNAME: str = os.getenv('AUTH_USERNAME', 'Goldy@gmail.com')
    AUTH_PASSWORD: str = os.getenv('AUTH_PASSWORD', '123456')
    
    # MongoDB Settings
    MONGODB_URL: str = os.getenv('MONGODB_URL', 'mongodb://localhost:27017')
    MONGODB_DB_NAME: str = os.getenv('MONGODB_DATABASE', os.getenv('MONGODB_DB_NAME', 'audio-to-text-db'))
    
    # API Keys
    OPENAI_API_KEY: str = os.getenv('OPENAI_API_KEY', '')
    DEEPGRAM_API_KEY: str = os.getenv('DEEPGRAM_API_KEY', '')
    RESAMPLER_API_URL: str = os.getenv('RESAMPLER_API_URL', 'http://16.171.115.103:8001')
    OPENAI_MODEL: str = os.getenv('OPENAI_MODEL', 'gpt-4o')
    
    # Storage
    AUDIO_STORAGE_DIR: str = os.getenv('AUDIO_STORAGE_DIR', 'audio_storage')
    MAX_FILE_SIZE_MB: int = int(os.getenv('MAX_FILE_SIZE_MB', 100))
    
    # CORS
    ALLOWED_ORIGINS: List[str] = ["*", "http://16.171.115.103:8000"]

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

settings = Settings()
