import logging
import os
import re
import json
import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

def main(eventGridEvent: func.EventGridEvent):
    """
    Azure Function triggered by Event Grid when a blob is deleted from the legaldocsrag container.
    Automatically removes corresponding documents from the Azure Cognitive Search index.
    
    This completes the business-admin workflow:
    - Upload/Replace: Automatic processing and index update
    - Delete: Automatic index cleanup (this function)
    """
    logging.info(f"Event Grid deletion trigger activated")
    
    # Parse Event Grid event
    event_data = eventGridEvent.get_json()
    logging.info(f"Event type: {eventGridEvent.event_type}")
    logging.info(f"Event data: {json.dumps(event_data, indent=2)}")
    
    # Only process blob deletion events
    if eventGridEvent.event_type != "Microsoft.Storage.BlobDeleted":
        logging.info(f"Ignoring event type: {eventGridEvent.event_type}")
        return
    
    # Extract blob URL from event data
    blob_url = event_data.get('url', '')
    logging.info(f"Deleted blob URL: {blob_url}")
    
    # Extract filename from URL
    filename = blob_url.split('/')[-1]
    logging.info(f"Deleted blob name: {filename}")

    # Extract ISO code from filename
    match = re.match(r"([A-Z]{2})\.docx", filename)
    if not match:
        logging.warning(f"Deleted file doesn't match expected format: {filename}. Expected 'XX.docx' where XX is a 2-letter ISO code.")
        return
    
    iso_code = match.group(1)
    logging.info(f"Processing deletion for ISO code: {iso_code}")

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
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return

    # Initialize Search client
    search_credential = AzureKeyCredential(search_key)
    search_client = SearchClient(endpoint=search_endpoint, index_name=search_index_name, credential=search_credential)

    try:
        # Search for existing documents with this ISO code
        logging.info(f"Searching for documents with iso_code: {iso_code}")
        results = search_client.search(search_text="*", filter=f"iso_code eq '{iso_code}'", select="id")
        docs_to_delete = [{"id": doc["id"]} for doc in results]

        if docs_to_delete:
            logging.info(f"Found {len(docs_to_delete)} documents to delete for ISO code {iso_code}")
            
            # Delete the documents from the search index
            delete_result = search_client.delete_documents(documents=docs_to_delete)
            
            # Check results
            successful_deletes = [res for res in delete_result if res.succeeded]
            failed_deletes = [res for res in delete_result if not res.succeeded]
            
            if successful_deletes:
                logging.info(f"Successfully deleted {len(successful_deletes)} documents for {iso_code}")
            
            if failed_deletes:
                logging.error(f"Failed to delete {len(failed_deletes)} documents for {iso_code}")
                for failed in failed_deletes:
                    logging.error(f"Failed deletion: {failed}")
            
            # Log final status
            if len(successful_deletes) == len(docs_to_delete):
                logging.info(f"✅ Complete cleanup: All documents for {iso_code} removed from search index")
            else:
                logging.warning(f"⚠️ Partial cleanup: {len(successful_deletes)}/{len(docs_to_delete)} documents deleted")
                
        else:
            logging.info(f"No documents found in search index for ISO code {iso_code} - nothing to clean up")

    except Exception as e:
        logging.error(f"Error during index cleanup for {iso_code}: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")

    logging.info(f"Blob deletion processing completed for {iso_code}")
