#!/bin/bash

# Start script for Medical Transcription API

echo "🚀 Starting Medical Transcription & SOAP Note API..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "📥 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env file not found!"
    echo "📝 Please copy .env.example to .env and configure your API keys"
    echo ""
    echo "Run: cp .env.example .env"
    echo "Then edit .env with your actual API keys"
    echo ""
    exit 1
fi

# Start the application
echo ""
echo "✅ Starting API server..."
echo "📖 API Documentation: http://localhost:8000/docs"
echo "🔍 Health Check: http://localhost:8000/health"
echo ""

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

