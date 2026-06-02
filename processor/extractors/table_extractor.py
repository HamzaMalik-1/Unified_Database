"""
extractors/table_extractor.py

Responsible for finding the target table / collection name in a query.

Resolution order:
  1. Exact token match against known tables  (fastest)
  2. Regex "FROM <word>" pattern
  3. Regex verb pattern  ("show/list/find <word>")
  4. Fuzzy match via RapidFuzz  (catches typos like "custommers")
  5. spaCy dependency-parse fallback  (catches complex phrasing)
"""

from __future__ import annotations
import re
from typing import Optional, List

# ── Optional heavy deps (lazy-loaded) ─────────────────────────────────────
_spacy_nlp = None
_rapidfuzz_process = None


def _get_spacy():
    global _spacy_nlp
    if _spacy_nlp is None:
        try:
            import spacy
            _spacy_nlp = spacy.load("en_core_web_sm")
        except Exception:
            _spacy_nlp = False
    return _spacy_nlp if _spacy_nlp is not False else None


def _get_rapidfuzz():
    global _rapidfuzz_process
    if _rapidfuzz_process is None:
        try:
            from rapidfuzz import process as rfprocess, fuzz
            _rapidfuzz_process = (rfprocess, fuzz)
        except ImportError:
            _rapidfuzz_process = False
    return _rapidfuzz_process if _rapidfuzz_process is not False else None


# Words that look like table names but aren't
_SKIP_WORDS = {
    "top", "bottom", "first", "last", "all", "distinct", "unique",
    "rows", "records", "entries", "data", "results", "me", "the",
    "from", "where", "by", "on", "in", "into", "set", "and", "or",
    "not", "is", "are", "was", "were", "have", "has", "with", "a",
    "an", "of", "for", "that", "which", "who",
}


class TableExtractor:
    """
    Extract the primary table/collection name from a natural-language query.
    Requires a reference list of known tables to enable fuzzy matching.
    """

    # Minimum fuzzy score to accept a candidate (0-100)
    FUZZY_THRESHOLD = 75

    def extract(self, text: str, known_tables: List[str]) -> Optional[str]:
        """
        Parameters
        ----------
        text         : lower-cased, stripped query text
        known_tables : all table/collection names in the current database(s)

        Returns
        -------
        Matched table name (original casing from known_tables) or None.
        """

        # ── 1. Exact word-boundary match ───────────────────────────────────
        # Sort longest-first so "order_items" beats "items"
        for table in sorted(known_tables, key=len, reverse=True):
            if re.search(rf'\b{re.escape(table.lower())}\b', text):
                return table

        # ── 2. FROM <word> ─────────────────────────────────────────────────
        from_match = re.search(r'\bfrom\s+([a-z_][a-z0-9_]*)', text)
        if from_match:
            candidate = from_match.group(1)
            resolved = self._resolve_candidate(candidate, known_tables)
            if resolved:
                return resolved

        # ── 3. Verb pattern: "show/list/find [top N] <word>" ──────────────
        verb_match = re.search(
            r'\b(?:show|list|find|select|get|fetch|display|retrieve)\s+'
            r'(?:(?:top|bottom|first|last)\s+)?(?:\d+\s+)?([a-z_][a-z0-9_]*)',
            text,
        )
        if verb_match:
            candidate = verb_match.group(1)
            if candidate not in _SKIP_WORDS:
                resolved = self._resolve_candidate(candidate, known_tables)
                if resolved:
                    return resolved

        # ── 4. Fuzzy match (full text tokens vs known tables) ─────────────
        fuzzy_result = self._fuzzy_match(text, known_tables)
        if fuzzy_result:
            return fuzzy_result

        # ── 5. spaCy dependency parse ──────────────────────────────────────
        return self._spacy_extract(text, known_tables)

    # ──────────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _resolve_candidate(self, candidate: str, known_tables: List[str]) -> Optional[str]:
        """Try exact → fuzzy resolution for a single candidate word."""
        # Exact
        for t in known_tables:
            if t.lower() == candidate.lower():
                return t

        # Fuzzy
        rf = _get_rapidfuzz()
        if rf and known_tables:
            rfprocess, fuzz = rf
            match = rfprocess.extractOne(
                candidate, known_tables,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self.FUZZY_THRESHOLD,
            )
            if match:
                return match[0]
        return None

    def _fuzzy_match(self, text: str, known_tables: List[str]) -> Optional[str]:
        """
        Tokenise the text and fuzzy-match each token against known tables.
        Returns the best matching table above FUZZY_THRESHOLD.
        """
        rf = _get_rapidfuzz()
        if not rf or not known_tables:
            return None

        rfprocess, fuzz = rf
        tokens = [t for t in re.findall(r'[a-z_][a-z0-9_]*', text) if t not in _SKIP_WORDS]

        best_table, best_score = None, 0
        for token in tokens:
            match = rfprocess.extractOne(
                token, known_tables,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self.FUZZY_THRESHOLD,
            )
            if match and match[1] > best_score:
                best_table, best_score = match[0], match[1]

        return best_table

    def _spacy_extract(self, text: str, known_tables: List[str]) -> Optional[str]:
        """Use spaCy POS/dep tags to find the most likely table noun."""
        nlp = _get_spacy()
        if nlp is None:
            return None

        doc = nlp(text)

        # Look for direct-object noun of a query verb
        query_verbs = {"list", "show", "find", "select", "get", "fetch", "retrieve", "display"}
        for token in doc:
            if token.lemma_ in query_verbs and token.pos_ == "VERB":
                for child in token.children:
                    if child.dep_ == "dobj" and child.pos_ == "NOUN":
                        candidate = child.lemma_.lower()
                        resolved = self._resolve_candidate(candidate, known_tables)
                        if resolved:
                            return resolved

        # Noun after "from"
        for i, token in enumerate(doc):
            if token.lower_ == "from" and i + 1 < len(doc):
                nxt = doc[i + 1]
                if nxt.pos_ == "NOUN":
                    candidate = nxt.lemma_.lower()
                    resolved = self._resolve_candidate(candidate, known_tables)
                    if resolved:
                        return resolved

        # Last resort: first noun after the main verb
        for token in doc:
            if token.lemma_ in query_verbs and token.pos_ == "VERB":
                for following in doc[token.i + 1:]:
                    if following.pos_ == "NOUN" and following.lower_ not in _SKIP_WORDS:
                        candidate = following.lemma_.lower()
                        resolved = self._resolve_candidate(candidate, known_tables)
                        if resolved:
                            return resolved
                break

        return None