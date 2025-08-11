# ask.py
import os, json, requests, re
from azure.core.credentials import AzureKeyCredential
# from azure.search.documents import SearchClient # Bypassing buggy client
from openai import AzureOpenAI  # pip install azure-search-documents openai>=1.13

SEARCH_ENDPOINT   = os.environ["KNIFE_SEARCH_ENDPOINT"]
SEARCH_KEY        = os.environ["KNIFE_SEARCH_KEY"]       # admin *or* query key ok for reads
INDEX_NAME        = os.environ.get("KNIFE_SEARCH_INDEX", "knife-index")

OPENAI_ENDPOINT   = os.environ["KNIFE_OPENAI_ENDPOINT"]
OPENAI_KEY        = os.environ["KNIFE_OPENAI_KEY"]
DEPLOY_CHAT       = os.environ.get("OPENAI_CHAT_DEPLOY", "gpt-4.1") # gpt-4.1
DEPLOY_EMBED      = os.environ.get("OPENAI_EMBED_DEPLOY", "text-embedding-3-large")
API_VERSION       = os.environ.get("OPENAI_API_VERSION", "2024-02-15-preview")

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

def embed(text:str)->list[float]:
    cli = AzureOpenAI(
        azure_endpoint = OPENAI_ENDPOINT,
        api_key        = OPENAI_KEY,
        api_version    = API_VERSION,
    )
    emb = cli.embeddings.create(
        input=[text],
        model=DEPLOY_EMBED
    ).data[0].embedding
    return emb

