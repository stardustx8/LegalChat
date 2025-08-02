import logging, os, re, tempfile, json, itertools
from pathlib import Path

import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
import docx2txt, requests

# ---------------------------------------------------------------------------
# 1.  TEXT‑PREPARATION UTILITIES
# ---------------------------------------------------------------------------

_SENT_END = re.compile(r"[.!?]\s+")
_BULLET = re.compile(r"^\s*(?:\*|-|•|\d+[.)])\s+", re.M)

def split_legal_text(text: str,
                     chunk_size: int = 750,
                     overlap: int = 100) -> list[str]:
    """
    Heuristically preserves legal structure while keeping chunks short enough
    for the embedding model.  The algorithm prefers to cut on double line
    breaks, then on list markers, then on sentence ends.
    """
    start, n = 0, len(text)
    chunks: list[str] = []

    while start < n:
        raw = text[start:start + chunk_size + 200]       # +200 look‑ahead
        if len(raw) <= chunk_size:
            chunks.append(raw.strip())
            break

        # candidate cutpoints, scored by desirability
        candidates = {
    raw.rfind("\n\n", 0, chunk_size): 3,
    max((m.end() for m in _BULLET.finditer(raw[:chunk_size])), default=-1): 2,
    max((m.end() for m in _SENT_END.finditer(raw[:chunk_size])), default=-1): 1,
    raw.rfind(" ", 0, chunk_size): 0
}
        cut = max((pos for pos in candidates if pos > 0),
                  key=lambda p: candidates[p])

        chunk = raw[:cut].strip()
        chunks.append(chunk)
        start += max(cut - overlap, 1)

    return [c for c in chunks if c]                      # drop empties


# ---------------------------------------------------------------------------
# 2.  OPENAI HELPER
# ---------------------------------------------------------------------------

def embed_batch(texts, openai_url, headers, batch=16):
    """Batched embeddings with graceful degradation."""
    for i in range(0, len(texts), batch):
        payload = {"input": texts[i:i + batch]}
        resp = requests.post(openai_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        datas = resp.json()["data"]
        for rec in datas:
            yield rec["embedding"]


# ---------------------------------------------------------------------------
# 3.  FUNCTION ENTRY‑POINT
# ---------------------------------------------------------------------------

def main(myblob: func.InputStream):
    log = logging.getLogger("knife‑ingest")
    log.info("Processing blob %s (%d B)", myblob.name, myblob.length)

    m = re.match(r"([A-Z]{2})\.docx$", Path(myblob.name).name)
    if not m:
        log.error("Filename must be two‑letter ISO code, got %s", myblob.name)
        return
    iso = m.group(1)

    # --- environment ---
    try:
        cfg = {
            "search_endpoint": os.environ["KNIFE_SEARCH_ENDPOINT"],
            "search_key":      os.environ["KNIFE_SEARCH_KEY"],
            "index_name":      os.environ["KNIFE_SEARCH_INDEX"],
            "openai_endpoint": os.environ["KNIFE_OPENAI_ENDPOINT"],
            "openai_key":      os.environ["KNIFE_OPENAI_KEY"],
            "openai_deploy":   os.environ["KNIFE_OPENAI_DEPLOY"]
        }
    except KeyError as k:
        log.error("Missing env var: %s", k.args[0]);  return

    openai_url = (f"{cfg['openai_endpoint']}/openai/deployments/"
                  f"{cfg['openai_deploy']}/embeddings?api-version=2023-05-15")
    headers = {"api-key": cfg["openai_key"], "Content-Type": "application/json"}

    # --- extract plain text ---
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(myblob.read())
        tmp.flush()
        full_text = docx2txt.process(tmp.name)
    Path(tmp.name).unlink(missing_ok=True)

    if not full_text.strip():
        log.warning("No text extracted from %s", myblob.name);  return

    # --- chunk & embed ---
    chunks = split_legal_text(full_text)
    log.info("Split into %d chunks", len(chunks))

    embeddings = list(embed_batch(chunks, openai_url, headers))
    assert len(embeddings) == len(chunks)

    # --- prepare docs ---
    docs = [{
        "id": f"{iso}-{i}",
        "iso_code": iso,
        "chunk_index": i,
        "content": chunk,
        "vector": vec
    } for i, (chunk, vec) in enumerate(zip(chunks, embeddings))]

    # --- upsert into search ---
    search = SearchClient(cfg["search_endpoint"],
                          cfg["index_name"],
                          AzureKeyCredential(cfg["search_key"]))

    # wipe old docs for fresh rebuild
    old = [{"id": d["id"]} for d in
           search.search("*", filter=f"iso_code eq '{iso}'", select="id")]
    if old:
        search.delete_documents(old)

    res = search.upload_documents(docs)
    failures = [r for r in res if not r.succeeded]
    if failures:
        log.error("Failed to upload %d/%d docs", len(failures), len(docs))
    else:
        log.info("Ingestion complete: %d docs for %s", len(docs), iso)