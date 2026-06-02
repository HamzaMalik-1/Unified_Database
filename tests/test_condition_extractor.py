import sys
import os

# Add the project root (one level above 'backend') to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from backend.processor.extractors.condition_extractor import ConditionExtractor

extractor = ConditionExtractor()

# ----------------------------------------------------------------------
# Basic comparisons
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("age greater than 25", "age > 25"),
    ("price more than 100", "price > 100"),
    ("temperature above 30", "temperature > 30"),
    ("count over 5", "count > 5"),
    ("age older than 18", "age > 18"),
    ("age less than 30", "age < 30"),
    ("amount below 50", "amount < 50"),
    ("weight under 200", "weight < 200"),
    ("age younger than 65", "age < 65"),
    ("age at least 18", "age >= 18"),
    ("age greater than or equal 21", "age >= 21"),
    ("age at most 65", "age <= 65"),
    ("age less than or equal 5", "age <= 5"),
    ("age not equal 0", "age != 0"),
    ("name not equals 'John'", "name != 'John'"),
    ("status different from 'active'", "status != 'active'"),
    ("age equals 30", "age = 30"),
    ("name equal to 'Alice'", "name = 'Alice'"),
    ("city is 'Boston'", "city = 'Boston'"),
])
def test_comparisons(input_text, expected):
    assert extractor.extract(input_text) == expected

# ----------------------------------------------------------------------
# NULL / NOT NULL
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("email is missing", "email IS NULL"),
    ("address is empty", "address IS NULL"),
    ("phone is null", "phone IS NULL"),
    ("middle_name is none", "middle_name IS NULL"),
    ("salary is blank", "salary IS NULL"),
    ("email is not missing", "email IS NOT NULL"),
    ("address is not empty", "address IS NOT NULL"),
    ("phone is not null", "phone IS NOT NULL"),
    ("no email", "email IS NULL"),
    ("without phone", "phone IS NULL"),
])
def test_null_handling(input_text, expected):
    assert extractor.extract(input_text) == expected

# ----------------------------------------------------------------------
# BETWEEN / NOT BETWEEN
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("age between 18 and 25", "age BETWEEN 18 AND 25"),
    ("price not between 100 and 200", "price NOT BETWEEN 100 AND 200"),
    ("amount between 50 and 100", "amount BETWEEN 50 AND 100"),
])
def test_between(input_text, expected):
    assert extractor.extract(input_text) == expected

# ----------------------------------------------------------------------
# IN / NOT IN
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("id in (1,2,3)", "id IN (1,2,3)"),
    ("status not in ('active','pending')", "status NOT IN ('active','pending')"),
    ("city in ( 'NYC', 'LA' )", "city IN (NYC, LA)"),
])
def test_in(input_text, expected):
    out = extractor.extract(input_text)
    assert out == expected or out.replace("'", "") == expected.replace("'", "")

# ----------------------------------------------------------------------
# LIKE / NOT LIKE
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("name like 'A%'", "name LIKE 'A%'"),
    ("name not like '%son'", "name NOT LIKE '%son'"),
])
def test_like(input_text, expected):
    assert extractor.extract(input_text) == expected

# ----------------------------------------------------------------------
# Date expressions
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("created after today", "created > CURRENT_DATE"),
    ("modified before yesterday", "modified < CURRENT_DATE - 1"),
    ("event after last week", "event > CURRENT_DATE - 7"),
    ("timestamp after now", "timestamp > CURRENT_TIMESTAMP"),
    ("date between last month and today", "date BETWEEN CURRENT_DATE - 30 AND CURRENT_DATE"),
])
def test_date_expressions(input_text, expected):
    out = extractor.extract(input_text)
    assert out.replace(" ", "") == expected.replace(" ", "")

# ----------------------------------------------------------------------
# Word numbers
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("age greater than twenty five", "age > 25"),
    ("price less than one hundred fifty", "price < 150"),
    ("count at least thirty", "count >= 30"),
    ("limit twenty", None),
])
def test_word_numbers(input_text, expected):
    assert extractor.extract(input_text) == expected

# ----------------------------------------------------------------------
# "with id" shorthand
# ----------------------------------------------------------------------
@pytest.mark.parametrize("input_text, expected", [
    ("user with id 5", "id = 5"),
    ("product id 10", "id = 10"),
    ("order with id twenty", "id = 20"),
])
def test_id_shorthand(input_text, expected):
    assert extractor.extract(input_text) == expected

# ----------------------------------------------------------------------
# Complex AND/OR
# ----------------------------------------------------------------------
def test_and_condition():
    assert extractor.extract("age > 25 and city = 'Boston'") == "age > 25 AND city = 'Boston'"

def test_or_condition():
    out = extractor.extract("age < 18 or age > 65")
    assert "OR" in out
    assert "age < 18" in out and "age > 65" in out

def test_mixed_and_or():
    out = extractor.extract("age > 25 and city = 'Boston' or status = 'vip'")
    assert "OR" in out
    assert "AND" in out.split("OR")[0]

# ----------------------------------------------------------------------
# "whose"
# ----------------------------------------------------------------------
def test_whose():
    assert extractor.extract("users whose age > 30") == "age > 30"

# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------
def test_no_condition():
    assert extractor.extract("show users") is None

def test_unknown_pattern():
    assert extractor.extract("something weird") is None

# ----------------------------------------------------------------------
# Realistic
# ----------------------------------------------------------------------
def test_realistic_where():
    out = extractor.extract("orders where amount between 100 and 500 and status not in ('cancelled','refunded')")
    assert "amount BETWEEN 100 AND 500" in out
    assert "status NOT IN ('cancelled','refunded')" in out

def test_word_number_and_date():
    out = extractor.extract("created after last month and price less than twenty five")
    assert "created > CURRENT_DATE - 30" in out
    assert "price < 25" in out

if __name__ == "__main__":
    pytest.main([__file__, "-v"])