import azure.functions as func
import json
import logging
import os
from azure.storage.blob import BlobServiceClient
import base64

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Upload blob function processed a request.')
    
    try:
        # Parse request body
        req_body = req.get_json()
        if not req_body:
            return func.HttpResponse(
                json.dumps({"success": False, "message": "No JSON body provided"}),
                status_code=400,
                mimetype="application/json"
            )
        
        # Extract parameters
        container = req_body.get('container', 'legaldocsrag')
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
        blob_client = blob_service_client.get_blob_client(container=container, blob=filename)
        
        # Decode base64 file data
        try:
            file_bytes = base64.b64decode(file_data)
        except Exception as e:
            return func.HttpResponse(
                json.dumps({"success": False, "message": f"Invalid base64 file data: {str(e)}"}),
                status_code=400,
                mimetype="application/json"
            )
        
        # Upload blob (this will overwrite if exists - perfect for replace functionality)
        blob_client.upload_blob(file_bytes, overwrite=True)
        
        logging.info(f"Successfully uploaded {filename} to container {container}")
        
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "message": f"Successfully uploaded {filename}",
                "filename": filename,
                "container": container,
                "size_bytes": len(file_bytes)
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
