# backend/processor/condition_extractor.py

"""
Extracts WHERE / HAVING conditions from a natural-language query and
converts them to SQL predicate strings.

Supports:
  • Comparison operators  (>, <, >=, <=, =, !=)
  • NULL / NOT NULL checks
  • BETWEEN … AND … / NOT BETWEEN
  • IN (…) / NOT IN (…)
  • LIKE / NOT LIKE
  • Date expressions  (today, yesterday, last week, …)
  • Word numbers      ("twenty five" → 25 via word2number)
  • OR / AND chaining
  • "with id N", "id N" shorthand
"""

from __future__ import annotations
import re
from typing import Optional

from ..constants import DATE_KEYWORDS, COMPARISON_ALIASES

try:
    from word2number import w2n as _w2n
    def _to_num(val: str) -> str:
        try:
            return str(_w2n.word_to_num(val))
        except Exception:
            return val
except ImportError:
    def _to_num(val: str) -> str:
        return val


def _quote(val: str) -> str:
    """Wrap in single quotes if not numeric / SQL keyword."""
    if val.lstrip("-").isdigit():
        return val
    if val.upper() in {"CURRENT_DATE", "CURRENT_TIMESTAMP", "NULL", "TRUE", "FALSE"}:
        return val
    return f"'{val}'"


