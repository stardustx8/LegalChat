import logging
import os
import azure.functions as func

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("=== HTTP DIAGNOSTIC FUNCTION TRIGGERED ===")
    
    # Check environment variables
    env_vars = [
        "KNIFE_SEARCH_ENDPOINT",
        "KNIFE_SEARCH_KEY", 
        "KNIFE_SEARCH_INDEX",
        "KNIFE_OPENAI_ENDPOINT",
        "KNIFE_OPENAI_KEY",
        "KNIFE_OPENAI_DEPLOY",
        "KNIFE_STORAGE_CONNECTION_STRING"
    ]
    
    env_status = {}
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            env_status[var] = f"PRESENT (length: {len(value)})"
            logging.info(f"ENV VAR {var}: PRESENT (length: {len(value)})")
        else:
            env_status[var] = "MISSING"
            logging.error(f"ENV VAR {var}: MISSING")
    
    # Test basic imports
    try:
        import docx2txt
        import_status = "docx2txt: SUCCESS"
        logging.info("Import test - docx2txt: SUCCESS")
    except Exception as e:
        import_status = f"docx2txt: FAILED - {e}"
        logging.error(f"Import test - docx2txt: FAILED - {e}")
    
    logging.info("=== HTTP DIAGNOSTIC FUNCTION COMPLETED ===")
    
    return func.HttpResponse(
        f"HTTP Diagnostic Function Results:\n\nEnvironment Variables:\n" + 
        "\n".join([f"{k}: {v}" for k, v in env_status.items()]) +
        f"\n\nImport Test:\n{import_status}\n\nCheck logs for detailed information.",
        status_code=200
    )
