#!/bin/bash
# Safe cleanup script for LegalChat repository
# Usage: ./safe_clean.sh [--dry-run|--apply]

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
BACKUP_DIR="$REPO_ROOT/.backup_$(date +%Y%m%d_%H%M%S)"
MODE="${1:---dry-run}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

if [[ "$MODE" != "--dry-run" && "$MODE" != "--apply" ]]; then
    log_error "Usage: $0 [--dry-run|--apply]"
    exit 1
fi

cd "$REPO_ROOT"

if [[ "$MODE" == "--dry-run" ]]; then
    log_info "DRY RUN MODE - No files will be deleted"
else
    log_warn "APPLY MODE - Files will be deleted/moved"
    mkdir -p "$BACKUP_DIR"
    log_info "Backup directory: $BACKUP_DIR"
fi

# 1. Clean Python cache files (Impact: 0)
log_info "Finding Python cache files..."
PYCACHE_DIRS=$(find . -type d -name __pycache__ 2>/dev/null | grep -v .venv || true)
PYC_FILES=$(find . -name "*.pyc" 2>/dev/null | grep -v .venv || true)

if [[ -n "$PYCACHE_DIRS" ]]; then
    echo "$PYCACHE_DIRS" | while read -r dir; do
        if [[ "$MODE" == "--apply" ]]; then
            rm -rf "$dir"
            log_info "Deleted: $dir"
        else
            log_info "Would delete: $dir"
        fi
    done
fi

if [[ -n "$PYC_FILES" ]]; then
    echo "$PYC_FILES" | while read -r file; do
        if [[ "$MODE" == "--apply" ]]; then
            rm -f "$file"
            log_info "Deleted: $file"
        else
            log_info "Would delete: $file"
        fi
    done
fi

# 2. Clean empty directories (Impact: 1)
if [[ -d "LegalDocProcessor/.python_packages" ]]; then
    if [[ "$MODE" == "--apply" ]]; then
        rmdir "LegalDocProcessor/.python_packages" 2>/dev/null || log_warn "Could not remove .python_packages (not empty?)"
    else
        log_info "Would remove empty dir: LegalDocProcessor/.python_packages"
    fi
fi

# 3. Move test/temp files to backup (Impact: 1-2)
TEST_FILES=("AE_test.docx" "payload.json" "cl0.json" "current_index.json")
for file in "${TEST_FILES[@]}"; do
    if [[ -f "$file" ]]; then
        if [[ "$MODE" == "--apply" ]]; then
            mv "$file" "$BACKUP_DIR/"
            log_info "Moved to backup: $file"
        else
            log_info "Would move to backup: $file"
        fi
    fi
done

# 4. Handle function.zip (Impact: 2)
if [[ -f "function.zip" ]]; then
    if [[ "$MODE" == "--apply" ]]; then
        if [[ "${SAFE_TRASH:-0}" == "1" ]]; then
            mv "function.zip" "$BACKUP_DIR/"
            log_info "Moved to backup: function.zip"
        else
            log_warn "Keeping function.zip (CI artifact)"
        fi
    else
        log_info "Would handle: function.zip (set SAFE_TRASH=1 to move)"
    fi
fi

# 5. Archive folder (Impact: 3)
if [[ -d "archive" ]]; then
    if [[ "$MODE" == "--apply" ]]; then
        if [[ "${ARCHIVE_BACKUP:-0}" == "1" ]]; then
            mv "archive" "$BACKUP_DIR/"
            log_info "Moved archive/ to backup"
        else
            log_warn "Keeping archive/ (historical versions)"
        fi
    else
        log_info "Would handle: archive/ (set ARCHIVE_BACKUP=1 to move)"
    fi
fi

# Summary
if [[ "$MODE" == "--apply" ]]; then
    log_info "Cleanup complete. Backup at: $BACKUP_DIR"
    if [[ -d "$BACKUP_DIR" ]] && [[ -z "$(ls -A "$BACKUP_DIR")" ]]; then
        rmdir "$BACKUP_DIR"
        log_info "Backup dir was empty, removed"
    fi
else
    log_info "Dry run complete. Run with --apply to execute."
fi

# Post-cleanup verification
log_info "Running post-cleanup checks..."
python3 -c "import sys; print('Python import test: OK')" || log_error "Python import failed"
test -d "Legal" && log_info "Legal/ directory: OK" || log_error "Legal/ missing"
test -d "LegalDocProcessor" && log_info "LegalDocProcessor/ directory: OK" || log_error "LegalDocProcessor/ missing"
