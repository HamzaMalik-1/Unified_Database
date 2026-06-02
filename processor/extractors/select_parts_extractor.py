"""
extractors/select_parts_extractor.py

Extracts the SELECT-clause components from a natural-language query:
  • Column list
  • DISTINCT flag
  • Aggregate functions (COUNT / SUM / AVG / MIN / MAX)
  • GROUP BY column
  • HAVING clause
  • ORDER BY column(s) + direction
  • LIMIT / OFFSET
"""

from __future__ import annotations
import re
from typing import Optional, List, Tuple

from ..constants import AGGREGATE_PATTERNS, COLUMN_SYNONYMS


class SelectPartsExtractor:
    """
    All helpers return None / False when the feature is absent in the query.
    Call them independently; SelectGenerator composes the results.
    """

    # ──────────────────────────────────────────────────────────────────────
    #  Column list
    # ──────────────────────────────────────────────────────────────────────

    def extract_columns(
        self,
        text: str,
        table: str,
        known_cols: List[str],
    ) -> Optional[List[str]]:
        """
        Return the explicit column list mentioned before the table name,
        or None if only '*' is implied.
        """
        # Everything *before* the table name is the candidate region
        idx = text.lower().find(table.lower())
        if idx == -1:
            return None
        before = text[:idx]

        # Grab word tokens that can include dots (for nested fields)
        candidates = re.findall(r'\b([a-z_][a-z0-9_.]*)\b', before, re.IGNORECASE)
        known_lower = {c.lower(): c for c in known_cols}

        cols: list[str] = []
        for c in candidates:
            cl = c.lower()
            # Direct match
            if cl in known_lower:
                cols.append(known_lower[cl])
            # Synonym
            elif cl in COLUMN_SYNONYMS and COLUMN_SYNONYMS[cl] in known_lower.values():
                cols.append(COLUMN_SYNONYMS[cl])
            # Dot-notation nested field
            elif "." in c:
                cols.append(c)

        # Preserve order, remove duplicates
        seen: set[str] = set()
        unique = [c for c in cols if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]
        return unique if unique else None

    # ──────────────────────────────────────────────────────────────────────
    #  DISTINCT
    # ──────────────────────────────────────────────────────────────────────

    def extract_distinct(self, text: str) -> bool:
        return bool(re.search(r'\b(distinct|unique)\b', text))

    # ──────────────────────────────────────────────────────────────────────
    #  Aggregates  →  list of (func, col, alias)
    # ──────────────────────────────────────────────────────────────────────

    def extract_aggregates(self, text: str) -> List[Tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        for pattern, func in AGGREGATE_PATTERNS:
            for m in re.finditer(pattern, text):
                col = m.group(1)
                col = COLUMN_SYNONYMS.get(col, col)
                alias = f"{col}_{func.lower()}"
                results.append((func, col, alias))

        if re.search(r'\b(count of all|count of rows|total rows|total count)\b', text):
            results.append(("COUNT", "*", "total"))

        # Deduplicate
        seen: set[tuple[str, str, str]] = set()
        return [r for r in results if not (r in seen or seen.add(r))]  # type: ignore[func-returns-value]

    # ──────────────────────────────────────────────────────────────────────
    #  GROUP BY
    # ──────────────────────────────────────────────────────────────────────

    def extract_group_by(self, text: str) -> Optional[str]:
        m = re.search(r'\bgroup\s+by\s+(\w+)', text)
        if m:
            return m.group(1)
        m = re.search(r'\bper\s+(\w+)\b', text)
        if m:
            return m.group(1)
        return None

    # ──────────────────────────────────────────────────────────────────────
    #  HAVING
    # ──────────────────────────────────────────────────────────────────────

    def extract_having(self, text: str) -> Optional[str]:
        m = re.search(r'\bhaving\s+(.+?)(?:\s+order\s+by|\s+limit|$)', text)
        if not m:
            return None
        cond = m.group(1).strip()
        # Normalise natural language ops
        cond = re.sub(r'greater\s+than\s+(\d+)', r'> \1', cond)
        cond = re.sub(r'less\s+than\s+(\d+)',    r'< \1', cond)
        cond = re.sub(r'([a-z_]+)\s+is\s+',      r'\1 = ', cond)
        return cond

    # ──────────────────────────────────────────────────────────────────────
    #  ORDER BY  →  list of (col, direction)
    # ──────────────────────────────────────────────────────────────────────

    def extract_order_by(self, text: str) -> Optional[List[Tuple[str, str]]]:
        m = re.search(r'\border\s+by\s+(.+?)(?:\s+limit|\s+offset|$)', text)
        if not m:
            # Natural language: "sorted by X descending"
            m2 = re.search(r'\bsorted\s+by\s+(\w+)(?:\s+(asc(?:ending)?|desc(?:ending)?))?', text)
            if m2:
                col = m2.group(1)
                raw_dir = m2.group(2) or "asc"
                direction = "DESC" if raw_dir.lower().startswith("desc") else "ASC"
                return [(col, direction)]
            return None

        order_part = m.group(1).strip()
        specs: list[tuple[str, str]] = []
        for part in order_part.split(","):
            part = part.strip()
            pm = re.match(r'^(\w+)\s*(asc(?:ending)?|desc(?:ending)?)?$', part, re.IGNORECASE)
            if pm:
                col = pm.group(1)
                raw_dir = pm.group(2) or "asc"
                direction = "DESC" if raw_dir.lower().startswith("desc") else "ASC"
                specs.append((col, direction))
        return specs if specs else None

    # ──────────────────────────────────────────────────────────────────────
    #  LIMIT
    # ──────────────────────────────────────────────────────────────────────

    def extract_limit(self, text: str) -> Optional[int]:
        # "top N" / "first N"
        m = re.search(r'\b(?:top|first)\s+(\d+)\b', text)
        if m:
            return int(m.group(1))
        # "limit N"
        m = re.search(r'\blimit\s+(\d+)\b', text)
        if m:
            return int(m.group(1))
        # "fetch first N rows only"
        m = re.search(r'\bfetch\s+first\s+(\d+)\s+rows?\s+only\b', text)
        if m:
            return int(m.group(1))
        return None

    # ──────────────────────────────────────────────────────────────────────
    #  OFFSET
    # ──────────────────────────────────────────────────────────────────────

    def extract_offset(self, text: str) -> Optional[int]:
        m = re.search(r'\boffset\s+(\d+)\b', text)
        return int(m.group(1)) if m else None

    # ──────────────────────────────────────────────────────────────────────
    #  JOIN detection  →  (join_type, other_table, on_condition) or None
    # ──────────────────────────────────────────────────────────────────────

    def extract_join(
        self,
        text: str,
        primary_table: str,
        known_tables: List[str],
    ) -> Optional[Tuple[str, str, str]]:
        from ..constants import JOIN_ALIASES

        # Find an explicitly mentioned second table
        other_table: Optional[str] = None
        for t in sorted(known_tables, key=len, reverse=True):
            if t != primary_table and re.search(rf'\b{re.escape(t)}\b', text, re.IGNORECASE):
                other_table = t
                break

        if not other_table:
            return None

        # Determine join type
        join_type = "INNER"
        for phrase, jtype in sorted(JOIN_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
            if phrase in text:
                join_type = jtype
                break

        # Natural-language hint: "even if no <table>" → LEFT JOIN
        if re.search(r'\beven\s+if\s+no\b', text):
            join_type = "LEFT"

        # ON condition
        on_m = re.search(r'\bon\s+([\w.]+)\s*=\s*([\w.]+)', text)
        if on_m:
            on_cond = f"{on_m.group(1)} = {on_m.group(2)}"
        else:
            on_cond = f"{primary_table}.id = {other_table}.{primary_table}_id"

        return (join_type, other_table, on_cond)
