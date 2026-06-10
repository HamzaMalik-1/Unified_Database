"""
nlp_processor.py  –  Drop-in replacement for the original monolithic file.

Architecture
────────────
  NLPProcessor
    │
    ├── pipeline/IntentClassifier     → classifies query intent (SELECT / INSERT / …)
    │
    ├── extractors/TableExtractor     → finds table/collection name (exact → fuzzy → spaCy)
    ├── extractors/ConditionExtractor → builds WHERE predicates
    ├── extractors/SelectPartsExtractor → columns, aggregates, ORDER BY, LIMIT …
    │
    ├── generators/SelectGenerator   → assembles SELECT (+ UNION / CTE / JOINs)
    ├── generators/DMLGenerator      → INSERT / UPDATE / DELETE
    └── generators/DDLGenerator      → CREATE/DROP/ALTER TABLE, INDEX, VIEW, TRIGGER …

Processing pipeline
───────────────────
  process(text)
    │
    ├─ 1. pre-process (lowercase, strip)
    ├─ 2. fine‑tuned model (zero‑shot)   → fast, learned patterns
    ├─ 3. rule‑based                    → fast, deterministic fallback
    └─ 4. few‑shot LLM (Ollama)         → slow, flexible fallback

Public API (backwards-compatible with the original NLPProcessor)
────────────────────────────────────────────────────────────────
  NLPProcessor(catalog)
  .process(text)          → Optional[str]
  ._llm_fallback(text)    → Optional[str]   (kept for /api/nlp/llm endpoint)
"""

from __future__ import annotations

import re
from typing import Optional, List
import subprocess
import random
import json
from pathlib import Path
import sys
from .constants import COLUMN_SYNONYMS, EXPLAIN_MODEL, OLLAMA_MODEL, OLLAMA_URL, OLLAMA_TIMEOUT
from .pipeline.intent_classifier import (
    IntentClassifier,
    SELECT, INSERT, UPDATE, DELETE,
    DROP_TABLE, DROP_COLLECTION, CREATE_TABLE, CREATE_COLLECTION,
    CREATE_INDEX, DROP_INDEX, CREATE_TEXT_INDEX, DROP_TEXT_INDEX,
    ALTER_TABLE, CREATE_VIEW, DROP_VIEW,
    CREATE_PROCEDURE, DROP_PROCEDURE, CREATE_TRIGGER, DROP_TRIGGER,
)
from .extractors.table_extractor import TableExtractor
from .extractors.condition_extractor import ConditionExtractor
from .extractors.select_parts_extractor import SelectPartsExtractor
from .generators.select_generator import SelectGenerator
from .generators.dml_generator import DMLGenerator
from .generators.ddl_generator import DDLGenerator
import hashlib
import httpx


