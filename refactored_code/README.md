# Medical Transcription & SOAP Note API 🏥

A clean, fast, and maintainable FastAPI application for medical audio transcription and SOAP note generation using Deepgram Nova-3 Medical and GPT-4.

## ✨ Features

- 🎤 **Audio Transcription** - Deepgram Nova-3 Medical model with orthopedic optimization
- 📝 **SOAP Note Generation** - GPT-4 powered structured clinical documentation
- 💬 **Feedback System** - Collect and analyze transcription quality feedback
- 🔐 **JWT Authentication** - Secure API access with token-based auth
- 🎯 **Medical Terminology** - Automatic corrections for common medical terms
- 🔊 **Multi-Format Support** - WAV, MP3, OPUS, and more
- ⚡ **Async Operations** - Fast, non-blocking request handling
- 📊 **Medical Keyterms** - Optimized for orthopedic clinical documentation
- 💾 **MongoDB Storage** - Persistent, scalable feedback storage

## 🚀 Quick Start

### 1. Setup Environment

```bash
# Clone or navigate to the directory
cd refactored_code

# Copy environment variables template
cp .env.example .env

# Edit .env with your actual API keys
nano .env  # or use your preferred editor
```

### 2. Configure API Keys

Edit `.env` file:

```bash
DEEPGRAM_API_KEY=your_actual_deepgram_key
OPENAI_API_KEY=your_actual_openai_key
AUTH_USERNAME=admin
AUTH_PASSWORD=your_secure_password
SECRET_KEY=your_jwt_secret_key

# MongoDB Configuration (for feedback storage)
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=audio-to-text-db
```

### 3. Install MongoDB

MongoDB is required for feedback storage.

**macOS (using Homebrew):**

```bash
brew tap mongodb/brew
brew install mongodb-community
brew services start mongodb-community
```

**Ubuntu/Debian:**

```bash
wget -qO - https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu focal/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
sudo apt-get update
sudo apt-get install -y mongodb-org
sudo systemctl start mongod
```

**Windows:**

- Download from [MongoDB Download Center](https://www.mongodb.com/try/download/community)
- Run installer and follow setup wizard

**Using Docker (Quick & Easy):**

```bash
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

### 4. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 5. Install FFmpeg (Required for OPUS audio)

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg

# Windows
# Download from: https://ffmpeg.org/download.html
```

### 6. Start the Server

```bash
# Using the start script
chmod +x start.sh
./start.sh

# Or manually
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at:

- **API**: http://localhost:8000
- **Documentation**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health

## 📚 API Endpoints

### 1. Authentication

#### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin"
  }'
```

**Response:**

```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

### 2. Transcribe Audio

#### Without SOAP Note

```bash
curl -X POST http://localhost:8000/api/v1/transcribe \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@audio.wav"
```

#### With SOAP Note Generation

```bash
curl -X POST http://localhost:8000/api/v1/transcribe \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@audio.wav" \
  -F "generate_soap=true"
```

**Response:**

```json
{
  "transcription_id": "txn_1234567890123",
  "text": "The patient is a 31 year old female who presents...",
  "confidence": 0.9971397,
  "language": "en-US",
  "duration": 107.29781,
  "soap_note": "S: Subjective\n\nChief Complaint:...",
  "created_at": "2025-10-27T17:47:45.821463"
}
```

### 3. Generate SOAP Note from Text

```bash
curl -X POST http://localhost:8000/generate-soap \
  -F "text=The patient is a 31 year old female who presents with right knee pain..."
```

**Response:**

```json
{
  "text": "The patient is a 31 year old female who presents...",
  "soap_note": "S: Subjective\n\nChief Complaint:...\n\nO: Objective...",
  "created_at": "2025-10-27T17:47:45.821463"
}
```

### 4. Health Check

```bash
curl http://localhost:8000/health
```

**Response:**

```json
{
  "status": "healthy",
  "timestamp": "2025-10-27T17:47:45.821463",
  "services": {
    "deepgram": "configured",
    "openai": "configured"
  }
}
```

### 5. Feedback API

#### Submit Feedback

```bash
curl -X POST http://localhost:8000/api/v1/feedback/submit \
  -H "Content-Type: application/json" \
  -d '{
    "rating": 5,
    "rating_text": "Excellent transcription",
    "transcription_preview": "The patient is a 31 year old...",
    "errors_found": [
      {"wrong": "catching lock", "correct": "catching, locking"}
    ],
    "total_errors": 1,
    "feedback_type": "detailed",
    "feedback": "Medical terminology was mostly accurate"
  }'
