import azure.functions as func
import json
import logging
import os
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from collections import defaultdict

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Document status function processed a request.')
    
    try:
        # Get environment variables
        search_endpoint = os.environ.get('KNIFE_SEARCH_ENDPOINT')
        search_key = os.environ.get('KNIFE_SEARCH_KEY')
        search_index = os.environ.get('KNIFE_SEARCH_INDEX')
        storage_connection = os.environ.get('KNIFE_STORAGE_CONNECTION_STRING')
        
        if not all([search_endpoint, search_key, search_index, storage_connection]):
            return func.HttpResponse(
                json.dumps({"success": False, "message": "Missing required environment variables"}),
                status_code=500,
                mimetype="application/json"
            )
        
        # Initialize search client
        search_client = SearchClient(
            endpoint=search_endpoint,
            index_name=search_index,
            credential=AzureKeyCredential(search_key)
        )
        
        # Initialize blob service client
        blob_service_client = BlobServiceClient.from_connection_string(storage_connection)
        container_client = blob_service_client.get_container_client('legaldocsrag')
        
        # Get all documents from search index
        search_results = search_client.search(
            search_text="*",
            select=["iso_code", "id"],
            top=1000
        )
        
        # Count documents per country
        country_docs = defaultdict(int)
        for result in search_results:
            iso_code = result.get('iso_code', '').upper()
            if iso_code:
                country_docs[iso_code] += 1
        
        # Get all blobs from storage
        storage_files = {}
        try:
            blobs = container_client.list_blobs()
            for blob in blobs:
                if blob.name.endswith('.docx') and len(blob.name) == 7:
                    iso_code = blob.name[:2].upper()
                    storage_files[iso_code] = {
                        'filename': blob.name,
                        'size': blob.size,
                        'last_modified': blob.last_modified.isoformat() if blob.last_modified else None
                    }
        except Exception as e:
            logging.warning(f"Could not list storage blobs: {str(e)}")
        
        # Define all European countries
        all_countries = {
            'AD': 'Andorra', 'AL': 'Albania', 'AT': 'Austria', 'BA': 'Bosnia and Herzegovina',
            'BE': 'Belgium', 'BG': 'Bulgaria', 'BY': 'Belarus', 'CH': 'Switzerland',
            'CY': 'Cyprus', 'CZ': 'Czech Republic', 'DE': 'Germany', 'DK': 'Denmark',
            'EE': 'Estonia', 'ES': 'Spain', 'FI': 'Finland', 'FR': 'France',
            'GB': 'United Kingdom', 'GR': 'Greece', 'HR': 'Croatia', 'HU': 'Hungary',
            'IE': 'Ireland', 'IS': 'Iceland', 'IT': 'Italy', 'LI': 'Liechtenstein',
            'LT': 'Lithuania', 'LU': 'Luxembourg', 'LV': 'Latvia', 'MC': 'Monaco',
            'MD': 'Moldova', 'ME': 'Montenegro', 'MK': 'North Macedonia', 'MT': 'Malta',
            'NL': 'Netherlands', 'NO': 'Norway', 'PL': 'Poland', 'PT': 'Portugal',
            'RO': 'Romania', 'RS': 'Serbia', 'RU': 'Russia', 'SE': 'Sweden',
            'SI': 'Slovenia', 'SK': 'Slovakia', 'SM': 'San Marino', 'UA': 'Ukraine',
            'VA': 'Vatican City', 'XK': 'Kosovo'
        }
        
        # Build status for all countries
        document_status = []
        for iso_code, country_name in all_countries.items():
            chunk_count = country_docs.get(iso_code, 0)
            file_info = storage_files.get(iso_code)
            
            status = {
                'iso_code': iso_code,
                'country_name': country_name,
                'has_document': chunk_count > 0,
                'chunk_count': chunk_count,
                'has_file': file_info is not None,
                'file_info': file_info
            }
            
            # Determine overall status
            if chunk_count > 0 and file_info:
                status['status'] = 'available'
                status['status_text'] = f'Available ({chunk_count} chunks)'
            elif chunk_count > 0 and not file_info:
                status['status'] = 'indexed_only'
                status['status_text'] = f'Indexed only ({chunk_count} chunks)'
            elif not chunk_count and file_info:
                status['status'] = 'processing'
                status['status_text'] = 'Processing...'
            else:
                status['status'] = 'missing'
                status['status_text'] = 'Missing'
            
            document_status.append(status)
        
        # Sort by country name
        document_status.sort(key=lambda x: x['country_name'])
        
        return func.HttpResponse(
            json.dumps({
                "success": True,
                "document_status": document_status,
                "total_countries": len(document_status),
                "available_count": len([d for d in document_status if d['status'] == 'available']),
                "missing_count": len([d for d in document_status if d['status'] == 'missing'])
            }),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logging.error(f"Error getting document status: {str(e)}")
        return func.HttpResponse(
            json.dumps({"success": False, "message": f"Status check failed: {str(e)}"}),
            status_code=500,
            mimetype="application/json"
        )
