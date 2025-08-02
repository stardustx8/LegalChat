#!/usr/bin/env python3
"""
Clear all documents from Azure Cognitive Search index while preserving the index structure.
Uses the same environment variables as the RAG Assistant.
"""

import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def clear_search_index():
    """Clear all documents from the search index."""
    
    # Get configuration from environment variables
    search_endpoint = os.environ.get("KNIFE_SEARCH_ENDPOINT")
    search_key = os.environ.get("KNIFE_SEARCH_KEY")
    index_name = os.environ.get("KNIFE_SEARCH_INDEX", "knife-index")
    api_version = "2023-11-01"
    
    if not all([search_endpoint, search_key]):
        print("❌ Missing required environment variables:")
        print("   - KNIFE_SEARCH_ENDPOINT")
        print("   - KNIFE_SEARCH_KEY")
        return False
    
    print(f"🔍 Connecting to search service: {search_endpoint}")
    print(f"📋 Target index: {index_name}")
    
    # Step 1: Get all document IDs
    search_url = f"{search_endpoint}/indexes/{index_name}/docs/search"
    headers = {
        "Content-Type": "application/json",
        "api-key": search_key
    }
    
    search_body = {
        "search": "*",
        "select": "id",
        "top": 1000  # Adjust if you have more than 1000 documents
    }
    
    try:
        print("📥 Retrieving all document IDs...")
        response = requests.post(
            f"{search_url}?api-version={api_version}",
            headers=headers,
            json=search_body
        )
        response.raise_for_status()
        
        search_results = response.json()
        documents = search_results.get("value", [])
        
        if not documents:
            print("✅ Index is already empty!")
            return True
        
        print(f"📊 Found {len(documents)} documents to delete")
        
        # Step 2: Delete all documents
        delete_url = f"{search_endpoint}/indexes/{index_name}/docs/index"
        delete_actions = [
            {"@search.action": "delete", "id": doc["id"]}
            for doc in documents
        ]
        
        delete_body = {"value": delete_actions}
        
        print("🗑️  Deleting all documents...")
        delete_response = requests.post(
            f"{delete_url}?api-version={api_version}",
            headers=headers,
            json=delete_body
        )
        delete_response.raise_for_status()
        
        delete_results = delete_response.json()
        successful_deletes = sum(1 for result in delete_results.get("value", []) 
                               if result.get("status"))
        
        print(f"✅ Successfully deleted {successful_deletes} documents")
        print("🏗️  Index structure preserved - ready for new documents")
        
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error during index cleanup: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return False

if __name__ == "__main__":
    print("🧹 Azure Cognitive Search Index Cleanup Tool")
    print("=" * 50)
    
    confirmation = input("⚠️  This will delete ALL documents from the search index. Continue? (y/N): ")
    
    if confirmation.lower() in ['y', 'yes']:
        success = clear_search_index()
        if success:
            print("\n🎉 Index cleanup completed successfully!")
            print("💡 You can now upload new documents to rebuild the index")
        else:
            print("\n❌ Index cleanup failed. Check the error messages above.")
    else:
        print("🚫 Operation cancelled.")
