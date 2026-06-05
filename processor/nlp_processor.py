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
    ├─ 2. rule-based  →  intent classify  →  dispatch to correct generator
    └─ 3. LLM fallback (Ollama) if rule-based returns None

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
from .constants import COLUMN_SYNONYMS, OLLAMA_MODEL, OLLAMA_URL, OLLAMA_TIMEOUT
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
        """
        Convert a natural-language query to an engine command string.

        Returns a command string (e.g. ``"SQL SELECT * FROM users"``)
        or ``None`` if the query could not be understood.
        """
        normalised = self._normalise(text)
        command = self._rule_based(normalised)
        if command:
            return command
        return self._llm_fallback(text)  # LLM gets original casing

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