class NLPProcessor:
    """
    Converts natural-language database queries into engine commands.

    Parameters
    ----------
    catalog : object
        The UnifiedDB catalog that exposes SQL / NoSQL engines.
    """

    def __init__(self, catalog):
        self.catalog = catalog

        # ── Sub-components ──────────────────────────────────────────────
        self.intent      = IntentClassifier()
        self.table_ext   = TableExtractor()
        self.conditions  = ConditionExtractor()
        self.select_parts = SelectPartsExtractor()

        self.select_gen  = SelectGenerator(self)
        self.dml_gen     = DMLGenerator(self)
        self.ddl_gen     = DDLGenerator(self)

    # ══════════════════════════════════════════════════════════════════════
    #  Public entry point
    # ══════════════════════════════════════════════════════════════════════

    def process(self, text: str) -> Optional[str]:
        normalised = self._normalise(text)
        is_short = len(text) <= 45     # treat sentences of 20 characters or fewer as "short"
        # is_short = len(text) <= 75     # treat sentences of 20 characters or fewer as "short"
        
        if is_short:
            # Short sentences: rule‑based first (fast, deterministic)
            command = self._rule_based(normalised)
            if command:
                return command
            
            command = self._call_finetuned_model(normalised)
            if command and self._validate_command(command):
                return command
            
            return self._llm_fallback(text)
        
        else:
            # Long sentences: fine‑tuned model first (better understanding)
            command = self._call_finetuned_model(normalised)
            if command and self._validate_command(command):
                return command
            
            command = self._rule_based(normalised)
            if command:
                return command
            
            return self._llm_fallback(text)

    # ══════════════════════════════════════════════════════════════════════
    #  Fine‑tuned model (zero‑shot)
    # ══════════════════════════════════════════════════════════════════════

    def _call_finetuned_model(self, text: str) -> Optional[str]:
        schema = self._get_current_schema_as_string()
        prompt = f"<|user|>\n### Schema: {schema}\n### Question: {text}\n<|assistant|>\n"
        
        # ── DEBUG: Print the exact prompt ─────────────────────────────────────
        print("\n" + "="*80)
        print("[FineTuned] Sending prompt to model:")
        print("-"*80)
        print(prompt)
        print("-"*80)
        
        try:
            with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
                response = client.post(
                    OLLAMA_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.0,
                            "num_predict": 150,
                            "stop": ["<|user|>", "\n<|", "</s>"]
                        }
                    }
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                
                # ── DEBUG: Print raw model output ─────────────────────────────
                print("[FineTuned] Raw model output:")
                print("-"*80)
                print(raw)
                print("-"*80)
                
                # ── Extract full command (supports multi-line and markdown) ────
                command = self._extract_command_from_output(raw)
                
                if command:
                    print(f"[FineTuned] Extracted command: {command}")
                    print("="*80 + "\n")
                    return command
                else:
                    print("[FineTuned] No valid command extracted from output")
                    print("="*80 + "\n")
                    return None
                    
        except Exception as e:
            print(f"[FineTuned] Error: {e}", file=sys.stderr)
            print("="*80 + "\n")
            return None

    def _extract_command_from_output(self, raw: str) -> Optional[str]:
        """
        Extract a complete SQL/NoSQL command from raw model output,
        handling multi-line statements, markdown code blocks, and extra prose.
        """
        if not raw:
            return None
        
        # Remove markdown code fences (```sql ... ``` or ``` ... ```)
        # Also remove any surrounding triple backticks.
        import re
        cleaned = re.sub(r'^```(?:sql|nosql)?\s*\n?', '', raw, flags=re.IGNORECASE)
        cleaned = re.sub(r'\n?```$', '', cleaned)
        
        lines = cleaned.split('\n')
        command_parts = []
        in_command = False
        semicolon_seen = False
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                # Empty line: if we are already collecting, stop (end of command)
                if in_command:
                    break
                continue
            
            upper = stripped.upper()
            
            # Start of command detection
            if not in_command:
                if upper.startswith(("SQL ", "NOSQL ")):
                    command_parts.append(stripped)
                    in_command = True
                elif upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "WITH", "CALL")):
                    prefix = "NOSQL" if re.search(r'\b(CONTAINS|PUSH|PULL|ADDTOSET)\b', stripped, re.IGNORECASE) else "SQL"
                    command_parts.append(f"{prefix} {stripped}")
                    in_command = True
                else:
                    # Not a command line – skip until we find one
                    continue
            else:
                # Already collecting command lines
                # Stop if we encounter a line that looks like prose (common English words)
                if re.search(r'\b(example|note|this|the|to|for|and|will|update|set|where|values|insert|delete|create|alter|drop|select|from|join)\b', upper):
                    # But if the line still looks like SQL continuation (e.g., starts with SET, WHERE, etc.), keep it
                    if re.match(r'^(SET|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|JOIN|ON|AND|OR|VALUES|INTO|FROM)', upper):
                        command_parts.append(stripped)
                    else:
                        # Likely prose – stop collecting
                        break
                else:
                    command_parts.append(stripped)
                
                # If the line ends with a semicolon, we have a complete statement
                if stripped.endswith(';'):
                    semicolon_seen = True
                    break
        
        if not command_parts:
            # Fallback: try to extract using simple regex
            # Look for pattern: (SQL|NOSQL) ... ;
            match = re.search(r'(SQL|NOSQL)\s+.*?;', cleaned, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(0).rstrip(';')
            return None
        
        # Join parts with space (or newline? We'll use space for consistency)
        full_command = ' '.join(command_parts).rstrip(';')
        
        # Ensure it starts with SQL or NOSQL (it should already)
        if not full_command.upper().startswith(("SQL ", "NOSQL ")):
            return None
        
        return full_command

    def _validate_command(self, command: str) -> bool:
        """Basic syntax validation for a generated command."""
        if not command:
            return False
        upper = command.upper().strip()
        # Must start with SQL or NOSQL
        if not (upper.startswith("SQL ") or upper.startswith("NOSQL ")):
            return False
        # Must contain a recognised verb after the prefix
        rest = upper[4:].strip() if upper.startswith("SQL ") else upper[6:].strip()
        verbs = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "WITH", "CALL")
        return any(rest.startswith(v) for v in verbs)

    # ══════════════════════════════════════════════════════════════════════
    #  Rule-based pipeline
    # ══════════════════════════════════════════════════════════════════════

    def _rule_based(self, text: str) -> Optional[str]:
        # ── Database commands (handled before any other intent) ──────────
        db_cmd = self._handle_database_command(text)
        if db_cmd:
            return db_cmd

        # ── Special case: "rename column" → ALTER_TABLE ──────────────────
        # Because the intent classifier may not catch it.
        if re.search(r'rename\s+column', text):
            token = ALTER_TABLE
        else:
            token = self.intent.classify(text)
        if token is None:
            return None

        dispatch: dict[str, callable] = {
            # ── DDL ──────────────────────────────────────────────────────
            CREATE_INDEX:       self.ddl_gen.create_index,
            DROP_INDEX:         self.ddl_gen.drop_index,
            CREATE_TEXT_INDEX:  self.ddl_gen.create_text_index,
            DROP_TEXT_INDEX:    self.ddl_gen.drop_text_index,
            ALTER_TABLE:        self.ddl_gen.alter_table,
            CREATE_VIEW:        self.ddl_gen.create_view,
            DROP_VIEW:          self.ddl_gen.drop_view,
            CREATE_PROCEDURE:   self.ddl_gen.create_procedure,
            DROP_PROCEDURE:     self.ddl_gen.drop_procedure,
            CREATE_TRIGGER:     self.ddl_gen.create_trigger,
            DROP_TRIGGER:       self.ddl_gen.drop_trigger,
            CREATE_TABLE:       self.ddl_gen.create_table,
            CREATE_COLLECTION:  self.ddl_gen.create_collection,
            DROP_TABLE:         self.ddl_gen.drop_table,
            DROP_COLLECTION:    self.ddl_gen.drop_collection,
            # ── DML ──────────────────────────────────────────────────────
            SELECT:             self.select_gen.generate,
            INSERT:             self.dml_gen.insert,
            UPDATE:             self.dml_gen.update,
            DELETE:             self.dml_gen.delete,
        }

        handler = dispatch.get(token)
        if handler:
            return handler(text)
        return None

    def _handle_database_command(self, text: str) -> Optional[str]:
        """
        Recognise natural language for database operations.
        Returns a command like "DATABASE CREATE testdb" or "DATABASE USE testdb".
        """
        # Create database
        m = re.search(r'create\s+database\s+(\w+)', text)
        if m:
            return f"DATABASE CREATE {m.group(1)}"

        # Use database – allow "use testdb", "use database testdb", "switch to testdb", etc.
        # The word "database" is optional.
        m = re.search(r'\b(?:use|switch\s+to|connect\s+to)\s+(?:database\s+)?(\w+)', text)
        if m:
            return f"DATABASE USE {m.group(1)}"

        # Drop database
        m = re.search(r'drop\s+database\s+(\w+)', text)
        if m:
            return f"DATABASE DROP {m.group(1)}"

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  LLM fallback  (Ollama)
    # ══════════════════════════════════════════════════════════════════════

    def _get_current_schema_as_string(self) -> str:
        """Return a human‑readable schema description of the current database."""
        try:
            sql_engine = self.catalog.get_current_sql_engine()
            nosql_engine = self.catalog.get_current_nosql_engine()
        except Exception:
            return "SQL tables: none. NoSQL collections: none."

        sql_tables = []
        for table_name, rows in sql_engine.storage.data.get("tables", {}).items():
            schema = sql_engine.storage.get_schema(table_name)
            if schema:
                cols = [col["name"] for col in schema]
                sql_tables.append(f"{table_name} ({', '.join(cols)})")

        nosql_collections = []
        for coll_name, docs in nosql_engine.storage.data.get("collections", {}).items():
            # Infer fields from first document (simple)
            fields = set()
            for doc in docs[:1]:
                fields.update(doc.keys())
            if fields:
                nosql_collections.append(f"{coll_name} ({', '.join(fields)})")
            else:
                nosql_collections.append(coll_name)

        result = ""
        if sql_tables:
            result += f"SQL tables: {', '.join(sql_tables)}. "
        else:
            result += "SQL tables: none. "
        if nosql_collections:
            result += f"NoSQL collections: {', '.join(nosql_collections)}."
        else:
            result += "NoSQL collections: none."
        return result

    def _llm_fallback(self, text: str) -> Optional[str]:
        """
        Few‑shot fallback using Ollama command‑line.
        Loads random examples from train.jsonl, injects current schema,
        and extracts the command, adding the correct prefix (SQL/NOSQL)
        based on the table/collection name or command keywords.
        """
        print("Call LLM")
        # ------------------------------------------------------------------
        # 1. Load and cache few‑shot examples
        # ------------------------------------------------------------------
        if not hasattr(self, '_few_shot_examples'):
            dataset_path = Path(__file__).parent.parent / "dataset" / "train.jsonl"
            if not dataset_path.exists():
                print(f"[NLP] Dataset not found: {dataset_path}", file=sys.stderr)
                return None
            with open(dataset_path, 'r', encoding='utf-8') as f:
                self._few_shot_examples = [json.loads(line) for line in f if line.strip()]
            if not self._few_shot_examples:
                print("[NLP] No valid examples in dataset", file=sys.stderr)
                return None

        # ------------------------------------------------------------------
        # 2. Pick 1‑2 random examples
        # ------------------------------------------------------------------
        num_shots = min(2, len(self._few_shot_examples))
        few_shot = random.sample(self._few_shot_examples, num_shots)

        # ------------------------------------------------------------------
        # 3. Build schema description
        # ------------------------------------------------------------------
        schema_str = self._get_current_schema_as_string()
        if not schema_str:
            schema_str = "SQL tables: none. NoSQL collections: none."

        # ------------------------------------------------------------------
        # 4. Build prompt (strong instruction to output prefix)
        # ------------------------------------------------------------------
        prompt_parts = []
        prompt_parts.append("You are a database assistant. Output ONLY the command – no explanation, no markdown. The command MUST start with 'SQL ' or 'NOSQL '.\n")
        for ex in few_shot:
            prompt_parts.append(
                f"### Schema: {ex['schema']}\n"
                f"### Question: {ex['question']}\n"
                f"### Command: {ex['output']}\n"
            )
        prompt_parts.append(
            f"### Schema: {schema_str}\n"
            f"### Question: {text}\n"
            f"### Command:"
        )
        full_prompt = "\n".join(prompt_parts)

        # ------------------------------------------------------------------
        # 5. Call ollama run
        # ------------------------------------------------------------------
        try:
            result = subprocess.run(
                ["ollama", "run", OLLAMA_MODEL],
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=OLLAMA_TIMEOUT,
                encoding='utf-8'
            )
        except subprocess.TimeoutExpired:
            print("[NLP] Ollama subprocess timed out", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[NLP] Ollama subprocess error: {e}", file=sys.stderr)
            return None

        if result.returncode != 0:
            print(f"[NLP] Ollama error (code {result.returncode}): {result.stderr}", file=sys.stderr)
            return None

        # ------------------------------------------------------------------
        # 6. Extract the raw command and add correct prefix
        # ------------------------------------------------------------------
        raw_output = result.stdout.strip()
        raw_command = None

        for line in raw_output.split('\n'):
            line_clean = line.strip()
            if not line_clean:
                continue
            upper = line_clean.upper()
            # If already has valid prefix, return as is
            if upper.startswith(("SQL ", "NOSQL ")):
                return line_clean.rstrip(';')
            # Otherwise capture the first line that looks like a command
            if upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE",
                                 "CREATE", "ALTER", "DROP", "WITH", "CALL",
                                 "NOSQL", "CONTAINS", "PUSH", "PULL", "ADDTOSET")):
                raw_command = line_clean
                break

        if not raw_command:
            # Fallback: use first non‑empty line
            first_line = raw_output.split('\n')[0].strip() if raw_output else None
            if not first_line:
                return None
            raw_command = first_line

        # ------------------------------------------------------------------
        # 7. Determine correct prefix (SQL or NOSQL)
        # ------------------------------------------------------------------
        # First, try to extract the first table/collection name from the command
        import re
        patterns = [
            r'\bFROM\s+([a-z_][a-z0-9_]*)',
            r'\bINTO\s+([a-z_][a-z0-9_]*)',
            r'\bUPDATE\s+([a-z_][a-z0-9_]*)',
            r'\bDELETE\s+FROM\s+([a-z_][a-z0-9_]*)',
            r'\bJOIN\s+([a-z_][a-z0-9_]*)',
            r'\bTABLE\s+([a-z_][a-z0-9_]*)',
            r'\bCOLLECTION\s+([a-z_][a-z0-9_]*)'
        ]
        table = None
        for pat in patterns:
            m = re.search(pat, raw_command, re.IGNORECASE)
            if m:
                table = m.group(1)
                break

        if table:
            sql_tables = self.get_sql_tables()
            if table in sql_tables:
                prefix = "SQL"
            else:
                prefix = "NOSQL"
        else:
            # Fallback: check for NoSQL‑specific keywords
            if re.search(r'\b(CONTAINS|PUSH|PULL|ADDTOSET)\b', raw_command, re.IGNORECASE):
                prefix = "NOSQL"
            else:
                prefix = "SQL"

        final_command = f"{prefix} {raw_command.rstrip(';')}"
        return final_command    

    # ══════════════════════════════════════════════════════════════════════
    #  Catalog helpers  (shared by generators & extractors)
    # ══════════════════════════════════════════════════════════════════════

    def get_known_tables(self) -> List[str]:
        """Return all table/collection names visible in the current database."""
        tables: list[str] = []
        try:
            sql_engine = self.catalog.get_current_sql_engine()
            tables.extend(sql_engine.storage.data.get("tables", {}).keys())
        except Exception:
            pass
        try:
            nosql_engine = self.catalog.get_current_nosql_engine()
            tables.extend(nosql_engine.storage.data.get("collections", {}).keys())
        except Exception:
            pass
        return tables

    def get_sql_tables(self) -> List[str]:
        """Return only SQL table names."""
        try:
            return list(
                self.catalog.get_current_sql_engine().storage.data.get("tables", {}).keys()
            )
        except Exception:
            return []

    def get_prefix(self, table: str) -> str:
        """Return ``"SQL"`` or ``"NOSQL"`` for the given table/collection."""
        return "SQL" if table in self.get_sql_tables() else "NOSQL"

    def get_table(self, text: str) -> Optional[str]:
        """Extract the table name from *text* using TableExtractor."""
        return self.table_ext.extract(text, self.get_known_tables())

    def get_column_names(self, table: str) -> List[str]:
        """Return column / field names for a table or collection."""
        # SQL schema
        try:
            sql_engine = self.catalog.get_current_sql_engine()
            schema = sql_engine.storage.get_schema(table)
            if schema:
                return [col["name"] for col in schema]
        except Exception:
            pass

        # NoSQL – infer from first document
        try:
            nosql_engine = self.catalog.get_current_nosql_engine()
            docs = nosql_engine.storage.data.get("collections", {}).get(table, [])
            if docs:
                first = docs[0]
                cols = list(first.keys())
                for key, val in first.items():
                    if isinstance(val, dict):
                        cols.extend(f"{key}.{sub}" for sub in val)
                return cols
        except Exception:
            pass

        # Generic fallback
        return ["id", "name", "age", "email", "city", "amount", "price", "quantity",
                "address.city", "address.zip", "address.street"]

    # ══════════════════════════════════════════════════════════════════════
    #  Utilities
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _normalise(text: str) -> str:
        """Lowercase, strip, collapse whitespace."""
        return re.sub(r'\s+', ' ', text.lower().strip())
    
    
    
        

    def _get_explanation_cache_path(self) -> Path:
        """Returns path to the persistent explanation cache JSON file."""
        return Path(__file__).parent.parent / "dataset" / "explanation_cache.json"

    def _load_explanation_cache(self) -> dict:
        """Load explanation cache from disk."""
        cache_path = self._get_explanation_cache_path()
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_explanation_cache(self, cache: dict) -> None:
        """Persist explanation cache to disk."""
        cache_path = self._get_explanation_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)

    def explain_command(self, command: str) -> Optional[str]:
        """
        Generate a plain-English explanation of a SQL/NoSQL command.
        
        Uses phi3.5 base model (not fine-tuned) via Ollama HTTP API.
        Caches results to disk keyed on command hash only.
        
        Parameters
        ----------
        command : str
            The SQL/NoSQL command to explain (e.g. "SQL SELECT AVG(age) FROM users")
        
        Returns
        -------
        str or None
            One-sentence plain English explanation, or None if it fails.
        """
        # ── 1. Check persistent cache first ──────────────────────────────
        cache_key = hashlib.md5(command.strip().encode()).hexdigest()
        cache = self._load_explanation_cache()
        
        if cache_key in cache:
            print(f"[Explain] Cache hit for command: {command[:50]}...")
            return cache[cache_key]

        # ── 2. Get current schema for context ────────────────────────────
        schema = self._get_current_schema_as_string()

        # ── 3. Build prompt ───────────────────────────────────────────────
        # Ending with "Explanation:" forces model to complete from there.
        # No task field needed — prompt structure is the instruction.
        prompt = (
            "You are a database assistant. "
            "Explain the following database command in one simple sentence. "
            "Use plain English. Do not repeat the command. Do not use technical jargon.\n\n"
            f"Database schema:\n{schema}\n\n"
            f"Command:\n{command}\n\n"
            "Explanation:"
        )

        # ── 4. Call Ollama HTTP API (not subprocess) ──────────────────────
        try:
            with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
                response = client.post(
                    f"{OLLAMA_URL}",
                    json={
                        "model": EXPLAIN_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 80,    # cap at ~80 tokens — one sentence is enough
                            "temperature": 0.1    # low temperature = consistent, factual output
                        }
                    }
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()

        except httpx.TimeoutException:
            print(f"[Explain] Ollama timeout for model {EXPLAIN_MODEL}", file=sys.stderr)
            return None
        except httpx.HTTPStatusError as e:
            print(f"[Explain] Ollama HTTP error: {e.response.status_code}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[Explain] Ollama error: {e}", file=sys.stderr)
            return None

        # ── 5. Clean output ───────────────────────────────────────────────
        # Take only the first sentence — model may ramble beyond one sentence
        explanation = self._extract_first_sentence(raw)
        if not explanation:
            print(f"[Explain] Empty output from model. Raw: {raw!r}", file=sys.stderr)
            return None

        # ── 6. Save to persistent cache ───────────────────────────────────
        cache[cache_key] = explanation
        self._save_explanation_cache(cache)
        print(f"[Explain] Generated and cached explanation for: {command[:50]}...")

        return explanation

    @staticmethod
    def _extract_first_sentence(text: str) -> Optional[str]:
        """
        Extract the first complete sentence from model output.
        Handles cases where model outputs multiple sentences or extra whitespace.
        """
        if not text:
            return None
        
        # Strip any leading labels the model might echo (e.g. "Explanation: ...")
        text = re.sub(r'^explanation\s*:\s*', '', text, flags=re.IGNORECASE).strip()
        
        # Split on sentence-ending punctuation
        sentences = re.split(r'(?<=[.!?])\s+', text)
        first = sentences[0].strip() if sentences else text.strip()
        
        # Ensure it ends with punctuation
        if first and first[-1] not in '.!?':
            first += '.'
        
        return first if len(first) > 10 else None  # reject garbage like "Ok." or "Done."
    
    


    def _load_error_fix_examples(self) -> list:
        """
        Load and cache few-shot error fix examples from error_fixes.jsonl.
        Cached in memory after first load — file is read once per server session.
        """
        if not hasattr(self, '_error_fix_examples'):
            path = Path(__file__).parent.parent / "dataset" / "error_fixes.jsonl"
            examples = []
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                examples.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            self._error_fix_examples = examples
            print(f"[ErrorFixer] Loaded {len(examples)} few-shot examples")
        return self._error_fix_examples

    def _select_relevant_examples(self, error_message: str, failed_query: str, n: int = 5) -> list:
        """
        Select the most relevant few-shot examples based on error type similarity.
        Avoids picking the first N blindly — matches by error pattern instead.
        """
        examples = self._load_error_fix_examples()
        if not examples:
            return []

        # Score each example by relevance to current error
        scored = []
        error_lower = error_message.lower()
        query_lower = failed_query.lower()

        for ex in examples:
            score = 0
            ex_error = ex.get('error', '').lower()
            ex_query = ex.get('failed_query', '').lower()

            # Error type match — most important signal
            if "does not exist" in error_lower and "does not exist" in ex_error:
                score += 3
            if "column" in error_lower and "column" in ex_error:
                score += 2
            if "table" in error_lower and "table" in ex_error:
                score += 2
            if "collection" in error_lower and "collection" in ex_error:
                score += 2
            if "syntax" in error_lower and "syntax" in ex_error:
                score += 2

            # Query type match
            for keyword in ["select", "insert", "update", "delete", "nosql"]:
                if keyword in query_lower and keyword in ex_query:
                    score += 1

            scored.append((score, ex))

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:n]]

    # def _extract_fixed_query(self, raw_output: str) -> Optional[str]:
    #     """
    #     Extract a valid SQL/NOSQL command from raw model output.
    #     Handles cases where model echoes 'Fixed:' or outputs extra text.
    #     """
    #     if not raw_output:
    #         return None

    #     for line in raw_output.strip().split('\n'):
    #         line = line.strip()
    #         if not line:
    #             continue

    #         # Strip any "Fixed:" label the model might echo
    #         line = re.sub(r'^fixed\s*:\s*', '', line, flags=re.IGNORECASE).strip()

    #         upper = line.upper()

    #         # Must start with SQL or NOSQL prefix
    #         if upper.startswith("SQL ") or upper.startswith("NOSQL "):
    #             return line.rstrip(';')

    #         # Model forgot the prefix — add it based on command type
    #         if upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE",
    #                             "CREATE", "ALTER", "DROP")):
    #             return f"SQL {line.rstrip(';')}"

    #     return None

    def _validate_fixed_query(self, fixed_query: str) -> tuple[bool, str]:
        """
        Basic validation before executing a model-generated fix.
        Returns (is_valid, reason).
        """
        if not fixed_query:
            return False, "Empty query"

        upper = fixed_query.upper().strip()

        # Must have valid prefix
        if not (upper.startswith("SQL ") or upper.startswith("NOSQL ")):
            return False, "Missing SQL/NOSQL prefix"

        # Must have a recognizable command keyword
        keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"]
        if not any(kw in upper for kw in keywords):
            return False, "No recognizable SQL command found"

        # Sanity length check — model hallucinations tend to be very long
        if len(fixed_query) > 1000:
            return False, "Query suspiciously long — possible hallucination"

        return True, "OK"

    def _get_fix_cache_path(self) -> Path:
        return Path(__file__).parent.parent / "dataset" / "fix_cache.json"

    def _load_fix_cache(self) -> dict:
        path = self._get_fix_cache_path()
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_fix_cache(self, cache: dict) -> None:
        path = self._get_fix_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)

    def fix_failed_query(self, failed_query: str, error_message: str) -> Optional[str]:
        """
        Attempt to fix a failed SQL/NoSQL query using few-shot LLM correction.
        Uses the base model (phi35:latest) for better generalization.
        """
        # ── 1. Normalise query (remove prefix for matching) ──────────────
        failed_query_norm = re.sub(r'^(SQL|NOSQL)\s+', '', failed_query.strip(), flags=re.IGNORECASE).strip()

        # ── 2. Cache check ───────────────────────────────────────────────
        cache_key = hashlib.md5(
            f"{failed_query_norm}||{error_message.strip()}".encode()
        ).hexdigest()
        cache = self._load_fix_cache()
        if cache_key in cache:
            print(f"[ErrorFixer] Cache hit: {failed_query_norm[:50]}")
            return cache[cache_key]

        # ── 3. Get current schema ────────────────────────────────────────
        schema = self._get_current_schema_as_string()

        # ── 4. Select relevant few-shot examples ─────────────────────────
        examples = self._select_relevant_examples(error_message, failed_query_norm, n=5)

        # ── 5. Build prompt (use original failed query with prefix) ──────
        prompt_parts = [
            "You are a database assistant. Fix the failed SQL/NoSQL query.\n"
            "Output ONLY the fixed query starting with SQL or NOSQL. No explanation.\n"
        ]
        for ex in examples:
            prompt_parts.append(
                f"Failed: {ex['failed_query']}\n"
                f"Error: {ex['error']}\n"
                f"Schema: {ex['schema']}\n"
                f"Fixed: {ex['fixed']}\n"
            )
        prompt_parts.append(
            f"Failed: {failed_query}\n"
            f"Error: {error_message}\n"
            f"Schema: {schema}\n"
            f"Fixed:"
        )
        full_prompt = "\n".join(prompt_parts)

        # ── 6. Call Ollama API (use OLLAMA_URL directly, no extra path) ──
        try:
            with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
                response = client.post(
                    OLLAMA_URL,          # ✅ correct – OLLAMA_URL already includes /api/generate
                    json={
                        "model": OLLAMA_MODEL,   # ✅ base model (phi35:latest)
                        "prompt": full_prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 150,
                            "temperature": 0.1
                        }
                    }
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                print(f"[ErrorFixer] Raw output: {raw[:200]}")
        except httpx.TimeoutException:
            print("[ErrorFixer] Ollama timeout", file=sys.stderr)
            return None
        except httpx.HTTPStatusError as e:
            print(f"[ErrorFixer] HTTP error: {e.response.status_code}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[ErrorFixer] Error: {e}", file=sys.stderr)
            return None

        # ── 7. Extract and validate ──────────────────────────────────────
        fixed = self._extract_fixed_query(raw)
        if not fixed:
            print(f"[ErrorFixer] Could not extract query from: {raw!r}", file=sys.stderr)
            return None

        # Ensure prefix exists
        if not fixed.upper().startswith(("SQL ", "NOSQL ")):
            if fixed.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")):
                fixed = f"SQL {fixed}"
            else:
                fixed = f"SQL {fixed}"

        is_valid, reason = self._validate_fixed_query(fixed)
        if not is_valid:
            print(f"[ErrorFixer] Validation failed: {reason} — query: {fixed!r}", file=sys.stderr)
            return None

        # ── 8. Cache and return ──────────────────────────────────────────
        cache[cache_key] = fixed
        self._save_fix_cache(cache)
        print(f"[ErrorFixer] Fixed: {failed_query[:50]} → {fixed[:50]}")
        return fixed


    def _extract_fixed_query(self, raw_output: str) -> Optional[str]:
        """
        Extract a valid SQL/NOSQL command from raw model output.
        Handles cases where model echoes 'Fixed:' or outputs extra text.
        """
        if not raw_output:
            return None

        for line in raw_output.strip().split('\n'):
            line = line.strip()
            if not line:
                continue

            # Strip common labels like "Fixed:", "Answer:", etc.
            line = re.sub(r'^(fixed|answer|correction|corrected|suggestion)\s*:\s*', '', line, flags=re.IGNORECASE).strip()

            upper = line.upper()

            # Already has correct prefix
            if upper.startswith("SQL ") or upper.startswith("NOSQL "):
                return line.rstrip(';')

            # Missing prefix – add SQL prefix for typical SQL commands
            if upper.startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")):
                return f"SQL {line.rstrip(';')}"

        # Fallback: first line that contains a SQL keyword
        for line in raw_output.strip().split('\n'):
            line = line.strip()
            if line and any(kw in line.upper() for kw in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE")):
                return f"SQL {line.rstrip(';')}"

        return None
        
    def suggest_full_queries(self, partial_query: str, limit: int = 3) -> list:
        # Remove any existing "sql " or "nosql " prefix
        clean_partial = re.sub(r'^(sql|nosql)\s+', '', partial_query.strip(), flags=re.IGNORECASE)
        
        schema = self._get_current_schema_as_string()
        
        # Very strict prompt – instruct to output ONLY the command, nothing else
        prompt = (
            f"Schema: {schema}\n"
            f"Question: Complete the following SQL/NoSQL query: {clean_partial}\n"
            f"Output (only the command, no extra words):"
        )
        
        try:
            with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
                response = client.post(
                    OLLAMA_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.0,
                            "num_predict": 80,      # shorter output
                            "stop": ["\n", "."]     # stop at newline or period
                        }
                    }
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                print(f"[Suggest] Raw: {raw}")
        except Exception as e:
            print(f"[Suggest] Error: {e}")
            return []
        
        # Take only the first line that resembles a command
        commands = []
        for line in raw.split('\n'):
            line = line.strip().rstrip(';')
            if not line:
                continue
            # If it starts with SQL/NOSQL, great
            if line.upper().startswith(("SQL ", "NOSQL ")):
                commands.append(line)
                break
            # If it starts with SELECT/INSERT/etc., add SQL prefix
            if line.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")):
                prefix = "NOSQL" if re.search(r'\b(CONTAINS|PUSH|PULL|ADDTOSET)\b', line, re.IGNORECASE) else "SQL"
                commands.append(f"{prefix} {line}")
                break
            # If it's a short completion like "= 1", prepend the original partial
            if re.match(r'^[=<>\s\d\w\'\"\(\)]+$', line) and len(line) < 30:
                full_cmd = f"SQL {clean_partial} {line}".strip()
                commands.append(full_cmd)
                break
        
        return commands[:limit]