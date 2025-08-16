# Legal Docs Admin Upload Flow

## Overview
The Legal Docs Admin Upload provides a secure, streamlined interface for administrators to upload legal .docx files with automatic indexing and overwrite semantics.

## Key Features

### 1. Passcode-Gated Access
- **Admin Link**: Low-contrast "Admin" link at bottom-right of the main interface
- **Passcode Protection**: Requires `LEGAL_UPLOAD_PASSWORD` environment variable
- **Session Caching**: Passcode stored in session storage to avoid repeated prompts

### 2. Lean Upload Interface
- **Multi-file Selection**: Drag-and-drop or browse for multiple .docx files
- **Country Code Validation**: Enforces ISO 3166-1 alpha-2 format (e.g., CH.docx, FR.docx)
- **Overwrite Confirmation**: Clear warning about data replacement
- **Upload Status**: Real-time progress indicators (Uploading → Indexed)

### 3. Atomic Overwrite Semantics
- **Idempotent Operations**: Same file uploaded multiple times results in single entry
- **Deterministic IDs**: `doc_id` based on normalized content hash
- **No Duplicates**: Knife-index maintains single source of truth per document

## Technical Implementation

### Frontend Components
```javascript
// Key functions in Legal/index.html
- openAdminUpload()      // Opens modal, checks session passcode
- verifyPasscode()       // Validates and stores passcode
- processAdminFiles()    // Validates .docx files and country codes
- uploadFiles()          // Handles sequential upload with status updates
```

### Backend Endpoint
```
POST /api/upload_blob
Headers:
  - x-legal-admin-passcode: <passcode>
  - Content-Type: application/json
Body:
  {
    "filename": "CH.docx",
    "file_data": "<base64-encoded-content>"
  }
```

### Environment Variables
- `LEGAL_UPLOAD_PASSWORD`: Admin passcode for upload access

## Upload Workflow

1. **Access Admin Interface**
   - Click "Admin" link (bottom-right)
   - Enter passcode on first access

2. **Select Files**
   - Drag & drop or browse for .docx files
   - System validates 2-letter country codes
   - Shows list of selected files

3. **Confirm Upload**
   - Warning displayed about overwrite semantics
   - Note about ~15-minute propagation delay
   - Reminder that deletion requires IT ticket

4. **Upload Processing**
   - Files uploaded sequentially
   - Status shown per file:
     - 📄 CH.docx: 🟠 Uploading...
     - 📄 CH.docx: ✅ Indexed

5. **Index Update**
   - Content normalized (whitespace, formatting)
   - Hash generated for `doc_id`
   - Atomic upsert to knife-index
   - Previous version overwritten if exists

## Operational Notes

### File Naming Convention
- **Format**: `XX.docx` where XX is ISO country code
- **Examples**: CH.docx, FR.docx, DE.docx, US.docx
- **Case**: Insensitive (ch.docx = CH.docx)

### Overwrite Behavior
- Uploading CH.docx replaces any existing CH content
- No versioning - latest upload wins
- No manual index cleanup required
- Business admins manage files only, not indexes

### Propagation Delay
- Index updates may take up to 15 minutes
- Due to Azure Search refresh cycles
- Test queries after delay period

### Deletion Process
- **Current**: Requires IT ServiceNow ticket
- **Future**: May add admin deletion with audit trail
- **Rationale**: Prevents accidental data loss

## Testing

### Manual Test Checklist
1. ✅ Access admin link and enter passcode
2. ✅ Upload single .docx file
3. ✅ Upload multiple files at once
4. ✅ Re-upload same file (test overwrite)
5. ✅ Try invalid filename (e.g., test.docx)
6. ✅ Try non-.docx file
7. ✅ Verify session passcode persistence
8. ✅ Check upload status indicators

### Test Scripts
```bash
# Simple upload test
python simple_upload.py CH.docx --passcode YOUR_PASSCODE

# Batch upload test
python test_upload.py --passcode YOUR_PASSCODE
```

## Security Considerations

1. **Passcode Protection**: Admin-only access via shared secret
2. **Session Storage**: Passcode never in localStorage/cookies
3. **Server Validation**: Backend re-validates passcode
4. **No Public Access**: Upload endpoint returns 401 without valid passcode
5. **Audit Trail**: Consider adding upload logs with timestamps/users

## Future Enhancements

1. **User Management**: Replace passcode with Azure AD integration
2. **Audit Logging**: Track who uploaded what and when
3. **Batch Operations**: ZIP file support for bulk updates
4. **Version History**: Optional versioning with rollback
5. **Delete UI**: Admin deletion with confirmation and audit

## Troubleshooting

### Common Issues

1. **"Invalid passcode"**
   - Check `LEGAL_UPLOAD_PASSWORD` environment variable
   - Ensure no trailing spaces in passcode

2. **"Invalid filename"**
   - Must be 2-letter country code + .docx
   - Examples: CH.docx ✅, Switzerland.docx ❌

3. **Upload fails silently**
   - Check browser console for errors
   - Verify Function App is running
   - Check CORS settings if using external URL

4. **Changes not visible**
   - Wait 15 minutes for propagation
   - Check Azure Search index refresh status
   - Verify upload showed "✅ Indexed" status

## Architecture Benefits

This lean admin upload flow provides:
- **Simplicity**: Single action for document updates
- **Safety**: Overwrite warnings and passcode protection
- **Autonomy**: Legal team manages content without IT
- **Reliability**: Atomic operations prevent duplicates
- **Discoverability**: Unobtrusive but findable admin access

The design prioritizes business user needs while maintaining data integrity and security.
