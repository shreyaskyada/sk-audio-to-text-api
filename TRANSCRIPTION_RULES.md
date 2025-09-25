# 🎤 Audio Transcription Rules & Guidelines

## 📋 System Overview
This document outlines all the rules, guidelines, and configurations used in our HIPAA-compliant audio transcription system powered by **Deepgram Nova-3 Medical**.

---

## 🔐 Authentication Rules

### **Login Credentials**
- **Username:** `Admin` (configurable via `AUTH_USERNAME` environment variable)
- **Password:** `Mike12@` (configurable via `AUTH_PASSWORD` environment variable)
- **Token Expiry:** 30 minutes (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`)

### **Authentication Flow**
1. User submits username/password
2. System validates against environment variables
3. JWT token generated with 30-minute expiry
4. Token stored in localStorage for frontend sessions

---

## 🎵 Audio File Rules

### **Supported Formats**
- **Primary Formats:** MP3, MP4, WAV, FLAC, AAC, OGG, WEBM, OPUS, M4A, WMA
- **Special Handling:** OPUS (automatically converted to WAV using FFmpeg)
- **Deepgram Optimized:** All formats optimized for medical transcription

### **File Size Limits**
- **Maximum Size:** 50MB (Deepgram Nova-3 Medical limit)
- **Minimum Size:** 1KB (to avoid corrupted files)
- **Recommended:** Under 25MB for optimal performance

### **File Validation**
- ✅ File type validation (audio/* content type)
- ✅ File size validation
- ✅ Extension validation
- ⚠️ OPUS files require FFmpeg installation

---

## 🏥 Medical Terminology Rules

### **Critical Medical Term Corrections**
The system automatically corrects these common transcription errors:

| **Incorrect** | **Correct** | **Context** |
|---------------|-------------|-------------|
| corticosterone | corticosteroid | Medical treatment |
| lock-in | locking | Joint condition |
| trichromatoma | tricompartmental | Knee anatomy |
| retired | reported | Patient history |
| asleep | slip | Medical condition |

### **Additional Medical Terms**
- osteoarthritis → osteoarthritis
- palpitation → palpation
- echymosis → ecchymosis
- arythema → erythema
- oedema → edema
- antenna → anterior

### **Medical Prompt Enhancement**
Every transcription includes this medical context prompt:
```
MEDICAL TRANSCRIPTION - CRITICAL TERMINOLOGY REQUIREMENTS: 
You MUST use these exact medical terms: corticosteroid (NEVER corticosterone), 
locking (NEVER lock-in), tricompartmental (NEVER trichromatoma), 
reported (NEVER retired), slip (NEVER asleep). 
Additional medical terms: osteoarthritis, injection, examination, 
palpation, ecchymosis, erythema, edema, neurovascular, instability, 
range of motion, anterior, posterior, medial, lateral, proximal, distal. 
This is a medical context - use precise medical terminology only. 
Do not substitute or approximate medical terms.
```

---

## 🛡️ HIPAA Compliance Rules

### **Audit Logging**
Every action is logged with:
- **User ID:** Who performed the action
- **Action Type:** What was done (audio_upload, transcription_complete)
- **Details:** File information, timestamps
- **IP Address:** Source of request (default: 127.0.0.1)
- **User Agent:** Client information

### **Data Encryption**
- **Encryption Key:** 32-byte key (configurable via `ENCRYPTION_KEY`)
- **Sensitive Data:** Automatically encrypted before storage
- **Key Verification:** System validates encryption keys on startup

### **Access Controls**
- ✅ JWT-based authentication
- ✅ Token expiration (30 minutes)
- ✅ Secure password validation
- ✅ API endpoint protection

### **Data Minimization**
- ✅ Temporary file cleanup after transcription
- ✅ No persistent audio file storage
- ✅ Audit logs stored in database only

---

## 🔧 Technical Configuration Rules

### **Environment Variables Required**
```bash
# API Configuration
OPENAI_API_KEY=your_openai_api_key_here
SECRET_KEY=your_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Authentication
AUTH_USERNAME=Admin
AUTH_PASSWORD=Mike12@

# HIPAA Compliance
ENCRYPTION_KEY=your_32_byte_encryption_key_here

# Environment
ENVIRONMENT=production  # or development

# Database (Local Development)
DATABASE_URL=sqlite:///./transcription.db

# Database (Production/Vercel)
DATABASE_URL=sqlite:///:memory:  # Auto-detected on Vercel
```

### **Database Rules**
- **Local Development:** SQLite file database (`transcription.db`)
- **Vercel Deployment:** In-memory SQLite (no persistent storage)
- **Tables:** Audit logs, user sessions
- **Cleanup:** Automatic session cleanup on token expiry

### **Logging Rules**
- **Local Development:** `./logs/audit.log` file
- **Vercel Deployment:** `/tmp/audit.log` or console-only
- **Log Levels:** INFO, WARNING, ERROR
- **Format:** Timestamp - Module - Level - Message

---

## 🌐 API Endpoint Rules

### **Available Endpoints**
```
GET  /                    - Root endpoint (API status)
GET  /health             - Health check
POST /api/v1/auth/login  - User authentication
POST /api/v1/transcribe  - Audio transcription
```

### **Request/Response Rules**
- **Content-Type:** `multipart/form-data` for file uploads
- **Authorization:** Bearer token in header
- **File Upload:** Single audio file per request
- **Response Format:** JSON with transcription results

### **Error Handling**
- ✅ File validation errors (400)
- ✅ Authentication errors (401)
- ✅ File size errors (413)
- ✅ Server errors (500)
- ✅ Graceful error messages for users

---

## 🚀 Deployment Rules

### **Vercel Configuration**
```json
{
  "version": 2,
  "builds": [
    {
      "src": "app/main.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "app/main.py"
    }
  ]
}
```

### **CORS Rules**
Allowed origins:
- `http://localhost:3000` (local development)
- `http://localhost:8080` (local development)
- `https://audio-to-text-frontend.vercel.app` (production)
- `https://audio-to-text-frontend-j3uuj5pkq-cresol-projects.vercel.app` (production)

---

## 📊 Quality Assurance Rules

### **Confidence Scoring**
- **Base Confidence:** 70%
- **Length Bonus:** +10% for >100 chars, +10% for >500 chars
- **Quality Penalties:** -20% for [inaudible], -10% for excessive "..."
- **Range:** 0.0 to 1.0

### **Transcription Settings**
- **Model:** whisper-1 (OpenAI)
- **Temperature:** 0.0 (deterministic output)
- **Response Format:** verbose_json
- **Language:** Auto-detect or specified

---

## 🔄 Workflow Rules

### **Transcription Process**
1. **File Upload:** Validate file type and size
2. **Authentication:** Verify user token
3. **HIPAA Logging:** Log access attempt
4. **File Processing:** Convert OPUS if needed
5. **OpenAI API:** Send to Whisper for transcription
6. **Medical Correction:** Apply terminology corrections
7. **HIPAA Logging:** Log successful transcription
8. **Cleanup:** Remove temporary files
9. **Response:** Return transcription results

### **Error Recovery**
- ✅ Temporary file cleanup on errors
- ✅ Graceful error handling
- ✅ User-friendly error messages
- ✅ Detailed logging for debugging

---

## 📝 Best Practices

### **For Users**
- Use clear, high-quality audio recordings
- Keep files under 10MB for best performance
- Ensure stable internet connection
- Use supported audio formats

### **For Developers**
- Always validate environment variables
- Implement proper error handling
- Log all HIPAA-relevant actions
- Clean up temporary files
- Use secure authentication methods

### **For Deployment**
- Set all required environment variables
- Configure CORS properly
- Enable proper logging
- Monitor API usage and errors
- Regular security updates

---

## 🆘 Troubleshooting

### **Common Issues**
1. **"Read-only file system"** → Vercel deployment issue (fixed)
2. **"Invalid API key"** → Check OPENAI_API_KEY
3. **"Database connection failed"** → Check DATABASE_URL
4. **"CORS error"** → Check allowed origins
5. **"OPUS conversion failed"** → FFmpeg not installed

### **Debug Steps**
1. Check environment variables
2. Verify API endpoints
3. Review logs for errors
4. Test with simple audio files
5. Validate authentication flow

---

**Last Updated:** September 25, 2025  
**Version:** 1.0.0  
**Maintainer:** Audio Transcription API Team
