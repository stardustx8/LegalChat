# Legal RAG Assistant — Setup, Environments, and Deployment

This repository contains a static frontend (`Legal/`) and an Azure Functions backend (`Legal/api/`) for a jurisdiction-aware Legal RAG assistant. The system uses Azure Cognitive Search for retrieval and Azure OpenAI for embeddings and chat.

- Frontend path: `Legal/`
- Backend path: `Legal/api/` (HTTP trigger at `/api/ask`)
- Health check: `GET /api/ask?ping=1` returns `pong` for warmup/cold-start mitigation

## Environment Variables
Set these for both the Azure Function App (App Settings) and local CLI testing. Defaults apply if not provided.

- Required
  - `KNIFE_SEARCH_ENDPOINT` — Azure Cognitive Search endpoint (e.g., https://<search>.search.windows.net)
  - `KNIFE_SEARCH_KEY` — Admin or query key
  - `KNIFE_OPENAI_ENDPOINT` — Azure OpenAI endpoint (e.g., https://<resource>.openai.azure.com)
  - `KNIFE_OPENAI_KEY` — Azure OpenAI API key
- Optional (with defaults)
  - `KNIFE_SEARCH_INDEX` — default: `knife-index`
  - `OPENAI_CHAT_DEPLOY` — default: `gpt-4.1`
  - `OPENAI_EMBED_DEPLOY` — default: `text-embedding-3-large`
  - `OPENAI_API_VERSION` — default: `2024-02-15-preview`

A minimal example is provided at `.env.example`. Copy it to `.env` and fill in your values for local testing.

## Local CLI Quickstart
The CLI uses the same environment variables as the Function App.

1) Copy env file and fill values

```
cp .env.example .env
# edit .env to set endpoints/keys
```

2) Export env and run

```
set -a; source .env; set +a
python3 CLI_only_query.py
```

If you see retrieval or field name mismatches, ensure your Azure Search index field names match the code. The backend expects vector field `embedding` and content field `chunk` in `Legal/api/ask/__init__.py`. The CLI currently references `vector` and `content`; adjust either your index or the CLI if needed.

## Azure Configuration

- Static Web App CI/CD workflow: `.github/workflows/azure-static-web-apps-witty-dune-0a317da03.yml`
  - `app_location: Legal`
  - `api_location: Legal/api`
  - `skip_app_build: true` (app already built)
  - GitHub secret required: `AZURE_STATIC_WEB_APPS_API_TOKEN_WITTY_DUNE_0A317DA03`

- Azure Function App (App Settings)
  - Set the environment variables listed above (required + optional).
  - For local Functions runtime, you can also use a `local.settings.json`:

```
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "KNIFE_SEARCH_ENDPOINT": "https://<search>.search.windows.net",
    "KNIFE_SEARCH_KEY": "<key>",
    "KNIFE_SEARCH_INDEX": "knife-index",
    "KNIFE_OPENAI_ENDPOINT": "https://<resource>.openai.azure.com",
    "KNIFE_OPENAI_KEY": "<key>",
    "OPENAI_CHAT_DEPLOY": "gpt-4.1",
    "OPENAI_EMBED_DEPLOY": "text-embedding-3-large",
    "OPENAI_API_VERSION": "2024-02-15-preview"
  }
}
```

## Health Check and Warmup
- Backend health check: `GET /api/ask?ping=1` returns `pong` immediately.
- Frontend triggers a warmup ping on page load and retries the first answer call once after a warmup if it fails.
- Optional: After deploy, run a ping to pre-warm the Function:

```
curl -sS "https://<your-swa-domain>/api/ask?ping=1"
```

> Tip: You can add a small post-deploy job to ping the API URL in CI after the `Azure/static-web-apps-deploy` step.

## Repository Housekeeping
Active backend file: `Legal/api/ask/__init__.py`.

Legacy/duplicates found:
- `Legal/api/ask/__init__.py.backup`
- `Legal/api/ask/__init__.py.production`

These are not used by Azure Functions. To avoid confusion, consider deleting or archiving them. I can move them to `Legal/api/ask/archive/` or remove them completely on request.

## Azure Resource Management
See `docs/AZURE_RESOURCE_MANAGEMENT.md` for a resource inventory, cost levers, safe cleanup, and rollback guidance.
