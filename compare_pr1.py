#!/usr/bin/env python3
"""
Compare auto-generated PR1 vs manually generated PR1
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def compare_pr1_generation():
    """Compare auto vs manual PR1 generation"""
    
    print("=" * 80)
    print("Comparing Auto-Generated vs Manual PR1")
    print("=" * 80)
    
    # Step 1: Create a transcription
    print("\n1. Creating transcription...")
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
        return
    
    trans_data = trans_response.json()
    transcription_id = trans_data.get("id")
    print(f"✅ Transcription created: {transcription_id}")
    
    # Step 2: Create SOAP note (triggers auto PR1 generation)
    print("\n2. Creating SOAP note (auto PR1 will be generated)...")
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
        return
    
    soap_data = response.json()
    soap_id = soap_data.get("document_id")
    print(f"✅ SOAP created: {soap_id}")
    
    # Step 3: Wait for auto PR1 generation
    print("\n3. Waiting for auto PR1 generation (10 seconds)...")
    time.sleep(10)
    
    # Step 4: Fetch auto-generated PR1
    print("\n4. Fetching auto-generated PR1...")
    auto_pr1_response = requests.get(f"{BASE_URL}/api/v1/pr1/saved/{soap_id}")
    
    auto_pr1_data = None
    if auto_pr1_response.status_code == 200:
        auto_pr1_result = auto_pr1_response.json()
        if auto_pr1_result.get("status") == "success":
            auto_pr1_data = auto_pr1_result.get("data", {}).get("form_data")
            print(f"✅ Auto PR1 fetched (status: {auto_pr1_result.get('data', {}).get('status')})")
        else:
            print(f"⚠️  Auto PR1 not found: {auto_pr1_result.get('message')}")
    else:
        print(f"❌ Failed to fetch auto PR1: {auto_pr1_response.status_code}")
    
    # Step 5: Generate manual PR1 with exact curl parameters
    print("\n5. Generating manual PR1 with exact curl parameters...")
    manual_pr1_response = requests.post(
        f"{BASE_URL}/api/v1/pr1/generate-from-soap",
        data={
            "soap_id": soap_id,
            "use_latest_intake": "true",
            "use_latest_followup": "false",
            "flags": json.dumps({
                "progress_report": True,
                "request_for_authorization": False,
                "change_in_patient_condition": False
            })
        }
    )
    
    manual_pr1_data = None
    if manual_pr1_response.status_code == 200:
        manual_pr1_result = manual_pr1_response.json()
        manual_pr1_data = manual_pr1_result.get("pr1_values")
        print(f"✅ Manual PR1 generated (status: {manual_pr1_result.get('status')})")
    else:
        print(f"❌ Manual PR1 generation failed: {manual_pr1_response.status_code}")
        print(manual_pr1_response.text)
    
    # Step 6: Compare the two
    print("\n" + "=" * 80)
    print("COMPARISON")
    print("=" * 80)
    
    if auto_pr1_data and manual_pr1_data:
        # Compare header_admin
        print("\n📋 Header Admin:")
        auto_header = auto_pr1_data.get("header_admin", {})
        manual_header = manual_pr1_data.get("header_admin", {})
        
        print(f"  Patient Name:")
        print(f"    Auto:   {auto_header.get('patient_name')}")
        print(f"    Manual: {manual_header.get('patient_name')}")
        
        print(f"  Date of Birth:")
        print(f"    Auto:   {auto_header.get('date_of_birth')}")
        print(f"    Manual: {manual_header.get('date_of_birth')}")
        
        # Compare section_a_rfa
        print("\n📋 Section A (RFA):")
        auto_section_a = auto_pr1_data.get("section_a_rfa", {})
        manual_section_a = manual_pr1_data.get("section_a_rfa", {})
        
        print(f"  RFA Items Count:")
        print(f"    Auto:   {len(auto_section_a.get('rfa_items', []))}")
        print(f"    Manual: {len(manual_section_a.get('rfa_items', []))}")
        
        # Compare section_b
        print("\n📋 Section B (Evaluation):")
        auto_section_b = auto_pr1_data.get("section_b_evaluation_management", {})
        manual_section_b = manual_pr1_data.get("section_b_evaluation_management", {})
        
        print(f"  Subjective:")
        print(f"    Auto:   {auto_section_b.get('subjective', '')[:100]}...")
        print(f"    Manual: {manual_section_b.get('subjective', '')[:100]}...")
        
        # Save full comparison to file
        with open("pr1_comparison.json", "w") as f:
            json.dump({
                "auto_generated": auto_pr1_data,
                "manual_generated": manual_pr1_data
            }, f, indent=2)
        print("\n💾 Full comparison saved to: pr1_comparison.json")
        
    else:
        if not auto_pr1_data:
            print("❌ Auto PR1 data is missing")
        if not manual_pr1_data:
            print("❌ Manual PR1 data is missing")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    compare_pr1_generation()
