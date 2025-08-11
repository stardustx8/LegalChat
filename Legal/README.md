# LegalChat — Legal RAG Assistant

This folder contains the production Static Web App (SWA) frontend for LegalChat, including the API function `Legal/api/ask` that serves the RAG pipeline.

The system provides:

- Jurisdiction-aware RAG answers over Azure Cognitive Search.
- On-demand Grade & Revise with evaluation metrics (precision/recall/F1), missing facts, unsupported claims, and coverage by jurisdiction.
- HTML-aware diff visualization between the model’s draft and refined answers.
- A unified document management UI (upload/replace/delete) so the legal team never needs Azure Portal access.

## Repository layout

- `Legal/` — Static frontend and SWA API.
  - `Legal/api/ask/` — Azure Functions (Python) HTTP endpoint `/api/ask`.
- `LegalDocProcessor/` — Azure Function App that processes document uploads and maintains the search index.
- `archive/` — Versioned code moved out of the live paths for safe review. Production builds ignore this folder.

## Architecture

- Frontend + API are deployed as an Azure Static Web App.
- Document ingestion is a separate Azure Function App (blob trigger for create/update; HTTP-trigger for cleanup on deletions).
- Vector store: Azure Cognitive Search with an `embedding` field used for vector search.
- LLM: Azure OpenAI deployments for chat and embeddings.

## Environment variables (SWA API)

Set these in the SWA environment configuration:

- `KNIFE_SEARCH_ENDPOINT`
- `KNIFE_SEARCH_KEY`
- `KNIFE_SEARCH_INDEX` (e.g., `knife-index`)
- `KNIFE_OPENAI_ENDPOINT`
- `KNIFE_OPENAI_KEY`
- `OPENAI_CHAT_DEPLOY` (e.g., `gpt-4.1`)
- `OPENAI_EMBED_DEPLOY` (e.g., `text-embedding-3-large`) 
- `OPENAI_API_VERSION` (e.g., `2024-02-15-preview`)

These names are read in `Legal/api/ask/__init__.py` when handling a request.

## API contract

- Endpoint: `GET/POST /api/ask`
- Query/body fields:
  - `question` (string, required)
  - `grade` (bool, optional) — when true, returns evaluation and refined answer
- Health check: `/api/ask?ping=1` → `200 ok`

Response JSON:

```json
{
  "country_header": "markdown-table",
  "refined_answer": "string (markdown)",
  "country_detection": {"iso_codes": [], "available": [], "summary": ""},
  "evaluation": { /* present only if grade=true */ },
  "draft_answer": "string (markdown)"
}
```

## Grading UI and diff visualization

- Uses `htmldiff-js` (loaded via ESM from `https://esm.sh/htmldiff-js@1.0.5`) to compute HTML-aware diffs between draft and refined answers.
- Insertions are wrapped in `<ins>`, deletions in `<del>`. Missing facts can be highlighted inside `<ins>` segments.
- DOMPurify is configured to allow `ins/del/mark` tags and relevant classes for safe rendering.
- Fallbacks: diff-match-patch text diff and a regex-based highlight when `<ins>` is absent.

## CI/CD

- SWA Workflow: `.github/workflows/azure-static-web-apps-witty-dune-0a317da03.yml`.
  - PRs produce preview environments; the preview URL appears in the workflow logs as “Visit your site at: …azurestaticapps.net”.
  - The “Close Pull Request” job may show “No matching static site found” when a preview wasn’t created; this is benign.
- The document processor has its own Function App workflow under `LegalDocProcessor/`.

## Local development

Because production is a Static Web App with a Python API function:

- You can test the API endpoint remotely with:
  ```bash
  curl -sS "https://<your-prod-or-preview-host>/api/ask?ping=1"
  ```
- For local iteration on the frontend, use any static server to serve `Legal/` and mock `/api/ask` if needed, or develop directly against the remote API.

## Operations runbook (high level)

- Upload/replace/delete documents using the in-app document management UI. Files must follow `<iso_code>.docx`.
- The ingestion Function App processes new/updated blobs and updates the search index.
- For deletions, use the HTTP-triggered cleanup function in the processor app to remove index entries if needed.

## Troubleshooting

