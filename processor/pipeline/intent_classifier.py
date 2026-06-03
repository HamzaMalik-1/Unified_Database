"""
pipeline/intent_classifier.py

Classifies a normalised (lower-cased, stripped) natural-language string
into one of the engine's known intents.

Priority order:
  1. Specific DDL phrases  (longest-match first)
  2. Compound DDL keywords ("create table", "drop collection", …)
  3. ALTER TABLE patterns ("add column", "drop column", "rename column", "rename table", …)
  4. General CRUD keywords

Returns a string intent token or None if unrecognised.
"""

from __future__ import annotations
import re
from typing import Optional

from ..constants import (
    DDL_PATTERNS,
    SELECT_KEYWORDS,
    INSERT_KEYWORDS,
    UPDATE_KEYWORDS,
    DELETE_KEYWORDS,
)

# ── Intent tokens ────────────────────────────────────────────────────────────
SELECT              = "SELECT"
INSERT              = "INSERT"
UPDATE              = "UPDATE"
DELETE              = "DELETE"
DROP_TABLE          = "DROP_TABLE"
DROP_COLLECTION     = "DROP_COLLECTION"
CREATE_TABLE        = "CREATE_TABLE"
CREATE_COLLECTION   = "CREATE_COLLECTION"

# DDL tokens (handled separately by DDLGenerator)
CREATE_INDEX        = "CREATE_INDEX"
DROP_INDEX          = "DROP_INDEX"
CREATE_TEXT_INDEX   = "CREATE_TEXT_INDEX"
DROP_TEXT_INDEX     = "DROP_TEXT_INDEX"
ALTER_TABLE         = "ALTER_TABLE"
CREATE_VIEW         = "CREATE_VIEW"
DROP_VIEW           = "DROP_VIEW"
CREATE_PROCEDURE    = "CREATE_PROCEDURE"
DROP_PROCEDURE      = "DROP_PROCEDURE"
CREATE_TRIGGER      = "CREATE_TRIGGER"
DROP_TRIGGER        = "DROP_TRIGGER"

_DDL_TOKEN_MAP: dict[str, str] = {
    "create text index": CREATE_TEXT_INDEX,
    "drop text index":   DROP_TEXT_INDEX,
    "create index":      CREATE_INDEX,
    "drop index":        DROP_INDEX,
    "alter table":       ALTER_TABLE,
    "create view":       CREATE_VIEW,
    "drop view":         DROP_VIEW,
    "create procedure":  CREATE_PROCEDURE,
    "drop procedure":    DROP_PROCEDURE,
    "create trigger":    CREATE_TRIGGER,
    "drop trigger":      DROP_TRIGGER,
}

# Sort by descending phrase length so "create text index" matches before "create index"
_DDL_SORTED = sorted(_DDL_TOKEN_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)

# Patterns that indicate an ALTER TABLE operation (must be caught before DELETE/DROP)
_ALTER_PATTERNS = [
    r'\badd\s+column\b',
    r'\bdrop\s+column\b',
    r'\brename\s+column\b',
    r'\brename\s+table\b',
    r'\balter\s+column\s+\w+\s+(set|drop)\s+default\b',
    r'\badd\s+check\b',
]


class IntentClassifier:
    """
    Stateless classifier – call ``classify(text)`` with a pre-normalised string.
    """

    def classify(self, text: str) -> Optional[str]:
        """Return an intent token or None."""

        # ── 1. DDL phrase match ──────────────────────────────────────────────
        for phrase, token in _DDL_SORTED:
            if phrase in text:
                return token

        # ── 2. Compound DDL  ─────────────────────────────────────────────────
        if "create table" in text:
            return CREATE_TABLE
        if "create collection" in text:
            return CREATE_COLLECTION

        # ── 3. ALTER TABLE patterns (must be checked before DELETE/DROP) ──────
        for pattern in _ALTER_PATTERNS:
            if re.search(pattern, text):
                return ALTER_TABLE

        # DROP TABLE vs DROP COLLECTION vs general DELETE ────────────────────
        if any(kw in text for kw in ("delete", "remove", "erase", "purge", "drop")):
            if "table" in text:
                return DROP_TABLE
            if "collection" in text:
                return DROP_COLLECTION
            # "drop" alone without table/collection → still a DELETE intent
            return DELETE

        # ── 4. CRUD keywords ──────────────────────────────────────────────────
        for kw in SELECT_KEYWORDS:
            if re.search(rf'\b{re.escape(kw)}\b', text):
                return SELECT

        for kw in INSERT_KEYWORDS:
            if re.search(rf'\b{re.escape(kw)}\b', text):
                return INSERT

        for kw in UPDATE_KEYWORDS:
            if re.search(rf'\b{re.escape(kw)}\b', text):
                return UPDATE

        return None