import logging
import os
import re
import io
import docx
from langchain.text_splitter import RecursiveCharacterTextSplitter

import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
import openai

def main(myblob: func.InputStream):
    logging.info(f"Python blob trigger function processed blob")
    logging.info(f"Name: {myblob.name}")
    logging.info(f"Size: {myblob.length} Bytes")

    # Extract ISO code from filename
    match = re.match(r"documents/([A-Z]{2})\.docx", myblob.name)
    if not match:
        logging.error(f"Invalid filename format: {myblob.name}. Expected 'documents/XX.docx' where XX is a 2-letter ISO code.")
        return
    iso_code = match.group(1)
    logging.info(f"Processing document for ISO code: {iso_code}")

    # Azure Cognitive Search settings
    search_endpoint = os.environ.get("KNIFE_SEARCH_ENDPOINT")
    search_key = os.environ.get("KNIFE_SEARCH_KEY")
    search_index_name = os.environ.get("KNIFE_SEARCH_INDEX")

    # Azure OpenAI settings
    openai_endpoint = os.environ.get("KNIFE_OPENAI_ENDPOINT")
    openai_key = os.environ.get("KNIFE_OPENAI_KEY")
    openai_embedding_deployment = os.environ.get("OPENAI_EMBED_DEPLOY")

    if not all([search_endpoint, search_key, search_index_name, openai_endpoint, openai_key, openai_embedding_deployment]):
        logging.error("Missing one or more required environment variables.")
        return

    # Initialize OpenAI client
    openai.api_type = "azure"
    openai.api_base = openai_endpoint
    openai.api_version = "2023-05-15"
    openai.api_key = openai_key

    # Initialize Search client
    search_credential = AzureKeyCredential(search_key)
    search_client = SearchClient(endpoint=search_endpoint, index_name=search_index_name, credential=search_credential)

    try:
        # Read the document from the blob stream
        blob_bytes = myblob.read()
        doc_stream = io.BytesIO(blob_bytes)
        document = docx.Document(doc_stream)
        full_text = "\n".join([para.text for para in document.paragraphs])
        logging.info("Successfully read .docx content.")

        # Split text into chunks
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks = text_splitter.split_text(full_text)
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
            response = openai.Embedding.create(
                input=chunk,
                engine=openai_embedding_deployment
            )
            embedding = response['data'][0]['embedding']
            
            doc = {
                "id": f"{iso_code}-{i}",
                "content": chunk,
                "embedding": embedding,
                "iso_code": iso_code,
                "source_file": f"{iso_code}.docx"
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
