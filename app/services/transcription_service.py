import os
import tempfile
from typing import Dict, Any, Optional
import logging
from datetime import datetime
import subprocess
import requests
from urllib.parse import urlencode, quote

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
        self.api_key = os.getenv('DEEPGRAM_API_KEY')
        if not self.api_key:
            raise ValueError(ERROR_MESSAGES['api_key_missing'])
        
        self.supported_formats = FILE_VALIDATION_RULES['supported_extensions']
        self.max_file_size = TRANSCRIPTION_SETTINGS['max_file_size_mb'] * 1024 * 1024
        
        logger.info(SUCCESS_MESSAGES['deepgram_connected'])
        logger.info(SUCCESS_MESSAGES['medical_model_active'])
    
    def transcribe_audio(
        self, 
        audio_data: bytes, 
        filename: str,
        language: Optional[str] = None,
        model: str = "nova-3-medical"
    ) -> Dict[str, Any]:
        """
        Transcribe audio using Deepgram Nova-3 Medical API
        """
        try:
            # Validate file size
            if len(audio_data) > self.max_file_size:
                raise ValueError(f"File size exceeds maximum allowed size")
            
            file_extension = self._get_file_extension(filename)
            if file_extension not in self.supported_formats:
                raise ValueError(f"Unsupported file format: {file_extension}")
            
            # Handle OPUS conversion if needed
            if file_extension == 'opus':
                logger.info("Converting OPUS file to WAV format")
                audio_data = self._convert_opus_to_wav(audio_data)
                file_extension = 'wav'
            
            # Build Deepgram API URL with enhanced parameters
            keyterms = [
                "pes anserine", "antalgic gait", "corticosteroid injection",
                "intra-articular", "ligamentous", "osteoarthritis",
                "bursitis", "MCL", "ACL", "PCL", "LCL", "McMurray test",
                "contralateral", "neurovascularly intact",
                "range of motion", "joint line tenderness",
                "effusion", "crepitus", "meniscus", "patellofemoral"
            ]
            
            query_params = {
                "model": model or "nova-3-medical",
                "numerals": "true",
                "language": language or "en-US",
                "version": "latest",
                "smart_format": "true",
                "diarize": "true",
                "custom_intent": "orthopedic_patient_assessment",
                "custom_intent_mode": "extended",
                "sentiment": "false"
            }
            
            # Build URL with keyterms
            url = f"https://api.deepgram.com/v1/listen?" + urlencode(query_params)
            for term in keyterms:
                url += f"&keyterm={quote(term)}"
            
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "audio/wav"
            }
            
            # Make API request to Deepgram
            logger.info(f"Sending request to Deepgram API for file: {filename}")
            response = requests.post(url, headers=headers, data=audio_data)
            response.raise_for_status()
            result = response.json()
            
            # Extract transcription
            transcription_text = result["results"]["channels"][0]["alternatives"][0]["transcript"]
            confidence = result["results"]["channels"][0]["alternatives"][0]["confidence"]
            
            # Apply medical terminology corrections
            transcription_text = self._correct_medical_terminology(transcription_text)
            
            # Get metadata
            detected_language = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("language", language or "en-US")
            duration = result.get("metadata", {}).get("duration", 0)
            
            logger.info(f"Transcription complete: {filename}")
            
            return {
                "text": transcription_text,
                "confidence": confidence,
                "language": detected_language,
                "duration": duration,
                "model": model,
                "file_size": len(audio_data),
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": {
                    "model_used": model,
                    "file_format": file_extension,
                    "transcription_method": "deepgram_nova3_medical",
                    "provider": "deepgram"
                }
            }
            
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise
    
    def _get_file_extension(self, filename: str) -> str:
        """Extract file extension from filename"""
        return filename.lower().split('.')[-1] if '.' in filename else ''
    
    def _convert_opus_to_wav(self, opus_data: bytes) -> bytes:
        """Convert OPUS audio to WAV format using ffmpeg"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as opus_file:
                opus_file.write(opus_data)
                opus_path = opus_file.name
            
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
                wav_path = wav_file.name
            
            cmd = [
                'ffmpeg', '-i', opus_path, '-acodec', 'pcm_s16le', 
                '-ar', '16000', '-ac', '1', '-y', wav_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"FFmpeg conversion failed: {result.stderr}")
                raise Exception(f"Audio conversion failed: {result.stderr}")
            
            with open(wav_path, 'rb') as f:
                wav_data = f.read()
            
            os.unlink(opus_path)
            os.unlink(wav_path)
            
            return wav_data
            
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install FFmpeg to convert OPUS files.")
            raise Exception("FFmpeg not found")
        except Exception as e:
            logger.error(f"OPUS conversion error: {str(e)}")
            raise
    
    def _correct_medical_terminology(self, text: str) -> str:
        """Correct common medical terminology errors in transcription"""
        corrected_text = text
        for incorrect, correct in MEDICAL_TERMINOLOGY_CORRECTIONS.items():
            corrected_text = corrected_text.replace(incorrect, correct)
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
            file_size = len(audio_data)
            validation_result["file_info"]["size_bytes"] = file_size
            validation_result["file_info"]["size_mb"] = round(file_size / (1024 * 1024), 2)
            
            if file_size == 0:
                validation_result["valid"] = False
                validation_result["errors"].append("File is empty")
            
            if file_size > self.max_file_size:
                validation_result["valid"] = False
                validation_result["errors"].append(f"File size exceeds maximum")
            
            file_extension = self._get_file_extension(filename)
            validation_result["file_info"]["extension"] = file_extension
            
            if file_extension not in self.supported_formats:
                validation_result["valid"] = False
                validation_result["errors"].append(f"Unsupported file format: {file_extension}")
            
        except Exception as e:
            validation_result["valid"] = False
            validation_result["errors"].append(f"Validation error: {str(e)}")
        
        return validation_result
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get transcription service status"""
        try:
            if not self.api_key:
                return {
                    "status": "error",
                    "message": "API key missing",
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