# backend/tests/test_select_generator.py

import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pytest
from unittest.mock import Mock, MagicMock
from backend.processor.generators.select_generator import SelectGenerator


@pytest.fixture
def mock_processor():
    """Create a mock NLPProcessor with all methods used by SelectGenerator."""
    mock = Mock()

    # get_table
    def get_table(text):
        if "users" in text:
            return "users"
        if "orders" in text:
            return "orders"
        if "products" in text:
            return "products"
        if "customers" in text:
            return "customers"
        return None
    mock.get_table = get_table

    # get_known_tables
    mock.get_known_tables.return_value = ["users", "orders", "products", "customers"]

    # get_prefix
    def get_prefix(table):
        # All are SQL tables; NoSQL not tested here
        return "SQL"
    mock.get_prefix = get_prefix

    # get_column_names
    def get_column_names(table):
        if table == "users":
            return ["id", "name", "age", "city"]
        if table == "orders":
            return ["id", "user_id", "amount", "product"]
        return []
    mock.get_column_names = get_column_names

    # conditions.extract
    conditions_mock = Mock()
    conditions_mock.extract.return_value = None
    mock.conditions = conditions_mock

    # select_parts – a mock with all extract methods
    select_parts_mock = Mock()
    mock.select_parts = select_parts_mock

    return mock


@pytest.fixture
def generator(mock_processor):
    return SelectGenerator(mock_processor)


# ----------------------------------------------------------------------
# Helper to set select_parts return values
# ----------------------------------------------------------------------
def set_parts(mock, columns=None, distinct=False, aggregates=None, group_by=None,
              having=None, order_by=None, limit=None, offset=None,
              join=None, subquery=None):
    """Helper to configure select_parts mock returns."""
    mock.select_parts.extract_columns.return_value = columns
    mock.select_parts.extract_distinct.return_value = distinct
    mock.select_parts.extract_aggregates.return_value = aggregates or []
    mock.select_parts.extract_group_by.return_value = group_by
    mock.select_parts.extract_having.return_value = having
    mock.select_parts.extract_order_by.return_value = order_by
    mock.select_parts.extract_limit.return_value = limit
    mock.select_parts.extract_offset.return_value = offset
    mock.select_parts.extract_join.return_value = join
    mock.conditions.extract.return_value = None


# ----------------------------------------------------------------------
# Basic SELECT tests
# ----------------------------------------------------------------------
def test_simple_select(generator, mock_processor):
    set_parts(mock_processor, columns=None, distinct=False)
    text = "show users"
    assert generator.generate(text) == "SQL SELECT * FROM users"

def test_select_with_columns(generator, mock_processor):
    set_parts(mock_processor, columns=["name", "age"])
    text = "show name, age from users"
    assert generator.generate(text) == "SQL SELECT name, age FROM users"

def test_select_distinct(generator, mock_processor):
    set_parts(mock_processor, columns=["city"], distinct=True)
    text = "show distinct city from users"
    assert generator.generate(text) == "SQL SELECT DISTINCT city FROM users"

def test_select_with_where(generator, mock_processor):
    set_parts(mock_processor, columns=None)
    mock_processor.conditions.extract.return_value = "age > 30"
    text = "users where age > 30"
    assert generator.generate(text) == "SQL SELECT * FROM users WHERE age > 30"

def test_select_with_order_by(generator, mock_processor):
    set_parts(mock_processor, columns=None, order_by=[("age", "DESC")])
    text = "users order by age desc"
    assert generator.generate(text) == "SQL SELECT * FROM users ORDER BY age DESC"

def test_select_with_limit(generator, mock_processor):
    set_parts(mock_processor, columns=None, limit=5)
    text = "top 5 users"
    # Implicit order by id DESC (because no order_by, but limit present)
    assert generator.generate(text) == "SQL SELECT * FROM users ORDER BY id DESC LIMIT 5"

def test_select_with_limit_offset(generator, mock_processor):
    set_parts(mock_processor, columns=None, limit=5, offset=10)
    text = "users limit 5 offset 10"
    assert generator.generate(text) == "SQL SELECT * FROM users LIMIT 10, 5"

