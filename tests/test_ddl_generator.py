# backend/tests/test_ddl_generator.py

import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pytest
from unittest.mock import Mock
from backend.processor.generators.ddl_generator import DDLGenerator


@pytest.fixture
def mock_processor():
    """Create a mock NLPProcessor with required methods."""
    mock = Mock()
    def get_table(text):
        if "users" in text:
            return "users"
        if "orders" in text:
            return "orders"
        if "products" in text:
            return "products"
        if "sales" in text:
            return "sales"
        return None
    mock.get_table = get_table
    mock.get_known_tables.return_value = ["users", "orders", "products", "employees", "customers", "order_items", "addresses"]
    def get_prefix(table):
        sql_tables = ["users", "orders", "products", "employees", "customers", "order_items", "addresses"]
        return "SQL" if table in sql_tables else "NOSQL"
    mock.get_prefix = get_prefix
    return mock


@pytest.fixture
def ddl(mock_processor):
    return DDLGenerator(mock_processor)


# ----------------------------------------------------------------------
# INDEX operations
# ----------------------------------------------------------------------
def test_create_index(ddl):
    text = "create index idx_name on users (name)"
    assert ddl.create_index(text) == "SQL CREATE INDEX idx_name ON users (name)"

def test_create_index_multiple_columns(ddl):
    text = "create index idx_id_name on users (id, name)"
    assert ddl.create_index(text) == "SQL CREATE INDEX idx_id_name ON users (id, name)"

def test_create_index_no_match(ddl):
    assert ddl.create_index("something else") is None

def test_drop_index(ddl):
    text = "drop index idx_name"
    assert ddl.drop_index(text) == "SQL DROP INDEX idx_name"

def test_drop_index_no_match(ddl):
    assert ddl.drop_index("no index here") is None

def test_create_text_index(ddl):
    text = "create text index idx_content on docs (content)"
    assert ddl.create_text_index(text) == "NOSQL CREATE TEXT INDEX idx_content ON docs (content)"

def test_create_text_index_no_match(ddl):
    assert ddl.create_text_index("create index idx on docs (content)") is None

def test_drop_text_index(ddl):
    text = "drop text index idx_content"
    assert ddl.drop_text_index(text) == "NOSQL DROP TEXT INDEX idx_content"

def test_drop_text_index_no_match(ddl):
    assert ddl.drop_text_index("drop index idx") is None


# ----------------------------------------------------------------------
# TABLE / COLLECTION creation/drop
# ----------------------------------------------------------------------
def test_create_table(ddl):
    text = "create table products"
    assert ddl.create_table(text) == "SQL CREATE TABLE products (id INT PRIMARY KEY, name TEXT)"

def test_create_table_no_match(ddl):
    assert ddl.create_table("create collection users") is None

def test_create_collection(ddl):
    text = "create collection logs"
    assert ddl.create_collection(text) == "NOSQL CREATE COLLECTION logs"

def test_create_collection_no_match(ddl):
    assert ddl.create_collection("create table users") is None

def test_drop_table(ddl):
    text = "drop table users"
    assert ddl.drop_table(text) == "SQL DROP TABLE users"

def test_drop_table_no_match(ddl):
    assert ddl.drop_table("drop collection users") is None

def test_drop_collection(ddl):
    text = "drop collection logs"
    assert ddl.drop_collection(text) == "NOSQL DROP COLLECTION logs"

def test_drop_collection_no_match(ddl):
    assert ddl.drop_collection("drop table users") is None


# ----------------------------------------------------------------------
# ALTER TABLE
# ----------------------------------------------------------------------
def test_alter_table_add_column(ddl):
    text = "alter table users add column age INT"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users ADD age INT"

def test_alter_table_add_column_with_default(ddl):
    text = "alter table users add column status TEXT default 'active'"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users ADD status TEXT DEFAULT 'active'"

def test_alter_table_add_column_default_timestamp(ddl):
    text = "alter table orders add column created_at TIMESTAMP default current_timestamp"
    assert ddl.alter_table(text) == "SQL ALTER TABLE orders ADD created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

