# Repository Cleanup Plan

## Deletion Table

| Path | Kind | Used-by Signals | Impact (0-5) | Why safe/unsafe | Deletion command | Restore command | Pre-delete dry-run | Post-delete check |
|------|------|-----------------|--------------|-----------------|------------------|-----------------|--------------------|--------------------|
| `__pycache__/` | Cache | None | 0 | Python bytecode cache, auto-regenerated | `find . -type d -name __pycache__ -exec rm -rf {} +` | N/A (auto-regenerated) | `find . -type d -name __pycache__ -print` | `python3 -c "import test_upload"` |
| `Legal/api/ask/__pycache__/` | Cache | None | 0 | Python bytecode cache | `rm -rf Legal/api/ask/__pycache__` | N/A | `ls -la Legal/api/ask/__pycache__` | `python3 -c "import sys; sys.path.insert(0,'Legal/api'); import ask"` |
| `*.pyc` files | Cache | None | 0 | Python bytecode, auto-regenerated | `find . -name "*.pyc" -delete` | N/A | `find . -name "*.pyc"` | `python3 -m py_compile test_upload.py` |
| `.venv-index/` | Dev-only | Local dev environment | 1 | Virtual env, not needed for deploy | `rm -rf .venv-index` | `python3 -m venv .venv-index` | `ls -la .venv-index` | `test -d .venv-index && echo "exists"` |
| `LegalDocProcessor/.python_packages/` | Deploy artifact | Empty, unused | 1 | Empty directory, no runtime impact | `rmdir LegalDocProcessor/.python_packages` | `mkdir LegalDocProcessor/.python_packages` | `ls -la LegalDocProcessor/.python_packages` | `test -d LegalDocProcessor/.python_packages` |
| `function.zip` | Build artifact | Referenced in CI | 2 | CI artifact, can regenerate | `rm -f function.zip` | `git checkout -- function.zip` | `ls -la function.zip` | `test -f function.zip` |
| `archive/` | Backup | Historical versions | 3 | Contains old versions, may want to keep | `mv archive .archive_backup_$(date +%Y%m%d)` | `mv .archive_backup_* archive` | `du -sh archive` | `test -d archive` |
| `cl0.json` | Data file | Unknown usage | 2 | Appears to be test data | `mv cl0.json .backup/` | `mv .backup/cl0.json .` | `cat cl0.json | head -5` | `test -f cl0.json` |
| `current_index.json` | Data file | Unknown usage | 2 | Appears to be index state | `mv current_index.json .backup/` | `mv .backup/current_index.json .` | `cat current_index.json | head -5` | `test -f current_index.json` |
| `payload.json` | Test data | Test payload | 1 | Test file | `mv payload.json .backup/` | `mv .backup/payload.json .` | `cat payload.json | head -5` | `test -f payload.json` |
| `AE_test.docx` | Test file | Testing | 1 | Test document | `mv AE_test.docx .backup/` | `mv .backup/AE_test.docx .` | `ls -la AE_test.docx` | `test -f AE_test.docx` |

## Classification Summary
- **Runtime**: Core application files (Legal/, LegalDocProcessor/)
- **Build**: CI/CD files (.github/, requirements.txt)
- **Test**: test_upload.py, simple_upload.py, test data files
- **Cache/Artifact**: __pycache__, *.pyc, function.zip
- **Dev-only**: .venv-index
- **Dead**: .python_packages (empty)