def test_select_with_where_order_limit(generator, mock_processor):
    set_parts(mock_processor, columns=None, order_by=[("age", "ASC")], limit=3)
    mock_processor.conditions.extract.return_value = "city = 'NYC'"
    text = "users where city = 'NYC' order by age asc limit 3"
    assert generator.generate(text) == "SQL SELECT * FROM users WHERE city = 'NYC' ORDER BY age ASC LIMIT 3"


# ----------------------------------------------------------------------
# Aggregates, GROUP BY, HAVING
# ----------------------------------------------------------------------
def test_aggregate_count(generator, mock_processor):
    set_parts(mock_processor, aggregates=[("COUNT", "*", "total")])
    text = "count of all rows from users"
    assert generator.generate(text) == "SQL SELECT COUNT(*) AS total FROM users"

def test_aggregate_sum(generator, mock_processor):
    set_parts(mock_processor, aggregates=[("SUM", "amount", "amount_sum")])
    text = "total amount from orders"
    assert generator.generate(text) == "SQL SELECT SUM(amount) AS amount_sum FROM orders"

def test_aggregate_with_group_by(generator, mock_processor):
    set_parts(mock_processor, aggregates=[("AVG", "age", "age_avg")], group_by="city")
    text = "average age group by city from users"
    assert generator.generate(text) == "SQL SELECT AVG(age) AS age_avg, city FROM users GROUP BY city"

def test_group_by_without_aggregates(generator, mock_processor):
    set_parts(mock_processor, columns=None, group_by="city")
    text = "show users group by city"
    assert generator.generate(text) == "SQL SELECT city FROM users GROUP BY city"

def test_group_by_with_having(generator, mock_processor):
    set_parts(mock_processor, aggregates=[("COUNT", "*", "total")], group_by="product",
              having="COUNT(*) > 5")
    text = "count orders by product having count > 5"
    assert generator.generate(text) == "SQL SELECT COUNT(*) AS total, product FROM orders GROUP BY product HAVING COUNT(*) > 5"


# ----------------------------------------------------------------------
# JOINs
# ----------------------------------------------------------------------
def test_join_inner(generator, mock_processor):
    join_info = ("INNER", "orders", "users.id = orders.user_id")
    set_parts(mock_processor, join=join_info)
    text = "users and orders"
    assert generator.generate(text) == "SQL SELECT * FROM users INNER JOIN orders ON users.id = orders.user_id"

def test_join_left(generator, mock_processor):
    join_info = ("LEFT", "orders", "users.id = orders.user_id")
    set_parts(mock_processor, join=join_info)
    text = "users left join orders"
    assert generator.generate(text) == "SQL SELECT * FROM users LEFT JOIN orders ON users.id = orders.user_id"

def test_join_with_where_and_order(generator, mock_processor):
    join_info = ("INNER", "orders", "users.id = orders.user_id")
    set_parts(mock_processor, join=join_info, order_by=[("users.name", "ASC")])
    mock_processor.conditions.extract.return_value = "orders.amount > 100"
    text = "users join orders on users.id = orders.user_id where amount > 100 order by name"
    result = generator.generate(text)
    assert "INNER JOIN" in result
    assert "WHERE orders.amount > 100" in result
    assert "ORDER BY users.name ASC" in result


# ----------------------------------------------------------------------
# Subqueries (IN / EXISTS)
# ----------------------------------------------------------------------
def test_subquery_in(generator, mock_processor):
    # The generator's _detect_subquery will be called because no join, etc.
    # We need to mock the method? Actually the generator calls self._detect_subquery,
    # which uses known_tables and text. We can't easily mock that without patching.
    # Instead, we rely on the method's internal logic. But for reliable testing,
    # we can set up the mock to return a specific subquery info via a different mechanism?
    # Actually the generator does not have a separate mockable subquery extractor;
    # it calls a method that uses regex on the text. So we must test with real text.
    # So we will use realistic subquery texts.
    # For the purpose of this test file, we'll test the subquery path with actual text
    # that triggers the internal method. We'll need to ensure the tables exist in mock.
    mock_processor.get_known_tables.return_value = ["users", "orders"]
    set_parts(mock_processor)  # no join
    text = "users who have orders"
    # This should detect subquery: "who have orders" -> EXISTS (orders)
    result = generator.generate(text)
    assert result == "SQL SELECT * FROM users WHERE EXISTS (SELECT 1 FROM orders WHERE orders.users_id = users.id)"  # note: default foreign key name is {main_table}_id
    # The exact condition may be `orders.users_id = users.id` – that's the default.
    assert "EXISTS" in result

