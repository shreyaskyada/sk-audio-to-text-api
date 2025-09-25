# 📝 Prompts Configuration Guide

## 🎯 Overview
All transcription prompts, settings, and configurations are now centralized in `app/prompts.py`. This makes it easy to modify transcription behavior without touching the main code.

---

## 📁 File Location
```
audiototext-api/app/prompts.py
```

---

## 🔧 What You Can Modify

### **1. Medical Transcription Prompt**
**File:** `MEDICAL_TRANSCRIPTION_PROMPT`
**Purpose:** Main prompt sent to OpenAI Whisper for medical transcriptions
**Example Change:**
```python
MEDICAL_TRANSCRIPTION_PROMPT = """MEDICAL TRANSCRIPTION: Focus on accurate medical terminology. Use precise terms for medications, procedures, and conditions."""
```

### **2. Medical Terminology Corrections**
**File:** `MEDICAL_TERMINOLOGY_CORRECTIONS`
**Purpose:** Dictionary of incorrect → correct medical terms
**Example Change:**
```python
MEDICAL_TERMINOLOGY_CORRECTIONS = {
    'corticosterone': 'corticosteroid',
    'lock-in': 'locking',
    # Add new corrections here:
    'new_wrong_term': 'correct_term',
}
```

### **3. Transcription Settings**
**File:** `TRANSCRIPTION_SETTINGS`
**Purpose:** OpenAI API settings and file limits
**Example Changes:**
```python
TRANSCRIPTION_SETTINGS = {
    "model": "whisper-1",
    "temperature": 0.2,  # Less deterministic
    "response_format": "verbose_json",
    "max_file_size_mb": 50,  # Increase limit
    "supported_formats": [
        'flac', 'm4a', 'mp3', 'mp4', 'mpeg', 'mpga', 'oga', 'ogg', 'wav', 'webm', 'aac'  # Add new format
    ]
}
```

### **4. Confidence Calculation**
**File:** `CONFIDENCE_SETTINGS`
**Purpose:** How confidence scores are calculated
**Example Changes:**
```python
CONFIDENCE_SETTINGS = {
    "base_confidence": 0.8,  # Higher base confidence
    "length_bonus_100": 0.15,  # More bonus for longer text
    "length_bonus_500": 0.15,
    "inaudible_penalty": 0.3,  # Higher penalty for unclear audio
    "excessive_dots_penalty": 0.15,
    "min_confidence": 0.0,
    "max_confidence": 1.0
}
```

### **5. File Validation Rules**
**File:** `FILE_VALIDATION_RULES`
**Purpose:** File size limits and supported formats
**Example Changes:**
```python
FILE_VALIDATION_RULES = {
    "min_file_size_bytes": 2048,  # 2KB minimum
    "max_file_size_bytes": 50 * 1024 * 1024,  # 50MB maximum
    "supported_extensions": [
        'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm', 'opus', 'aac'  # Add AAC
    ],
    "content_types": [
        'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/webm', 'audio/ogg', 'audio/opus', 'audio/aac'  # Add AAC
    ]
}
```

### **6. Error Messages**
**File:** `ERROR_MESSAGES`
**Purpose:** User-friendly error messages
**Example Changes:**
```python
ERROR_MESSAGES = {
    "invalid_file_type": "Please upload an audio file (MP3, WAV, etc.)",
    "file_too_large": "File size exceeds 50MB limit",
    # Add new error messages:
    "new_error": "Custom error message",
}
```

### **7. Success Messages**
**File:** `SUCCESS_MESSAGES`
**Purpose:** Success notifications
**Example Changes:**
```python
SUCCESS_MESSAGES = {
    "transcription_complete": "Audio transcribed successfully!",
    "medical_corrections_applied": "Medical terms corrected automatically",
    "opus_converted": "OPUS file converted to WAV",
    # Add new success messages:
    "new_success": "Custom success message",
}
```

---

## 🚀 How to Make Changes

### **Step 1: Edit the Prompts File**
```bash
# Open the prompts file
nano app/prompts.py
# or
code app/prompts.py
```

### **Step 2: Modify What You Need**
- Change prompts for different transcription styles
- Add new medical terminology corrections
- Adjust confidence calculation rules
- Update file size limits
- Modify error messages

### **Step 3: Restart the API**
```bash
# Stop the current server (Ctrl+C)
# Restart it:
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### **Step 4: Test Your Changes**
- Upload an audio file
- Check if your changes are applied
- Verify the transcription behavior

---

## 💡 Common Modifications

### **Add New Medical Terms**
```python
MEDICAL_TERMINOLOGY_CORRECTIONS = {
    # Existing corrections...
    'acetylsalicylic': 'aspirin',
    'acetaminophen': 'paracetamol',
    'myocardial': 'heart muscle',
}
```

### **Change Transcription Style**
```python
MEDICAL_TRANSCRIPTION_PROMPT = """MEDICAL TRANSCRIPTION: 
1. Use formal medical language
2. Include all medical measurements
3. Preserve exact medication names
4. Maintain professional tone"""
```

### **Adjust File Limits**
```python
FILE_VALIDATION_RULES = {
    "max_file_size_bytes": 100 * 1024 * 1024,  # 100MB limit
    "supported_extensions": [
        'mp3', 'mp4', 'wav', 'flac', 'aac', 'ogg'  # More formats
    ]
}
```

### **Customize Confidence Scoring**
```python
CONFIDENCE_SETTINGS = {
    "base_confidence": 0.9,  # Very high base confidence
    "length_bonus_100": 0.05,  # Smaller bonuses
    "inaudible_penalty": 0.5,  # Heavy penalty for unclear audio
}
```

---

## ⚠️ Important Notes

1. **Backup First:** Always backup your prompts file before making changes
2. **Test Thoroughly:** Test changes with different audio files
3. **Restart Required:** Changes only take effect after restarting the API
4. **Syntax Check:** Make sure Python syntax is correct
5. **Deployment:** Changes need to be deployed to Vercel to take effect in production

---

## 🔄 Version Control

Keep track of your prompt changes:
```bash
git add app/prompts.py
git commit -m "Updated medical terminology corrections"
git push origin master
```

---

**Happy Transcribing! 🎤✨**
