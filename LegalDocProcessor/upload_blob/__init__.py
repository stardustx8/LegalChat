import azure.functions as func
import json
import logging
import os
from azure.storage.blob import BlobServiceClient
import base64

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Upload blob function processed a request.')
    
    try:
        # Parse JSON body
        req_body = req.get_json()
        
        if not req_body:
            return func.HttpResponse(
                json.dumps({"error": "Request body is required"}),
                status_code=400,
                mimetype="application/json"
            )

        # Admin passcode gating (if LEGAL_UPLOAD_PASSWORD is set)
        expected_pass = os.environ.get('LEGAL_UPLOAD_PASSWORD', '').strip()
        if expected_pass:
            provided_pass = (req.headers.get('x-legal-admin-passcode') or req_body.get('passcode') or '').strip()
            if provided_pass != expected_pass:
                return func.HttpResponse(
                    json.dumps({"success": False, "message": "Unauthorized: invalid passcode"}),
                    status_code=401,
                    mimetype="application/json"
                )

        # Extract required fields
        filename = req_body.get('filename')
        file_data = req_body.get('file_data')  # Base64 encoded file content
        
        if not filename or not file_data:
            return func.HttpResponse(
                json.dumps({"success": False, "message": "Missing filename or file_data"}),
                status_code=400,
                mimetype="application/json"
            )
        
        # Validate filename format (XX.docx)
        if not filename.endswith('.docx') or len(filename) != 7 or filename[:2].upper() != filename[:2]:
            return func.HttpResponse(
                json.dumps({
                    "success": False, 
                    "message": f"Invalid filename format. Expected: XX.docx (e.g., DE.docx), got: {filename}"
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        # Get storage connection string
        connection_string = os.environ.get('KNIFE_STORAGE_CONNECTION_STRING')
        if not connection_string:
            return func.HttpResponse(
                json.dumps({"success": False, "message": "Storage connection string not configured"}),
                status_code=500,
                mimetype="application/json"
            )
        
        # Initialize blob service client
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_name = req_body.get('container', 'legaldocsrag')
        blob_client = blob_service_client.get_blob_client(
            container=container_name,
            blob=filename
        )
        
        # Decode base64 file data
        try:
            decoded_data = base64.b64decode(file_data)
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"success": False, "message": f"Invalid base64 file data: {str(e)}"}),
                status_code=400,
                mimetype="application/json"
            )
        
        # Upload the blob (caption/OCR always enabled)
        blob_client.upload_blob(
            decoded_data,
            overwrite=True
        )
        
        logging.info(f"Successfully uploaded {filename} to container {container_name}")
        
        # Extract ISO code from filename (first 2 characters)
        iso_code = filename[:2]
        
        return func.HttpResponse(
            json.dumps({
                "message": f"File {filename} uploaded successfully",
                "iso_code": iso_code
            }),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logging.error(f"Error uploading blob: {str(e)}")
        return func.HttpResponse(
            json.dumps({"success": False, "message": f"Upload failed: {str(e)}"}),
            status_code=500,
            mimetype="application/json"
        )
