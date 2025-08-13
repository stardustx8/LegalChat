# Images and Tables RAG Design (LegalChat)

Author: Cascade
Status: Draft for review
Last updated: 2025-08-13

## Executive Summary
The current LegalChat pipeline loses information contained in DOCX images and table structures. This design introduces ingestion, indexing, retrieval, and prompting improvements to make image and table content first-class citizens while controlling latency and cost. It proposes a hybrid "parent/child" index schema and an optional "late chunking" mode that expands larger parent sections into fine-grained children at query-time when appropriate. The plan stays Azure-native (Azure OpenAI, Azure Cognitive Search, Azure Functions, Azure Blob Storage) and requires only additive changes.

## Scope and Goals
- Preserve and exploit table structure from DOCX (headers, merges, captions) for retrieval and answering.
- Extract images, generate reliable captions/alt-text, and OCR text in images when needed; make them retrievable and usable in answers.
- Introduce parent/child indexing and an optional late-chunking retrieval path for high-precision queries (e.g., row/column lookups, figure-specific questions).
- Keep costs low via ingest-time processing, caching, and strict token/latency guardrails.

Out of scope (initially): non-DOCX formats beyond existing coverage; UI revamps beyond small additions for citations.

## Current System (abridged)
- Frontend: `Legal/` (Azure Static Web App). Backend Ask API: `Legal/api/ask/__init__.py`.
- Indexing: `LegalDocProcessor/` Azure Functions; current DOCX parsing primarily extracts plain text.
- Infra: Azure OpenAI (chat: `gpt-4.1`, embed: `text-embedding-3-large`), Azure Cognitive Search index (e.g., `knife-index`), Azure Blob Storage for documents.

## Gaps
- Tables are flattened to text; header/row/column semantics and merges are lost.
- Images are ignored; any embedded text or non-text semantics are not surfaced.
- Retrieval cannot target specific table rows/columns or figure/image-derived facts.

## Lessons from OldRAG (to be finalized after code recon)
Source: `OldRAG/rag_app.py` (Streamlit/Chroma)
- Image handling: Extract inline images from DOCX, generate captions and/or OCR text; attach to chunk metadata.
- Table handling: Convert tables to LLM-friendly Markdown, preserve headers and merged cells; optionally add a JSON normalization for programmatic querying.
- Chunking/ranking: Prefer table/image chunks for table- or figure-intent queries.

Action: After we review `OldRAG/rag_app.py`, this section will be updated with exact code pointers and deltas vs. LegalChat.

## Target Architecture

### Ingestion (Azure Functions: `LegalDocProcessor/process_document/`)
1) Parse DOCX
   - Use `python-docx` to walk the document structure.
   - Produce logical blocks:
     - Text blocks (paragraphs, lists, headings)
     - Table blocks (Markdown + JSON normalization)
     - Image blocks (Blob URL, captions, OCR text if needed)
   - Capture metadata: `doc_id`, `section_id`, `parent_id`, `page_or_section`, `position`, `figure_id`/`table_id`, captions, source offsets.

2) Tables
   - Markdown: preserve headers, merges, and captions; include source line/section anchors.
   - JSON: normalized representation (headers array, rows[], merged cell spans) to support deterministic summarization and potential programmatic QA.
   - Summaries: optional short summaries for very large tables (size thresholds, e.g., > N rows).

3) Images
   - Extract binaries; upload to Blob at `doc-artifacts/images/{doc_id}/{figure_id}.ext`.
   - Generate caption/alt-text using Azure OpenAI `gpt-4.1` with image input (or fallback caption templates).
   - OCR (optional): if image likely contains text or a scanned table, use Azure Document Intelligence or Azure AI Vision OCR; persist text.
   - Store references and cache captions/OCR results to avoid rework on reindex.

4) Embeddings
   - Compute embeddings for appropriate text fields (text, table_md, image_caption, image_text) using `text-embedding-3-large`.
   - Optionally compute a parent-level vector for the entire section to support late chunking.

### Storage / Index (Azure Cognitive Search)
- Hybrid schema with parent/child:
  - Parent document: section-level content (coarser), fields include `id`, `doc_id`, `section_id`, `parent_id = id`, `section_text`, `section_vector`, `page_or_section`, timestamps.
  - Child document: fine-grained chunk (text/table/image), fields include:
    - `id`, `parent_id`, `doc_id`, `chunk_type` ∈ {text, table, image}
    - `chunk_text` (for text), `table_md`, `table_json_uri` (Blob), `image_caption`, `image_text`
    - `page_or_section`, `figure_id`/`table_id`, `position`
    - `chunk_vector`
  - Both parent and child indexed and retrievable. Define filters and vector configs accordingly.

- Suggested fields (illustrative):
  - Common: `id` (key), `doc_id`, `parent_id`, `chunk_type`, `page_or_section`, `position`, `source_path`, `created_at`, `content_hash`.
  - Text: `section_text`, `chunk_text`, `section_vector`, `chunk_vector`.
  - Table: `table_md`, `table_json_uri`, `table_title/caption`, `table_columns` (searchable), `table_preview` (short summary).
  - Image: `image_caption`, `image_text`, `image_blob_uri`, `figure_id`.

### Retrieval Orchestration (Ask API)
- Query understanding: detect table/figure intent via lightweight heuristics or a small classifier (e.g., presence of "table", "row", "column", "figure", "image", "chart").
- Routing:
  - General queries → standard hybrid search across `chunk_text` and `section_text`.
  - Table intent → prioritize `table_md`, `table_columns`, and `chunk_type == 'table'`.
  - Image intent → prioritize `image_caption` + `image_text` and `chunk_type == 'image'`.

