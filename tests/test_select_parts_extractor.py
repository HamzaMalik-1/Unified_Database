# backend/tests/test_select_parts_extractor.py

import sys
import os
# Add the project root (the directory containing 'backend') to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pytest
# Correct import path
from backend.processor.extractors.select_parts_extractor import SelectPartsExtractor

# ----------------------------------------------------------------------
# Fixture
# ----------------------------------------------------------------------
@pytest.fixture
def extractor():
    return SelectPartsExtractor()

# ----------------------------------------------------------------------
# extract_columns
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, table, known_cols, expected", [
    ("show name, age from users", "users", ["id", "name", "age", "city"], ["name", "age"]),
    ("select address.city from users_nosql", "users_nosql", ["id", "name", "address.city"], ["address.city"]),
    ("list * from users", "users", ["id", "name"], None),
    ("show sales from orders", "orders", ["id", "amount"], None),
    ("get email, phone from contacts", "contacts", ["email", "phone", "address"], ["email", "phone"]),
    ("display total amount", "sales", ["amount", "product"], None),
])
def test_extract_columns(extractor, text, table, known_cols, expected):
    result = extractor.extract_columns(text, table, known_cols)
    assert result == expected

# ----------------------------------------------------------------------
# extract_distinct
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("show distinct names", True),
    ("select unique cities", True),
    ("list all users", False),
    ("get distinct id, name", True),
    ("show users", False),
])
def test_extract_distinct(extractor, text, expected):
    assert extractor.extract_distinct(text) == expected

# ----------------------------------------------------------------------
# extract_aggregates
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("count of users", [("COUNT", "users", "users_count")]),
    ("total amount from orders", [("SUM", "amount", "amount_sum")]),
    ("average age group by city", [("AVG", "age", "age_avg")]),
    ("minimum price, maximum quantity", [("MIN", "price", "price_min"), ("MAX", "quantity", "quantity_max")]),
    ("sum of sales", [("SUM", "sales", "sales_sum")]),
    ("count of all rows", [("COUNT", "*", "total")]),
    ("total rows", [("COUNT", "*", "total")]),
    ("no aggregates here", []),
])
def test_extract_aggregates(extractor, text, expected):
    result = extractor.extract_aggregates(text)
    assert set(result) == set(expected)

# ----------------------------------------------------------------------
# extract_group_by
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("group by city", "city"),
    ("per region", "region"),
    ("show users group by age", "age"),
    ("no group by here", None),
])
def test_extract_group_by(extractor, text, expected):
    assert extractor.extract_group_by(text) == expected

# ----------------------------------------------------------------------
# extract_having
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("having count > 5", "count > 5"),
    ("having total greater than 100", "total > 100"),
    ("having avg_price is less than 50", "avg_price = less than 50"),
    ("no having clause", None),
])
def test_extract_having(extractor, text, expected):
    result = extractor.extract_having(text)
    if result and expected:
        result = result.strip()
        expected = expected.strip()
    assert result == expected

# ----------------------------------------------------------------------
# extract_order_by
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("order by age desc", [("age", "DESC")]),
    ("order by name asc", [("name", "ASC")]),
    ("order by id, name desc", [("id", "ASC"), ("name", "DESC")]),
    ("sorted by date ascending", [("date", "ASC")]),
    ("sorted by revenue descending", [("revenue", "DESC")]),
    ("no order here", None),
])
def test_extract_order_by(extractor, text, expected):
    assert extractor.extract_order_by(text) == expected

# ----------------------------------------------------------------------
# extract_limit
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("limit 10", 10),
    ("top 5 users", 5),
    ("first 3 rows", 3),
    ("fetch first 20 rows only", 20),
    ("no limit here", None),
])
def test_extract_limit(extractor, text, expected):
    assert extractor.extract_limit(text) == expected

# ----------------------------------------------------------------------
# extract_offset
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("offset 5", 5),
    ("limit 10 offset 5", 5),
    ("no offset", None),
])
def test_extract_offset(extractor, text, expected):
    assert extractor.extract_offset(text) == expected

# ----------------------------------------------------------------------
# extract_join
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text, primary_table, known_tables, expected_join_type, expected_other, expected_cond_contains", [
    ("users and orders", "users", ["users", "orders"], "INNER", "orders", "users.id = orders.users_id"),
    ("users left join orders", "users", ["users", "orders"], "LEFT", "orders", "users.id = orders.users_id"),
    ("products full outer join categories", "products", ["products", "categories"], "FULL", "categories", "products.id = categories.products_id"),
    ("customers natural join addresses", "customers", ["customers", "addresses"], "NATURAL", "addresses", None),
    ("sales cross join regions", "sales", ["sales", "regions"], "CROSS", "regions", None),
    ("orders join customers on orders.customer_id = customers.id", "orders", ["orders", "customers"], "INNER", "customers", "orders.customer_id = customers.id"),
    ("even if no matches, users left join orders", "users", ["users", "orders"], "LEFT", "orders", "users.id = orders.users_id"),
    ("only one table", "users", ["users"], None, None, None),
])
def test_extract_join(extractor, text, primary_table, known_tables,
                      expected_join_type, expected_other, expected_cond_contains):
    result = extractor.extract_join(text, primary_table, known_tables)
    if expected_join_type is None:
        assert result is None
    else:
        assert result is not None
        join_type, other_table, condition = result
        assert join_type == expected_join_type
        assert other_table == expected_other
        if expected_cond_contains is not None:
            assert expected_cond_contains in condition
        else:
            assert condition is None

# ----------------------------------------------------------------------
# Integration test
# ----------------------------------------------------------------------
def test_extract_multiple_parts(extractor):
    text = "show distinct city, count(*) from users group by city having count(*) > 5 order by city desc limit 10 offset 2"
    assert extractor.extract_distinct(text) is True
    assert extractor.extract_columns(text, "users", ["city", "name"]) == ["city"]
    # The extract_aggregates may capture "count(*)" as COUNT with col = "*"
    agg = extractor.extract_aggregates(text)
    assert any(a[0] == "COUNT" for a in agg)
    assert extractor.extract_group_by(text) == "city"
    assert extractor.extract_having(text) is not None
    assert extractor.extract_order_by(text) == [("city", "DESC")]
    assert extractor.extract_limit(text) == 10
    assert extractor.extract_offset(text) == 2