# Updated deployment token - testing connection

## Archive and Lock Operations

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
