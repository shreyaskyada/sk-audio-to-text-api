#!/usr/bin/env python3
"""
Test script to verify PR1 generation after SOAP creation - Extended wait time
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_soap_to_pr1_workflow():
    """Test that PR1 is generated after SOAP creation"""
    
    print("=" * 80)
    print("Testing SOAP → PR1 Generation Workflow (Extended)")
    print("=" * 80)
    
    # Step 0: Create a transcription first
    print("\n0. Creating transcription...")
    transcription_payload = {
        "text": "Patient presents with right knee pain after fall. Physical exam shows swelling and tenderness. Assessment: Right knee contusion. Plan: Ice, rest, follow up in 1 week.",
        "confidence": 0.95,
        "language": "en",
        "duration": 30.5,
        "filename": "test_audio.mp3",
        "user_id": "test_user"
    }
    
    trans_response = requests.post(
        f"{BASE_URL}/api/v1/transcriptions",
        json=transcription_payload
    )
    
    if trans_response.status_code != 200:
        print(f"❌ Transcription creation failed: {trans_response.status_code}")
        print(trans_response.text)
        return
    
    trans_data = trans_response.json()
    transcription_id = trans_data.get("id")
    print(f"✅ Transcription created: {transcription_id}")
    
    # Step 1: Create a test SOAP note
    print("\n1. Creating SOAP note...")
    soap_payload = {
        "transcription_id": transcription_id,
        "userId": "test_user",
        "patient": {
            "name": "Test Patient",
            "age": 45,
            "gender": "Male"
        },
        "date_of_service": "2026-01-16",
        "location": "Test Clinic"
    }
    
    response = requests.post(
        f"{BASE_URL}/api/v1/generate-soap",
        json=soap_payload
    )
    
    if response.status_code != 200:
        print(f"❌ SOAP creation failed: {response.status_code}")
        print(response.text)
        return
    
    soap_data = response.json()
    soap_id = soap_data.get("document_id")
    transcription_id = soap_data.get("transcription_id")
    
    print(f"✅ SOAP created successfully")
    print(f"   SOAP ID: {soap_id}")
    print(f"   Transcription ID: {transcription_id}")
    print(f"   Status: {soap_data.get('status', 'N/A')}")
    
    # Step 2: Poll for PR1 completion
    print("\n2. Polling for PR1 completion...")
    max_attempts = 15
    for attempt in range(max_attempts):
        time.sleep(2)
        print(f"   Attempt {attempt + 1}/{max_attempts}...")
        
        pr1_response = requests.get(
            f"{BASE_URL}/api/v1/pr1/saved/{soap_id}"
        )
        
        if pr1_response.status_code == 200:
            pr1_data = pr1_response.json()
            if pr1_data.get("status") == "success":
                pr1_form = pr1_data.get("data", {})
                status = pr1_form.get("status", "N/A")
                print(f"   Current status: {status}")
                
                if status == "completed" and pr1_form.get("form_data"):
                    print("\n✅ PR1 generation completed!")
                    print(f"   Status: {status}")
                    print(f"   Created: {pr1_form.get('created_at', 'N/A')}")
                    print(f"   Updated: {pr1_form.get('updated_at', 'N/A')}")
                    print("   ✅ Form data is present")
                    break
                elif status == "pending":
                    continue
            else:
                print(f"   PR1 not found yet: {pr1_data.get('message')}")
        else:
            print(f"   Failed to fetch PR1: {pr1_response.status_code}")
    else:
        print("\n⚠️  PR1 did not complete within timeout")
    
    print("\n" + "=" * 80)
    print("Test completed!")
    print("=" * 80)

if __name__ == "__main__":
    test_soap_to_pr1_workflow()
