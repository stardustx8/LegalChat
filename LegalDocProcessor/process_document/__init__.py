import logging
import os
import re
import tempfile
import docx2txt
import json
import requests

import azure.functions as func
# Trigger workflow test - both frontend and backend operational
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

# Simple text splitter function to avoid langchain dependency
def simple_text_splitter(text, chunk_size=1000, chunk_overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Find the last space within the chunk to avoid breaking words
            while end > start and text[end] != ' ':
                end -= 1
            if end == start:  # No space found, use original end
                end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - chunk_overlap if end > chunk_overlap else end
    return chunks

def main(myblob: func.InputStream):
    logging.info(f"Python blob trigger function processed blob")
    logging.info(f"Name: {myblob.name}")
    logging.info(f"Size: {myblob.length} Bytes")

    # Extract ISO code from filename
    filename = myblob.name.split('/')[-1]
    match = re.match(r"([A-Z]{2})\.docx", filename)
    if not match:
        logging.error(f"Invalid filename format: {filename}. Expected 'XX.docx' where XX is a 2-letter ISO code.")
        return
    iso_code = match.group(1)
    logging.info(f"Processing document for ISO code: {iso_code}")

    # Azure Cognitive Search and OpenAI settings from environment variables
    search_endpoint = os.environ.get("KNIFE_SEARCH_ENDPOINT")
    search_key = os.environ.get("KNIFE_SEARCH_KEY")
    search_index_name = os.environ.get("KNIFE_SEARCH_INDEX")
    openai_endpoint = os.environ.get("KNIFE_OPENAI_ENDPOINT")
    openai_key = os.environ.get("KNIFE_OPENAI_KEY")
    openai_embedding_deployment = os.environ.get("KNIFE_OPENAI_DEPLOY")

    # Check environment variables and log missing
    env_vars = {
        "KNIFE_SEARCH_ENDPOINT": search_endpoint,
        "KNIFE_SEARCH_KEY": search_key,
        "KNIFE_SEARCH_INDEX": search_index_name,
        "KNIFE_OPENAI_ENDPOINT": openai_endpoint,
        "KNIFE_OPENAI_KEY": openai_key,
        "KNIFE_OPENAI_DEPLOY": openai_embedding_deployment
    }
    missing_vars = [key for key, value in env_vars.items() if not value]
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return

    # Prepare OpenAI REST API headers and endpoint
    openai_headers = {
        "Content-Type": "application/json",
        "api-key": openai_key
    }
    openai_url = f"{openai_endpoint}/openai/deployments/{openai_embedding_deployment}/embeddings?api-version=2023-05-15"

    # Initialize Search client
    search_credential = AzureKeyCredential(search_key)
    search_client = SearchClient(endpoint=search_endpoint, index_name=search_index_name, credential=search_credential)

    try:
        # Read the document from the blob stream using docx2txt
        blob_bytes = myblob.read()
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as temp_file:
            temp_file.write(blob_bytes)
            temp_file_path = temp_file.name
        full_text = docx2txt.process(temp_file_path)
        os.unlink(temp_file_path)
        logging.info("Successfully extracted .docx content using docx2txt.")

        # Split text into chunks using pure Python text splitter
        chunks = simple_text_splitter(full_text, chunk_size=1000, chunk_overlap=200)
        logging.info(f"Split text into {len(chunks)} chunks.")

        # Delete existing documents for this ISO code
        logging.info(f"Searching for existing documents with iso_code: {iso_code}")
        results = search_client.search(search_text="*", filter=f"iso_code eq '{iso_code}'", select="id")
        docs_to_delete = [{"id": doc["id"]} for doc in results]

        if docs_to_delete:
            logging.info(f"Found {len(docs_to_delete)} documents to delete.")
            delete_result = search_client.delete_documents(documents=docs_to_delete)
            if all([res.succeeded for res in delete_result]):
                logging.info("Successfully deleted old documents.")
            else:
                logging.error(f"Failed to delete some documents: {delete_result}")
        else:
            logging.info("No existing documents found for this ISO code.")

        # Generate embeddings and prepare documents for upload
        docs_to_upload = []
        for i, chunk in enumerate(chunks):
            # Make direct REST API call to Azure OpenAI
            payload = {
                "input": chunk
            }
            response = requests.post(openai_url, headers=openai_headers, json=payload)
            response.raise_for_status()
            embedding_data = response.json()
            embedding = embedding_data['data'][0]['embedding']
            
            doc = {
                "id": f"{iso_code}-{i}",
                "chunk": chunk,
                "embedding": embedding,
                "iso_code": iso_code
            }
            docs_to_upload.append(doc)

        if docs_to_upload:
            logging.info(f"Uploading {len(docs_to_upload)} new documents to the search index.")
            upload_result = search_client.upload_documents(documents=docs_to_upload)
            if all([res.succeeded for res in upload_result]):
                logging.info("Successfully uploaded new documents.")
            else:
                logging.error(f"Failed to upload some documents: {upload_result}")
        else:
            logging.warning("No documents to upload.")

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")