#!/usr/bin/env python3
"""
Test script to verify PR1 generation after SOAP creation
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_soap_to_pr1_workflow():
    """Test that PR1 is generated after SOAP creation"""
    
    print("=" * 80)
    print("Testing SOAP → PR1 Generation Workflow")
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
    
    # Step 2: Wait for background processing
    print("\n2. Waiting for background PR1 generation (5 seconds)...")
    time.sleep(5)
    
    # Step 3: Check if PR1 was generated
    print("\n3. Checking for PR1 form...")
    pr1_response = requests.get(
        f"{BASE_URL}/api/v1/pr1/saved/{soap_id}"
    )
    
    if pr1_response.status_code == 200:
        pr1_data = pr1_response.json()
        if pr1_data.get("status") == "success":
            print("✅ PR1 form found!")
            pr1_form = pr1_data.get("data", {})
            print(f"   Status: {pr1_form.get('status', 'N/A')}")
            print(f"   Created: {pr1_form.get('created_at', 'N/A')}")
            print(f"   Updated: {pr1_form.get('updated_at', 'N/A')}")
            
            if pr1_form.get("form_data"):
                print("   ✅ Form data is present")
            else:
                print("   ⚠️  Form data is missing")
        else:
            print(f"⚠️  PR1 not found: {pr1_data.get('message')}")
    else:
        print(f"❌ Failed to fetch PR1: {pr1_response.status_code}")
        print(pr1_response.text)
    
    # Step 4: Try manual PR1 generation for comparison
    print("\n4. Manually generating PR1 for comparison...")
    manual_pr1_response = requests.post(
        f"{BASE_URL}/api/v1/pr1/generate-from-soap",
        data={
            "soap_id": soap_id,
            "use_latest_intake": "true",
            "use_latest_followup": "true"
        }
    )
    
    if manual_pr1_response.status_code == 200:
        print("✅ Manual PR1 generation successful")
        manual_data = manual_pr1_response.json()
        print(f"   Status: {manual_data.get('status')}")
        if manual_data.get('pr1_values'):
            print("   ✅ PR1 values present")
            # Print a sample of the data
            pr1_vals = manual_data['pr1_values']
            if pr1_vals.get('header_admin'):
                print(f"   Patient: {pr1_vals['header_admin'].get('patient_name', 'N/A')}")
        else:
            print("   ⚠️  PR1 values missing")
    else:
        print(f"❌ Manual PR1 generation failed: {manual_pr1_response.status_code}")
        print(manual_pr1_response.text)
    
    print("\n" + "=" * 80)
    print("Test completed!")
    print("=" * 80)

if __name__ == "__main__":
    test_soap_to_pr1_workflow()
