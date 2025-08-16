#!/usr/bin/env python3
"""Test connection to Azure Function App upload endpoint"""

import requests
import json
import sys

# Configuration
FUNCTION_APP_URL = "https://legaldocs-processor.azurewebsites.net"
UPLOAD_ENDPOINT = f"{FUNCTION_APP_URL}/api/upload_blob"

print("Testing Azure Function App Connection")
print("=" * 50)
print(f"Function App URL: {FUNCTION_APP_URL}")
print(f"Upload Endpoint: {UPLOAD_ENDPOINT}")
print()

# Test 1: Basic connectivity
print("Test 1: Basic connectivity (no auth)")
try:
    response = requests.post(UPLOAD_ENDPOINT, timeout=10)
    print(f"  Status Code: {response.status_code}")
    print(f"  Response: {response.text[:200] if response.text else '(empty)'}")
    if response.status_code == 401:
        print("  ✓ Server is reachable and expecting authentication")
    elif response.status_code == 404:
        print("  ✗ Endpoint not found - check Function App deployment")
    else:
        print(f"  ? Unexpected response code")
except requests.exceptions.ConnectionError as e:
    print(f"  ✗ Connection failed: {e}")
    print("  Check if Function App is running")
except requests.exceptions.Timeout:
    print("  ✗ Request timed out")
except Exception as e:
    print(f"  ✗ Error: {e}")

print()

# Test 2: With passcode header (invalid)
print("Test 2: With invalid passcode")
headers = {
    "x-legal-admin-passcode": "wrong_password",
    "Content-Type": "application/json"
}
data = {
    "filename": "TEST.docx",
    "file_data": "dGVzdA=="  # base64 "test"
}

try:
    response = requests.post(UPLOAD_ENDPOINT, 
                            headers=headers, 
                            json=data,
                            timeout=10)
    print(f"  Status Code: {response.status_code}")
    print(f"  Response: {response.text[:200] if response.text else '(empty)'}")
    
    if response.status_code == 401:
        print("  ✓ Server correctly rejects invalid passcode")
    elif response.status_code == 400:
        print("  ? Server returned bad request - check error message")
    elif response.status_code == 200:
        print("  ✗ Server accepted invalid passcode - security issue!")
except Exception as e:
    print(f"  ✗ Error: {e}")

print()

# Test 3: Check CORS headers
print("Test 3: CORS headers check")
try:
    # OPTIONS request to check CORS
    response = requests.options(UPLOAD_ENDPOINT, 
                               headers={"Origin": "http://localhost:8000"},
                               timeout=10)
    print(f"  Status Code: {response.status_code}")
    cors_headers = {
        "Access-Control-Allow-Origin": response.headers.get("Access-Control-Allow-Origin", "Not set"),
        "Access-Control-Allow-Methods": response.headers.get("Access-Control-Allow-Methods", "Not set"),
        "Access-Control-Allow-Headers": response.headers.get("Access-Control-Allow-Headers", "Not set")
    }
    for header, value in cors_headers.items():
        print(f"  {header}: {value}")
    
    if cors_headers["Access-Control-Allow-Origin"] != "Not set":
        print("  ✓ CORS is configured")
    else:
        print("  ✗ CORS not configured - frontend requests will fail")
except Exception as e:
    print(f"  ✗ Error: {e}")

print()
print("=" * 50)
print("Next steps:")
print("1. If connection failed: Check Function App is deployed and running")
print("2. If CORS not configured: Add your frontend URL to CORS settings in Azure Portal")
print("3. If passcode rejected: Set LEGAL_UPLOAD_PASSWORD in Function App Configuration")
print("4. Check Function App logs in Azure Portal for detailed error messages")
