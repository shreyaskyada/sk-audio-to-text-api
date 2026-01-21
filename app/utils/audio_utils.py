import os
import subprocess
import shutil
import logging
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

def check_ffmpeg_available():
    """Check if FFmpeg is available on the system"""
    return shutil.which("ffmpeg") is not None

def convert_audio_to_wav(audio_data: bytes, input_format: str = 'webm') -> bytes:
    """Convert audio (OPUS/WebM/MP3) to WAV format using ffmpeg"""
    if not check_ffmpeg_available():
        logger.error("ffmpeg not found! Please install ffmpeg.")
        return audio_data
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{input_format}") as temp_input:
        temp_input.write(audio_data)
        temp_input_path = temp_input.name
        
    temp_output_path = temp_input_path + ".wav"
    
    try:
        command = [
            "ffmpeg", "-y", "-i", temp_input_path,
            "-ar", "16000", "-ac", "1", temp_output_path
        ]
        subprocess.run(command, check=True, capture_output=True)
        
        with open(temp_output_path, "rb") as f:
            wav_data = f.read()
        return wav_data
    except Exception as e:
        logger.error(f"Error converting audio: {e}")
        return audio_data
    finally:
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)

def get_audio_content_type(filename: str) -> str:
    """Get appropriate Content-Type for audio file"""
    ext = os.path.splitext(filename)[1].lower()
    mapping = {
        '.wav': 'audio/wav',
        '.mp3': 'audio/mpeg',
        '.opus': 'audio/opus',
        '.webm': 'audio/webm',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg'
    }
    return mapping.get(ext, 'application/octet-stream')

def convert_opus_to_wav(opus_data: bytes) -> bytes:
    """Legacy function for backward compatibility"""
    return convert_audio_to_wav(opus_data, 'opus')

def normalize_audio_bytes(audio_data: bytes) -> bytes:
    """Normalize audio bytes (no conversion needed)"""
    return audio_data
