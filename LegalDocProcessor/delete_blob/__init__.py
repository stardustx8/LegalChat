import logging
import os
import json
import azure.functions as func
from azure.storage.blob import BlobServiceClient

def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP-triggered Azure Function to delete blobs from Azure Storage.
    
    Usage:
    POST /api/delete_blob
    Body: {
        "container": "legaldocsrag",
        "filename": "FR.docx"
    }
    
    This function handles the storage deletion part of the document lifecycle.
    The frontend calls this first, then calls cleanup_index for complete removal.
    """
    logging.info('HTTP trigger function for blob deletion started')

    try:
        # Parse request body
        req_body = req.get_json()
        if not req_body:
            return func.HttpResponse(
                json.dumps({"error": "Request body is required", "success": False}),
                status_code=400,
                mimetype="application/json"
            )
        
        container_name = req_body.get('container', '').strip()
        filename = req_body.get('filename', '').strip()
        
        if not container_name or not filename:
            return func.HttpResponse(
                json.dumps({"error": "Both 'container' and 'filename' are required", "success": False}),
                status_code=400,
                mimetype="application/json"
            )
        
        logging.info(f"Processing blob deletion request: container='{container_name}', filename='{filename}'")

        # Get storage connection string from environment variables
        storage_connection_string = os.environ.get("KNIFE_STORAGE_CONNECTION_STRING")
        if not storage_connection_string:
            error_msg = "Missing required environment variable: KNIFE_STORAGE_CONNECTION_STRING"
            logging.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": error_msg, "success": False}),
                status_code=500,
                mimetype="application/json"
            )

        # Initialize Blob Service Client
        blob_service_client = BlobServiceClient.from_connection_string(storage_connection_string)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=filename)

        # Check if blob exists before attempting deletion
        try:
            blob_properties = blob_client.get_blob_properties()
            blob_exists = True
            logging.info(f"Blob '{filename}' found in container '{container_name}' (size: {blob_properties.size} bytes)")
        except Exception as e:
            if "BlobNotFound" in str(e) or "404" in str(e):
                blob_exists = False
                logging.info(f"Blob '{filename}' not found in container '{container_name}'")
            else:
                # Some other error occurred
                logging.error(f"Error checking blob existence: {e}")
                return func.HttpResponse(
                    json.dumps({"error": f"Error checking blob existence: {str(e)}", "success": False}),
                    status_code=500,
                    mimetype="application/json"
                )

        if blob_exists:
            # Delete the blob
            try:
                delete_result = blob_client.delete_blob()
                logging.info(f"Successfully deleted blob '{filename}' from container '{container_name}'")
                
                response_data = {
                    "success": True,
                    "message": f"Successfully deleted {filename} from {container_name}",
                    "container": container_name,
                    "filename": filename,
                    "was_deleted": True
                }
                
            except Exception as e:
                error_msg = f"Failed to delete blob '{filename}': {str(e)}"
                logging.error(error_msg)
                return func.HttpResponse(
                    json.dumps({"error": error_msg, "success": False}),
                    status_code=500,
                    mimetype="application/json"
                )
        else:
            # Blob doesn't exist, but this isn't necessarily an error
            response_data = {
                "success": True,
                "message": f"Blob {filename} was not found in {container_name} (may have been already deleted)",
                "container": container_name,
                "filename": filename,
                "was_deleted": False
            }
            logging.info(f"Blob '{filename}' was not found in container '{container_name}' - treating as successful (already deleted)")

        return func.HttpResponse(
            json.dumps(response_data, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        error_msg = f"Error during blob deletion: {str(e)}"
        logging.error(error_msg)
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        
        return func.HttpResponse(
            json.dumps({"error": error_msg, "success": False}),
            status_code=500,
            mimetype="application/json"
        )
