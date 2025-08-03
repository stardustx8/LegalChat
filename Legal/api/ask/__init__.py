import logging
import os, json, requests, re, time
import azure.functions as func
from openai import AzureOpenAI

# --- Prompts and Helper Functions ---
# Note: Environment variables are loaded within the main() function to prevent module-level errors.

COUNTRY_DETECTION_PROMPT = """
ROLE
You are a specialized assistant whose sole task is to extract country references from user text.

SCOPE OF EXTRACTION
Return **every** genuine country reference that can be inferred, using the rules below:

1.  ISO 3166-1 ALPHA-2 CODES
    •  Detect any two-letter, UPPER-CASE sequence in the input text that is a valid ISO 3166-1 alpha-2 country code (e.g., CH, US, CN, DE).
    •  Crucially, ignore common short words (typically 2-3 letters), especially if lowercase, that might incidentally resemble country codes OR that you might mistakenly associate with a country. This includes articles, prepositions, pronouns, and other grammatical particles in any language (e.g., English "in", "on", "it", "is", "at", "to", "do", "am", "pm", "id", "tv", "an", "of", "or"; German "ich", "er", "sie", "es", "der", "die", "das", "ein", "mit", "auf", "in", "zu", "so", "ob"). Such words should ONLY be considered if they are unambiguously used as a direct country reference AND appear in uppercase as a specific ISO code.
    •  Context must strongly support that the sequence is a country indicator, not an accidental substring or a common word.

2.  COUNTRY NAMES (any language)
    •  Official and common names, case-insensitive: “Switzerland”, “switzerland”.
    •  Major international variants: “Deutschland”, “Schweiz”, “Suiza”, “Éire”, …
    •  Adjectival forms that clearly point to a country: “Swiss law”, “German regulations”.

3.  TRANSNATIONAL ENTITIES, GEOPOLITICAL GROUPINGS & WELL-KNOWN NICKNAMES
    Your goal is to identify entities that represent a group of countries.
    - For the explicitly listed examples below, you MUST expand them to ALL their constituent ISO codes as specified. For each constituent country, create a separate JSON entry using the original detected entity/nickname as the "detected_phrase".
        - "EuroAirport" (also "Basel-Mulhouse-Freiburg"): output CH, FR
        - "Benelux": output BE, NL, LU
        - "The Nordics" (context-dependent): output DK, NO, SE, FI, IS
        - "Iberian Peninsula" (also "Iberische Halbinsel"): output ES, PT
        - "Baltics" (also "Baltische Staaten"): output EE, LV, LT
        - "Scandinavia" (also "Skandinavien"): output DK, NO, SE
    - For other similar transnational entities, intergovernmental organizations (e.g., EFTA, ASEAN, Mercosur), or well-known geopolitical groupings not explicitly listed, if you can confidently identify them and their constituent member countries, you SHOULD also expand them in the same way. If you are not confident about the members of such an unlisted group, do not extract it.

    •  When such an entity is processed, output *all* its known constituent countries.
    •  Do **not** substitute “EU” (the European Union itself is not a country for this purpose, though its member states are if individually referenced).

4.  CONTEXTUAL RULES
    •  Prepositions or articles (“in Switzerland”) never block detection of the country name itself.
    •  Mixed lists are fine: “switzerland, Deutschland & CN”.
    •  Ambiguous or purely figurative uses → **skip**. Err on the side of precision. Only extract if you are highly confident it's a geographical reference.

FORMATTING RULES
•  Output a JSON array exactly in this form:

    ```json
    [
      {"detected_phrase": "<exact text>", "code": "XX"},
      …
    ]
    ```

•  Preserve the original casing from the input text in "detected_phrase".
•  The "detected_phrase" itself: if its length is 4 characters or less, it MUST be a valid ISO 3166-1 alpha-2 code AND it MUST have appeared in ALL UPPERCASE in the original user text. For example, if the user types "us", do not extract it; if the user types "US", extract it as {"detected_phrase": "US", "code": "US"}. Common lowercase words like "in", "it", "am", "is", "to", "der", "mit" (even if their uppercase versions are valid ISO codes like "IN", "IT", "AM", "IS", "TO", "DE") must not be extracted if they appeared lowercase in the input and are being used as common words.
•  If nothing is found, return `[]`.
"""

def embed(text: str, client: AzureOpenAI, deploy_embed: str) -> list[float]:
    """Generates embeddings for a given text using a specific deployment."""
    return client.embeddings.create(
        input=[text],
        model=deploy_embed
    ).data[0].embedding

