import logging
import os
import re
import azure.functions as func

def main(myblob: func.InputStream):
    logging.info("=== DIAGNOSTIC FUNCTION TRIGGERED ===")
    logging.info(f"Blob name: {myblob.name}")
    logging.info(f"Blob size: {myblob.length} bytes")
    
    # Extract filename
    filename = myblob.name.split('/')[-1]
    logging.info(f"Extracted filename: {filename}")
    
    # Check ISO code format
    match = re.match(r"([A-Z]{2})\.docx", filename)
    if not match:
        logging.error(f"INVALID FILENAME FORMAT: {filename}. Expected 'XX.docx' where XX is a 2-letter ISO code.")
        return
    
    iso_code = match.group(1)
    logging.info(f"VALID ISO CODE DETECTED: {iso_code}")
    
    # Check environment variables
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
            logging.info(f"ENV VAR {var}: PRESENT (length: {len(value)})")
        else:
            logging.error(f"ENV VAR {var}: MISSING")
    
    # Read blob content
    try:
        blob_bytes = myblob.read()
        logging.info(f"Successfully read blob content: {len(blob_bytes)} bytes")
        
        # Try to read as text to see if it's actually a docx
        try:
            text_preview = blob_bytes[:100].decode('utf-8', errors='ignore')
            logging.info(f"Blob content preview: {text_preview}")
        except Exception as e:
            logging.info(f"Blob appears to be binary (expected for .docx): {e}")
            
    except Exception as e:
        logging.error(f"Failed to read blob content: {e}")
        return
    
    logging.info("=== DIAGNOSTIC FUNCTION COMPLETED SUCCESSFULLY ===")