- 404 on `/api/ask`: ensure SWA is deploying from `Legal/` with `api_location: Legal/api`.
- 400/401 from Azure Search: ensure the `select` fields and vector field name match the index schema (`embedding`).
- “No matching static site found” in a PR’s Close step: harmless; it only indicates no preview to clean up.

## Health checks

```bash
curl -sS "https://witty-dune-0a317da03.1.azurestaticapps.net/api/ask?ping=1" | head -n1
```

---

## Appendix A — Archive and Lock Operations

Use these commands to safely archive Azure resources and protect the archive resource group.

### Health check (quick)

```bash
curl -sS "https://witty-dune-0a317da03.1.azurestaticapps.net/api/ask?ping=1" | head -n1
# Or ask the RAG Assistant a question and verify a correct answer
```

### Protective lock on archive RG

```bash
# List locks
az lock list --resource-group rg-archive-LegalChat -o table

# Remove soft-hold lock (before moving more resources)
LOCK_ID=$(az lock list --resource-group rg-archive-LegalChat --query "[?name=='soft-hold'].id" -o tsv)
[ -n "$LOCK_ID" ] && az lock delete --ids "$LOCK_ID"

# Re-add lock (after moves)
az lock create --name soft-hold --lock-type CanNotDelete --resource-group rg-archive-LegalChat
```

### Helper: archive a single resource (tags + move)

```bash
archive() {
  NAME="$1"; TYPE="$2"
  RG=$(az resource list --resource-type "$TYPE" --query "[?name=='$NAME'].resourceGroup" -o tsv)
  if [ -z "$RG" ]; then echo "Skip: $NAME ($TYPE) not found"; return 0; fi
  RID=$(az resource show -g "$RG" -n "$NAME" --resource-type "$TYPE" --query id -o tsv)
  echo "Archiving: $RID"
  az resource tag --ids "$RID" --tags archived=true archivedOn=$(date +%F) reason="unused"
  az resource move --destination-group rg-archive-LegalChat --ids "$RID"
}
```

### Robust archive loops (handle spaces in resource IDs)

```bash
# Action Groups
while IFS= read -r RID; do
  [ -n "$RID" ] || continue
  echo "Archiving: $RID"
  az resource tag --ids "$RID" --tags archived=true archivedOn=$(date +%F) reason="unused"
  az resource move --destination-group rg-archive-LegalChat --ids "$RID"
done < <(az resource list --resource-type Microsoft.Insights/actionGroups --query "[].id" -o tsv)

# Metric Alerts
while IFS= read -r RID; do
  [ -n "$RID" ] || continue
  echo "Archiving: $RID"
  az resource tag --ids "$RID" --tags archived=true archivedOn=$(date +%F) reason="unused"
  az resource move --destination-group rg-archive-LegalChat --ids "$RID"
done < <(az resource list --resource-type Microsoft.Insights/metricAlerts --query "[].id" -o tsv)

# Scheduled Query Rules
while IFS= read -r RID; do
  [ -n "$RID" ] || continue
  echo "Archiving: $RID"
  az resource tag --ids "$RID" --tags archived=true archivedOn=$(date +%F) reason="unused"
  az resource move --destination-group rg-archive-LegalChat --ids "$RID"
done < <(az resource list --resource-type Microsoft.Insights/scheduledQueryRules --query "[].id" -o tsv)

# Smart Detector Alert Rules (if present)
while IFS= read -r RID; do
  [ -n "$RID" ] || continue
  echo "Archiving: $RID"
  az resource tag --ids "$RID" --tags archived=true archivedOn=$(date +%F) reason="unused"
  az resource move --destination-group rg-archive-LegalChat --ids "$RID"
done < <(az resource list --resource-type Microsoft.AlertsManagement/smartDetectorAlertRules --query "[].id" -o tsv)
```

### Example: scale Search to minimum before archive

```bash
SVC=kniferacdemosearch01
API=2023-11-01
SUB=$(az account show --query id -o tsv)
RG=$(az resource list --resource-type Microsoft.Search/searchServices \
     --query "[?name=='$SVC'].resourceGroup" -o tsv)

az rest --method patch \
  --url "https://management.azure.com/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.Search/searchServices/${SVC}?api-version=${API}" \
  --body '{"properties":{"replicaCount":1,"partitionCount":1}}' \
  --headers "Content-Type=application/json" 2>/dev/null || true

archive "$SVC" Microsoft.Search/searchServices