- Late chunking (feature-flagged):
  - Mode `RAG_CHUNKING_MODE = early | late | hybrid`.
  - Late mode: retrieve parent sections first using `section_vector`/keywords, then expand to top-K children (fine-grained chunks) under those parents; cap total tokens; cache expansions using a composite key (query, parent_ids, version).
  - Hybrid: run a small K over both parents and children, merge and rerank.
  - Guardrails: `LATE_CHUNK_CHILDREN_K`, `LATE_CHUNK_MAX_TOKENS`, timeouts, retries with backoff.

### Answering / Prompting
- For table-grounded answers:
  - Include the specific table Markdown and a brief table summary.
  - Prompt snippet: "Use the provided table(s) to answer. If aggregations are needed, keep them simple and show the line(s) used. Cite the table id and section."
- For image-grounded answers:
  - Include caption and OCR text; if the image is non-textual (e.g., chart), encourage cautious language and cite figure id + section.
- Citations: always include `parent_id` and `figure_id`/`table_id` where applicable.

## Late Chunking: Evaluation and Recommendation
- When it wins:
  - Queries requiring narrow rows/columns from large tables.
  - Queries targeting specific figures or image-derived text.
  - Documents with sparse signals where coarse chunks dilute relevance.
- Tradeoffs:
  - Increases query-time tokens and latency; mitigated by small K expansion, strict token caps, and caching.
  - Slightly more complex orchestration and caching logic.
- Initial recommendation:
  - Default to `hybrid` with conservative K and token caps.
  - Fall back to early-only on timeout or when hybrid yields negligible lift.

## Performance and Cost Controls
- Ingest-time heavy lifting (captioning/normalization) to minimize per-query costs.
- Batching for embeddings and Azure OpenAI requests; retries with exponential backoff.
- Size thresholds for table normalization and summarization.
- Cache captions/OCR results; hash-based dedupe of chunks.
- Strict token budgets per query and per answer; short-circuit on limits.

## Risks and Mitigations
- OCR quality on low-res images → prefer native DOCX structure; use OCR only when necessary; flag low-confidence results.
- Token blow-up from large tables → summarize at ingest; enforce caps; provide citations for user drill-down.
- Latency under late chunking → caching and tight K; auto-fallback to early-only.
- Schema migration complexity → versioned index and reindex script; blue/green cutover.

## Rollout Plan
1) Stage behind feature flags and environment variables; ship ingestion first.
2) Migrate index with parent/child schema; reindex a subset; verify KPIs.
3) Enable hybrid retrieval on a subset of traffic; monitor P50/P95 latency, grounding, and accuracy.
4) Gradually ramp; keep rollback to early-only one toggle away.

## File-by-File Changes (no code yet)
- `LegalDocProcessor/process_document/__init__.py`
  - Add DOCX parser pipeline emitting text/table/image blocks with metadata.
  - Upload images to Blob; generate captions; optional OCR for text-in-image.
  - Emit parent (section) and child (chunk) entities for indexing.
- `LegalDocProcessor/requirements.txt`
  - Add `python-docx`, `Pillow`, optional `azure-ai-documentintelligence`/`azure-ai-vision`, `tenacity` for retries.
- `scripts/reindex_images_tables.py`
  - One-off script to rebuild the index with new schema; safe progress and resume.
- `Legal/api/ask/__init__.py`
  - Retrieval changes: intent detection, type-aware reranking, optional parent→child expansion, token caps, caching.
  - Prompt assembly for table/image grounding with citations.
- `search/index_schema.json` (new)
  - Azure Cognitive Search schema (parents + children) with vector configs and searchable fields for table/image.
- `docs/ImagesAndTables-RAG-Design.md` (this file)
- `docs/experiments/LateChunking-AB-Plan.md` (new)
  - A/B design, toggles, metrics, and analysis checklist.

## Azure Resources and App Settings
- Blob container `doc-artifacts/` with SAS policy; folders: `images/`, `tables/`.
- Optional: Azure Document Intelligence for OCR/table reading when DOCX embeds scans.
- App settings to add (SWA + Function App):
  - `RAG_CHUNKING_MODE` = `early|late|hybrid`
  - `LATE_CHUNK_CHILDREN_K` (e.g., 3–8)
  - `LATE_CHUNK_MAX_TOKENS` (e.g., 2000–4000)
  - `OCR_ENABLED` (bool)
  - `CAPTION_CACHE_TTL` (hours)

## Test Plan
- Corpus: DOCX with multi-page tables (merged cells, captions), images, and scanned inserts.
- Queries:
  - Row/column-specific table queries (“What is the limit in row X, column Y?”).
  - Figure-specific queries (“What does Figure 2 show about …?”).
  - Mixed text + table and pure text queries for baseline.
- Metrics:
  - Grounding accuracy (table/image references present and correct), citation precision, latency P50/P95, token usage, and cost.
- Success criteria:
  - >= X% improvement on table/figure queries vs. baseline; latency within budget; cost acceptable.

## Research Queue (to be finalized)
- Populate 5–10 recent (2024–2025) sources covering table RAG, parent/child indexing in Azure Cognitive Search, late chunking patterns, Unstructured.io DOCX partitioning, Azure Vision/Document Intelligence, and image captioning with Azure OpenAI. Each source will include a link and a one-line takeaway.

---

Appendix A: Open Questions for Stakeholders
1) Are scanned PDFs converted to DOCX in our pipeline, and how often do we encounter images of tables vs. native tables?
2) Latency budget for query P95? Token budget per request/response?
3) Storage constraints for Blob artifacts and retention policy for derived assets (captions/JSON)?
