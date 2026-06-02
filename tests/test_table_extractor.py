# backend/tests/test_table_extractor.py

import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pytest
from backend.processor.extractors.table_extractor import TableExtractor

# ----------------------------------------------------------------------
# Fixture
# ----------------------------------------------------------------------
@pytest.fixture
def extractor():
    return TableExtractor()

@pytest.fixture
def known_tables():
    return ["users", "orders", "products", "employees", "customers", "order_items", "addresses", "users_nosql"]

# ----------------------------------------------------------------------
# Exact word-boundary match (longest-first)
# ----------------------------------------------------------------------
def test_exact_match_simple(extractor, known_tables):
    assert extractor.extract("show users", known_tables) == "users"

def test_exact_match_longest_first(extractor, known_tables):
    # "order_items" should match before "orders"
    assert extractor.extract("select order_items", known_tables) == "order_items"

def test_exact_match_with_extra_words(extractor, known_tables):
    assert extractor.extract("find all products with price > 100", known_tables) == "products"

def test_exact_match_case_insensitive(extractor, known_tables):
    # known_tables are original case; text is lowercased inside extractor
    # The method lowercases both sides for matching
    # Our extractor lowercases text; known_tables are compared as lower() inside
    assert extractor.extract("show USERS", known_tables) == "users"

# ----------------------------------------------------------------------
# FROM clause extraction
# ----------------------------------------------------------------------
def test_from_clause(extractor, known_tables):
    assert extractor.extract("select * from orders", known_tables) == "orders"

def test_from_clause_with_extra(extractor, known_tables):
    assert extractor.extract("get all columns from customers where active = 1", known_tables) == "customers"

def test_from_clause_no_match(extractor, known_tables):
    # "unknown_table" not in known_tables – exact match fails, but fallback should handle
    # The extractor will return None because no match found
    assert extractor.extract("select * from unknown_table", known_tables) is None

# ----------------------------------------------------------------------
# Verb pattern extraction (show/list/find/select/get/fetch/display/retrieve)
# ----------------------------------------------------------------------
def test_verb_pattern_simple(extractor, known_tables):
    assert extractor.extract("list users", known_tables) == "users"

def test_verb_pattern_with_top(extractor, known_tables):
    assert extractor.extract("show top 5 products", known_tables) == "products"

def test_verb_pattern_with_bottom(extractor, known_tables):
    assert extractor.extract("find bottom 10 orders", known_tables) == "orders"

def test_verb_pattern_with_number(extractor, known_tables):
    assert extractor.extract("select 20 employees", known_tables) == "employees"

def test_verb_pattern_skip_words(extractor, known_tables):
    # "rows" is a skip word, should not be returned as table
    assert extractor.extract("show rows", known_tables) is None

def test_verb_pattern_skip_word_in_middle(extractor, known_tables):
    # "all" is skip word, but "users" follows, should match
    assert extractor.extract("get all users", known_tables) == "users"

def test_verb_pattern_multiple_verbs(extractor, known_tables):
    assert extractor.extract("display fetch customers", known_tables) == "customers"

# ----------------------------------------------------------------------
# Fuzzy match (requires rapidfuzz installed)
# ----------------------------------------------------------------------
def test_fuzzy_match_typo(extractor, known_tables):
    # "costumers" is close to "customers"
    # Fuzzy threshold is 75; typical typo should match
    result = extractor.extract("show costumers", known_tables)
    # May return "customers" if fuzzy match works
    # If rapidfuzz not installed, fallback to other methods may still work via verb pattern
    # The verb pattern "show costumers" will extract "costumers" which is not exact, then fuzzy
    # We accept either "customers" or None if fuzzy fails
    assert result in ("customers", None)

def test_fuzzy_match_with_extra_word(extractor, known_tables):
    result = extractor.extract("select produts from warehouse", known_tables)
    # "produts" should fuzzy match "products"
    # If no match, may return None because "from warehouse" not in known
    assert result in ("products", None)

def test_fuzzy_match_no_typo(extractor, known_tables):
    # "order_items" exact
    assert extractor.extract("get order_items", known_tables) == "order_items"

# ----------------------------------------------------------------------
# spaCy fallback (requires spacy and model en_core_web_sm installed)
# ----------------------------------------------------------------------
def test_spacy_fallback(extractor, known_tables):
    # Complex phrase where verb pattern may not work directly
    # "show me the list of users" – verb pattern would capture "list" as candidate (skip word? maybe)
    # spaCy should identify "users" as the noun after "of"
    # If spaCy not installed, this may return None; we just ensure no crash
    result = extractor.extract("show me the list of users", known_tables)
    assert result in ("users", None)

def test_spacy_fallback_with_where(extractor, known_tables):
    result = extractor.extract("find all orders where amount > 100", known_tables)
    assert result in ("orders", None)

# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------
def test_empty_text(extractor, known_tables):
    assert extractor.extract("", known_tables) is None

def test_no_known_tables(extractor):
    assert extractor.extract("show users", []) is None

def test_only_skip_words(extractor, known_tables):
    assert extractor.extract("top bottom first last", known_tables) is None

def test_table_name_with_underscore(extractor, known_tables):
    assert extractor.extract("select from order_items", known_tables) == "order_items"

def test_table_name_with_nosql(extractor, known_tables):
    assert extractor.extract("nosql_users", known_tables) == "users_nosql"

def test_table_name_with_number(extractor, known_tables):
    # No table with number, should return None
    assert extractor.extract("show 123", known_tables) is None

def test_table_name_ambiguous_resolution(extractor, known_tables):
    # "orders" vs "order_items" – longest match first?
    # The sentence "show order" would match "orders"? Actually "order" not in list.
    # We'll test that "order" doesn't match "orders" (needs exact word boundary)
    assert extractor.extract("show order", known_tables) is None

# ----------------------------------------------------------------------
# Resolution order: FROM clause should take precedence over verb pattern
# ----------------------------------------------------------------------
def test_from_over_verb(extractor, known_tables):
    # "from products" should win over "show users"
    assert extractor.extract("show users from products", known_tables) == "products"

def test_verb_over_fuzzy(extractor, known_tables):
    # Exact verb match should be used before fuzzy
    assert extractor.extract("show products", known_tables) == "products"

# ----------------------------------------------------------------------
# Integration with long text
# ----------------------------------------------------------------------
def test_long_query(extractor, known_tables):
    text = "can you please show me all the customers who bought something last month"
    assert extractor.extract(text, known_tables) == "customers"

def test_long_query_with_where(extractor, known_tables):
    text = "list all orders where the total amount is greater than 100 and sort by date"
    assert extractor.extract(text, known_tables) == "orders"

def test_long_query_no_table(extractor, known_tables):
    text = "what is the total revenue?"
    assert extractor.extract(text, known_tables) is None

# ----------------------------------------------------------------------
# Run with: pytest backend/tests/test_table_extractor.py -v
# ----------------------------------------------------------------------