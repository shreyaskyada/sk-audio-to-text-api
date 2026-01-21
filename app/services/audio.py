
import os
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

def check_ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def convert_audio_to_wav(audio_data: bytes, input_format: str = 'webm') -> bytes:
    """Convert audio (OPUS/WebM/MP3) to WAV format using ffmpeg"""
    # Note: This function assumes FFmpeg is available. Check before calling.
    if not check_ffmpeg_available():
        raise FileNotFoundError("FFmpeg not found. Please install FFmpeg to convert audio files.")
    
    try:
        # Create temporary files
        input_ext = f'.{input_format}' if input_format else '.webm'
        with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as input_file:
            input_file.write(audio_data)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
            wav_path = wav_file.name
        
        # Convert using ffmpeg - High-fidelity conversion
        # Preserves input sample rate and channels for best Deepgram accuracy
        # -acodec pcm_s16le: Standard WAV format (Lossless PCM)
        cmd = [
            'ffmpeg', '-i', input_path, 
            '-acodec', 'pcm_s16le', 
            '-y', wav_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg conversion failed: {result.stderr}")
            raise Exception(f"Audio conversion failed: {result.stderr}")
        
        # Read converted file
        with open(wav_path, 'rb') as f:
            wav_data = f.read()
        
        # Clean up temporary files
        try:
            os.unlink(input_path)
            os.unlink(wav_path)
        except:
            pass
        
        logger.info(f"Successfully converted {input_format} to WAV format ({len(wav_data)} bytes)")
        return wav_data
        
    except Exception as e:
        logger.error(f"Audio conversion error: {str(e)}")
        raise


def get_audio_content_type(filename: str) -> str:
    """Get appropriate Content-Type for audio file"""
    filename_lower = filename.lower()
    if filename_lower.endswith('.webm'):
        return 'audio/webm'
    elif filename_lower.endswith('.opus'):
        return 'audio/opus'
    elif filename_lower.endswith('.mp3'):
        return 'audio/mpeg'
    elif filename_lower.endswith('.m4a'):
        return 'audio/mp4'
    elif filename_lower.endswith('.wav'):
        return 'audio/wav'
    else:
        return 'audio/wav'  # Default to WAV


def convert_opus_to_wav(opus_data: bytes) -> bytes:
    """Convert OPUS audio to WAV format using ffmpeg (legacy function for backward compatibility)"""
    return convert_audio_to_wav(opus_data, 'opus')


def normalize_audio_bytes(audio_data: bytes) -> bytes:
    """Normalize audio bytes (for MP3/WAV files that don't need conversion)"""
    return audio_data
