#!/usr/bin/env python3
import requests
import base64
import json
import os
import sys

# Simple test DOCX content
docx_content = b'PK\x03\x04\x14\x00\x00\x00\x08\x00\x00\x00!\x00test'

# Base64 encode
file_data_b64 = base64.b64encode(docx_content).decode('utf-8')

# Payload
payload = {
    "filename": "AE.docx",
    "file_data": file_data_b64
}

url = "https://legaldocs-processor-djefd2eygvcugdgz.westeurope-01.azurewebsites.net/api/upload_blob"

# Optional admin passcode: CLI arg takes precedence, then env LEGAL_UPLOAD_PASSWORD
passcode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LEGAL_UPLOAD_PASSWORD", "")
headers = {"Content-Type": "application/json"}
if passcode:
    headers["x-legal-admin-passcode"] = passcode

print(f"Uploading to: {url}")
response = requests.post(url, json=payload, headers=headers, timeout=30)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
