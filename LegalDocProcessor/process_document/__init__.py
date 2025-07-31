import logging
import azure.functions as func

def main(myblob: func.InputStream):
    logging.info("=== DIAGNOSTIC FUNCTION STARTED ===")
    logging.info(f"Blob name: {myblob.name}")
    logging.info(f"Blob size: {myblob.length} bytes")
    
    # Test imports one by one
    try:
        logging.info("Testing import: os")
        import os
        logging.info("✅ os imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import os: {e}")
        return
    
    try:
        logging.info("Testing import: re")
        import re
        logging.info("✅ re imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import re: {e}")
        return
    
    try:
        logging.info("Testing import: io")
        import io
        logging.info("✅ io imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import io: {e}")
        return
    
    try:
        logging.info("Testing import: docx")
        import docx
        logging.info("✅ docx imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import docx: {e}")
        return
    
    try:
        logging.info("Testing import: lxml")
        import lxml
        logging.info("✅ lxml imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import lxml: {e}")
        return
    
    try:
        logging.info("Testing import: openai")
        import openai
        logging.info("✅ openai imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import openai: {e}")
        return
    
    try:
        logging.info("Testing import: langchain")
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        logging.info("✅ langchain imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import langchain: {e}")
        return
    
    try:
        logging.info("Testing import: azure.search.documents")
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential
        logging.info("✅ azure.search.documents imported successfully")
    except Exception as e:
        logging.error(f"❌ Failed to import azure.search.documents: {e}")
        return
    
    logging.info("🎉 ALL IMPORTS SUCCESSFUL!")
    logging.info("=== DIAGNOSTIC FUNCTION COMPLETED ===")