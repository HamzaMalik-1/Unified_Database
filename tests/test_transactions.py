# backend/tests/test_transactions.py
import sys
import os
import tempfile
import shutil
import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dispatcher.unified_db import UnifiedDB
from utils.response import APIResponse
from catalog.database_catalog import DatabaseCatalog


def reset_catalog():
    """Reset the DatabaseCatalog singleton so each test gets a fresh catalog."""
    if DatabaseCatalog._instance:
        # Clear internal data
        DatabaseCatalog._instance._databases.clear()
        DatabaseCatalog._instance._current_db = None
    DatabaseCatalog._instance = None


@pytest.fixture
def temp_db():
    """Create an isolated database environment for each test."""
    # Reset the singleton catalog
    reset_catalog()

    # Create a temporary directory and change into it
    original_cwd = os.getcwd()
    temp_dir = tempfile.mkdtemp()
    os.chdir(temp_dir)

    # Create the required directory structure for the default database
    os.makedirs("data/default", exist_ok=True)

    # Initialize the UnifiedDB (will create default database)
    db = UnifiedDB()

    yield db

    # Cleanup
    os.chdir(original_cwd)
    shutil.rmtree(temp_dir, ignore_errors=True)


def execute_and_assert_success(db: UnifiedDB, command: str) -> APIResponse:
    resp = db.execute(command)
    assert resp.success, f"Command failed: {command}\nError: {resp.message}"
    return resp


def execute_and_assert_error(db: UnifiedDB, command: str, expected_substring: str = None):
    resp = db.execute(command)
    assert not resp.success, f"Command unexpectedly succeeded: {command}"
    if expected_substring:
        assert expected_substring.lower() in resp.message.lower(), \
            f"Expected '{expected_substring}' not in '{resp.message}'"
    return resp


def get_sql_data(db: UnifiedDB, query: str):
    resp = db.execute(query)
    assert resp.success
    if resp.data.type == "table":
        return resp.data.content
    elif resp.data.type == "json":
        return resp.data.content
    else:
        return []


def get_nosql_data(db: UnifiedDB, query: str):
    resp = db.execute(query)
    assert resp.success
    if resp.data.type == "table":
        return resp.data.content
    elif resp.data.type == "json":
        return resp.data.content
    else:
        return []


