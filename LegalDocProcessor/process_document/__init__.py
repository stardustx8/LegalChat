import logging
import azure.functions as func

def main(myblob: func.InputStream):
    try:
        logging.info("=== STEP 1: Function started ===")
        logging.info(f"Blob name: {myblob.name}")
        logging.info(f"Blob size: {myblob.length} bytes")
        
        logging.info("=== STEP 2: Testing imports ===")
        
        # Test basic imports one by one
        logging.info("Testing os import...")
        import os
        logging.info("os import: SUCCESS")
        
        logging.info("Testing re import...")
        import re
        logging.info("re import: SUCCESS")
        
        logging.info("Testing tempfile import...")
        import tempfile
        logging.info("tempfile import: SUCCESS")
        
        logging.info("Testing docx2txt import...")
        import docx2txt
        logging.info("docx2txt import: SUCCESS")
        
        logging.info("Testing simple text splitter (no langchain)...")
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
        logging.info("Simple text splitter: SUCCESS")
        
        logging.info("Testing azure.core import...")
        from azure.core.credentials import AzureKeyCredential
        logging.info("azure.core import: SUCCESS")
        
        logging.info("Testing azure.search import...")
        from azure.search.documents import SearchClient
        logging.info("azure.search import: SUCCESS")
        
        logging.info("Testing openai import...")
        import openai
        logging.info("openai import: SUCCESS")
        
        logging.info("=== STEP 3: Testing filename parsing ===")
        filename = myblob.name.split('/')[-1]
        logging.info(f"Extracted filename: {filename}")
        
        match = re.match(r"([A-Z]{2})\.docx", filename)
        if not match:
            logging.error(f"Invalid filename format: {filename}")
            return
        
        iso_code = match.group(1)
        logging.info(f"ISO code: {iso_code}")
        
        logging.info("=== STEP 4: Testing environment variables ===")
        env_vars = [
            "KNIFE_SEARCH_ENDPOINT",
            "KNIFE_SEARCH_KEY", 
            "KNIFE_SEARCH_INDEX",
            "KNIFE_OPENAI_ENDPOINT",
            "KNIFE_OPENAI_KEY",
            "KNIFE_OPENAI_DEPLOY"
        ]
        
        for var in env_vars:
            value = os.environ.get(var)
            if value:
                logging.info(f"ENV {var}: PRESENT (length: {len(value)})")
            else:
                logging.error(f"ENV {var}: MISSING")
                return
        
        logging.info("=== STEP 5: Testing blob read ===")
        blob_bytes = myblob.read()
        logging.info(f"Successfully read {len(blob_bytes)} bytes from blob")
        
        logging.info("=== STEP 6: Testing OpenAI client initialization ===")
        openai.api_type = "azure"
        openai.api_base = os.environ.get("KNIFE_OPENAI_ENDPOINT")
        openai.api_version = "2023-05-15"
        openai.api_key = os.environ.get("KNIFE_OPENAI_KEY")
        logging.info("OpenAI client initialized successfully")
        
        logging.info("=== STEP 7: Testing Search client initialization ===")
        search_credential = AzureKeyCredential(os.environ.get("KNIFE_SEARCH_KEY"))
        search_client = SearchClient(
            endpoint=os.environ.get("KNIFE_SEARCH_ENDPOINT"), 
            index_name=os.environ.get("KNIFE_SEARCH_INDEX"), 
            credential=search_credential
        )
        logging.info("Search client initialized successfully")
        
        logging.info("=== DIAGNOSTIC FUNCTION COMPLETED SUCCESSFULLY ===")
        
    except Exception as e:
        logging.error(f"DIAGNOSTIC ERROR at step: {e}")
        import traceback
        logging.error(f"DIAGNOSTIC TRACEBACK: {traceback.format_exc()}")
        raise  # Re-raise to ensure function fails and we can see the error