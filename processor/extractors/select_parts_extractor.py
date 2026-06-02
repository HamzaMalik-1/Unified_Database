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

from ..constants import AGGREGATE_PATTERNS, COLUMN_SYNONYMS,GROUP_BY_STOP_WORDS


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
        idx = text.lower().find(table.lower())
        if idx == -1:
            return None
        before = text[:idx]

        candidates = re.findall(r'\b([a-z_][a-z0-9_.]*)\b', before, re.IGNORECASE)
        known_lower = {c.lower(): c for c in known_cols}

        cols: list[str] = []
        for c in candidates:
            cl = c.lower()
            if cl in known_lower:
                cols.append(known_lower[cl])
            # Don't convert aggregate/business words like
# "sales", "revenue", etc. into actual columns here.
# Column extraction should only return columns
# explicitly requested by the user.
            elif (
                    cl in COLUMN_SYNONYMS
                    and COLUMN_SYNONYMS[cl] in known_lower.values()
                    and COLUMN_SYNONYMS[cl].lower() == cl
                ):
                    cols.append(COLUMN_SYNONYMS[cl])
            elif "." in c:
                cols.append(c)

        seen: set[str] = set()
        unique = [c for c in cols if not (c in seen or seen.add(c))]
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
        text_l = text.lower().strip()

        results: list[tuple[str, str, str]] = []

        # --------------------------------------------------
        # COUNT(*)
        # --------------------------------------------------
        if re.search(
            r'\b(count of all|count of rows|total rows|total count)\b',
            text_l
        ):
            return [("COUNT", "*", "total")]

        # --------------------------------------------------
        # COUNT(*)
        # --------------------------------------------------
        if re.search(r'count\s*\(\s*\*\s*\)', text_l):
            results.append(("COUNT", "*", "total"))

        # --------------------------------------------------
        # Standard aggregate patterns
        # --------------------------------------------------
        for pattern, func in AGGREGATE_PATTERNS:

            for m in re.finditer(pattern, text, re.IGNORECASE):

                col = m.group(1)

                # Don't convert user column names
                # such as "sales" into "amount"
                alias = f"{col}_{func.lower()}"

                results.append((func, col, alias))

        seen = set()
        unique = []

        for item in results:
            if item not in seen:
                seen.add(item)
                unique.append(item)

        return unique
    # ──────────────────────────────────────────────────────────────────────
    #  HAVING
    # ──────────────────────────────────────────────────────────────────────

    def extract_having(self, text: str) -> Optional[str]:
        # Search for HAVING anywhere in the text
        m = re.search(
            r'\bhaving\s+(.+?)(?:\s+order\s+by|\s+limit|\s+offset|$)',
            text,
            re.IGNORECASE,
        )
        if not m:
            return None
        cond = m.group(1).strip()
        # Reject false positives: if the captured condition is a single word
        # that is not a number and contains no operators, it's not a real HAVING clause.
        if re.match(r'^[a-zA-Z_]+$', cond) and not cond.isdigit():
            return None
        # Normalise natural language operators
        cond = re.sub(r'greater\s+than\s+(\d+)', r'> \1', cond, flags=re.IGNORECASE)
        cond = re.sub(r'([a-z_]+)\s+is\s+', r'\1 = ', cond, flags=re.IGNORECASE)
        return cond

    # ──────────────────────────────────────────────────────────────────────
    #  ORDER BY  →  list of (col, direction)
    # ──────────────────────────────────────────────────────────────────────

    def extract_order_by(self, text: str) -> Optional[List[Tuple[str, str]]]:
        m = re.search(r'\border\s+by\s+(.+?)(?:\s+limit|\s+offset|$)', text)
        if not m:
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
        m = re.search(r'\b(?:top|first)\s+(\d+)\b', text)
        if m:
            return int(m.group(1))
        m = re.search(r'\blimit\s+(\d+)\b', text)
        if m:
            return int(m.group(1))
        m = re.search(r'\bfetch\s+first\s+(\d+)\s+rows?\s+only\b', text)
        if m:
            return int(m.group(1))
        return None

    # ──────────────────────────────────────────────────────────────────────
    #  OFFSET
    # ──────────────────────────────────────────────────────────────────────

    def extract_offset(self, text: str) -> Optional[int]:
        # Match "offset <number>" anywhere, even after "limit"
        match = re.search(r'\boffset\s+(\d+)\b', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

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

        other_table: Optional[str] = None
        for t in sorted(known_tables, key=len, reverse=True):
            if t != primary_table and re.search(rf'\b{re.escape(t)}\b', text, re.IGNORECASE):
                other_table = t
                break

        if not other_table:
            return None

        join_type = "INNER"
        for phrase, jtype in sorted(JOIN_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
            if phrase in text:
                join_type = jtype
                break

        if re.search(r'\beven\s+if\s+no\b', text):
            join_type = "LEFT"

        # For NATURAL and CROSS joins, there is no ON clause – condition should be None
        if join_type in ("NATURAL", "CROSS"):
            on_cond = None
        else:
            on_m = re.search(r'\bon\s+([\w.]+)\s*=\s*([\w.]+)', text)
            if on_m:
                on_cond = f"{on_m.group(1)} = {on_m.group(2)}"
            else:
                on_cond = f"{primary_table}.id = {other_table}.{primary_table}_id"

        return (join_type, other_table, on_cond)
    
    
    def extract_group_by(self, text: str) -> Optional[str]:
        m = re.search(r'\bgroup\s+by\s+(\w+)', text)
        if m:
            candidate = m.group(1)
            # Only return the column if it's not a stop word
            if candidate.lower() not in GROUP_BY_STOP_WORDS:
                return candidate
        m = re.search(r'\bper\s+(\w+)\b', text)
        if m:
            return m.group(1)
        return None