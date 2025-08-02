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
    """Enhanced text splitter that preserves legal document structure.
    
    Prioritizes breaking at:
    1. Double newlines (paragraph breaks)
    2. Bullet points and numbered lists
    3. Attachment boundaries
    4. Sentence endings
    5. Word boundaries (fallback)
    """
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        if end >= len(text):
            # Last chunk
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break
            
        # Find the best break point within the chunk
        best_break = find_best_break_point(text, start, end)
        
        chunk = text[start:best_break].strip()
        if chunk:
            chunks.append(chunk)
            
        # Calculate next start with overlap, but avoid splitting mid-sentence
        next_start = max(start + 1, best_break - chunk_overlap)
        
        # Adjust start to avoid breaking words or bullet points
        while next_start < best_break and next_start < len(text):
            if text[next_start] in ' \n\t' or text[next_start:next_start+2] in ['• ', '- ', '* ']:
                break
            if next_start > 0 and text[next_start-1:next_start+1] in ['. ', '! ', '? ']:
                break
            next_start += 1
            
        start = next_start
        
    return chunks


def find_best_break_point(text, start, max_end):
    """Find the best point to break text while preserving legal document structure."""
    # Look for break points in order of preference
    search_start = max(start + 500, max_end - 300)  # Don't break too early
    
    # 1. Double newlines (paragraph breaks) - highest priority
    for i in range(max_end - 1, search_start - 1, -1):
        if text[i:i+2] == '\n\n':
            return i + 2
    
    # 2. Attachment or section boundaries
    for i in range(max_end - 10, search_start - 1, -1):
        if any(pattern in text[i:i+20].lower() for pattern in 
               ['attachment', 'section', 'article', 'chapter', 'annex']):
            # Find the end of this line
            line_end = text.find('\n', i)
            if line_end != -1 and line_end <= max_end:
                return line_end + 1
    
    # 3. Bullet points or numbered lists
    for i in range(max_end - 1, search_start - 1, -1):
        if text[i] == '\n' and i + 1 < len(text):
            next_chars = text[i+1:i+4]
            if (next_chars.startswith(('• ', '- ', '* ')) or 
                (len(next_chars) >= 2 and next_chars[0].isdigit() and next_chars[1] in '. )')):
                return i + 1
    
    # 4. Sentence endings
    for i in range(max_end - 1, search_start - 1, -1):
        if text[i] in '.!?' and i + 1 < len(text) and text[i+1] == ' ':
            return i + 1
    
    # 5. Word boundaries (fallback)
    for i in range(max_end - 1, search_start - 1, -1):
        if text[i] == ' ':
            return i + 1
    
    # 6. Last resort - use max_end
    return max_end

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
        text = docx2txt.process(temp_file_path)
        os.unlink(temp_file_path)
        if not text.strip():
            logging.warning(f"No text extracted from {filename}")
            return

        # Split into chunks with legal-aware strategy
        logging.info(f"Splitting document into chunks (length: {len(text)} chars)")
        chunks = simple_text_splitter(text)
        logging.info(f"Created {len(chunks)} chunks with enhanced legal structure preservation")

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
        failed_chunks = 0
        
        for i, chunk in enumerate(chunks):
            try:
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
                    "iso_code": iso_code,
                    "chunk_index": i  # For pinpoint citations
                }
                docs_to_upload.append(doc)
                
            except Exception as e:
                failed_chunks += 1
                logging.error(f"Failed to generate embedding for chunk {i}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logging.error(f"Response status: {e.response.status_code}")
                    logging.error(f"Response content: {e.response.text}")
                continue  # Skip this chunk and continue with the next one
        
        if failed_chunks > 0:
            logging.warning(f"Failed to process {failed_chunks} out of {len(chunks)} chunks")
        
        logging.info(f"Successfully prepared {len(docs_to_upload)} documents for upload")

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