def balance_country_representation(results: list[dict], iso_codes: list[str], target_k: int) -> list[dict]:
    """Ensures balanced representation from all detected countries in search results.
    
    For multi-country queries (e.g., EuroAirport -> CH, FR), this function ensures
    that documents from all countries are included in the final results, not just
    the most semantically similar ones which might all come from one country.
    
    Args:
        results: Raw search results from Azure Cognitive Search
        iso_codes: List of detected ISO country codes
        target_k: Target number of documents to return
    
    Returns:
        Balanced list of documents with representation from all countries
    """
    if not results or len(iso_codes) <= 1:
        return results[:target_k]
    
    # Group results by country
    by_country = {}
    for result in results:
        country = result.get('iso_code', 'UNKNOWN')
        if country not in by_country:
            by_country[country] = []
        by_country[country].append(result)
    
    # Calculate how many documents to take from each country
    available_countries = [code for code in iso_codes if code in by_country]
    if not available_countries:
        return results[:target_k]
    
    docs_per_country = max(1, target_k // len(available_countries))
    remainder = target_k % len(available_countries)
    
    balanced_results = []
    
    # Take documents from each country
    for i, country in enumerate(available_countries):
        country_docs = by_country[country]
        # Give extra documents to first few countries if there's a remainder
        take_count = docs_per_country + (1 if i < remainder else 0)
        balanced_results.extend(country_docs[:take_count])
    
    # If we still need more documents and some countries have extras, add them
    if len(balanced_results) < target_k:
        for country in available_countries:
            if len(balanced_results) >= target_k:
                break
            remaining_docs = by_country[country][docs_per_country:]
            needed = target_k - len(balanced_results)
            balanced_results.extend(remaining_docs[:needed])
    
    return balanced_results[:target_k]

def extract_iso_codes(text: str, client: AzureOpenAI, deploy_chat: str) -> list[str]:
    """Extracts ISO-3166-1 alpha-2 country codes from text using an LLM call."""
    try:
        response = client.chat.completions.create(
            model=deploy_chat,
            messages=[
                {"role": "system", "content": COUNTRY_DETECTION_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0.0,
        )
        raw_content = response.choices[0].message.content.strip()
        cleaned_content = re.sub(r'^```json\s*|\s*```$', '', raw_content)
        data = json.loads(cleaned_content)
        if not isinstance(data, list):
            return []

        used_codes = set()
        results = []
        for item in data:
            if isinstance(item, dict):
                code = item.get("code")
                if code and code not in used_codes:
                    results.append(code)
                    used_codes.add(code)
        return results

    except (json.JSONDecodeError, IndexError, AttributeError) as e:
        logging.error(f"Error parsing country detection response: {e}")
        return []

def retrieve(query: str, iso_codes: list[str], client: AzureOpenAI, config: dict, k: int = 5) -> list[dict]:
    """Retrieves documents from Azure Cognitive Search based on a vector query and filters.
    
    For multi-country queries (e.g., EuroAirport), ensures balanced representation
    from all detected countries rather than just the most semantically similar documents.
    """
    logging.info(f"DEBUG: retrieve() called with query='{query}', iso_codes={iso_codes}")
    
    if not iso_codes:
        logging.info("DEBUG: No ISO codes provided, returning empty list")
        return []
    
    try:
        logging.info("DEBUG: Step 2 - Generating embedding for query...")
        step2_start = time.time()
        vec = embed(query, client, config['deploy_embed'])
        step2_time = time.time() - step2_start
        logging.info(f"DEBUG: Embedding generated successfully in {step2_time:.2f}s, length={len(vec)}")
    except Exception as e:
        logging.error(f"DEBUG: Failed to generate embedding: {e}")
        raise
    
    search_url = f"{config['search_endpoint']}/indexes/{config['index_name']}/docs/search?api-version=2023-11-01"
    headers = {'Content-Type': 'application/json', 'api-key': config['search_key']}
    filter_str = f"search.in(iso_code, '{','.join(iso_codes)}', ',')"
    
    # For multi-country queries, increase k to ensure we get documents from all countries
    search_k = max(k * len(iso_codes), 10) if len(iso_codes) > 1 else k
    
    payload = {
        "vectorQueries": [
            {
                "kind": "vector",
                "vector": vec,
                "fields": "embedding",
                "k": search_k
            }
        ],
        "filter": filter_str,
        "select": "chunk,iso_code,id"
    }
    
    logging.info(f"DEBUG: Sending search request to {search_url}")
    logging.info(f"DEBUG: Filter: {filter_str}")
    logging.info(f"DEBUG: Search k adjusted from {k} to {search_k} for {len(iso_codes)} countries")
    logging.info(f"DEBUG: Payload keys: {list(payload.keys())}")
    
    try:
        step3_start = time.time()
        logging.info("DEBUG: Step 3 - Performing vector search")
        response = requests.post(search_url, headers=headers, json=payload)
        step3_time = time.time() - step3_start
        logging.info(f"DEBUG: Search response status: {response.status_code}")
        response.raise_for_status()
        raw_results = response.json().get('value', [])
        logging.info(f"DEBUG: Vector search completed, found {len(raw_results)} results in {step3_time:.2f}s")
        
        # For multi-country queries, ensure balanced representation
        if len(iso_codes) > 1 and raw_results:
            balanced_results = balance_country_representation(raw_results, iso_codes, k)
            logging.info(f"DEBUG: Balanced results: {len(balanced_results)} documents from {len(set(r['iso_code'] for r in balanced_results))} countries")
            return balanced_results
        else:
            return raw_results[:k]  # Limit to original k for single-country queries
            
    except requests.exceptions.RequestException as e:
        logging.error(f"DEBUG: Search request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"DEBUG: Response content: {e.response.text}")
        raise
    except Exception as e:
        logging.error(f"DEBUG: Unexpected error in retrieve: {e}")
        raise

def iso_to_flag(iso_code: str) -> str:
    """Converts a two-letter ISO country code to a flag emoji."""
    if not isinstance(iso_code, str) or len(iso_code) != 2:
        return ""
    return "".join(chr(ord(char.upper()) - ord('A') + 0x1F1E6) for char in iso_code)

def build_response_header(iso_codes: list[str], found_iso_codes: set[str]) -> str:
    """Builds a Markdown table header to display detected countries and their availability."""
    if not iso_codes:
        return ""

    # Main header for the section
    main_header = "# Country Detection"

    # Create the table header and separator rows
    table_header_line = "| Detected in Query | Document Available |"
    table_separator_line = "|:-----------------:|:------------------:|"
    
    # Create the data rows for each country
    data_lines = []
    for code in sorted(iso_codes):
        flag = iso_to_flag(code)
        availability_icon = "✅" if code in found_iso_codes else "❌"
        # Combine flag and code in the first column for clarity
        data_lines.append(f"| {flag} ({code}) | {availability_icon} |")

    # Combine all parts into a single Markdown table string
    table = "\n".join([table_header_line, table_separator_line] + data_lines)
    
    # Combine the main header and the table
    return f"{main_header}\n\n{table}\n\n---\n\n"

GRADER_DRAFTER_PROMPT = """
You are a legal research assistant specializing in knife law analysis. Your task is to draft a comprehensive answer based on the provided legal context.

## WORKFLOW

### Step 1: Analyze Context
- Review all provided legal sources carefully
- Identify relevant jurisdictions and their specific rules
- Note any exceptions, exemptions, or special conditions

### Step 2: Draft Answer
Create a structured response with exactly two sections:

**## TL;DR Summary**
- Provide bullet points with key legal facts
- Include specific measurements, age limits, penalties when mentioned
- Start each bullet with a bold key phrase
- Be precise about jurisdictional differences

**## Detailed Explanation**
- Provide flowing prose explanation
- Include all relevant legal details from the context
- Explain any jurisdictional variations clearly
- Maintain professional, clear language

### Step 3: Citation Policy
- Present information in clear, professional language
- Only include citations that appear naturally within source documents
- Do NOT add technical chunk references

### Step 4: JSON Response
Return your response in this exact JSON format:
```json
{
  "answer": "## TL;DR Summary\n\n• **[Key Point]**: [Details]\n\n## Detailed Explanation\n\n[Comprehensive explanation...]"
}
```

## QUALITY REQUIREMENTS
- Be comprehensive but concise
- Include all relevant legal facts from the provided context
- Maintain accuracy to source material
- Use professional legal language
- Ensure both sections are well-structured
"""

GRADER_REFINER_PROMPT = """
You are a specialized legal document evaluator and refiner. Your task is to systematically evaluate a draft answer against source documents and produce an improved version.

## EVALUATION METHODOLOGY (Industry Best Practice)

### Step 1: Extract Ground Truth Facts (RAG-Only, Jurisdiction-Aware)
From the CONTEXT documents, create a comprehensive inventory of ALL relevant legal facts.

**CRITICAL CONSTRAINTS:**
- **RAG-ONLY**: Use ONLY the provided CONTEXT documents as ground truth - no external legal knowledge
- **COMPREHENSIVE**: Extract EVERY relevant fact from the provided sources
- **JURISDICTION-AWARE**: For multi-jurisdictional queries, ensure COMPLETE coverage from ALL jurisdictions

**For multi-jurisdictional queries, extract from EACH jurisdiction separately:**
- Country-specific regulations from each provided jurisdiction document
- International/EU rules that appear in the provided sources
- Transit/border-crossing specific rules found in the context
- Conflict resolution between jurisdictions as stated in the sources

**Extract these fact types from EACH relevant jurisdiction (be exhaustive):**
- Explicit rules and prohibitions (what is/isn't allowed)
- Exceptions and exemptions (who/what/when exceptions apply)
- Procedural requirements (permits, licenses, registration)
- Definitions and classifications (what constitutes X, categories)
- Age limits, measurement criteria, thresholds (specific numbers/limits)
- Cross-border/international provisions (transit, import/export rules)
- Penalties and consequences (what happens if violated)
- Temporal aspects (time limits, validity periods)
- Location-specific rules (public vs private, specific venues)
- Conditional requirements (if X then Y rules)

**For each fact, note:**
- Which jurisdiction(s) it applies to
- Whether it directly answers the question
- Whether it provides important context
- The specific text passage that supports this fact
- How it interacts with other jurisdictions' rules

### Step 2: Systematic Draft Evaluation (Multi-Jurisdictional)
For EACH fact in your ground truth inventory (ensuring complete coverage of ALL jurisdictions):

**RECALL CHECK**: Is this fact present in the draft?
- ✅ PRESENT: Fact is clearly stated (may use different wording)
- ❌ MISSING: Fact is completely absent
- ⚠️ UNCLEAR: Fact is mentioned but lacks clarity/precision

**PRECISION CHECK** (RAG-Only): For each claim in the draft:
- ✅ SUPPORTED: Claim has exact textual support in provided CONTEXT
- ❌ UNSUPPORTED: Claim lacks any support in the provided CONTEXT documents
- ⚠️ IMPRECISE: Claim misquotes or misrepresents the provided text
- 🚫 EXTERNAL: Claim appears to use knowledge not found in provided CONTEXT (flag as unsupported)

### Step 3: Calculate Objective Metrics
- **Recall** = (Facts correctly included) / (Total relevant facts)
- **Precision** = (Supported claims) / (Total claims made)
- **F1 Score** = 2 × (Precision × Recall) / (Precision + Recall)

### Step 4: Produce Refined Answer
Create an improved answer that:
- Includes ALL missing relevant facts from ground truth
- Preserves all correct elements from the draft
- Ensures every claim has proper source support
- Presents information in a clear, professional manner without technical chunk references
- Only includes citations that appear naturally within the original source documents
- **MAINTAINS MARKDOWN STRUCTURE**: Preserve the exact section format with ## Summary and ## Details headings

## CRITICAL EVALUATION RULES

1. **NO FALSE POSITIVES**: Only flag content as "missing" if it's genuinely absent, not just phrased differently
2. **COMPREHENSIVE RECALL** (RAG-Only): Include ALL relevant facts from provided CONTEXT, especially ensuring complete coverage of multi-jurisdictional scenarios - each jurisdiction in the provided sources must be fully represented. Never supplement with external legal knowledge.
3. **PROFESSIONAL PRESENTATION**: Present information clearly without technical chunk references
4. **NEGATIVE CLAIMS** (RAG-Only): Only state "no X exists" if explicitly stated in the provided CONTEXT sources. If a topic is not addressed in the provided documents, state "The supplied sources do not address..."

## OUTPUT FORMAT

Provide a JSON response with this exact structure:

```json
{
  "evaluation": {
    "ground_truth_facts": [
      {"fact": "description", "in_draft": true/false, "supporting_text": "exact quote from context"}
    ],
    "recall_analysis": {
      "total_relevant_facts": N,
      "facts_included": N,
      "recall_score": 0.XX,
      "jurisdictions_covered": ["list of jurisdictions with facts included"],
      "jurisdictions_missing": ["list of jurisdictions with missing facts"]
    },
    "precision_analysis": {
      "total_claims": N,
      "supported_claims": N, 
      "precision_score": 0.XX
    },
    "f1_score": 0.XX,
    "missing_facts": ["list of genuinely missing facts"],
    "unsupported_claims": ["list of claims lacking source support"]
  },
  "refined_answer": "Complete improved answer text with all facts presented in clear, professional language"
}
```

**CRITICAL RAG-ONLY RULES:**
- Only use facts that appear in the provided CONTEXT documents
- Never supplement with external legal knowledge or assumptions
- Be extremely careful to avoid false positives in missing_facts list
- Only include facts that are: (1) present in provided CONTEXT and (2) genuinely absent from the draft
- Present information professionally without technical chunk citations
"""

def chat(question, client, config):
    """Main RAG chat function that processes a question and returns an answer."""
    import time
    chat_start_time = time.time()
    logging.info("DEBUG: Starting chat function")
    
    try:
        logging.info("DEBUG: Step 1 - Extracting ISO codes")
        iso_codes = extract_iso_codes(question, client, config['deploy_chat'])
        logging.info(f"DEBUG: ISO codes extracted: {iso_codes}")
        
        if not iso_codes:
            logging.info("DEBUG: No ISO codes found, returning error message")
            return "Could not determine a country from your query. Please be more specific."

        logging.info("DEBUG: Step 2 - Retrieving documents")
        # Dynamic k strategy based on query complexity and country count
        # Legal documents need higher k due to complexity and verbosity
        base_k = 15  # Higher baseline for legal documents (vs typical k=4)
        
        if len(iso_codes) == 1:
            # Single country: use base k
            retrieval_k = base_k
        else:
            # Multi-country: scale up to ensure balanced representation
            # Minimum 10 per country, but cap at reasonable limit
            retrieval_k = min(len(iso_codes) * 10, 50)
        
        logging.info(f"DEBUG: Using dynamic k={retrieval_k} for {len(iso_codes)} countries: {iso_codes}")
        logging.info(f"DEBUG: Multi-jurisdictional query detected: {len(iso_codes) > 1}")
        chunks = retrieve(question, iso_codes, client, config, k=retrieval_k)
        logging.info(f"DEBUG: Retrieved {len(chunks)} chunks")

        if not chunks:
            logging.info("DEBUG: No chunks found, building no-docs response")
            # Even if no docs are found, we can still show the header with availability status
            found_iso_codes = set()
            header = build_response_header(iso_codes, found_iso_codes)
            no_docs_message = f"No documents found for the specified countries: {', '.join(iso_codes)}. Please try another query or check if the relevant legislation is available."
            logging.info("DEBUG: Returning no-docs message")
            return header + no_docs_message

        logging.info("DEBUG: Step 3 - Preparing context and drafting")
        drafter_system_message = """
{
  "role": "expert_legal_research_assistant",
  "private_thought_key": "internal_chain_of_thought",

  /*―――――――――― WORKFLOW ――――――――――*/
  "workflow": [
    { "step": "collect_passages",
      "action": "retrieve candidate chunks responsive to the user question" },

    { "step": "re_rank",
      "action": "order candidates by combined semantic + lexical relevance" },

    { "step": "necessity_filter",
      "action": "KEEP a passage only if removing it would break support for ≥ 1 intended public statement" },

    { "step": "scope_lock",
      "action": "Identify the legal object(s) & jurisdiction(s); DROP passages about other objects or places" },

    { "step": "salience_upgrade",
      "action": "From the kept passages extract every element that conditions legality:  \n                 • numeric thresholds (length, amount, age, time, penalty, etc.)  \n                 • categorical qualifiers (e.g. “automatic”, “concealed”, “professional use”)  \n                 • explicit exceptions / carve‑outs / special categories (e.g. butterfly knives, antique items)  \n                 • permit / licence or exemption regimes  \n                 • enforcement or penalty provisions  \n                 • age or capacity prerequisites explicitly stated in the text  \n                 • lawful‑tool / dangerous‑object clauses  \n                 Mark **each** item as MUST‑MENTION verbatim (units included)." },

    { "step": "deduplicate",
      "action": "When two passages support the same atomic fact, keep the shorter, more precise one" },

    { "step": "draft_answer",
      "action": "Write exactly two sections:  \n                 – TL;DR Summary: bullet list; every bullet begins with a bold key phrase, includes all relevant MUST‑MENTION items for that point.  \n                 – Detailed Explanation: flowing prose with clear, professional language.  \n                 Do NOT add tables, extra headings, or technical chunk citations." },

    { "step": "citation_pruner",
      "action": "Review content for clarity and completeness; ensure all factual claims are supported by the provided context." },

    { "step": "fact_source_check",
      "action": "For EVERY factual fragment: confirm it is explicitly present (or directly inferable) in at least one provided passage.  \n                 – If a claim is *negative* (e.g. "no age restriction", "no permit required") you must either:  \n                   (a) reference a passage that expressly states the absence, OR  \n                   (b) write "The supplied sources do not address …".  \n                 – If support is lacking: delete, rewrite, or express uncertainty with qualifying language." },

    { "step": "permit_check",
      "action": "If any kept passage mentions a permit, licence, or exemption regime, ensure TL;DR contains a bullet that names the rule, states whether the object requires it, and cites the permit passage.  Fail otherwise." },

    { "step": "alignment_check",
      "action": "Fail if:  \n                 (a) any sentence lacks a citation;  \n                 (b) a citation points to a dropped passage;  \n                 (c) ANY MUST‑MENTION item is missing or altered;  \n                 (d) output contains forbidden tables or headings." },

    { "step": "format_guard",
      "action": "Final sweep: ensure only the two authorised markdown headings; strip stray blank lines; verify no sentence is citation‑free." }
  ],

  /*―――――――― CITATION POLICY ――――――――*/
  "citation_policy": {
    "in_corpus": "Present information in clear, professional language without technical chunk references. Only include citations that appear naturally within the source documents themselves.",
    "external_quote": "Reproduce any citation strings that appear verbatim within the original source documents"
  },

  /*―――――――― OUTPUT FORMAT ――――――――*/
  "output_format": {
    "sections": ["TL;DR Summary", "Detailed Explanation"],
    "markdown_headings": true
    /* no hard limits—provide all salient information */
  },

  /*―――――――― FAILURE MODES ――――――――*/
  "failure_modes": {
    "no_relevant_passages": "<CASEB>",
    "validation_error": "<PROMPT‑VIOLATION>"
  },

  /*―――――――― REFERENCE EXAMPLES (few‑shot guidance) ――――――――*/
  "reference_examples": [
    {
      "input": "I am a professional chef commuting by car in Switzerland. May I keep my 20 cm kitchen knife in the glove box while driving to work?",
      "retained_passages": ["KL CH §1.1", "KL CH §1.3", "KL CH §1.4"],
      "must_mention": ["automatic one‑hand opening mechanism (absent)", "tool purpose legitimises carry"],
      "assistant_output": {
        "TL;DR Summary": [
          "A fixed 20 cm chef’s knife is **not a weapon** because it lacks an automatic one‑hand opening mechanism",
          "Transport is lawful when clearly for work and stowed safely; brandishing could re‑classify it as a dangerous object.",
          "Employers or parking‑lot owners may still ban knives.",
          "You can be punished for misdemeanour (Weapon's Act)."
        ],
        "Detailed Explanation": "Under the Swiss Weapons Act, only folding or dagger‑type knives meeting the one‑hand‑opening and length thresholds are weapons. A chef’s knife is treated as a tool. Keeping it in a sheath or roll inside the glove box demonstrates legitimate use. Visible or threatening display could trigger the ‘dangerous object’ clause. The Weapons Act implies misdemeanour penalties. Venue rules may override federal permissiveness. [...]"
      }
    },
    {
      "input": "I commute between Switzerland and Germany and transit EuroAirport with a 10 cm lockable knife; may I carry it?",
      "retained_passages": ["KL CH §1.1", "KL CH §1.3", "KL CH §1.4", "KL DE §42a Absatz 1", "KL CH‑EU‑ASM Art 4"],
      "must_mention": ["automatic one‑hand opening mechanism", "German §42a public‑carry ban", "EU blade > 6 cm aviation limit"],
      "assistant_output": {
        "TL;DR Summary": [
          "**Switzerland**: Knife is legal if it lacks automatic one‑hand opening and is carried as a tool.",
          "**Germany**: One‑hand lockable knives barred from public carry (§42a Abs 1 WaffG).",
          "**EuroAirport**: EU rule forbids blades > 6 cm in security zones (Reg (EU) 2015/1998 Att 4‑C)."
        ],
        "Detailed Explanation": "Swiss law treats non‑one‑hand lockable knives as tools; improper display triggers the dangerous‑object clause. German §42a bans public carry of lockable one‑hand knives unless a statutory exception applies. EuroAirport enforces EU aviation security rules: blades over 6 cm cannot pass passenger checkpoints. [...]"
      }
    }
  ]
}
        """
        # Build structured context with source mapping for systematic evaluation (OPTIMIZED)
        structured_context = []
        total_context_chars = 0
        max_context_chars = 12000  # Optimize context length for faster processing
        
        for i, chunk in enumerate(chunks):
            chunk_text = f"**SOURCE {i+1}: KL {chunk['iso_code']} (Document Section)**\n{chunk['chunk']}"
            if total_context_chars + len(chunk_text) > max_context_chars:
                logging.info(f"DEBUG: Context truncated at {total_context_chars} chars for performance")
                break
            structured_context.append(chunk_text)
            total_context_chars += len(chunk_text)
        
        context = "\n\n---\n\n".join(structured_context)
        logging.info(f"DEBUG: Structured context built with {len(structured_context)} sources, {len(context)} characters")
        # Build jurisdiction list for logging
        jurisdiction_list = [f"KL {chunk['iso_code']}" for chunk in chunks]
        logging.info(f"DEBUG: Sources by jurisdiction: {jurisdiction_list}")
        logging.info(f"DEBUG: Jurisdiction-aware evaluation will expect comprehensive coverage of: {iso_codes}")
        
        # --- Step 1: Draft Answer (OPTIMIZED: Efficient prompting with GPT-4.1) ---
        logging.info("DEBUG: Step 4 - Calling OpenAI for draft answer...")
        draft_start_time = time.time()
        try:
            # Use GPT-4.1 with optimized prompting for efficiency
            draft_model = config['deploy_chat']  # Use available GPT-4.1 deployment
            
            # Build optimized drafter message
            drafter_user_message = f"Context:\n{context}\n\nQuestion: {question}"
            
            draft_resp = client.chat.completions.create(
                model=draft_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": GRADER_DRAFTER_PROMPT},
                    {"role": "user", "content": drafter_user_message}
                ],
                temperature=0.0,
                max_tokens=1800,  # Optimized token limit for drafting
            )
            draft_output_json = draft_resp.choices[0].message.content.strip()
            draft_time = time.time() - draft_start_time
            logging.info(f"DEBUG: Draft answer generated successfully in {draft_time:.2f}s using {draft_model}")
            
            # Parse the draft JSON to extract the answer
            try:
                draft_data = json.loads(draft_output_json)
                draft_answer = draft_data.get('answer', draft_output_json)  # Fallback to raw if no 'answer' key
                logging.info("DEBUG: Draft JSON parsed successfully")
            except json.JSONDecodeError:
                # If JSON parsing fails, use the raw response
                draft_answer = draft_output_json
                logging.warning("DEBUG: Draft JSON parsing failed, using raw response")
                
        except Exception as draft_error:
            logging.error(f"DEBUG: Draft step failed: {draft_error}")
            raise

        logging.info("DEBUG: Step 5 - Building response header")
        # Build the dynamic markdown table header
        found_iso_codes = {chunk['iso_code'] for chunk in chunks}
        header = build_response_header(iso_codes, found_iso_codes)
        
        # Prepend header to the draft answer before sending to refiner
        draft_with_header = header + draft_answer
        logging.info("DEBUG: Header built and prepended to draft")

        # --- Step 2: Grade and Refine Answer ---
        logging.info("DEBUG: Step 6 - Calling OpenAI for refining...")
        refiner_user_message = f"""
QUESTION:
{question}

CONTEXT DOCUMENTS:
{context}

DRAFT ANSWER TO EVALUATE:
{draft_with_header}

Apply the systematic evaluation methodology to:
1. Extract all relevant legal facts from the context
2. Check which facts are present/missing in the draft
3. Verify source support for all draft claims
4. Calculate objective recall/precision metrics
5. Produce a comprehensive refined answer

Be extremely precise - only flag content as "missing" if genuinely absent, not just differently worded.
"""
        logging.info(f"DEBUG: Systematic evaluation message length: {len(refiner_user_message)} characters")
        
        # Check if context is too long for the refiner (OPTIMIZED: context window management)
        context_lines = context.split('\n')
        if len(context) > 12000:  # Optimized limit for faster processing
            logging.info(f"DEBUG: Context too long ({len(context)} chars), truncating for systematic evaluation")
            truncated_lines = context_lines[:int(len(context_lines) * 0.75)]  # Keep 75% of context
            context = '\n'.join(truncated_lines) + "\n\n[Context truncated for systematic evaluation]"
            # Rebuild refiner message with truncated context
            refiner_user_message = f"""Context:
{context}

Draft Answer (with header):
{draft_with_header}

Question: {question}

Apply the systematic evaluation methodology to:
1. Extract all relevant legal facts from the context
2. Check which facts are present/missing in the draft
3. Verify source support for all draft claims
4. Calculate objective recall/precision metrics
5. Produce a comprehensive refined answer

Be extremely precise - only flag content as "missing" if genuinely absent, not just differently worded.
"""

        refine_start_time = time.time()
        try:
            # Use GPT-4.1 for refinement with optimized token allocation
            refine_model = config['deploy_chat']  # GPT-4.1 deployment
            refine_resp = client.chat.completions.create(
                model=refine_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": GRADER_REFINER_PROMPT},
                    {"role": "user", "content": refiner_user_message}
                ],
                temperature=0.0,
                max_tokens=2500,  # Optimized tokens for refinement
            )
            refined_output_json = refine_resp.choices[0].message.content.strip()
            refine_time = time.time() - refine_start_time
            logging.info(f"DEBUG: Refined answer generated successfully in {refine_time:.2f}s using {refine_model}")
        except Exception as refine_error:
            logging.error(f"DEBUG: Refine step failed: {refine_error}")
            raise

        logging.info("DEBUG: Step 7 - Processing systematic evaluation JSON response")
        try:
            refined_data = json.loads(refined_output_json)
            
            # Extract evaluation metrics for logging and debugging
            evaluation = refined_data.get('evaluation', {})
            if 'recall_analysis' in evaluation:
                recall_score = evaluation['recall_analysis'].get('recall_score', 'N/A')
                logging.info(f"DEBUG: Evaluation recall score: {recall_score}")
            if 'precision_analysis' in evaluation:
                precision_score = evaluation['precision_analysis'].get('precision_score', 'N/A')
                logging.info(f"DEBUG: Evaluation precision score: {precision_score}")
            if 'f1_score' in evaluation:
                f1_score = evaluation.get('f1_score', 'N/A')
                logging.info(f"DEBUG: Evaluation F1 score: {f1_score}")
            
            # Log missing facts and unsupported claims for debugging
            missing_facts = evaluation.get('missing_facts', [])
            unsupported_claims = evaluation.get('unsupported_claims', [])
            logging.info(f"DEBUG: Missing facts count: {len(missing_facts)}")
            logging.info(f"DEBUG: Unsupported claims count: {len(unsupported_claims)}")
            
            # Log jurisdiction coverage for multi-country queries
            if 'recall_analysis' in evaluation:
                jurisdictions_covered = evaluation['recall_analysis'].get('jurisdictions_covered', [])
                jurisdictions_missing = evaluation['recall_analysis'].get('jurisdictions_missing', [])
                logging.info(f"DEBUG: Jurisdictions covered: {jurisdictions_covered}")
                logging.info(f"DEBUG: Jurisdictions missing facts: {jurisdictions_missing}")
                logging.info(f"DEBUG: Multi-jurisdictional coverage: {len(jurisdictions_covered)} covered, {len(jurisdictions_missing)} incomplete")
            
            # Get the refined answer (should already include header since we evaluated draft_with_header)
            answer = refined_data.get('refined_answer', '')
            logging.info("DEBUG: Systematic evaluation JSON parsing successful")
            
        except json.JSONDecodeError as json_error:
            logging.error(f"DEBUG: Failed to decode systematic evaluation JSON: {json_error}")
            logging.error(f"DEBUG: Raw refiner output: {refined_output_json[:500]}...")
            # Fallback to the draft with header if the refiner fails
            refined_data = {
                "evaluation": {
                    "error": "Systematic evaluation output was not valid JSON.", 
                    "raw_output": refined_output_json,
                    "fallback_used": True
                },
                "refined_answer": draft_with_header
            }
            answer = draft_with_header

        logging.info("DEBUG: Step 8 - Building final systematic evaluation response")
        # Return the complete evaluation data for debugging and quality monitoring
        evaluation_data = refined_data.get('evaluation', {})
        final_response = {
            "refined_answer": answer,
            "evaluation_metrics": evaluation_data,
            "country_header_included": True,
            "systematic_evaluation": True,
            "jurisdiction_aware_evaluation": True,
            "detected_countries": iso_codes,
            "sources_count": len(chunks)
        }
        
        logging.info("DEBUG: Systematic evaluation pipeline completed")
        total_time = time.time() - chat_start_time
        final_response = {
            "country_header": header,
            "refined_answer": answer
        }
        
        logging.info(f"DEBUG: Chat function completed successfully in {total_time:.2f}s")
        logging.info(f"DEBUG: Performance breakdown - Draft: {draft_time:.2f}s, Refine: {refine_time:.2f}s, Total: {total_time:.2f}s")
        return json.dumps(final_response, indent=2)
        
    except Exception as e:
        logging.error(f"DEBUG: Chat function failed at some step: {e}", exc_info=True)
        return json.dumps({"error": f"Chat function failed: {str(e)}"})

# --- Azure Function Main Entry Point ---
def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('API function invoked.')

    # 1. Load and validate all required environment variables
    try:
        required_vars = {
            "search_endpoint": "KNIFE_SEARCH_ENDPOINT",
            "search_key": "KNIFE_SEARCH_KEY",
            "openai_endpoint": "KNIFE_OPENAI_ENDPOINT",
            "openai_key": "KNIFE_OPENAI_KEY"
        }
        config = {key: os.environ[val] for key, val in required_vars.items()}

        # Add optional vars with defaults
        config.update({
            "index_name": os.environ.get("KNIFE_SEARCH_INDEX", "knife-index"),
            "deploy_chat": os.environ.get("OPENAI_CHAT_DEPLOY", "gpt-4.1"),
            "deploy_embed": os.environ.get("OPENAI_EMBED_DEPLOY", "text-embedding-3-large"),
            "api_version": os.environ.get("OPENAI_API_VERSION", "2024-02-15-preview")
        })
    except KeyError as e:
        error_msg = f"Configuration error: Missing required environment variable: {e}"
        logging.error(error_msg)
        return func.HttpResponse(error_msg, status_code=500)

    # 2. Process the request and run the RAG pipeline
    try:
        question = req.params.get('question')
        if not question:
            try:
                req_body = req.get_json()
            except ValueError:
                pass
            else:
                question = req_body.get('question')

        if not question:
            return func.HttpResponse(
                "Please pass a question on the query string or in the request body, e.g., /api/ask?question=...",
                status_code=400
            )

        # Initialize the Azure OpenAI client
        client = AzureOpenAI(
            azure_endpoint=config['openai_endpoint'],
            api_key=config['openai_key'],
            api_version=config['api_version'],
        )

        # Execute the RAG pipeline
        answer = chat(question, client, config)

        # Return the response
        return func.HttpResponse(answer, mimetype="application/json", status_code=200)

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        return func.HttpResponse(
            f"An internal server error occurred. Please check the logs for details. Error ID: {getattr(e, 'error_id', 'N/A')}", 
            status_code=500
        )
