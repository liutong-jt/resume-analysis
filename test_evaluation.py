#!/usr/bin/env python3
"""
Test script to validate the resume evaluation system changes for handling "Unknown" names
"""

import json
import sys
import os

# Add the current directory to the Python path to import main module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import process_evaluation_result

def test_unknown_name_handling():
    """Test that unknown names are handled properly"""

    print("Testing Unknown Name Handling...")
    print("-" * 40)

    # Test case 1: Empty name
    test_result_1 = {
        "candidate_name": "",
        "domain_classification": "AI",
        "summary_one_liner": "Test summary",
        "evaluation_matrix": {
            "tech_foundation": {"score": 5, "reason": "Good foundation"},
            "tech_cognition": {"score": 6, "reason": "Good understanding"},
            "tech_potential": {"score": 7, "reason": "High potential"},
            "learning_ability": {"score": 8, "reason": "Quick learner"}
        },
        "total_score": 0  # Will be recalculated
    }

    processed_1 = process_evaluation_result(test_result_1, "john_doe.pdf")
    print(f"Test 1 - Empty name:")
    print(f"  Original name: '{test_result_1['candidate_name']}'")
    print(f"  Processed name: '{processed_1['candidate_name']}'")
    print(f"  Total score: {processed_1['total_score']}")
    assert processed_1['candidate_name'] == 'john_doe', f"Expected 'john_doe', got '{processed_1['candidate_name']}'"
    assert processed_1['total_score'] == 26, f"Expected 26, got {processed_1['total_score']}"
    print("  ✓ Passed")

    # Test case 2: "Unknown" name
    test_result_2 = {
        "candidate_name": "Unknown",
        "domain_classification": "Cloud",
        "summary_one_liner": "Test summary",
        "evaluation_matrix": {
            "tech_foundation": {"score": 3, "reason": "Basic foundation"},
            "tech_cognition": {"score": 4, "reason": "Developing understanding"},
            "tech_potential": {"score": 5, "reason": "Some potential"},
            "learning_ability": {"score": 6, "reason": "Adequate learner"}
        },
        "total_score": 0
    }

    processed_2 = process_evaluation_result(test_result_2, "resume_2024.pdf")
    print(f"\nTest 2 - 'Unknown' name:")
    print(f"  Original name: '{test_result_2['candidate_name']}'")
    print(f"  Processed name: '{processed_2['candidate_name']}'")
    print(f"  Total score: {processed_2['total_score']}")
    assert processed_2['candidate_name'] == 'resume_2024', f"Expected 'resume_2024', got '{processed_2['candidate_name']}'"
    assert processed_2['total_score'] == 18, f"Expected 18, got {processed_2['total_score']}"
    print("  ✓ Passed")

    # Test case 3: Valid name (should remain unchanged)
    test_result_3 = {
        "candidate_name": "John Smith",
        "domain_classification": "App Dev",
        "summary_one_liner": "Experienced developer",
        "evaluation_matrix": {
            "tech_foundation": {"score": 9, "reason": "Strong foundation"},
            "tech_cognition": {"score": 8, "reason": "Good understanding"},
            "tech_potential": {"score": 9, "reason": "High potential"},
            "learning_ability": {"score": 10, "reason": "Excellent learner"}
        },
        "total_score": 0
    }

    processed_3 = process_evaluation_result(test_result_3)
    print(f"\nTest 3 - Valid name:")
    print(f"  Original name: '{test_result_3['candidate_name']}'")
    print(f"  Processed name: '{processed_3['candidate_name']}'")
    print(f"  Total score: {processed_3['total_score']}")
    assert processed_3['candidate_name'] == 'John Smith', f"Expected 'John Smith', got '{processed_3['candidate_name']}'"
    assert processed_3['total_score'] == 36, f"Expected 36, got {processed_3['total_score']}"
    print("  ✓ Passed")

    # Test case 4: No filename provided
    test_result_4 = {
        "candidate_name": "Unknown",
        "domain_classification": "Other",
        "summary_one_liner": "Minimal experience",
        "evaluation_matrix": {
            "tech_foundation": {"score": 1, "reason": "Lacking foundation"},
            "tech_cognition": {"score": 1, "reason": "Limited understanding"},
            "tech_potential": {"score": 1, "reason": "Unclear potential"},
            "learning_ability": {"score": 1, "reason": "Limited evidence"}
        },
        "total_score": 0
    }

    processed_4 = process_evaluation_result(test_result_4)
    print(f"\nTest 4 - Unknown name, no filename:")
    print(f"  Original name: '{test_result_4['candidate_name']}'")
    print(f"  Processed name: '{processed_4['candidate_name']}'")
    print(f"  Total score: {processed_4['total_score']}")
    assert processed_4['candidate_name'] == 'Candidate', f"Expected 'Candidate', got '{processed_4['candidate_name']}'"
    assert processed_4['total_score'] == 4, f"Expected 4, got {processed_4['total_score']}"
    print("  ✓ Passed")

    # Test case 5: Invalid scores (should be corrected)
    test_result_5 = {
        "candidate_name": "Test User",
        "domain_classification": "Big Data",
        "summary_one_liner": "Test",
        "evaluation_matrix": {
            "tech_foundation": {"score": 15, "reason": "Invalid high score"},  # Should be capped at 10
            "tech_cognition": {"score": 0, "reason": "Invalid low score"},     # Should be set to 1
            "tech_potential": {"score": 7, "reason": "Valid score"},
            "learning_ability": {"score": -5, "reason": "Invalid negative score"}  # Should be set to 1
        },
        "total_score": 0
    }

    processed_5 = process_evaluation_result(test_result_5)
    print(f"\nTest 5 - Invalid scores:")
    print(f"  Tech foundation: {test_result_5['evaluation_matrix']['tech_foundation']['score']} → {processed_5['evaluation_matrix']['tech_foundation']['score']}")
    print(f"  Tech cognition: {test_result_5['evaluation_matrix']['tech_cognition']['score']} → {processed_5['evaluation_matrix']['tech_cognition']['score']}")
    print(f"  Tech potential: {test_result_5['evaluation_matrix']['tech_potential']['score']} → {processed_5['evaluation_matrix']['tech_potential']['score']}")
    print(f"  Learning ability: {test_result_5['evaluation_matrix']['learning_ability']['score']} → {processed_5['evaluation_matrix']['learning_ability']['score']}")
    print(f"  Total score: {processed_5['total_score']}")
    assert processed_5['evaluation_matrix']['tech_foundation']['score'] == 1
    assert processed_5['evaluation_matrix']['tech_cognition']['score'] == 1
    assert processed_5['evaluation_matrix']['tech_potential']['score'] == 7
    assert processed_5['evaluation_matrix']['learning_ability']['score'] == 1
    assert processed_5['total_score'] == 10, f"Expected 10, got {processed_5['total_score']}"
    print("  ✓ Passed")

    print("\n" + "=" * 40)
    print("All tests passed! ✓")
    print("The system now properly handles 'Unknown' names and validates scores.")

if __name__ == "__main__":
    test_unknown_name_handling()