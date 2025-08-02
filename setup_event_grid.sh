#!/bin/bash

# Setup Event Grid subscription for blob deletion detection
# This connects storage account deletions to the delete_document Azure Function

echo "🔗 Setting up Event Grid subscription for blob deletion detection"
echo "=============================================================="

# Configuration variables
SUBSCRIPTION_ID="your-subscription-id"  # Replace with your subscription ID
RESOURCE_GROUP="rg-legalchat"
STORAGE_ACCOUNT="legaldocsrag"
FUNCTION_APP="legaldocs-processor"
FUNCTION_NAME="delete_document"

echo "📋 Configuration:"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Storage Account: $STORAGE_ACCOUNT"
echo "  Function App: $FUNCTION_APP"
echo "  Function: $FUNCTION_NAME"
echo ""

# Get current subscription ID if not set
if [ "$SUBSCRIPTION_ID" = "your-subscription-id" ]; then
    echo "🔍 Getting current subscription ID..."
    SUBSCRIPTION_ID=$(az account show --query id --output tsv)
    echo "  Found: $SUBSCRIPTION_ID"
fi

# Build resource IDs
STORAGE_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT"
FUNCTION_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Web/sites/$FUNCTION_APP/functions/$FUNCTION_NAME"

echo ""
echo "🚀 Creating Event Grid subscription..."

# Create Event Grid subscription
az eventgrid event-subscription create \
  --name "blob-deletion-trigger" \
  --source-resource-id "$STORAGE_RESOURCE_ID" \
  --endpoint-type "azurefunction" \
  --endpoint "$FUNCTION_RESOURCE_ID" \
  --included-event-types "Microsoft.Storage.BlobDeleted" \
  --subject-begins-with "/blobServices/default/containers/legaldocsrag/" \
  --subject-ends-with ".docx" \
  --output table

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Event Grid subscription created successfully!"
    echo ""
    echo "🎯 What happens now:"
    echo "  1. Delete any .docx file from 'legaldocsrag' container"
    echo "  2. Event Grid sends deletion event to delete_document function"
    echo "  3. Function automatically removes documents from search index"
    echo ""
    echo "📊 To verify setup:"
    echo "  az eventgrid event-subscription list --source-resource-id '$STORAGE_RESOURCE_ID' --output table"
else
    echo ""
    echo "❌ Failed to create Event Grid subscription"
    echo "💡 Try running this manually in Azure Portal:"
    echo "  1. Go to Storage Account '$STORAGE_ACCOUNT'"
    echo "  2. Navigate to Events → Event subscriptions"
    echo "  3. Create new subscription with these settings:"
    echo "     - Event Types: Microsoft.Storage.BlobDeleted"
    echo "     - Endpoint: Azure Function → $FUNCTION_APP → $FUNCTION_NAME"
    echo "     - Filters: Subject begins with '/blobServices/default/containers/legaldocsrag/'"
    echo "     - Filters: Subject ends with '.docx'"
fi