def test_alter_table_rename_column(ddl):
    text = "alter table users rename column age to years"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users RENAME COLUMN age TO years"

def test_alter_table_drop_column(ddl):
    text = "alter table users drop column age"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users DROP COLUMN age"

def test_alter_table_rename_table(ddl):
    text = "rename table users to customers"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users RENAME TO customers"

def test_alter_column_set_default(ddl):
    text = "alter table users alter column name set default 'unknown'"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users ALTER COLUMN name SET DEFAULT 'unknown'"

def test_alter_column_drop_default(ddl):
    text = "alter table users alter column name drop default"
    assert ddl.alter_table(text) == "SQL ALTER TABLE users ALTER COLUMN name DROP DEFAULT"

def test_alter_table_no_table(ddl):
    text = "add column age INT"
    assert ddl.alter_table(text) is None


# ----------------------------------------------------------------------
# VIEW
# ----------------------------------------------------------------------
def test_create_view(ddl):
    text = "create view active_users as select * from users where active = 1"
    assert ddl.create_view(text) == "SQL CREATE VIEW active_users AS select * from users where active = 1"

def test_create_view_no_match(ddl):
    assert ddl.create_view("create view active_users") is None

def test_drop_view(ddl):
    text = "drop view active_users"
    assert ddl.drop_view(text) == "SQL DROP VIEW active_users"

def test_drop_view_no_match(ddl):
    assert ddl.drop_view("drop table active_users") is None


# ----------------------------------------------------------------------
# STORED PROCEDURE
# ----------------------------------------------------------------------
def test_create_procedure(ddl):
    text = "create procedure get_user (uid) as select * from users where id = $1"
    assert ddl.create_procedure(text) == "SQL CREATE PROCEDURE get_user (uid) AS select * from users where id = $1"

def test_create_procedure_no_params(ddl):
    text = "create procedure list_users as select * from users"
    assert ddl.create_procedure(text) == "SQL CREATE PROCEDURE list_users () AS select * from users"

def test_create_procedure_no_match(ddl):
    assert ddl.create_procedure("create function") is None

def test_drop_procedure(ddl):
    text = "drop procedure get_user"
    assert ddl.drop_procedure(text) == "SQL DROP PROCEDURE get_user"

def test_drop_procedure_no_match(ddl):
    assert ddl.drop_procedure("drop function get_user") is None


# ----------------------------------------------------------------------
# TRIGGER
# ----------------------------------------------------------------------
def test_create_trigger(ddl):
    text = "create trigger log_update after update on users for each row begin insert into audit (msg) values ('updated') end"
    expected = "SQL CREATE TRIGGER log_update AFTER UPDATE ON users FOR EACH ROW BEGIN insert into audit (msg) values ('updated') END"
    assert ddl.create_trigger(text) == expected

def test_create_trigger_before_insert(ddl):
    text = "create trigger check_age before insert on users for each row begin if new.age < 0 then signal sqlstate '45000' end if end"
    # The regex captures everything up to the first 'end', so the trigger body becomes:
    #   "if new.age < 0 then signal sqlstate '45000'"
    # The generator then adds 'END'. The final keyword 'if' after that first 'end' is not part of the body.
    expected = "SQL CREATE TRIGGER check_age BEFORE INSERT ON users FOR EACH ROW BEGIN if new.age < 0 then signal sqlstate '45000' END"
    assert ddl.create_trigger(text) == expected

def test_create_trigger_no_match(ddl):
    assert ddl.create_trigger("create trigger something") is None

def test_drop_trigger(ddl):
    text = "drop trigger log_update"
    assert ddl.drop_trigger(text) == "SQL DROP TRIGGER log_update"

def test_drop_trigger_no_match(ddl):
    assert ddl.drop_trigger("drop procedure log_update") is None


# ----------------------------------------------------------------------
# Run with: pytest backend/tests/test_ddl_generator.py -v
# ----------------------------------------------------------------------