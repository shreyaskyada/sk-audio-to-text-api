#!/usr/bin/env python3
"""
Test Work Status generation specifically
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_work_status_generation():
    print("=" * 80)
    print("Testing Work Status Generation")
    print("=" * 80)
    
    # Create transcription
    print("\n1. Creating transcription...")
    trans_resp = requests.post(f"{BASE_URL}/api/v1/transcriptions", json={
        "text": "Patient presents with right knee pain. Work Status: Modified duty, no lifting over 20 lbs.",
        "confidence": 0.95,
        "language": "en",
        "duration": 30.5,
        "filename": "test.mp3",
        "user_id": "ws_test"
    })
    trans_id = trans_resp.json().get("id")
    print(f"✅ Transcription: {trans_id}")
    
    # Create SOAP
    print("\n2. Creating SOAP...")
    soap_resp = requests.post(f"{BASE_URL}/api/v1/generate-soap", json={
        "transcription_id": trans_id,
        "userId": "ws_test",
        "patient": {"name": "WS Test", "age": 45, "gender": "Male"},
        "date_of_service": "2026-01-16",
        "location": "Test Clinic"
    })
    soap_id = soap_resp.json().get("document_id")
    print(f"✅ SOAP ID: {soap_id}")
    
    # Wait for background generation
    print("\n3. Waiting for background generation (15 seconds)...")
    for i in range(15):
        time.sleep(1)
        print(f"   {i+1}/15...", end="\r")
    print()
    
    # Check PR1
    print("\n4. Checking PR1...")
    pr1_resp = requests.get(f"{BASE_URL}/api/v1/pr1/saved/{soap_id}")
    if pr1_resp.status_code == 200:
        pr1_data = pr1_resp.json()
        print(f"✅ PR1 Status: {pr1_data.get('data', {}).get('status')}")
    else:
        print(f"❌ PR1 not found: {pr1_resp.status_code}")
    
    # Check Work Status
    print("\n5. Checking Work Status...")
    ws_resp = requests.get(f"{BASE_URL}/api/v1/work-status-form/saved/{soap_id}")
    print(f"   Response code: {ws_resp.status_code}")
    if ws_resp.status_code == 200:
        ws_data = ws_resp.json()
        print(f"✅ Work Status found!")
        print(f"   Status: {ws_data.get('data', {}).get('status')}")
        print(f"   Created: {ws_data.get('data', {}).get('created_at')}")
    else:
        print(f"❌ Work Status not found")
        print(f"   Error: {ws_resp.text}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    test_work_status_generation()
