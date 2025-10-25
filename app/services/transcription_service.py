import os
import tempfile
from deepgram import Deepgram
from typing import Dict, Any, Optional, TypedDict
import logging
from datetime import datetime
import json
import subprocess
from app.prompts import (
    DEEPGRAM_OPTIONS,
    DEEPGRAM_MODEL,
    MEDICAL_TERMINOLOGY_CORRECTIONS,
    TRANSCRIPTION_SETTINGS,
    CONFIDENCE_SETTINGS,
    FILE_VALIDATION_RULES,
    ERROR_MESSAGES,
    SUCCESS_MESSAGES
)

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Service for audio transcription using Deepgram Nova-3 Medical API"""
    
    def __init__(self):
        api_key = os.getenv('DEEPGRAM_API_KEY')
        if not api_key:
            raise ValueError(ERROR_MESSAGES['api_key_missing'])
        
        # Initialize Deepgram client with v2 syntax
        self.deepgram = Deepgram(api_key)
        self.supported_formats = FILE_VALIDATION_RULES['supported_extensions']
        self.max_file_size = TRANSCRIPTION_SETTINGS['max_file_size_mb'] * 1024 * 1024
        
        logger.info(SUCCESS_MESSAGES['deepgram_connected'])
        logger.info(SUCCESS_MESSAGES['medical_model_active'])
    
    async def transcribe_audio(
        self, 
        audio_data: bytes, 
        filename: str,
        language: Optional[str] = None,
        model: str = None
    ) -> Dict[str, Any]:
        """
        Transcribe audio using Deepgram Nova-3 Medical API
        """
        try:
            # Validate file size
            if len(audio_data) > self.max_file_size:
                raise ValueError(f"File size ({len(audio_data)} bytes) exceeds maximum allowed size ({self.max_file_size} bytes)")
            
            # Create temporary file
            file_extension = self._get_file_extension(filename)
            if file_extension not in self.supported_formats:
                raise ValueError(f"Unsupported file format: {file_extension}")
            
            # Handle OPUS files by converting to WAV
            if file_extension == 'opus':
                logger.info(SUCCESS_MESSAGES['opus_converted'])
                audio_data = self._convert_opus_to_wav(audio_data)
                file_extension = 'wav'
            
            with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_file_path = temp_file.name
            
            try:
                # Transcribe using Deepgram Nova-3 Medical
                with open(temp_file_path, 'rb') as audio_file:
                    audio_data_bytes = audio_file.read()
                    
                    # Determine mimetype based on file extension
                    mimetype_map = {
                        'mp3': 'audio/mpeg',
                        'mp4': 'audio/mp4',
                        'wav': 'audio/wav',
                        'flac': 'audio/flac',
                        'ogg': 'audio/ogg',
                        'opus': 'audio/opus',
                        'webm': 'audio/webm',
                        'm4a': 'audio/mp4'
                    }
                    mimetype = mimetype_map.get(file_extension, 'audio/mpeg')
                    
                    # Create source dictionary for Deepgram v2 SDK
                    source = {
                        'buffer': audio_data_bytes,
                        'mimetype': mimetype
                    }
                    
                    # Configure Deepgram options for v2 SDK (as dictionary)
                    options = {
                        'model': DEEPGRAM_MODEL,
                        'smart_format': True,
                        'punctuate': True,
                        'diarize': False,
                        'language': language or "en-US",
                        'multichannel': False,
                        'utterances': True,
                        'detect_language': False,
                    }
                    
                    # Make the API request using correct v2 syntax
                    response = await self.deepgram.transcription.prerecorded(
                        source,
                        options
                    )
                
                # Extract transcription details (response is a dict in v2 SDK)
                transcription_text = response['results']['channels'][0]['alternatives'][0]['transcript']
                confidence = response['results']['channels'][0]['alternatives'][0]['confidence']
                
                # Apply medical terminology corrections
                transcription_text = self._correct_medical_terminology(transcription_text)
                
                # Get additional metadata (with fallbacks for missing keys)
                detected_language = response.get('results', {}).get('language', 'en-US')
                duration = response.get('metadata', {}).get('duration', 0)
                
                # Clean up temporary file
                os.unlink(temp_file_path)
                
                logger.info(f"{SUCCESS_MESSAGES['transcription_complete']}: {filename}")
                
                return {
                    "text": transcription_text,
                    "confidence": confidence,
                    "language": detected_language,
                    "duration": duration,
                    "model": DEEPGRAM_MODEL,
                    "file_size": len(audio_data),
                    "timestamp": datetime.utcnow().isoformat(),
                    "metadata": {
                        "model_used": DEEPGRAM_MODEL,
                        "file_format": file_extension,
                        "transcription_method": "deepgram_nova3_medical",
                        "provider": "deepgram"
                    }
                }
                
            except Exception as e:
                # Clean up temporary file in case of error
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                raise e
                
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise
    
    def _get_file_extension(self, filename: str) -> str:
        """Extract file extension from filename"""
        return filename.lower().split('.')[-1] if '.' in filename else ''
    
    def _convert_opus_to_wav(self, opus_data: bytes) -> bytes:
        """Convert OPUS audio to WAV format using ffmpeg"""
        try:
            # Create temporary files
            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as opus_file:
                opus_file.write(opus_data)
                opus_path = opus_file.name
            
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
                wav_path = wav_file.name
            
            # Convert using ffmpeg
            cmd = [
                'ffmpeg', '-i', opus_path, '-acodec', 'pcm_s16le', 
                '-ar', '16000', '-ac', '1', '-y', wav_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"FFmpeg conversion failed: {result.stderr}")
                raise Exception(f"Audio conversion failed: {result.stderr}")
            
            # Read converted file
            with open(wav_path, 'rb') as f:
                wav_data = f.read()
            
            # Clean up temporary files
            os.unlink(opus_path)
            os.unlink(wav_path)
            
            return wav_data
            
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install FFmpeg to convert OPUS files.")
            raise Exception(ERROR_MESSAGES['ffmpeg_not_found'])
        except Exception as e:
            logger.error(f"OPUS conversion error: {str(e)}")
            raise
    
    def _correct_medical_terminology(self, text: str) -> str:
        """Correct common medical terminology errors in transcription"""
        corrected_text = text
        for incorrect, correct in MEDICAL_TERMINOLOGY_CORRECTIONS.items():
            corrected_text = corrected_text.replace(incorrect, correct)
            
        logger.info(SUCCESS_MESSAGES['medical_corrections_applied'])
        return corrected_text
    
    async def get_supported_formats(self) -> list:
        """Get list of supported audio formats"""
        return self.supported_formats.copy()
    
    async def validate_audio_file(self, audio_data: bytes, filename: str) -> Dict[str, Any]:
        """Validate audio file before transcription"""
        validation_result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "file_info": {}
        }
        
        try:
            # Check file size
            file_size = len(audio_data)
            validation_result["file_info"]["size_bytes"] = file_size
            validation_result["file_info"]["size_mb"] = round(file_size / (1024 * 1024), 2)
            
            if file_size == 0:
                validation_result["valid"] = False
                validation_result["errors"].append("File is empty")
            
            if file_size > self.max_file_size:
                validation_result["valid"] = False
                validation_result["errors"].append(f"File size ({file_size} bytes) exceeds maximum allowed size ({self.max_file_size} bytes)")
            
            # Check file extension
            file_extension = self._get_file_extension(filename)
            validation_result["file_info"]["extension"] = file_extension
            
            if file_extension not in self.supported_formats:
                validation_result["valid"] = False
                validation_result["errors"].append(f"Unsupported file format: {file_extension}")
            
            # Check for minimum file size (very small files might be corrupted)
            if file_size < FILE_VALIDATION_RULES['min_file_size_bytes']:
                validation_result["warnings"].append("File is very small and might be corrupted")
            
        except Exception as e:
            validation_result["valid"] = False
            validation_result["errors"].append(f"Validation error: {str(e)}")
        
        return validation_result
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get transcription service status"""
        try:
            # Test API connection
            api_key = os.getenv('DEEPGRAM_API_KEY')
            if not api_key:
                return {
                    "status": "error",
                    "message": ERROR_MESSAGES['api_key_missing'],
                    "available": False
                }
            
            return {
                "status": "healthy",
                "message": "Deepgram Nova-3 Medical transcription service is available",
                "available": True,
                "provider": "Deepgram",
                "model": DEEPGRAM_MODEL,
                "supported_formats": self.supported_formats,
                "max_file_size_mb": round(self.max_file_size / (1024 * 1024), 2),
                "features": [
                    "Medical terminology optimization",
                    "Automatic punctuation",
                    "Smart formatting",
                    "High accuracy",
                    "Large file support"
                ]
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"Service error: {str(e)}",
                "available": False
            }