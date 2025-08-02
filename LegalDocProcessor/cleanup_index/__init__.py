import logging
import os
import re
import json
import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP-triggered Azure Function to manually clean up search index for deleted documents.
    
    Usage:
    POST /api/cleanup_index
    Body: {"iso_code": "FR"} or {"iso_code": "all"}
    
    This provides a reliable alternative to Event Grid for index cleanup.
    """
    logging.info('HTTP trigger function for index cleanup started')

    try:
        # Parse request body
        req_body = req.get_json()
        if not req_body:
            return func.HttpResponse(
                json.dumps({"error": "Request body is required"}),
                status_code=400,
                mimetype="application/json"
            )
        
        iso_code = req_body.get('iso_code', '').upper()
        if not iso_code:
            return func.HttpResponse(
                json.dumps({"error": "iso_code is required"}),
                status_code=400,
                mimetype="application/json"
            )
        
        logging.info(f"Processing cleanup request for ISO code: {iso_code}")

        # Azure Cognitive Search settings from environment variables
        search_endpoint = os.environ.get("KNIFE_SEARCH_ENDPOINT")
        search_key = os.environ.get("KNIFE_SEARCH_KEY")
        search_index_name = os.environ.get("KNIFE_SEARCH_INDEX")

        # Check required environment variables
        env_vars = {
            "KNIFE_SEARCH_ENDPOINT": search_endpoint,
            "KNIFE_SEARCH_KEY": search_key,
            "KNIFE_SEARCH_INDEX": search_index_name
        }
        missing_vars = [key for key, value in env_vars.items() if not value]
        if missing_vars:
            error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
            logging.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": error_msg}),
                status_code=500,
                mimetype="application/json"
            )

        # Initialize Search client
        search_credential = AzureKeyCredential(search_key)
        search_client = SearchClient(endpoint=search_endpoint, index_name=search_index_name, credential=search_credential)

        # Handle cleanup
        if iso_code == "ALL":
            # Clean up all documents (admin function)
            logging.info("Processing cleanup for ALL documents")
            results = search_client.search(search_text="*", select="id,iso_code")
            docs_to_delete = [{"id": doc["id"]} for doc in results]
            cleanup_type = "all documents"
        else:
            # Validate ISO code format
            if not re.match(r"^[A-Z]{2}$", iso_code):
                return func.HttpResponse(
                    json.dumps({"error": "iso_code must be a 2-letter country code (e.g., 'FR', 'DE')"}),
                    status_code=400,
                    mimetype="application/json"
                )
            
            # Clean up specific country
            logging.info(f"Searching for documents with iso_code: {iso_code}")
            results = search_client.search(search_text="*", filter=f"iso_code eq '{iso_code}'", select="id")
            docs_to_delete = [{"id": doc["id"]} for doc in results]
            cleanup_type = f"documents for {iso_code}"

        if docs_to_delete:
            logging.info(f"Found {len(docs_to_delete)} documents to delete")
            
            # Delete the documents from the search index
            delete_result = search_client.delete_documents(documents=docs_to_delete)
            
            # Check results
            successful_deletes = [res for res in delete_result if res.succeeded]
            failed_deletes = [res for res in delete_result if not res.succeeded]
            
            if successful_deletes:
                logging.info(f"Successfully deleted {len(successful_deletes)} documents")
            
            if failed_deletes:
                logging.error(f"Failed to delete {len(failed_deletes)} documents")
                for failed in failed_deletes:
                    logging.error(f"Failed deletion: {failed}")
            
            # Prepare response
            response_data = {
                "success": True,
                "message": f"Cleaned up {cleanup_type}",
                "deleted_count": len(successful_deletes),
                "failed_count": len(failed_deletes),
                "iso_code": iso_code
            }
            
            if len(successful_deletes) == len(docs_to_delete):
                logging.info(f"✅ Complete cleanup: All {cleanup_type} removed from search index")
            else:
                logging.warning(f"⚠️ Partial cleanup: {len(successful_deletes)}/{len(docs_to_delete)} documents deleted")
                response_data["warning"] = "Some documents failed to delete"
                
        else:
            logging.info(f"No documents found for cleanup: {cleanup_type}")
            response_data = {
                "success": True,
                "message": f"No {cleanup_type} found to clean up",
                "deleted_count": 0,
                "failed_count": 0,
                "iso_code": iso_code
            }

        return func.HttpResponse(
            json.dumps(response_data, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        error_msg = f"Error during index cleanup: {str(e)}"
        logging.error(error_msg)
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        
        return func.HttpResponse(
            json.dumps({"error": error_msg, "success": False}),
            status_code=500,
            mimetype="application/json"
        )