# ----------------------------------------------------------------------
# 1. SQL Transaction Tests
# ----------------------------------------------------------------------
def test_sql_transaction_commit(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE test_sql (id INT PRIMARY KEY, name TEXT)")

    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO test_sql (id, name) VALUES (1, 'Alice')")
    rows = get_sql_data(db, "SQL SELECT * FROM test_sql WHERE id = 1")
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"

    execute_and_assert_success(db, "SQL COMMIT")
    rows = get_sql_data(db, "SQL SELECT * FROM test_sql WHERE id = 1")
    assert len(rows) == 1


def test_sql_transaction_rollback(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE test_sql_rb (id INT PRIMARY KEY, name TEXT)")

    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO test_sql_rb (id, name) VALUES (2, 'Bob')")
    rows = get_sql_data(db, "SQL SELECT * FROM test_sql_rb WHERE id = 2")
    assert len(rows) == 1
    execute_and_assert_success(db, "SQL ROLLBACK")
    rows = get_sql_data(db, "SQL SELECT * FROM test_sql_rb WHERE id = 2")
    assert len(rows) == 0


def test_sql_transaction_nested_begin_error(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_error(db, "SQL BEGIN TRANSACTION", "already active")


def test_sql_transaction_no_active_commit_error(temp_db):
    db = temp_db
    execute_and_assert_error(db, "SQL COMMIT", "No active")


def test_sql_transaction_no_active_rollback_error(temp_db):
    db = temp_db
    execute_and_assert_error(db, "SQL ROLLBACK", "No active")


def test_sql_transaction_nosql_command_rejected(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_error(db, "NOSQL CREATE COLLECTION temp_coll", "Only SQL commands allowed")


# ----------------------------------------------------------------------
# 2. NoSQL Transaction Tests
# ----------------------------------------------------------------------
def test_nosql_transaction_commit(temp_db):
    db = temp_db
    execute_and_assert_success(db, "NOSQL CREATE COLLECTION test_nosql")

    execute_and_assert_success(db, "NOSQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "NOSQL INSERT INTO test_nosql (id, name) VALUES (1, 'Charlie')")
    docs = get_nosql_data(db, "NOSQL SELECT * FROM test_nosql WHERE id = 1")
    assert len(docs) == 1
    execute_and_assert_success(db, "NOSQL COMMIT")
    docs = get_nosql_data(db, "NOSQL SELECT * FROM test_nosql WHERE id = 1")
    assert len(docs) == 1


def test_nosql_transaction_rollback(temp_db):
    db = temp_db
    execute_and_assert_success(db, "NOSQL CREATE COLLECTION test_nosql_rb")
    execute_and_assert_success(db, "NOSQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "NOSQL INSERT INTO test_nosql_rb (id, name) VALUES (2, 'Diana')")
    docs = get_nosql_data(db, "NOSQL SELECT * FROM test_nosql_rb WHERE id = 2")
    assert len(docs) == 1
    execute_and_assert_success(db, "NOSQL ROLLBACK")
    docs = get_nosql_data(db, "NOSQL SELECT * FROM test_nosql_rb WHERE id = 2")
    assert len(docs) == 0


def test_nosql_transaction_sql_command_rejected(temp_db):
    db = temp_db
    execute_and_assert_success(db, "NOSQL BEGIN TRANSACTION")
    execute_and_assert_error(db, "SQL SELECT 1", "Only NoSQL commands allowed")


# ----------------------------------------------------------------------
# 3. Cross‑model Transaction Tests
# ----------------------------------------------------------------------
def test_cross_transaction_commit(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE cross_sql (id INT PRIMARY KEY, name TEXT)")
    execute_and_assert_success(db, "NOSQL CREATE COLLECTION cross_nosql")

    execute_and_assert_success(db, "DATABASE BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO cross_sql (id, name) VALUES (10, 'CrossSQL')")
    execute_and_assert_success(db, "NOSQL INSERT INTO cross_nosql (user_id, bio) VALUES (10, 'CrossNoSQL')")

    sql_rows = get_sql_data(db, "SQL SELECT * FROM cross_sql WHERE id = 10")
    assert len(sql_rows) == 1
    nosql_docs = get_nosql_data(db, "NOSQL SELECT * FROM cross_nosql WHERE user_id = 10")
    assert len(nosql_docs) == 1

    execute_and_assert_success(db, "DATABASE COMMIT")
    sql_rows = get_sql_data(db, "SQL SELECT * FROM cross_sql WHERE id = 10")
    assert len(sql_rows) == 1
    nosql_docs = get_nosql_data(db, "NOSQL SELECT * FROM cross_nosql WHERE user_id = 10")
    assert len(nosql_docs) == 1


def test_cross_transaction_rollback(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE cross_rb (id INT PRIMARY KEY, name TEXT)")
    execute_and_assert_success(db, "NOSQL CREATE COLLECTION cross_rb_nosql")

    execute_and_assert_success(db, "DATABASE BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO cross_rb (id, name) VALUES (20, 'RollbackSQL')")
    execute_and_assert_success(db, "NOSQL INSERT INTO cross_rb_nosql (user_id, bio) VALUES (20, 'RollbackNoSQL')")
    execute_and_assert_success(db, "DATABASE ROLLBACK")

    sql_rows = get_sql_data(db, "SQL SELECT * FROM cross_rb WHERE id = 20")
    assert len(sql_rows) == 0
    nosql_docs = get_nosql_data(db, "NOSQL SELECT * FROM cross_rb_nosql WHERE user_id = 20")
    assert len(nosql_docs) == 0


def test_cross_transaction_mixed_commands_allowed(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE cross_mix (id INT PRIMARY KEY, name TEXT)")
    execute_and_assert_success(db, "DATABASE BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO cross_mix (id, name) VALUES (30, 'Mixed')")
    # DDL inside cross‑model transaction is auto‑committed (as per your design)
    execute_and_assert_success(db, "NOSQL CREATE COLLECTION temp_coll")
    execute_and_assert_success(db, "DATABASE ROLLBACK")
    # After rollback, the SQL row should be gone, but the NoSQL collection (DDL) remains.
    sql_rows = get_sql_data(db, "SQL SELECT * FROM cross_mix WHERE id = 30")
    assert len(sql_rows) == 0


# ----------------------------------------------------------------------
# 4. DDL Auto‑commit (DDL inside a transaction is auto‑committed)
# ----------------------------------------------------------------------
def test_ddl_auto_commit_inside_sql_transaction(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL CREATE TABLE auto_table (id INT)")
    execute_and_assert_success(db, "SQL ROLLBACK")
    # Table should still exist after rollback (DDL auto‑commit)
    resp = db.execute("SQL SELECT * FROM auto_table")
    assert resp.success, "Table should exist after rollback because DDL auto‑commits"


# ----------------------------------------------------------------------
# 5. Durability Test (simulate restart)
# ----------------------------------------------------------------------
def test_sql_transaction_durability_after_restart(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE durable (id INT PRIMARY KEY)")
    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO durable (id) VALUES (99)")
    execute_and_assert_success(db, "SQL COMMIT")

    # Simulate restart: create a new UnifiedDB instance (catalog reset will happen)
    reset_catalog()
    # The temporary directory already has the database files
    new_db = UnifiedDB()
    rows = get_sql_data(new_db, "SQL SELECT * FROM durable WHERE id = 99")
    assert len(rows) == 1, "Committed data should survive restart"
    
    
def test_crash_recovery(temp_db):
    db = temp_db
    execute_and_assert_success(db, "SQL CREATE TABLE test_recovery (id INT PRIMARY KEY, name TEXT)")
    execute_and_assert_success(db, "SQL BEGIN TRANSACTION")
    execute_and_assert_success(db, "SQL INSERT INTO test_recovery (id, name) VALUES (1, 'Recovered')")
    execute_and_assert_success(db, "SQL COMMIT")
    # Simulate crash: delete the JSON file (or corrupt it)
    os.remove("data/default/sql_data.json")
    # Restart the engine (create a new instance)
    reset_catalog()
    new_db = UnifiedDB()
    rows = get_sql_data(new_db, "SQL SELECT * FROM test_recovery WHERE id = 1")
    assert len(rows) == 1 and rows[0]["name"] == "Recovered"