```

**Response:**

```json
{
  "message": "Feedback saved successfully",
  "feedback_id": "1",
  "total_feedback": 1
}
```

#### Get Feedback Statistics

```bash
curl http://localhost:8000/api/v1/feedback/stats
```

**Response:**

```json
{
  "total_feedback": 10,
  "average_rating": 4.5,
  "recent_feedback": [...]
}
```

#### Get All Feedback

```bash
curl http://localhost:8000/api/v1/feedback/all
```

**Response:**

```json
{
  "total_feedback": 10,
  "feedback": [...]
}
```

## 📁 Project Structure

```
refactored_code/
├── app/
│   ├── __init__.py          # Package initialization
│   ├── main.py              # Main FastAPI application
│   ├── schemas.py           # Pydantic request/response models
│   ├── mongodb.py           # MongoDB connection & management
│   ├── prompts.py           # Medical terminology corrections
│   └── api/
│       ├── __init__.py      # API package
│       └── feedback.py      # Feedback API router
├── .env.example             # Environment variables template
├── .gitignore               # Git ignore rules
├── README.md                # This file
├── QUICK_START.md           # Quick setup guide
├── MONGODB_SETUP.md         # MongoDB installation guide
├── MONGODB_MIGRATION.md     # MongoDB migration details
├── requirements.txt         # Python dependencies
└── start.sh                 # Startup script
```

## 🏗️ Architecture

### Modular Code Organization

1. **`app/main.py`** - Main application with endpoints

   - Configuration and environment variables
   - FastAPI app initialization
   - Authentication (JWT)
   - Transcription endpoints
   - SOAP note generation
   - Startup/shutdown events

2. **`app/schemas.py`** - Pydantic models

   - Request/response schemas
   - Data validation models
   - Type definitions

3. **`app/mongodb.py`** - Database layer

   - MongoDB connection management
   - Database instance getter
   - Connection lifecycle

4. **`app/api/feedback.py`** - Feedback API router

   - Feedback submission endpoint
   - Statistics endpoint
   - List all feedback endpoint
   - MongoDB feedback operations

5. **`app/prompts.py`** - Medical terminology
   - Terminology corrections dictionary
   - Medical term mappings

### Key Features

- **Modular Architecture** - Separation of concerns across multiple files
- **MongoDB Integration** - Production-ready feedback storage
- **Async Operations** - Non-blocking I/O throughout
- **Direct REST APIs** - No heavy SDK dependencies
- **Clean Error Handling** - Proper HTTP status codes and messages
- **Comprehensive Logging** - Detailed logs for debugging
- **Medical Optimization** - Keyterms and corrections for orthopedic care

## 🔧 Configuration

### Environment Variables

| Variable           | Required | Default        | Description                        |
| ------------------ | -------- | -------------- | ---------------------------------- |
| `DEEPGRAM_API_KEY` | Yes      | -              | Deepgram API key for transcription |
| `OPENAI_API_KEY`   | Yes\*    | -              | OpenAI API key for SOAP generation |
| `AUTH_USERNAME`    | No       | admin          | API authentication username        |
| `AUTH_PASSWORD`    | No       | admin          | API authentication password        |
| `SECRET_KEY`       | No       | change-this... | JWT secret key                     |
| `MAX_FILE_SIZE_MB` | No       | 100            | Maximum upload file size in MB     |

\*Required for SOAP note generation

### Deepgram Configuration

The API uses Deepgram Nova-3 Medical with:

- **Model**: `nova-3-medical`
- **Features**: Diarization, smart formatting, numerals
- **Custom Intent**: `orthopedic_patient_assessment`
- **Medical Keyterms**: Optimized for orthopedic terminology

### Medical Terminology

Automatic corrections applied:

- "false lip trauma" → "fall slip trauma"
- "catching lock" → "catching, locking"
- "open condition" → "open skin lesion"
- And more...

## 🧪 Testing

### Using Interactive Documentation

Visit http://localhost:8000/docs for Swagger UI where you can:

1. Authenticate using the login endpoint
2. Copy the access token
3. Click "Authorize" button and paste token
4. Test all endpoints interactively

### Using Python

```python
import requests

# Login
response = requests.post(
    "http://localhost:8000/api/v1/auth/login",
    json={"username": "admin", "password": "admin"}
)
token = response.json()["access_token"]

# Transcribe with SOAP
with open("audio.wav", "rb") as f:
    response = requests.post(
        "http://localhost:8000/api/v1/transcribe",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": f},
        data={"generate_soap": "true"}
    )

result = response.json()
print(result["soap_note"])
```

## 📊 Performance

- **Startup Time**: ~1 second
- **Request Handling**: Async, non-blocking
- **File Processing**: Supports up to 100MB (configurable)
- **Transcription**: Depends on Deepgram API (~1-2s per minute of audio)
- **SOAP Generation**: ~2-5 seconds with GPT-4

## 🔒 Security

- **JWT Authentication** - Token-based API access
- **Token Expiration** - 30-minute default timeout
- **File Validation** - Size and type checking
- **CORS Enabled** - Configurable origins
- **Environment Variables** - Sensitive data not in code

## 🐛 Troubleshooting

### FFmpeg Not Found

```bash
# Install FFmpeg for OPUS conversion support
brew install ffmpeg  # macOS
sudo apt install ffmpeg  # Ubuntu
```

### Import Errors

```bash
# Ensure virtual environment is activated
source venv/bin/activate
pip install -r requirements.txt
```

### API Key Errors

```bash
# Check .env file exists and has valid keys
cat .env
# Make sure DEEPGRAM_API_KEY and OPENAI_API_KEY are set
```

### Port Already in Use

```bash
# Change port in start command
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## 📝 Development

### Adding New Features

1. **New Endpoint**: Add function in "API ENDPOINTS" section
2. **New Model**: Add Pydantic model in "PYDANTIC MODELS" section
3. **New Helper**: Add function in "HELPER FUNCTIONS" section

### Code Style

- Follow PEP 8 guidelines
- Use type hints
- Add docstrings to all functions
- Keep functions focused and single-purpose

## 📄 License

This project is for internal use. All rights reserved.

## 🤝 Support

For issues or questions:

1. Check the logs for detailed error messages
2. Verify API keys are configured correctly
3. Ensure all dependencies are installed
4. Check FFmpeg is installed for OPUS support

## 🎉 What's Next?

This refactored version is:

- ✅ Production-ready
- ✅ Clean and maintainable
- ✅ Fast and efficient
- ✅ Well-documented
- ✅ Easy to extend

Enjoy your streamlined medical transcription API!
