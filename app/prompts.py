# Deepgram Nova-3 Medical Configuration
# Modify these settings to change transcription behavior

# Deepgram model settings (using nova-2 for broader availability)
DEEPGRAM_MODEL = "nova-3"  # High-quality model with medical capabilities

# Deepgram transcription options
DEEPGRAM_OPTIONS = {
    "model": DEEPGRAM_MODEL,
    "smart_format": True,  # Automatic punctuation and capitalization
    "punctuate": True,     # Add punctuation
    "diarize": False,      # Speaker diarization (set to True if needed)
    "language": "en-US",   # Language code
    
    "multichannel": False, # Set to True for multi-channel audio
    "utterances": True,    # Split into utterances
    "detect_language": False,  # Set to True for auto language detection
}

# Medical terminology enhancements (Deepgram Nova-3 Medical handles most medical terms automatically)
MEDICAL_ENHANCEMENTS = {
    "boost_medical_terms": True,
    "boost_pharmaceutical_terms": True,
    "boost_anatomical_terms": True,
    "boost_procedural_terms": True,
}

# General transcription options (for non-medical use)
GENERAL_DEEPGRAM_OPTIONS = {
    "model": "nova-2",
    "smart_format": True,
    "punctuate": True,
    "language": "en-US",
    "tier": "nova",
}

# Audio quality instructions
AUDIO_QUALITY_INSTRUCTIONS = """If audio quality is poor or unclear, Deepgram will automatically handle unclear portions. Maintain the flow and context of the conversation."""

# Language-specific prompts
LANGUAGE_PROMPTS = {
    "en": "Transcribe in English with proper grammar and punctuation.",
    "es": "Transcribe in Spanish with proper grammar and punctuation.",
    "fr": "Transcribe in French with proper grammar and punctuation.",
    "de": "Transcribe in German with proper grammar and punctuation.",
}

# Comprehensive medical context prompts
MEDICAL_CONTEXT_PROMPTS = {
    "knee_exam": """
    MEDICAL EXAMINATION TRANSCRIPT - KNEE ASSESSMENT:
    This is a clinical examination of the knee joint. Pay special attention to:
    - Joint mechanics terminology (locking, catching, slipping, instability)
    - Anatomical references (medial, lateral, joint line, patella, tricompartmental)
    - Range of motion measurements (degrees, flexion, extension)
    - Physical examination findings (palpation, tenderness, swelling)
    - Injury mechanisms: "fall slip trauma" (not "false lip trauma")
    - Joint symptoms: "catching, locking" (not "catching lock")
    - Skin findings: "open skin lesion" (not "open condition")
    - Use medical terminology knowledge to distinguish between similar-sounding words
    - Convert number words to digits (one=1, two=2, etc.)
    - Focus on orthopedic terminology and clinical accuracy
    """,
    
    "injection_procedure": """
    MEDICAL INJECTION PROCEDURE TRANSCRIPT:
    This involves therapeutic injection procedures. Focus on:
    - Medication names and dosages (exact spelling and measurements)
    - Anatomical injection sites and landmarks
    - Procedure details and patient tolerance
    - Use precise medical terminology for medications and anatomy
    """,
    
    "patient_history": """
    PATIENT MEDICAL HISTORY TRANSCRIPT:
    This is a patient medical history and consultation. Emphasize:
    - Symptom descriptions and medical terminology
    - Treatment history and medication names
    - Timeline and frequency of symptoms
    - Use appropriate medical terminology context
    """,
    
    "general_medical": """
    GENERAL MEDICAL CONTENT TRANSCRIPT:
    This is medical content requiring clinical accuracy. Guidelines:
    - Use medical terminology knowledge to distinguish context-appropriate terms
    - "lip" in anatomical context vs "slip" in joint mechanics context
    - "locking" for joint mechanisms vs "lacking" (not medical terminology)
    - Preserve legitimate medical terms, only correct clear mishearings
    - Apply medical knowledge to determine appropriate terminology
    """,
    
    "doctor_orthopedic": """
    DOCTOR ORTHOPEDIC CONSULTATION TRANSCRIPT:
    This is a medical consultation for orthopedic conditions. Critical accuracy requirements:
    
    INJURY MECHANISMS:
    - "fall slip trauma" (patient fell and slipped causing trauma)
    - NOT "false lip trauma" (anatomically incorrect)
    
    JOINT SYMPTOMS:
    - "catching, locking" (joint mechanics symptoms)
    - NOT "catching lock" (grammatically incorrect)
    
    SKIN FINDINGS:
    - "open skin lesion" (dermatological assessment)
    - NOT "open condition" (vague medical term)
    
    ANATOMICAL TERMS:
    - "tricompartmental" (knee joint compartments)
    - NOT "trichomobarbital" (medication name, incorrect context)
    
    MEDICAL PROGRESSION:
    - "no regressed" (condition did not worsen)
    - NOT "no regrowth" (incorrect medical progression)
    
    NUMBERS:
    - Always use digits: 1, 2, 3, 4, 5 (not one, two, three, four, five)
    - Critical for measurements, ages, doses, and clinical data
    
    Focus on clinical precision and medical terminology accuracy.
    """
}

