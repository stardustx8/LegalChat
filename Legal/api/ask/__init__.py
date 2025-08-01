import logging
import os, json, requests, re
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
    """Retrieves documents from Azure Cognitive Search based on a vector query and filters."""
    logging.info(f"DEBUG: retrieve() called with query='{query}', iso_codes={iso_codes}")
    
    if not iso_codes:
        logging.info("DEBUG: No ISO codes provided, returning empty list")
        return []
    
    try:
        logging.info("DEBUG: Generating embedding for query...")
        vec = embed(query, client, config['deploy_embed'])
        logging.info(f"DEBUG: Embedding generated successfully, length={len(vec)}")
    except Exception as e:
        logging.error(f"DEBUG: Failed to generate embedding: {e}")
        raise
    
    search_url = f"{config['search_endpoint']}/indexes/{config['index_name']}/docs/search?api-version=2023-11-01"
    headers = {'Content-Type': 'application/json', 'api-key': config['search_key']}
    filter_str = f"search.in(iso_code, '{','.join(iso_codes)}', ',')"
    
    payload = {
        "vectorQueries": [
            {
                "kind": "vector",
                "vector": vec,
                "fields": "embedding",
                "k": k
            }
        ],
        "filter": filter_str,
        "select": "chunk,iso_code,id"
    }
    
    logging.info(f"DEBUG: Sending search request to {search_url}")
    logging.info(f"DEBUG: Filter: {filter_str}")
    logging.info(f"DEBUG: Payload keys: {list(payload.keys())}")
    
    try:
        response = requests.post(search_url, headers=headers, json=payload)
        logging.info(f"DEBUG: Search response status: {response.status_code}")
        response.raise_for_status()
        result = response.json().get('value', [])
        logging.info(f"DEBUG: Search returned {len(result)} documents")
        return result
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
{
  "role": "grader_and_refiner_agent",
  "private_thought_key": "internal_grading_and_refinement_process",

  "goal": "First, critically evaluate a DRAFT_ANSWER against the provided CONTEXT. Second, produce a REFINED_ANSWER that corrects all identified flaws and perfectly adheres to the output format. The final output will contain both the evaluation and the refined answer for debugging.",

  "workflow": [
    { "step": "extract_salient_facts",
      "action": "From the CONTEXT passages, compile a comprehensive list of every atomic factual element (statutory conditions, exceptions, numeric thresholds, penalties, etc.) that is directly relevant to the user's QUESTION. This list will serve as the ground truth for grading." },

    { "step": "grade_draft",
      "action": "Evaluate the DRAFT_ANSWER against the salient_facts list. Calculate and record the following:\n                 - missing_facts: [An array of salient facts that were NOT included in the draft].\n                 - unsupported_claims: [An array of claims from the draft that are NOT supported by the CONTEXT].\n                 - scores: {\n                     'recall': '(# salient facts present) / (total salient facts)',\n                     'precision': '(# supported claims) / (total claims)',\n                     'F1': 'Harmonic mean of recall and precision'\n                   }" },

    { "step": "refine_answer",
      "action": "Rewrite the DRAFT_ANSWER into a REFINED_ANSWER to achieve recall=1.0 and precision≈1.0.\n                 - Integrate all 'missing_facts' with correct citations.\n                 - Remove or rewrite all 'unsupported_claims' to be strictly grounded in the CONTEXT.\n                 - Adhere perfectly to the answer format: two sections ('TL;DR Summary', 'Detailed Explanation'), with every sentence cited." },

    { "step": "finalize_output",
      "action": "Produce a single JSON object with two keys: 'evaluation' and 'refined_answer'.\n                 - The 'evaluation' key will contain the full output of the 'grade_draft' step.\n                 - The 'refined_answer' key will contain ONLY the final, user-facing text of the refined answer." }
  ],

  "house_rules": {
    "negative_claims": "A negative assertion (e.g., 'no age limit') must be supported by an explicit passage stating the absence. Otherwise, phrase it as 'The supplied sources do not address...' and give it NO citation.",
    "citation_format": "(KL {ISO-code} §section)"
  }
}
"""

def chat(question: str, client: AzureOpenAI, config: dict) -> str:
    """Orchestrates the RAG pipeline to answer a question."""
    logging.info("DEBUG: Starting chat function")
    
    try:
        logging.info("DEBUG: Extracting ISO codes...")
        iso_codes = extract_iso_codes(question, client, config['deploy_chat'])
        logging.info(f"DEBUG: Extracted ISO codes: {iso_codes}")
        
        if not iso_codes:
            logging.info("DEBUG: No ISO codes found")
            return json.dumps({"answer": "Could not determine a country from your query. Please be more specific."})

        logging.info(f"Detected countries: {', '.join(iso_codes)}")
        chunks = retrieve(question, iso_codes, client, config, k=5)
        logging.info(f"DEBUG: Retrieved {len(chunks)} chunks")
        
        found_iso_codes = {chunk['iso_code'] for chunk in chunks}
        header = build_response_header(iso_codes, found_iso_codes)
        logging.info("DEBUG: Built response header")

        if not chunks:
            logging.info("DEBUG: No chunks found, returning no docs message")
            no_docs_message = f"No documents found for the specified countries: {', '.join(iso_codes)}. Please try another query or check if the relevant legislation is available."
            return json.dumps({"answer": header + no_docs_message})
        
        # TEST: Try a simple OpenAI chat completion to verify the chat API works
        logging.info("DEBUG: Testing simple OpenAI chat completion...")
        try:
            test_response = client.chat.completions.create(
                model=config['deploy_chat'],
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Say 'OpenAI chat is working!'"}
                ],
                max_tokens=20,
                temperature=0.0
            )
            chat_test_result = test_response.choices[0].message.content.strip()
            logging.info(f"DEBUG: OpenAI chat test successful: {chat_test_result}")
        except Exception as chat_error:
            logging.error(f"DEBUG: OpenAI chat test failed: {chat_error}")
            return json.dumps({"error": f"OpenAI chat completion failed: {str(chat_error)}"})
        
        # SIMPLIFIED VERSION: Return the retrieved documents with chat test result
        logging.info("DEBUG: Returning simplified response with retrieved documents")
        simple_answer = f"Found {len(chunks)} documents for {', '.join(iso_codes)}. Documents retrieved successfully!\n\nOpenAI Chat Test: {chat_test_result}"
        
        # Include first chunk as sample
        if chunks:
            sample_chunk = chunks[0]
            simple_answer += f"\n\nSample document: {sample_chunk.get('chunk', 'No content')[:200]}..."
        
        return json.dumps({"answer": header + simple_answer})
        
    except Exception as e:
        logging.error(f"DEBUG: Error in chat function: {e}", exc_info=True)
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
