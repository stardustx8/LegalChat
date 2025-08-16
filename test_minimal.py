#!/usr/bin/env python3
"""Minimal test to debug Azure Function 500 error"""

import requests
import json

url = "https://legaldocs-processor-djefd2eygvcugdgz.westeurope-01.azurewebsites.net/api/upload_blob"

# Test 1: Empty request
print("Test 1: Empty POST request")
try:
    response = requests.post(url, timeout=10)
    print(f"  Status: {response.status_code}")
    print(f"  Response: {response.text if response.text else '(empty)'}")
except Exception as e:
    print(f"  Error: {e}")

print()

# Test 2: Invalid JSON
print("Test 2: Invalid JSON body")
try:
    response = requests.post(url, 
                            data="not json", 
                            headers={"Content-Type": "application/json"},
                            timeout=10)
    print(f"  Status: {response.status_code}")
    print(f"  Response: {response.text if response.text else '(empty)'}")
except Exception as e:
    print(f"  Error: {e}")

print()

# Test 3: Empty JSON object
print("Test 3: Empty JSON object")
try:
    response = requests.post(url, 
                            json={}, 
                            headers={"Content-Type": "application/json"},
                            timeout=10)
    print(f"  Status: {response.status_code}")
    print(f"  Response: {response.text if response.text else '(empty)'}")
except Exception as e:
    print(f"  Error: {e}")

print()

# Test 4: With passcode but no data
print("Test 4: With passcode header but empty JSON")
try:
    response = requests.post(url, 
                            json={},
                            headers={
                                "Content-Type": "application/json",
                                "x-legal-admin-passcode": "Vx99"
                            },
                            timeout=10)
    print(f"  Status: {response.status_code}")
    print(f"  Response: {response.text if response.text else '(empty)'}")
except Exception as e:
    print(f"  Error: {e}")

print()
print("If all tests return empty 500 errors, the Function App likely needs:")
print("1. Restart after adding environment variables")
print("2. Re-deployment with latest code")
print("3. Check Azure Portal > Function App > Functions > upload_blob > Monitor for error logs")
