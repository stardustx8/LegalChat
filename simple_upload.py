#!/usr/bin/env python3
import requests
import base64
import json

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

print(f"Uploading to: {url}")
response = requests.post(url, json=payload, timeout=30)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