def _parse_date(expr: str) -> str:
    expr = expr.strip().lower()
    # Remove surrounding quotes if any
    if expr.startswith("'") and expr.endswith("'"):
        expr = expr[1:-1]
    # Match longest keyword first
    for word, sql_expr in sorted(DATE_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if word in expr:
            return sql_expr
    return _quote(expr)


class ConditionExtractor:
    """
    Stateless extractor.  Call ``extract(text)`` → SQL condition string or None.
    """

    def extract(self, text: str) -> Optional[str]:
        """
        Parse all conditions present in *text* and return an SQL predicate.
        Handles top-level OR first (lower precedence), then AND inside each OR branch.
        """
        text = re.sub(r'\bwhose\b', 'where', text, flags=re.IGNORECASE)
        # Remove a leading "where" if present (e.g., "orders where ...")
        text = re.sub(r'^.*?\bwhere\b\s+', '', text, flags=re.IGNORECASE)

        # ---------- Step 1: Extract BETWEEN / NOT BETWEEN clauses ----------
        # They contain the word "and" but must not be split. We replace them with
        # placeholders and store their SQL representation.
        between_placeholders = {}   # placeholder -> sql_string
        between_index = 0

        def replace_between(m):
            nonlocal between_index
            placeholder = f"__BETWEEN_{between_index}__"
            # Build SQL condition
            col = m.group(1)
            not_flag = m.group(2) is not None
            lo = m.group(3).strip()
            hi = m.group(4).strip()
            # Convert lo and hi (may be numbers, words, or date expressions)
            lo_sql = _parse_date(lo)
            hi_sql = _parse_date(hi)
            if not_flag:
                sql_cond = f"{col} NOT BETWEEN {lo_sql} AND {hi_sql}"
            else:
                sql_cond = f"{col} BETWEEN {lo_sql} AND {hi_sql}"
            between_placeholders[placeholder] = sql_cond
            between_index += 1
            return placeholder

        # Pattern for BETWEEN and NOT BETWEEN (captures whole clause)
        # Stops at next "and" or "or" or end of string
        pattern = r'\b(\w+)\s+(not\s+)?between\s+(.+?)\s+and\s+(.+?)(?=\s+(?:and|or)\s+\w+|\s*$)'
        text = re.sub(pattern, replace_between, text, flags=re.IGNORECASE)

        # ---------- Step 2: Split on top-level OR ----------
        if " or " in text:
            parts = re.split(r'\s+or\s+', text)
            conds = []
            for part in parts:
                sub = self._extract_no_between(part, between_placeholders)
                if sub:
                    conds.append(sub)
            # No need to restore placeholders – they have already been expanded in _extract_no_between
            return " OR ".join(conds)

        # ---------- Step 3: No OR → process AND or single clause ----------
        result = self._extract_no_between(text, between_placeholders)
        return result

    def _extract_no_between(self, text: str, between_placeholders: dict) -> Optional[str]:
        """
        Extract conditions from text that has no BETWEEN placeholders.
        The placeholders are expanded inline.
        """
        # Split on AND
        if " and " in text:
            parts = re.split(r'\s+and\s+', text)
            conds = []
            for part in parts:
                # If the part is a placeholder, use its stored SQL
                if part in between_placeholders:
                    conds.append(between_placeholders[part])
                else:
                    sub = self._extract_single(part)
                    if sub:
                        conds.append(sub)
            return " AND ".join(conds) if conds else None
        else:
            # Single clause – might be a placeholder or a normal clause
            if text in between_placeholders:
                return between_placeholders[text]
            return self._extract_single(text)

    # ----------------------------------------------------------------------
    #  Extract conditions from a single clause (no AND/OR at top level)
    # ----------------------------------------------------------------------
    def _extract_single(self, text: str) -> Optional[str]:
        conditions = []
        remaining = text

        # ── "with id N" / "id N" shorthand ───────────────────────────────
        m = re.search(r'\bwith\s+id\s+(\w+)', remaining)
        if m:
            conditions.append(f"id = {_quote(_to_num(m.group(1)))}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        m = re.search(r'\bid\s+(\d+)\b', remaining)
        if m:
            conditions.append(f"id = {m.group(1)}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── NULL handling (remove matched parts) ─────────────────────────
        null_patterns = [
            (r'\b(\w+)\s+is\s+(?:missing|empty|null|none|blank)\b', "{} IS NULL"),
            (r'\b(\w+)\s+is\s+not\s+(?:missing|empty|null|none|blank)\b', "{} IS NOT NULL"),
            (r'\bno\s+(\w+)\b', "{} IS NULL"),
            (r'\bwithout\s+(\w+)\b', "{} IS NULL"),
        ]
        for pattern, template in null_patterns:
            m = re.search(pattern, remaining)
            if m:
                col = m.group(1)
                conditions.append(template.format(col))
                remaining = remaining[:m.start()] + remaining[m.end():]

        # ── NOT BETWEEN (direct, not placeholder) – should not appear here, but keep for safety ──
        m = re.search(r'\b(\w+)\s+not\s+between\s+(.+?)\s+and\s+(.+?)(?=\s+and\s+|\s*$)', remaining)
        if m:
            col, lo, hi = m.group(1), _parse_date(m.group(2)), _parse_date(m.group(3))
            conditions.append(f"{col} NOT BETWEEN {lo} AND {hi}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── BETWEEN (direct) ─────────────────────────────────────────────
        m = re.search(r'\b(\w+)\s+between\s+(.+?)\s+and\s+(.+?)(?=\s+and\s+|\s*$)', remaining)
        if m:
            col, lo, hi = m.group(1), _parse_date(m.group(2)), _parse_date(m.group(3))
            conditions.append(f"{col} BETWEEN {lo} AND {hi}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── NOT IN (with parentheses) – compact output (no spaces) ────────
        m = re.search(r'\b(\w+)\s+in\s+\(([^)]+)\)', remaining)
        if m:
            col = m.group(1)
            vals = ",".join(v.strip() for v in m.group(2).split(","))
            conditions.append(f"{col} IN ({vals})")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── IN (with parentheses) – compact output (no spaces) ────────────
        m = re.search(r'\b(\w+)\s+in\s+\(([^)]+)\)', remaining)
        if m:
            col = m.group(1)
            vals = ", ".join(v.strip().strip("'\"") for v in m.group(2).split(","))
            conditions.append(f"{col} IN ({vals})")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── NOT LIKE ──────────────────────────────────────────────────────
        m = re.search(r"\b(\w+)\s+not\s+like\s+'([^']+)'", remaining)
        if m:
            conditions.append(f"{m.group(1)} NOT LIKE '{m.group(2)}'")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── LIKE ──────────────────────────────────────────────────────────
        m = re.search(r"\b(\w+)\s+like\s+'([^']+)'", remaining)
        if m:
            conditions.append(f"{m.group(1)} LIKE '{m.group(2)}'")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── Date comparisons ("after"/"before") ───────────────────────────
        m = re.search(r'\b(\w+)\s+(after|before)\s+([a-z0-9\- ]+)', remaining)
        if m:
            col = m.group(1)
            op = ">" if m.group(2) == "after" else "<"
            conditions.append(f"{col} {op} {_parse_date(m.group(3))}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── Direct SQL operators (>, <, >=, <=, !=, =) ────────────────────
        m = re.search(r'\b(\w+)\s*([<>]=?|!=|=)\s*([\w\s\'\"\-]+?)(?=\s+and\s+|\s*$)', remaining)
        if m:
            col, op, val_raw = m.group(1), m.group(2), m.group(3).strip()
            val = _to_num(val_raw.strip("'\""))
            conditions.append(f"{col} {op} {_quote(val)}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── Natural-language comparison operators (longest alias first) ───
        # Important: aliases like "greater than or equal" must be matched before "greater than"
        # ── Natural-language comparison operators (longest alias first) ───
        for alias, op in sorted(COMPARISON_ALIASES.items(),
                                key=lambda kv: len(kv[0]),
                                reverse=True):

            pattern = (
                rf'\b(\w+)\s+'
                rf'{re.escape(alias)}'
                rf'\s+'
                rf'([^\s].*?)$'
            )

            m = re.search(pattern, remaining, flags=re.IGNORECASE)

            if m:
                col = m.group(1)
                val_raw = m.group(2).strip()

                if (
                    len(val_raw) >= 2
                    and val_raw[0] in "'\""
                    and val_raw[-1] == val_raw[0]
                ):
                    val_raw = val_raw[1:-1]

                val = _to_num(val_raw)

                if alias in ("older than", "younger than"):
                    col = "age"

                conditions.append(f"{col} {op} {_quote(val)}")

                remaining = remaining[:m.start()] + remaining[m.end():]
                break  # only one natural‑language comparison per clause

        # ── Standard equality: "col is 'val'" / "col = val" (if not already captured) ──
        m = re.search(r"\b(\w+)\s+(?:is|=)\s+'([^']+)'", remaining)
        if m:
            col, val = m.group(1), m.group(2)
            val = _to_num(val)
            conditions.append(f"{col} = {_quote(val)}")
            remaining = remaining[:m.start()] + remaining[m.end():]

        # ── Deduplicate while preserving order ────────────────────────────
        seen = set()
        unique = []
        for c in conditions:
            if c not in seen:
                seen.add(c)
                unique.append(c)

        return " AND ".join(unique) if unique else None