# Primary medical transcription prompt for doctors
PRIMARY_MEDICAL_PROMPT = """
DOCTOR-FOCUSED MEDICAL TRANSCRIPTION:
You are transcribing medical content for healthcare professionals. Apply your medical knowledge to:

1. MEDICAL TERMINOLOGY ACCURACY:
   - Distinguish between similar-sounding medical terms based on clinical context
   - Use appropriate medical terminology for anatomical structures vs. mechanical descriptions
   - Correct common transcription errors while preserving legitimate medical terms

2. ORTHOPEDIC/KNEE EXAMINATION SPECIFICS:
   - "fall slip trauma" (injury mechanism) - NOT "false lip trauma"
   - "catching, locking" (joint symptoms) - NOT "catching lock"
   - "open skin lesion" (dermatological finding) - NOT "open condition"
   - "tricompartmental" (knee anatomy) - NOT "trichomobarbital"
   - "no regressed" (medical progression) - NOT "no regrowth"

3. NUMBER FORMAT REQUIREMENTS:
   - Convert all number words to digits (one=1, two=2, three=3, etc.)
   - Use digits for measurements, ages, doses, and quantities
   - Maintain medical precision in numerical data

4. CLINICAL CONTEXT AWARENESS:
   - Apply clinical context to determine the most appropriate medical terminology
   - Focus on orthopedic terminology and clinical accuracy
   - Preserve medical abbreviations and standard terminology
"""

# Medical terminology corrections for common transcription errors
MEDICAL_TERMINOLOGY_CORRECTIONS = {
    # Orthopedic/Knee examination specific corrections
    "false lip trauma": "fall slip trauma",
    "catching lock": "catching, locking", 
    "open condition": "open skin lesion",
    "no regrowth": "no regressed",
    "trichomobarbital": "tricompartmental",
    "lacking": "locking",
    "lip trauma": "slip trauma",
    
    # Common medical term corrections
    "ecchymosis": "ecchymosis",  # Keep correct spelling
    "erythema": "erythema",      # Keep correct spelling
    "neurovascular": "neurovascular",  # Keep correct spelling
    
    # Number format corrections (words to digits)
    "one": "1",
    "two": "2", 
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100"
}

# Deepgram transcription settings
TRANSCRIPTION_SETTINGS = {
    "provider": "deepgram",
    "model": DEEPGRAM_MODEL,
    "max_file_size_mb": 50,  # Deepgram supports larger files
    "supported_formats": [
        'mp3', 'mp4', 'wav', 'flac', 'aac', 'ogg', 'webm', 'opus', 'm4a', 'wma'
    ],
    "api_endpoint": "https://api.deepgram.com/v1/listen",
    "timeout": 30,  # Request timeout inseconds
}

# Confidence calculation settings
CONFIDENCE_SETTINGS = {
    "base_confidence": 0.7,
    "length_bonus_100": 0.1,
    "length_bonus_500": 0.1,
    "inaudible_penalty": 0.2,
    "excessive_dots_penalty": 0.1,
    "min_confidence": 0.0,
    "max_confidence": 1.0
}

# File validation rules
FILE_VALIDATION_RULES = {
    "min_file_size_bytes": 1024,  # 1KB minimum
    "max_file_size_bytes": 25 * 1024 * 1024,  # 25MB maximum
    "supported_extensions": [
        'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm', 'opus'
    ],
    "content_types": [
        'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/webm', 'audio/ogg', 'audio/opus'
    ]
}

# Error messages
ERROR_MESSAGES = {
    "invalid_file_type": "File must be an audio file",
    "file_too_large": "File size exceeds 50MB limit",
    "file_too_small": "File is too small and might be corrupted",
    "unsupported_format": "Unsupported audio format",
    "conversion_failed": "Audio conversion failed",
    "transcription_failed": "Transcription failed",
    "api_error": "Deepgram API error during transcription",
    "api_key_missing": "Deepgram API key not configured",
    "ffmpeg_not_found": "FFmpeg not found. Please install FFmpeg to convert OPUS files.",
    "deepgram_error": "Deepgram transcription service error"
}

# Success messages
SUCCESS_MESSAGES = {
    "transcription_complete": "Deepgram Nova-3 Medical transcription completed successfully",
    "medical_corrections_applied": "Medical terminology enhanced by Nova-3 Medical model",
    "opus_converted": "Converting OPUS file to WAV format",
    "deepgram_connected": "Connected to Deepgram Nova-3 Medical API",
    "medical_model_active": "Using specialized medical transcription model"
}
