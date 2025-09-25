#!/usr/bin/env python3
"""
Simple script to check feedback from customers
Run this to see all feedback data
"""

import json
import os
from datetime import datetime

def check_feedback():
    feedback_file = "logs/feedback.json"
    
    if not os.path.exists(feedback_file):
        print("❌ No feedback file found at logs/feedback.json")
        print("Make sure customers have submitted feedback first.")
        return
    
    try:
        with open(feedback_file, 'r') as f:
            feedback = json.load(f)
        
        if not feedback:
            print("📝 No feedback entries found")
            return
        
        print(f"📊 FEEDBACK SUMMARY")
        print(f"Total feedback entries: {len(feedback)}")
        
        # Calculate average rating
        avg_rating = sum(f["rating"] for f in feedback) / len(feedback)
        print(f"Average rating: {avg_rating:.2f}/5")
        
        print(f"\n📝 ALL FEEDBACK:")
        print("-" * 80)
        
        for i, f in enumerate(feedback, 1):
            print(f"\n{i}. {f['timestamp']}")
            print(f"   Rating: {f['rating']}/5 ⭐")
            print(f"   Feedback: {f['feedback'] or 'No additional feedback'}")
            print(f"   Preview: {f['transcription_preview']}")
            print("-" * 80)
        
        # Show rating distribution
        rating_counts = {}
        for f in feedback:
            rating = f["rating"]
            rating_counts[rating] = rating_counts.get(rating, 0) + 1
        
        print(f"\n📈 RATING DISTRIBUTION:")
        for rating in sorted(rating_counts.keys()):
            count = rating_counts[rating]
            percentage = (count / len(feedback)) * 100
            print(f"   {rating} stars: {count} ({percentage:.1f}%)")
        
    except Exception as e:
        print(f"❌ Error reading feedback: {e}")

if __name__ == "__main__":
    check_feedback()
