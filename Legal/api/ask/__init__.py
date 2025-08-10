import logging
import os, json, requests, re, time, random
import azure.functions as func
from requests.adapters import HTTPAdapter
from openai import AzureOpenAI

# --- Prompts and Helper Functions ---
# Note: Environment variables are loaded within the main() function to prevent module-level errors.

def with_retries(fn, attempts=1, initial_delay=0.4, factor=2.0, jitter=0.1, max_delay=5.0):
    """Retry helper: runs fn() with exponential backoff and jitter.
    attempts denotes the number of additional retries beyond the first attempt.
    """
    delay = initial_delay
    last_exc = None
    for i in range(attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            logging.warning(f"Retryable error on attempt {i+1}/{attempts+1}: {e}")
            if i == attempts:
                raise
            sleep_for = min(delay + (random.random() * jitter), max_delay)
            time.sleep(sleep_for)
            delay *= factor

# Connection pooling for outbound HTTP (e.g., Azure Cognitive Search)
SESSION = None

def get_session() -> requests.Session:
    global SESSION
    if SESSION is None:
        s = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
        s.mount('http://', adapter)
        s.mount('https://', adapter)
        SESSION = s
    return SESSION

def _post_and_raise(session: requests.Session, url: str, headers: dict, payload: dict, timeout: int = 15) -> requests.Response:
    """POST helper that raises for HTTP errors so retries can trigger properly."""
    resp = session.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp

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
        response = with_retries(
            lambda: client.chat.completions.create(
                model=deploy_chat,
                messages=[
                    {"role": "system", "content": COUNTRY_DETECTION_PROMPT},
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
            ),
            attempts=2,
            initial_delay=0.4
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
        logging.info("DEBUG: Generating embedding for query...")
        t_embed_start = time.monotonic()
        vec = with_retries(lambda: embed(query, client, config['deploy_embed']), attempts=2, initial_delay=0.4)
        embed_ms = int((time.monotonic() - t_embed_start) * 1000)
        logging.info(f"DEBUG: Embedding generated successfully, length={len(vec)}")
        logging.info(f"TIMING: embed_ms={embed_ms}")
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
    
    session = get_session()
    try:
        t_search_start = time.monotonic()
        response = with_retries(lambda: _post_and_raise(session, search_url, headers, payload), attempts=2, initial_delay=0.4)
        logging.info(f"DEBUG: Search response status: {response.status_code}")
        response.raise_for_status()
        raw_results = response.json().get('value', [])
        search_ms = int((time.monotonic() - t_search_start) * 1000)
        logging.info(f"TIMING: search_ms={search_ms}")
        logging.info(f"DEBUG: Raw search returned {len(raw_results)} documents")
        
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

GRADER_REFINER_PROMPT = """
You are a legal assistant; use ONLY the provided CONTEXT to answer the QUESTION clearly and concisely, with no external knowledge.
"""

def chat(question: str, client: AzureOpenAI, config: dict) -> str:
    """Orchestrates the RAG pipeline to answer a question."""
    logging.info("DEBUG: Starting chat function")
    t_total_start = time.monotonic()
    
    try:
        logging.info("DEBUG: Step 1 - Extracting ISO codes")
        t_iso_start = time.monotonic()
        iso_codes = extract_iso_codes(question, client, config['deploy_chat'])
        iso_ms = int((time.monotonic() - t_iso_start) * 1000)
        logging.info(f"TIMING: iso_detection_ms={iso_ms}")
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
        t_retrieve_start = time.monotonic()
        chunks = retrieve(question, iso_codes, client, config, k=retrieval_k)
        retrieve_ms = int((time.monotonic() - t_retrieve_start) * 1000)
        logging.info(f"TIMING: retrieve_total_ms={retrieve_ms}")
        logging.info(f"DEBUG: Retrieved {len(chunks)} chunks")

        if not chunks:
            logging.info("DEBUG: No chunks found, building no-docs response")
            # Even if no docs are found, we can still show the header with availability status
            found_iso_codes = set()
            header = build_response_header(iso_codes, found_iso_codes)
            no_docs_message = f"No documents found for the specified countries: {', '.join(iso_codes)}. Please try another query or check if the relevant legislation is available."
            logging.info("DEBUG: Returning no-docs message")
            return header + no_docs_message

        logging.info("DEBUG: Step 3 - Preparing context for single-pass answer generation")
        # Build structured context with source mapping
        structured_context = []
        for i, chunk in enumerate(chunks):
            structured_context.append(f"**SOURCE {i+1}: KL {chunk['iso_code']} (Document Section)**\n{chunk['chunk']}")

        context = "\n\n---\n\n".join(structured_context)
        logging.info(f"DEBUG: Structured context built with {len(chunks)} sources, {len(context)} characters")
        # Build jurisdiction list for logging
        jurisdiction_list = [f"KL {chunk['iso_code']}" for chunk in chunks]
        logging.info(f"DEBUG: Sources by jurisdiction: {jurisdiction_list}")
        logging.info(f"DEBUG: Jurisdiction-aware evaluation will expect comprehensive coverage of: {iso_codes}")

        # Build the dynamic markdown table header for UI (not passed to the model)
        found_iso_codes = {chunk['iso_code'] for chunk in chunks}
        header = build_response_header(iso_codes, found_iso_codes)
        logging.info("DEBUG: Header built for UI")

        # --- Step 2: Grade and Refine Answer ---
        logging.info("DEBUG: Step 4 - Calling OpenAI for single-pass refine/answer...")
        refiner_user_message = f"""
QUESTION:
{question}

CONTEXT:
{context}

INSTRUCTIONS: Answer using only the CONTEXT; be concise and precise.
"""
        logging.info(f"DEBUG: Systematic evaluation message length: {len(refiner_user_message)} characters")
        
        # Conservative token limit for systematic evaluation with structured context
        if len(refiner_user_message) > 40000:
            logging.warning("DEBUG: Message too long for systematic evaluation, truncating context")
            # Preserve source structure while truncating
            context_lines = context.split('\n')
            truncated_lines = context_lines[:int(len(context_lines) * 0.7)]  # Keep 70% of context
            context = '\n'.join(truncated_lines) + "\n\n[Context truncated for systematic evaluation]"

        try:
            t_llm_start = time.monotonic()
            refine_resp = with_retries(
                lambda: client.chat.completions.create(
                    model=config['deploy_chat'],
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": GRADER_REFINER_PROMPT},
                        {"role": "user", "content": refiner_user_message}
                    ],
                    temperature=0.0,
                ),
                attempts=2,
                initial_delay=0.4
            )
            refined_output_json = refine_resp.choices[0].message.content.strip()
            logging.info("DEBUG: Refined answer generated successfully")
            llm_ms = int((time.monotonic() - t_llm_start) * 1000)
            logging.info(f"TIMING: llm_refine_ms={llm_ms}")
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
            # Do not degrade answer quality: bubble up to trigger retry at caller/front-end
            raise

        logging.info("DEBUG: Step 8 - Building final systematic evaluation response")
        # Return the complete evaluation data for debugging and quality monitoring
        evaluation_data = refined_data.get('evaluation', {})
        final_response = {
            "country_header": header,
            "refined_answer": answer
        }
        
        total_ms = int((time.monotonic() - t_total_start) * 1000)
        logging.info(f"TIMING: total_pipeline_ms={total_ms}")
        logging.info("DEBUG: Systematic evaluation pipeline completed")
        return json.dumps(final_response, indent=2)
        
    except Exception as e:
        logging.error(f"DEBUG: Chat function failed at some step: {e}", exc_info=True)
        # Bubble up to main() so that a proper 5xx is returned and the front-end can retry
        raise

# --- Azure Function Main Entry Point ---
def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('API function invoked.')

    # Lightweight health check to warm instance without heavy work
    if req.params.get('ping'):
        return func.HttpResponse("ok", mimetype="text/plain", status_code=200)

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
