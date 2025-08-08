# Azure Resource Management — Legal RAG Assistant

This guide documents the Azure resources used by the Legal RAG Assistant, cost levers, safe cleanup/rollback, and operational best practices.

## Resource Inventory
- Static Web App (SWA)
  - Hosts frontend (`Legal/`) and integrates Azure Functions (`Legal/api/`)
  - CI/CD via `.github/workflows/azure-static-web-apps-witty-dune-0a317da03.yml`
- Azure Functions (Python)
  - HTTP-trigger at `/api/ask`
  - Health check: `GET /api/ask?ping=1` returns `pong` (for warmup)
- Azure Cognitive Search
  - Index name default: `knife-index`
  - Requires vector field (backend expects `embedding`) and content field (`chunk`)
- Azure OpenAI
  - Chat model deployment (default `gpt-4.1`)
  - Embedding model deployment (default `text-embedding-3-large`)
- Azure Storage (documents)
  - Holds uploaded legal documents (by ISO alpha-2 filenames)
  - Upload/replace/delete is the only admin action required; indexing pipelines update automatically
- Optional: Application Insights/Log Analytics (for Functions/SWA monitoring)

## Environment Variables and Secrets
Required everywhere (Function App, local dev, CI secrets as needed):
- `KNIFE_SEARCH_ENDPOINT` — https://<search>.search.windows.net
- `KNIFE_SEARCH_KEY` — search key (admin or query key for reads)
- `KNIFE_OPENAI_ENDPOINT` — https://<resource>.openai.azure.com
- `KNIFE_OPENAI_KEY` — OpenAI key

Optional (defaults):
- `KNIFE_SEARCH_INDEX` — default `knife-index`
- `OPENAI_CHAT_DEPLOY` — default `gpt-4.1`
- `OPENAI_EMBED_DEPLOY` — default `text-embedding-3-large`
- `OPENAI_API_VERSION` — default `2024-02-15-preview`

CI secrets:
- `AZURE_STATIC_WEB_APPS_API_TOKEN_WITTY_DUNE_0A317DA03` — SWA deployment token
- Optional `SWA_WARMUP_URL` — full URL to `/api/ask?ping=1` for post-deploy warmup

Local dev:
- Use `.env` (see `.env.example`). For Functions local runtime, you may use `local.settings.json` (example in README).

## Cost Levers and Best Practices
- Azure Cognitive Search
  - Choose appropriate SKU; reduce replicas/partitions when traffic is low
  - Minimize index size by chunking and pruning unused fields
  - Use caching where applicable; avoid unnecessary re-indexing
- Azure OpenAI
  - Prefer smaller/cheaper deployments for embedding
  - Use deterministic settings where possible (temperature 0)
  - Consider shorter prompts/contexts to reduce tokens
- Azure Functions
  - Consumption plan is cost-effective; mitigate cold starts via warmup ping
  - Optional: schedule a cron (e.g., GitHub Actions) to ping periodically during business hours
- Storage
  - Use LRS (locally redundant) unless GRS is required
  - Add lifecycle rules to move old blobs to Cool/Archive if appropriate

## Safe Cleanup (De-provision Unused Resources)
1) Inventory
   - Identify the active Resource Group(s) and list resources (SWA, Function App, Search, OpenAI, Storage, Insights)
2) Back up
   - Export Search index schema
   - Snapshot Storage container (or copy to a safe location)
   - Save Function code (repo already contains current source)
3) Validate dependencies
   - Make sure no other apps depend on the target resources
4) De-provision
   - Delete unused duplicate SWA or Functions instances first (least data risk)
   - Delete old Search services only after exporting schema and verifying new service works
   - Delete unused OpenAI deployments/resources only if a replacement is confirmed

## Rollback Strategies
- Code rollback
  - Use git to revert to a known good commit
  - Backend is in `Legal/api/ask/__init__.py`; avoid using legacy backup files in production
- Infra rollback
  - Keep ARM/Bicep templates or documented portal settings; re-create from exported schemas
  - Maintain Search index schema export for quick re-provisioning
- Secrets rollback
  - Maintain a secure record of previous working keys/endpoints (rotate keys as needed)

## Operational Notes
- Health check endpoint is available: `/api/ask?ping=1`; use it for manual or automated warmup
- Frontend already performs a warmup ping on load and retries the first answer request once after a warmup if needed
- Document management is designed for business admins: upload/replace/delete in Storage auto-updates the index

## Field Name Alignment
- Backend expects:
  - Vector field: `embedding`
  - Content field: `chunk`
- Ensure your Azure Search index matches these names or adjust the code accordingly. The CLI has been aligned to the backend for consistency.
