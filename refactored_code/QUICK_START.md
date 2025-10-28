# Quick Start Guide 🚀

## 📋 Prerequisites

1. **Python 3.8+** installed
2. **MongoDB** installed and running (for feedback storage)
3. **FFmpeg** installed (for OPUS audio conversion)
4. **API Keys** from Deepgram and OpenAI

### Quick MongoDB Setup

**Using Docker (easiest):**

```bash
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

**Or install locally:**

- macOS: `brew install mongodb-community && brew services start mongodb-community`
- Ubuntu: `sudo apt install mongodb && sudo systemctl start mongod`
- Windows: Download from [mongodb.com](https://www.mongodb.com/try/download/community)

See `MONGODB_SETUP.md` for detailed instructions.

## ⚡ 5-Minute Setup

### Step 1: Configure Environment (1 min)

```bash
# Copy the example environment file
cp env.example .env

# Edit with your actual API keys
nano .env  # or use your preferred editor
```

**Required in `.env`:**

```bash
DEEPGRAM_API_KEY=your_actual_key_here
OPENAI_API_KEY=your_actual_key_here
AUTH_USERNAME=admin
AUTH_PASSWORD=your_secure_password
SECRET_KEY=your_jwt_secret_key

# MongoDB Configuration
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=audio-to-text-db
```

### Step 2: Install Dependencies (1 min)

```bash
# Make start script executable
chmod +x start.sh

# Run the start script (it will create venv and install everything)
./start.sh
```

**Or manually:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 3: Test the API (1 min)

**Open in browser:**

- API Docs: http://localhost:8000/docs
- Health Check: http://localhost:8000/health

**Or use curl:**

```bash
# Health check
curl http://localhost:8000/health

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}'
```

## 🎯 Quick API Test

### 1. Get Access Token

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r '.access_token')

echo "Token: $TOKEN"
```

### 2. Transcribe Audio

```bash
# Without SOAP
curl -X POST http://localhost:8000/api/v1/transcribe \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@your_audio.wav"

# With SOAP note
curl -X POST http://localhost:8000/api/v1/transcribe \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@your_audio.wav" \
  -F "generate_soap=true"
```

### 3. Generate SOAP Note from Text

```bash
curl -X POST http://localhost:8000/generate-soap \
  -F "text=The patient is a 31 year old female who presents with right knee pain after a fall. Physical exam shows tenderness over the medial joint line. McMurray test was negative. Plan includes knee brace and physical therapy."
```

## 📁 Project Structure

```
refactored_code/
├── app/
│   ├── __init__.py       # Package init
│   └── main.py           # All API logic (650 lines)
├── .gitignore            # Git ignore rules
├── env.example           # Environment template
├── README.md             # Full documentation
├── QUICK_START.md        # This file
├── requirements.txt      # Dependencies (8 packages)
└── start.sh              # Startup script
```

## ✅ Verification Checklist

- [ ] Python 3.8+ installed (`python3 --version`)
- [ ] FFmpeg installed (`ffmpeg -version`)
- [ ] `.env` file created and configured
- [ ] Dependencies installed (`pip list | grep fastapi`)
- [ ] Server starts without errors
- [ ] Can access http://localhost:8000/docs
- [ ] Health check returns "healthy"
- [ ] Can login and get access token
- [ ] Can transcribe audio file
- [ ] Can generate SOAP note

## 🆘 Common Issues

### Issue: "DEEPGRAM_API_KEY not configured"

**Solution:** Edit `.env` file and add your actual Deepgram API key

### Issue: "FFmpeg not found"

**Solution:** Install FFmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu
sudo apt install ffmpeg
```

### Issue: "Port 8000 already in use"

**Solution:** Change port in start command

```bash
uvicorn app.main:app --port 8001 --reload
```

### Issue: Import errors

**Solution:** Activate venv and reinstall

```bash
source venv/bin/activate
pip install -r requirements.txt
```

## 🎓 Next Steps

1. **Read Full Documentation**: See `README.md` for complete API reference
2. **Test All Endpoints**: Use the interactive docs at `/docs`
3. **Customize Settings**: Edit environment variables in `.env`
4. **Add Your Audio**: Test with your own audio files
5. **Integrate**: Use the API in your application

## 📞 API Endpoints Summary

| Endpoint             | Method | Auth | Description          |
| -------------------- | ------ | ---- | -------------------- |
| `/`                  | GET    | No   | API status           |
| `/health`            | GET    | No   | Health check         |
| `/docs`              | GET    | No   | Interactive API docs |
| `/api/v1/auth/login` | POST   | No   | Get access token     |
| `/api/v1/transcribe` | POST   | Yes  | Transcribe audio     |
| `/generate-soap`     | POST   | No   | Generate SOAP note   |

## 💡 Pro Tips

1. **Use the interactive docs** (`/docs`) - easiest way to test
2. **Keep your `.env` file secret** - never commit it to git
3. **Monitor logs** - they're very detailed for debugging
4. **Set file size limit** - configure `MAX_FILE_SIZE_MB` in `.env`
5. **Change default password** - update `AUTH_PASSWORD` in production

## 🚀 You're Ready!

Your Medical Transcription & SOAP Note API is now running and ready to use!

For detailed information, see `README.md`
