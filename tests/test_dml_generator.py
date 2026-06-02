# backend/tests/test_dml_generator.py

import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pytest
from unittest.mock import Mock
from backend.processor.generators.dml_generator import DMLGenerator


@pytest.fixture
def mock_processor():
    """Create a mock NLPProcessor with required methods."""
    mock = Mock()

    # get_table: return table name based on text content (supports singular/plural)
    def get_table(text):
        text_lower = text.lower()
        # NoSQL collection
        if "nosql_users" in text_lower:
            return "nosql_users"
        # SQL tables (handle singular "user" as well)
        if "user" in text_lower or "users" in text_lower:
            return "users"
        if "order" in text_lower or "orders" in text_lower:
            return "orders"
        if "product" in text_lower or "products" in text_lower:
            return "products"
        if "employee" in text_lower or "employees" in text_lower:
            return "employees"
        if "customer" in text_lower or "customers" in text_lower:
            return "customers"
        return None
    mock.get_table = get_table

    # get_known_tables
    mock.get_known_tables.return_value = ["users", "orders", "products", "employees", "customers", "nosql_users"]

    # get_prefix
    def get_prefix(table):
        sql_tables = ["users", "orders", "products", "employees", "customers"]
        return "SQL" if table in sql_tables else "NOSQL"
    mock.get_prefix = get_prefix

    # conditions.extract – will be set per test
    conditions_mock = Mock()
    conditions_mock.extract.return_value = None
    mock.conditions = conditions_mock

    return mock


@pytest.fixture
def dml(mock_processor):
    return DMLGenerator(mock_processor)


# ----------------------------------------------------------------------
# INSERT tests
# ----------------------------------------------------------------------
def test_insert_basic(dml):
    text = "add a user named Alice age 30"
    assert dml.insert(text) == "SQL INSERT INTO users (name, age) VALUES ('Alice', 30)"

def test_insert_with_now(dml):
    text = "add a user named Bob with current timestamp"
    result = dml.insert(text)
    # The order of columns may vary, so check both possibilities
    assert result.startswith("SQL INSERT INTO users (")
    assert "created_at" in result
    assert "name" in result
    assert "CURRENT_TIMESTAMP" in result
    assert "Bob" in result

def test_insert_no_table(dml):
    text = "add a record"
    assert dml.insert(text) == "Error: Could not determine table name for INSERT"

def test_insert_unknown_table(dml):
    text = "add a product named X into unknown_table"
    # get_table returns None because "unknown_table" not recognised
    assert dml.insert(text) == "Error: Could not determine table name for INSERT"

def test_insert_with_age_and_name(dml):
    text = "insert into users name 'Charlie', age 25"
    result = dml.insert(text)
    assert "Charlie" in result and "25" in result

def test_insert_default_values(dml):
    text = "add a user"  # no columns given
    assert dml.insert(text) == "SQL INSERT INTO users DEFAULT VALUES"

def test_insert_nosql_collection(dml):
    text = "add a document to nosql_users with name Test"
    result = dml.insert(text)
    assert result.startswith("NOSQL INSERT INTO nosql_users (name) VALUES ('Test')")

def test_insert_with_word_number_age(dml):
    text = "add a user named John age twenty five"
    assert dml.insert(text) == "SQL INSERT INTO users (name, age) VALUES ('John', 25)"


# ----------------------------------------------------------------------
# UPDATE tests
# ----------------------------------------------------------------------
def test_update_basic(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "update user with id 1 set name = 'Alice'"
    assert dml.update(text) == "SQL UPDATE users SET name = 'Alice' WHERE id = 1"

def test_update_no_where(dml):
    # Use explicit assignment with '='
    text = "update users set status = 'active'"
    assert dml.update(text) == "SQL UPDATE users SET status = 'active'"

def test_update_multiple_columns(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "update user id 1 set name = 'Bob', age = 30"
    result = dml.update(text)
    assert "SQL UPDATE users SET name = 'Bob'" in result
    assert "age = 30" in result
    assert "WHERE id = 1" in result

def test_update_nosql_array_push(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "update nosql_users set hobbies push 'swimming' where id = 1"
    assert dml.update(text) == "NOSQL UPDATE nosql_users SET hobbies PUSH swimming WHERE id = 1"

def test_update_nosql_array_pull(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "update nosql_users set hobbies pull 'reading' where id = 1"
    assert dml.update(text) == "NOSQL UPDATE nosql_users SET hobbies PULL reading WHERE id = 1"

def test_update_nosql_array_addtoset(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "update nosql_users set hobbies addtoset 'dancing' where id = 1"
    assert dml.update(text) == "NOSQL UPDATE nosql_users SET hobbies ADDTOSET dancing WHERE id = 1"

def test_update_no_table(dml):
    text = "update set name = 'X'"
    assert dml.update(text) == "Error: Could not determine table name for UPDATE"

def test_update_unknown_table(dml):
    text = "update unknown_table set status = 'active'"
    # get_table returns None because "unknown_table" not in recognised list
    assert dml.update(text) == "Error: Could not determine table name for UPDATE"

def test_update_no_set_pairs(dml):
    text = "update users"
    assert dml.update(text) == "SQL UPDATE users"

def test_update_with_word_number(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "update user id 1 set age = twenty"
    assert dml.update(text) == "SQL UPDATE users SET age = 20 WHERE id = 1"

def test_update_nosql_without_where(dml):
    text = "update nosql_users set hobbies push 'tennis'"
    assert dml.update(text) == "NOSQL UPDATE nosql_users SET hobbies PUSH tennis"


# ----------------------------------------------------------------------
# DELETE tests
# ----------------------------------------------------------------------
def test_delete_with_where(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 2"
    text = "delete user with id 2"
    assert dml.delete(text) == "SQL DELETE FROM users WHERE id = 2"

def test_delete_no_where(dml):
    text = "delete all users"
    assert dml.delete(text) == "SQL DELETE FROM users"

def test_delete_nosql(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "id = 1"
    text = "delete from nosql_users where id = 1"
    assert dml.delete(text) == "NOSQL DELETE FROM nosql_users WHERE id = 1"

def test_delete_no_table(dml):
    text = "delete all"
    assert dml.delete(text) == "Error: Could not determine table name for DELETE"

def test_delete_unknown_table(dml):
    text = "delete from unknown_table"
    assert dml.delete(text) == "Error: Could not determine table name for DELETE"

def test_delete_with_complex_condition(dml, mock_processor):
    mock_processor.conditions.extract.return_value = "age > 30 AND city = 'NYC'"
    text = "delete users where age > 30 and city = 'NYC'"
    assert dml.delete(text) == "SQL DELETE FROM users WHERE age > 30 AND city = 'NYC'"


# ----------------------------------------------------------------------
# Run with: pytest backend/tests/test_dml_generator.py -v
# ----------------------------------------------------------------------