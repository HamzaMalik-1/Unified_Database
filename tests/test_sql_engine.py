
# pytest -v
import pytest
import tempfile
import json
from backend.engine.sql_engine import SQLDatabase
from backend.storage.storage import Storage
from backend.engine.nosql_engine import NoSQLDatabase
from unittest.mock import MagicMock, patch

@pytest.fixture
def db():
    """Create an in‑memory SQLDatabase with a temporary storage file."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        storage = Storage(tmp.name)
        db = SQLDatabase(storage)
        # Mock NoSQL engine for cross‑model tests (if needed)
        mock_nosql = MagicMock(spec=NoSQLDatabase)
        db.set_nosql_engine(mock_nosql)
        return db

def test_create_table(db):
    resp = db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    assert "successful" in resp.message.lower()
    schema = db.get_schema("users")
    assert len(schema) == 2
    assert schema[0]["name"] == "id" and schema[0]["type"] == "INT"

def test_insert_and_select(db):
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice')")
    resp = db.execute("SELECT * FROM users")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"

def test_primary_key_violation(db):
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice')")
    resp = db.execute("INSERT INTO users VALUES (1, 'Bob')")
    assert "Error" in resp.message

def test_unique_constraint(db):
    db.execute("CREATE TABLE users (id INT, email TEXT UNIQUE)")
    db.execute("INSERT INTO users VALUES (1, 'a@b.com')")
    resp = db.execute("INSERT INTO users VALUES (2, 'a@b.com')")
    assert "Error" in resp.message

def test_check_constraint(db):
    db.execute("CREATE TABLE users (id INT, age INT CHECK (age >= 0))")
    resp = db.execute("INSERT INTO users VALUES (1, -5)")
    assert "Check constraint" in resp.message
    db.execute("INSERT INTO users VALUES (1, 25)")
    resp = db.execute("SELECT * FROM users")
    rows = json.loads(resp.data.content)
    assert rows[0]["age"] == 25

def test_default_and_auto_increment(db):
    db.execute("CREATE TABLE users (id SERIAL, name TEXT DEFAULT 'Guest')")
    db.execute("INSERT INTO users (name) VALUES ('Alice')")
    db.execute("INSERT INTO users (id, name) VALUES (10, 'Bob')")
    resp = db.execute("SELECT * FROM users ORDER BY id")
    rows = json.loads(resp.data.content)
    assert rows[0]["id"] == 1 and rows[0]["name"] == "Alice"
    assert rows[1]["id"] == 10 and rows[1]["name"] == "Bob"

def test_alter_table_add_drop_rename(db):
    db.execute("CREATE TABLE users (id INT)")
    db.execute("ALTER TABLE users ADD name TEXT")
    schema = db.get_schema("users")
    assert any(c["name"] == "name" for c in schema)
    db.execute("ALTER TABLE users RENAME COLUMN name TO full_name")
    schema = db.get_schema("users")
    assert any(c["name"] == "full_name" for c in schema)
    db.execute("ALTER TABLE users DROP COLUMN full_name")
    schema = db.get_schema("users")
    assert not any(c["name"] == "full_name" for c in schema)
    db.execute("ALTER TABLE users RENAME TO people")
    assert db.get_schema("people") is not None
    assert db.get_schema("users") is None

def test_update_and_delete(db):
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    db.execute("UPDATE users SET name = 'Alicia' WHERE id = 1")
    resp = db.execute("SELECT name FROM users WHERE id = 1")
    rows = json.loads(resp.data.content)
    assert rows[0]["name"] == "Alicia"
    db.execute("DELETE FROM users WHERE id = 2")
    resp = db.execute("SELECT * FROM users")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1

def test_select_where_conditions(db):
    db.execute("CREATE TABLE users (id INT, age INT, city TEXT)")
    db.execute("INSERT INTO users VALUES (1, 25, 'NYC'), (2, 30, 'LA'), (3, 35, 'NYC')")
    resp = db.execute("SELECT * FROM users WHERE age > 30")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["id"] == 3
    resp = db.execute("SELECT * FROM users WHERE city = 'NYC' AND age < 30")
    rows = json.loads(resp.data.content)
    assert rows[0]["id"] == 1
    resp = db.execute("SELECT * FROM users WHERE age BETWEEN 25 AND 30")
    rows = json.loads(resp.data.content)
    assert len(rows) == 2
    resp = db.execute("SELECT * FROM users WHERE city IN ('NYC', 'LA')")
    rows = json.loads(resp.data.content)
    assert len(rows) == 3

def test_order_by_limit_offset(db):
    db.execute("CREATE TABLE users (id INT, age INT)")
    for i in range(1, 6):
        db.execute(f"INSERT INTO users VALUES ({i}, {i*10})")
    resp = db.execute("SELECT * FROM users ORDER BY age DESC LIMIT 2")
    rows = json.loads(resp.data.content)
    assert rows[0]["age"] == 50 and rows[1]["age"] == 40
    resp = db.execute("SELECT * FROM users ORDER BY age LIMIT 2 OFFSET 1")
    rows = json.loads(resp.data.content)
    assert rows[0]["age"] == 20 and rows[1]["age"] == 30

def test_distinct(db):
    db.execute("CREATE TABLE users (name TEXT)")
    db.execute("INSERT INTO users VALUES ('Alice'), ('Bob'), ('Alice')")
    resp = db.execute("SELECT DISTINCT name FROM users")
    rows = json.loads(resp.data.content)
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert names == {"Alice", "Bob"}

def test_joins(db):
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    db.execute("CREATE TABLE orders (id INT, user_id INT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    db.execute("INSERT INTO orders VALUES (101, 1), (102, 1)")
    resp = db.execute("SELECT users.name, orders.id FROM users INNER JOIN orders ON users.id = orders.user_id")
    rows = json.loads(resp.data.content)
    assert len(rows) == 2
    assert rows[0]["name"] == "Alice"
    resp = db.execute("SELECT users.name, orders.id FROM users LEFT JOIN orders ON users.id = orders.user_id")
    rows = json.loads(resp.data.content)
    assert len(rows) == 3   # Bob has NULL order

def test_group_by_aggregates(db):
    db.execute("CREATE TABLE sales (product TEXT, region TEXT, amount INT)")
    db.execute("INSERT INTO sales VALUES ('Laptop', 'North', 1000), ('Laptop', 'South', 1500), ('Phone', 'North', 500)")
    resp = db.execute("SELECT region, COUNT(*) FROM sales GROUP BY region")
    rows = json.loads(resp.data.content)
    assert {r["region"]: r["COUNT(*)"] for r in rows} == {"North": 2, "South": 1}
    resp = db.execute("SELECT product, SUM(amount) FROM sales GROUP BY product")
    rows = json.loads(resp.data.content)
    assert {r["product"]: r["SUM(amount)"] for r in rows} == {"Laptop": 2500, "Phone": 500}
    resp = db.execute("SELECT region, AVG(amount) FROM sales GROUP BY region HAVING AVG(amount) > 1000")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["region"] == "South"

def test_subqueries(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("CREATE TABLE orders (user_id INT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    db.execute("INSERT INTO orders VALUES (1), (1)")
    resp = db.execute("SELECT name FROM users WHERE id IN (SELECT user_id FROM orders)")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["name"] == "Alice"
    # Scalar subquery
    resp = db.execute("SELECT name, (SELECT COUNT(*) FROM orders WHERE user_id = users.id) AS cnt FROM users")
    rows = json.loads(resp.data.content)
    assert rows[0]["cnt"] == 2 and rows[1]["cnt"] == 0

def test_exists_subquery(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("CREATE TABLE orders (user_id INT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    db.execute("INSERT INTO orders VALUES (1), (1)")
    resp = db.execute("SELECT * FROM users WHERE EXISTS (SELECT 1 FROM orders WHERE orders.user_id = users.id)")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["name"] == "Alice"
    resp = db.execute("SELECT * FROM users WHERE NOT EXISTS (SELECT 1 FROM orders WHERE orders.user_id = users.id)")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["name"] == "Bob"

def test_set_operations(db):
    db.execute("CREATE TABLE t1 (val INT)")
    db.execute("CREATE TABLE t2 (val INT)")
    db.execute("INSERT INTO t1 VALUES (1), (2), (3)")
    db.execute("INSERT INTO t2 VALUES (2), (3), (4)")
    resp = db.execute("SELECT val FROM t1 UNION SELECT val FROM t2")
    rows = json.loads(resp.data.content)
    assert {r["val"] for r in rows} == {1,2,3,4}
    resp = db.execute("SELECT val FROM t1 INTERSECT SELECT val FROM t2")
    rows = json.loads(resp.data.content)
    assert {r["val"] for r in rows} == {2,3}
    resp = db.execute("SELECT val FROM t1 EXCEPT SELECT val FROM t2")
    rows = json.loads(resp.data.content)
    assert {r["val"] for r in rows} == {1}

def test_cte(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    resp = db.execute("WITH cte AS (SELECT * FROM users WHERE id = 1) SELECT * FROM cte")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["name"] == "Alice"

def test_indexes(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    resp = db.execute("CREATE INDEX idx_name ON users (name)")
    assert "successful" in resp.message.lower()
    # Check that index does not break SELECT
    resp = db.execute("SELECT * FROM users WHERE name = 'Alice'")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1
    resp = db.execute("DROP INDEX idx_name")
    assert "successful" in resp.message.lower()

def test_views(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
    db.execute("CREATE VIEW active_users AS SELECT * FROM users WHERE id = 1")
    resp = db.execute("SELECT * FROM active_users")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["name"] == "Alice"
    db.execute("DROP VIEW active_users")
    resp = db.execute("SELECT * FROM active_users")
    assert "Error" in resp.message  # view should be gone

def test_procedures(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'Alice')")
    db.execute("CREATE PROCEDURE get_user (uid) AS SELECT * FROM users WHERE id = $1")
    resp = db.execute("CALL get_user(1)")
    rows = json.loads(resp.data.content)
    assert len(rows) == 1 and rows[0]["name"] == "Alice"
    db.execute("DROP PROCEDURE get_user")
    resp = db.execute("CALL get_user(1)")
    assert "does not exist" in resp.message

def test_triggers(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("CREATE TABLE audit (msg TEXT)")
    # AFTER INSERT trigger
    db.execute("CREATE TRIGGER log_insert AFTER INSERT ON users FOR EACH ROW BEGIN INSERT INTO audit (msg) VALUES ('inserted: ' || NEW.name) END")
    db.execute("INSERT INTO users VALUES (1, 'Alice')")
    resp = db.execute("SELECT * FROM audit")
    rows = json.loads(resp.data.content)
    assert "inserted: Alice" in rows[0]["msg"]
    # AFTER UPDATE trigger
    db.execute("CREATE TRIGGER log_update AFTER UPDATE ON users FOR EACH ROW BEGIN INSERT INTO audit (msg) VALUES ('updated: ' || OLD.name || ' -> ' || NEW.name) END")
    db.execute("UPDATE users SET name = 'Alicia' WHERE id = 1")
    resp = db.execute("SELECT msg FROM audit ORDER BY rowid")
    rows = json.loads(resp.data.content)
    assert "updated: Alice -> Alicia" in rows[1]["msg"]

def test_foreign_key_cascade(db):
    db.execute("CREATE TABLE users (id INT PRIMARY KEY)")
    db.execute("CREATE TABLE orders (id INT, user_id INT REFERENCES users(id) ON DELETE CASCADE)")
    db.execute("INSERT INTO users VALUES (1), (2)")
    db.execute("INSERT INTO orders VALUES (101, 1), (102, 1)")
    db.execute("DELETE FROM users WHERE id = 1")
    resp = db.execute("SELECT * FROM orders")
    rows = json.loads(resp.data.content)
    assert len(rows) == 0  # cascaded

def test_cross_model_insert_nosql(db):
    # Mock NoSQL engine to return a list of documents
    mock_docs = [{"user_id": 10, "user_name": "TestUser"}]
    mock_resp = MagicMock()
    mock_resp.success = True
    mock_resp.data.type = "table"
    mock_resp.data.content = mock_docs
    db.nosql_engine.execute.return_value = mock_resp
    db.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
    resp = db.execute("INSERT INTO users (id, name) SELECT user_id, user_name FROM NOSQL (SELECT user_id, user_name FROM nosql_collection)")
    assert "successful" in resp.message.lower()
    resp = db.execute("SELECT * FROM users")
    rows = json.loads(resp.data.content)
    assert rows[0]["id"] == 10 and rows[0]["name"] == "TestUser"

def test_alter_table_add_check(db):
    db.execute("CREATE TABLE users (id INT, age INT)")
    db.execute("ALTER TABLE users ADD CONSTRAINT age_check CHECK (age >= 0)")
    resp = db.execute("INSERT INTO users VALUES (1, -5)")
    assert "Check constraint" in resp.message
    db.execute("INSERT INTO users VALUES (1, 20)")
    resp = db.execute("SELECT * FROM users")
    rows = json.loads(resp.data.content)
    assert rows[0]["age"] == 20

def test_alter_table_set_default(db):
    db.execute("CREATE TABLE users (id INT, name TEXT)")
    db.execute("ALTER TABLE users ALTER COLUMN name SET DEFAULT 'Unknown'")
    db.execute("INSERT INTO users (id) VALUES (1)")
    resp = db.execute("SELECT name FROM users WHERE id = 1")
    rows = json.loads(resp.data.content)
    assert rows[0]["name"] == "Unknown"
    db.execute("ALTER TABLE users ALTER COLUMN name DROP DEFAULT")
    db.execute("INSERT INTO users (id) VALUES (2)")
    resp = db.execute("SELECT name FROM users WHERE id = 2")
    rows = json.loads(resp.data.content)
    assert rows[0]["name"] is None