def test_subquery_in_alternative(generator, mock_processor):
    mock_processor.get_known_tables.return_value = ["users", "orders"]
    set_parts(mock_processor)
    text = "users who bought something"
    result = generator.generate(text)
    assert "IN" in result or "EXISTS" in result  # either is fine

def test_subquery_with_limit(generator, mock_processor):
    mock_processor.get_known_tables.return_value = ["users", "orders"]
    set_parts(mock_processor, limit=5, order_by=[("id", "ASC")])
    text = "users who have orders limit 5"
    result = generator.generate(text)
    assert "LIMIT 5" in result
    assert "ORDER BY id ASC" in result


# ----------------------------------------------------------------------
# Set operations (UNION, INTERSECT, EXCEPT)
# ----------------------------------------------------------------------
def test_union(generator, mock_processor):
    # Need two separate SELECT queries – the generator splits and calls itself recursively.
    # We'll mock the necessary returns for both parts.
    # This test requires that the mock's get_table etc. work for both substrings.
    # We'll use a simple text that the generator can parse.
    # For reliable testing, we'll patch the generate method? Not needed – the generator
    # will call itself recursively on the left and right parts.
    text = "select name from users union select name from customers"
    # Ensure both tables are known
    mock_processor.get_known_tables.return_value = ["users", "customers"]
    set_parts(mock_processor, columns=["name"])
    result = generator.generate(text)
    # The result should be something like "SQL SELECT name FROM users UNION SELECT name FROM customers"
    # Note: The generator removes the prefix from subqueries and adds a single prefix.
    assert "UNION" in result
    assert "SELECT name FROM users" in result
    assert "SELECT name FROM customers" in result

def test_intersect(generator, mock_processor):
    mock_processor.get_known_tables.return_value = ["users", "customers"]
    set_parts(mock_processor, columns=["name"])
    text = "select name from users intersect select name from customers"
    result = generator.generate(text)
    assert "INTERSECT" in result

def test_except(generator, mock_processor):
    mock_processor.get_known_tables.return_value = ["users", "customers"]
    set_parts(mock_processor, columns=["name"])
    text = "select name from users except select name from customers"
    result = generator.generate(text)
    assert "EXCEPT" in result


# ----------------------------------------------------------------------
# CTE (WITH)
# ----------------------------------------------------------------------
def test_cte(generator, mock_processor):
    mock_processor.get_known_tables.return_value = ["users"]
    set_parts(mock_processor, columns=["*"])
    text = "with cte as (select * from users where age > 30) select * from cte"
    result = generator.generate(text)
    assert result.startswith("SQL WITH cte AS (SELECT * FROM users WHERE age > 30) SELECT * FROM cte")
    # The inner query will be parsed with the same generator; its "table" will be "users".
    # The outer table is "cte" – but get_table will return None because "cte" is not in known_tables.
    # However, in the CTE branch, the generator does not call get_table for the outer query
    # because it is handled by the CTE logic. So it should work.
    # The expected result may not exactly match because the outer SELECT * FROM cte will
    # be parsed as a normal SELECT (table "cte" not found) but within the CTE branch we don't go that path.
    # So the test will pass if the generator returns the command.
    assert "WITH cte AS" in result
    assert "SELECT * FROM cte" in result


# ----------------------------------------------------------------------
# Error cases
# ----------------------------------------------------------------------
def test_no_table(generator, mock_processor):
    set_parts(mock_processor)
    text = "show something"
    assert generator.generate(text) is None

def test_table_not_exist(generator, mock_processor):
    mock_processor.get_table.return_value = "unknown"
    mock_processor.get_known_tables.return_value = ["users"]
    text = "show unknown"
    assert generator.generate(text) == "Error: Table/Collection 'unknown' does not exist"


# ----------------------------------------------------------------------
# Run with: pytest backend/tests/test_select_generator.py -v
# ----------------------------------------------------------------------