#!/usr/bin/env python3
import requests
import base64
import json
import os
import sys

# Usage: python simple_upload.py <file_path> --passcode <passcode>
if len(sys.argv) < 2:
    print("Usage: python simple_upload.py <file_path> [--passcode <passcode>]")
    sys.exit(1)

file_path = sys.argv[1]
if not os.path.exists(file_path):
    print(f"Error: File {file_path} does not exist")
    sys.exit(1)

# Get filename from path
filename = os.path.basename(file_path)

# Read file content
with open(file_path, 'rb') as f:
    docx_content = f.read()

# Base64 encode
file_data_b64 = base64.b64encode(docx_content).decode('utf-8')

# Payload
payload = {
    "filename": filename,
    "file_data": file_data_b64
}

url = "https://legaldocs-processor-djefd2eygvcugdgz.westeurope-01.azurewebsites.net/api/upload_blob"

# Parse passcode from args
passcode = ""
if "--passcode" in sys.argv:
    idx = sys.argv.index("--passcode")
    if idx + 1 < len(sys.argv):
        passcode = sys.argv[idx + 1]
else:
    passcode = os.environ.get("LEGAL_UPLOAD_PASSWORD", "")

headers = {"Content-Type": "application/json"}
if passcode:
    headers["x-legal-admin-passcode"] = passcode

print(f"Uploading file: {filename}")
print(f"Uploading to: {url}")
print(f"File size: {len(docx_content)} bytes")
print(f"Using passcode: {'Yes' if passcode else 'No'}")
print(f"Passcode value: {passcode[:3]}..." if passcode else "No passcode")
print()

try:
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    print(f"Status: {response.status_code}")
    
    # Try to parse JSON response
    try:
        response_data = response.json()
        print(f"Response: {json.dumps(response_data, indent=2)}")
    except:
        print(f"Response: {response.text}")
    
    # Show headers for debugging
    if response.status_code >= 400:
        print(f"\nResponse headers:")
        for key, value in response.headers.items():
            if key.lower() in ['content-type', 'x-ms-error-code', 'date']:
                print(f"  {key}: {value}")
except Exception as e:
    print(f"Error: {e}")