def extract_iso_codes(text:str, client:AzureOpenAI)->list[str]:
    """Extracts ISO-3166-1 alpha-2 country codes from text using an LLM call."""
    try:
        response = client.chat.completions.create(
            model=DEPLOY_CHAT,
            messages=[
                {"role": "system", "content": COUNTRY_DETECTION_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0.0,
        )
        raw_content = response.choices[0].message.content.strip()
        
        # Clean up response from markdown code block
        cleaned_content = re.sub(r'^```json\s*|\s*```$', '', raw_content)
        
        data = json.loads(cleaned_content)
        if not isinstance(data, list):
            return []

        # Validate and deduplicate by code
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
        print(f"Error parsing country detection response: {e}")
        return [] # Return empty on parsing or response error

def retrieve(query: str, iso_codes: list[str], k: int = 5) -> list[dict]:
    if not iso_codes:
        return []
    vec = embed(query)
    search_url = f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}/docs/search?api-version=2023-11-01"
    headers = {'Content-Type': 'application/json', 'api-key': SEARCH_KEY}
    filter_str = f"search.in(iso_code, '{','.join(iso_codes)}', ',')"
    # New vectorQueries format for API version 2023-11-01:
    payload = {
        "vectorQueries": [
            {
                "kind": "vector",
                "vector": vec,
                "fields": "vector",
                "k": k
            }
        ],
        "filter": filter_str,
        "select": "content,iso_code,chunk_index"
    }
    response = requests.post(search_url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json().get('value', [])

def chat(question:str, client:AzureOpenAI)->dict:
    """Orchestrates the RAG pipeline to answer a question."""
    iso_codes = extract_iso_codes(question, client)
    if not iso_codes:
        return "Could not determine a country from your query. Please be more specific."

    print(f"Detected countries: {', '.join(iso_codes)}")
    chunks = retrieve(question, iso_codes, k=5)

    if not chunks:
        return f"No documents found for the specified countries: {', '.join(iso_codes)}. Please try another query or check if the relevant legislation is available."

    # Assemble the prompt for the drafter model
    drafter_system_message = """
{
  "role": "expert_legal_research_assistant",
  "output_mandate": "You are the sole and final agent. Your output is the direct, user-facing answer and will not be reviewed or modified by any other process. Produce the highest possible quality answer, as it will be judged on its standalone merits.",
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
      "action": "Write exactly two sections:  \n                 – TL;DR Summary: bullet list; every bullet begins with a bold key phrase, includes all relevant MUST‑MENTION items for that point, and ends with ≥ 1 citation.  \n                 – Detailed Explanation: flowing prose; EVERY sentence ends with ≥ 1 citation.  \n                 Do NOT add tables, extra headings, or uncited assertions." },

    { "step": "citation_pruner",
      "action": "Within each citation list, drop any passage whose removal leaves the sentence fully supported; delete sentences whose lists become empty." },

    { "step": "fact_source_check",
      "action": "For EVERY factual fragment: confirm it is explicitly present (or directly inferable) in at least one cited passage.  \n                 – If a claim is *negative* (e.g. “no age restriction”, “no permit required”) you must either:  \n                   (a) cite a passage that expressly states the absence, OR  \n                   (b) write “The supplied sources do not address …” **without attaching any citation**.  \n                 – If support is lacking: delete, rewrite, or express uncertainty with qualifying language." },

    { "step": "permit_check",
      "action": "If any kept passage mentions a permit, licence, or exemption regime, ensure TL;DR contains a bullet that names the rule, states whether the object requires it, and cites the permit passage.  Fail otherwise." },

    { "step": "alignment_check",
      "action": "Fail if:  \n                 (a) any sentence lacks a citation;  \n                 (b) a citation points to a dropped passage;  \n                 (c) ANY MUST‑MENTION item is missing or altered;  \n                 (d) output contains forbidden tables or headings." },

    { "step": "format_guard",
      "action": "Final sweep: ensure only the two authorised markdown headings; strip stray blank lines; verify no sentence is citation‑free." }
  ],

  /*―――――――― CITATION POLICY ――――――――*/
  "citation_policy": {
    "in_corpus": "Use exactly: (KL {ISO-code} §section[, §section…])",
    "external_quote": "Reproduce the statute’s own citation string verbatim as shown in the passage"
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
          "A fixed 20 cm chef’s knife is **not a weapon** because it lacks an automatic one‑hand opening mechanism (KL CH §1.1).",
          "Transport is lawful when clearly for work and stowed safely; brandishing could re‑classify it as a dangerous object (KL CH §1.3).",
          "Employers or parking‑lot owners may still ban knives (KL CH §1.4)."
        ],
        "Detailed Explanation": "Under the Swiss Weapons Act, only folding or dagger‑type knives meeting the one‑hand‑opening and length thresholds are weapons (KL CH §1.1). A chef’s knife is treated as a tool. Keeping it in a sheath or roll inside the glove box demonstrates legitimate use. Visible or threatening display could trigger the ‘dangerous object’ clause (KL CH §1.3). Venue rules may override federal permissiveness (KL CH §1.4)."
      }
    },
    {
      "input": "I commute between Switzerland and Germany and transit EuroAirport with a 10 cm lockable knife; may I carry it?",
      "retained_passages": ["KL CH §1.1", "KL CH §1.3", "KL CH §1.4", "KL DE §42a Absatz 1", "KL CH‑EU‑ASM Art 4"],
      "must_mention": ["automatic one‑hand opening mechanism", "German §42a public‑carry ban", "EU blade > 6 cm aviation limit"],
      "assistant_output": {
        "TL;DR Summary": [
          "**Switzerland**: Knife is legal if it lacks automatic one‑hand opening and is carried as a tool (KL CH §1.1, §1.3).",
          "**Germany**: One‑hand lockable knives barred from public carry (§42a Abs 1 WaffG) (KL DE §42a Abs 1).",
          "**EuroAirport**: EU rule forbids blades > 6 cm in security zones (Reg (EU) 2015/1998 Att 4‑C) (KL CH‑EU‑ASM Art 4)."
        ],
        "Detailed Explanation": "Swiss law treats non‑one‑hand lockable knives as tools; improper display triggers the dangerous‑object clause (KL CH §1.3). German §42a bans public carry of lockable one‑hand knives unless a statutory exception applies (KL DE §42a Abs 1). EuroAirport enforces EU aviation security rules: blades over 6 cm cannot pass passenger checkpoints (KL CH‑EU‑ASM Art 4)."
      }
    }
  ]
}
    """
    context = "\n\n---\n\n".join([c['content'] for c in chunks])
    # --- Step 1: Draft Answer ---
    print("\n--- Generating draft answer... ---")
    draft_resp = client.chat.completions.create(
        model=DEPLOY_CHAT,
        messages=[
            {"role": "system", "content": drafter_system_message},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}
        ],
        temperature=0.0,
    )
    draft_answer = draft_resp.choices[0].message.content.strip()
    print("--- Draft answer generated. ---")

    # --- Step 2: Grade and Refine Answer ---
    print("--- Grading and refining answer... ---")
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
    refiner_user_message = f"""CONTEXT:\n{context}\n\nQUESTION: {question}\n\nDRAFT_ANSWER:\n{draft_answer}"""

    refine_resp = client.chat.completions.create(
        model=DEPLOY_CHAT,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": GRADER_REFINER_PROMPT},
            {"role": "user", "content": refiner_user_message}
        ],
        temperature=0.0,
    )
    
    refined_output_json = refine_resp.choices[0].message.content.strip()
    print("--- Refined answer generated. ---")

    try:
        refined_data = json.loads(refined_output_json)
    except json.JSONDecodeError:
        print("ERROR: Failed to decode JSON from refiner model. Falling back to draft.")
        refined_data = {
            "evaluation": {"error": "Refiner output was not valid JSON.", "raw_output": refined_output_json},
            "refined_answer": draft_answer
        }

    return refined_data

def main():
    """Main execution function."""
    # Centralise the OpenAI client
    client = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_KEY,
        api_version=API_VERSION,
    )
    try:
        while True:
            q = input("Ask (or 'exit'): ")
            if q.lower() == 'exit':
                break
            print(chat(q, client))
    except KeyboardInterrupt:
        print("\nExiting.")

if __name__ == "__main__":
    main()