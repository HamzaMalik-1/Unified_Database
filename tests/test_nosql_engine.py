import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # project root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))  # backend folder

import pytest
import tempfile
from engine.nosql_engine import NoSQLDatabase
from storage.storage import Storage

@pytest.fixture
def nosql_db():
    """Create a NoSQLDatabase with a temporary storage file."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        storage = Storage(tmp.name)
        db = NoSQLDatabase(storage)
        return db

# -------------------------------------------------------------------
# Collection management
# -------------------------------------------------------------------
def test_create_collection(nosql_db):
    resp = nosql_db.execute("CREATE COLLECTION users")
    assert "created" in resp.message.lower()
    assert "users" in nosql_db.storage.data['collections']

def test_create_duplicate_collection(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    resp = nosql_db.execute("CREATE COLLECTION users")
    assert "already exists" in resp.message.lower()

def test_drop_collection(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    resp = nosql_db.execute("DROP COLLECTION users")
    assert "dropped" in resp.message.lower()
    assert "users" not in nosql_db.storage.data['collections']

# -------------------------------------------------------------------
# Insert & Select
# -------------------------------------------------------------------
def test_insert_and_select(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, name, age) VALUES (1, 'Alice', 30)")
    resp = nosql_db.execute("SELECT * FROM users")
    data = resp.data.content
    assert len(data) == 1
    assert data[0]['name'] == 'Alice'

def test_select_with_projection(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, name, age) VALUES (1, 'Alice', 30)")
    resp = nosql_db.execute("SELECT name, age FROM users")
    data = resp.data.content
    assert list(data[0].keys()) == ['name', 'age']

def test_nested_documents(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, address) VALUES (1, {'city':'Boston','zip':123})")
    resp = nosql_db.execute("SELECT address.city FROM users")
    data = resp.data.content
    assert data[0]['address.city'] == 'Boston'

def test_where_condition(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, age) VALUES (1, 25)")
    nosql_db.execute("INSERT INTO users (id, age) VALUES (2, 35)")
    resp = nosql_db.execute("SELECT * FROM users WHERE age > 30")
    data = resp.data.content
    assert len(data) == 1 and data[0]['id'] == 2

def test_where_contains_string(nosql_db):
    nosql_db.execute("CREATE COLLECTION docs")
    nosql_db.execute("INSERT INTO docs (id, content) VALUES (1, 'The quick fox')")
    resp = nosql_db.execute("SELECT * FROM docs WHERE content CONTAINS 'fox'")
    data = resp.data.content
    assert len(data) == 1

# -------------------------------------------------------------------
# Update (including array operators)
# -------------------------------------------------------------------
def test_update_set(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    nosql_db.execute("UPDATE users SET name = 'Alicia' WHERE id = 1")
    resp = nosql_db.execute("SELECT name FROM users WHERE id = 1")
    data = resp.data.content
    assert data[0]['name'] == 'Alicia'

def test_update_array_push(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, hobbies) VALUES (1, ['reading'])")
    nosql_db.execute("UPDATE users SET hobbies PUSH 'swimming' WHERE id = 1")
    resp = nosql_db.execute("SELECT hobbies FROM users WHERE id = 1")
    data = resp.data.content
    assert data[0]['hobbies'] == ['reading', 'swimming']

def test_update_array_pull(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, hobbies) VALUES (1, ['reading', 'swimming'])")
    nosql_db.execute("UPDATE users SET hobbies PULL 'reading' WHERE id = 1")
    resp = nosql_db.execute("SELECT hobbies FROM users WHERE id = 1")
    data = resp.data.content
    assert data[0]['hobbies'] == ['swimming']

def test_update_array_addtoset(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, hobbies) VALUES (1, ['reading'])")
    nosql_db.execute("UPDATE users SET hobbies ADDTOSET 'reading' WHERE id = 1")  # duplicate ignored
    nosql_db.execute("UPDATE users SET hobbies ADDTOSET 'dancing' WHERE id = 1")
    resp = nosql_db.execute("SELECT hobbies FROM users WHERE id = 1")
    data = resp.data.content
    assert data[0]['hobbies'] == ['reading', 'dancing']

# -------------------------------------------------------------------
# Delete
# -------------------------------------------------------------------
def test_delete_with_condition(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, age) VALUES (1, 20)")
    nosql_db.execute("INSERT INTO users (id, age) VALUES (2, 40)")
    nosql_db.execute("DELETE FROM users WHERE age > 30")
    resp = nosql_db.execute("SELECT * FROM users")
    data = resp.data.content
    assert len(data) == 1 and data[0]['age'] == 20

def test_delete_all(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id) VALUES (1)")
    nosql_db.execute("INSERT INTO users (id) VALUES (2)")
    nosql_db.execute("DELETE FROM users")
    resp = nosql_db.execute("SELECT * FROM users")
    data = resp.data.content
    assert data == []

# -------------------------------------------------------------------
# ORDER BY, LIMIT, OFFSET
# -------------------------------------------------------------------
def test_order_by(nosql_db):
    nosql_db.execute("CREATE COLLECTION sales")
    nosql_db.execute("INSERT INTO sales (amount) VALUES (100)")
    nosql_db.execute("INSERT INTO sales (amount) VALUES (50)")
    nosql_db.execute("INSERT INTO sales (amount) VALUES (200)")
    resp = nosql_db.execute("SELECT amount FROM sales ORDER BY amount DESC")
    data = resp.data.content
    assert [d['amount'] for d in data] == [200, 100, 50]

def test_limit_offset(nosql_db):
    nosql_db.execute("CREATE COLLECTION items")
    for i in range(1, 6):
        nosql_db.execute(f"INSERT INTO items (id) VALUES ({i})")
    resp = nosql_db.execute("SELECT id FROM items LIMIT 2 OFFSET 1")
    data = resp.data.content
    # offset 1 means skip first row -> rows 2 and 3
    assert data[0]['id'] == 2
    assert data[1]['id'] == 3

# -------------------------------------------------------------------
# GROUP BY & Aggregates
# -------------------------------------------------------------------
def test_group_by_single_column(nosql_db):
    nosql_db.execute("CREATE COLLECTION sales")
    nosql_db.execute("INSERT INTO sales (product, amount) VALUES ('A', 100)")
    nosql_db.execute("INSERT INTO sales (product, amount) VALUES ('A', 200)")
    nosql_db.execute("INSERT INTO sales (product, amount) VALUES ('B', 50)")
    resp = nosql_db.execute("SELECT product, SUM(amount) AS total FROM sales GROUP BY product")
    data = resp.data.content
    result = {row['product']: row['total'] for row in data}
    assert result == {'A': 300, 'B': 50}

def test_group_by_multiple_columns(nosql_db):
    nosql_db.execute("CREATE COLLECTION sales")
    inserts = [
        "INSERT INTO sales (product, region, amount) VALUES ('Laptop', 'North', 1000)",
        "INSERT INTO sales (product, region, amount) VALUES ('Laptop', 'South', 1500)",
        "INSERT INTO sales (product, region, amount) VALUES ('Phone', 'North', 500)",
        "INSERT INTO sales (product, region, amount) VALUES ('Phone', 'South', 600)"
    ]
    for ins in inserts:
        nosql_db.execute(ins)
    resp = nosql_db.execute("SELECT product, region, SUM(amount) AS total FROM sales GROUP BY product, region")
    data = resp.data.content
    assert len(data) == 4
    laptop_north = next(d for d in data if d['product'] == 'Laptop' and d['region'] == 'North')
    assert laptop_north['total'] == 1000

def test_having(nosql_db):
    nosql_db.execute("CREATE COLLECTION sales")
    nosql_db.execute("INSERT INTO sales (product, amount) VALUES ('A', 100)")
    nosql_db.execute("INSERT INTO sales (product, amount) VALUES ('A', 50)")
    nosql_db.execute("INSERT INTO sales (product, amount) VALUES ('B', 200)")
    resp = nosql_db.execute("SELECT product, SUM(amount) AS total FROM sales GROUP BY product HAVING total > 100")
    data = resp.data.content
    assert len(data) == 1 and data[0]['product'] == 'B'

def test_aggregate_without_group_by(nosql_db):
    nosql_db.execute("CREATE COLLECTION sales")
    nosql_db.execute("INSERT INTO sales (amount) VALUES (10)")
    nosql_db.execute("INSERT INTO sales (amount) VALUES (20)")
    nosql_db.execute("INSERT INTO sales (amount) VALUES (30)")
    resp = nosql_db.execute("SELECT COUNT(*), SUM(amount), AVG(amount) FROM sales")
    data = resp.data.content
    row = data[0]
    assert row['COUNT(*)'] == 3
    assert row['SUM(amount)'] == 60
    assert row['AVG(amount)'] == 20

# -------------------------------------------------------------------
# Indexes
# -------------------------------------------------------------------
def test_create_equality_index(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    nosql_db.execute("INSERT INTO users (id, name) VALUES (2, 'Bob')")
    resp = nosql_db.execute("CREATE INDEX idx_id ON users (id)")
    assert "successfully" in resp.message.lower()
    resp = nosql_db.execute("SELECT * FROM users WHERE id = 1")
    data = resp.data.content
    assert len(data) == 1 and data[0]['name'] == 'Alice'

def test_drop_index(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("CREATE INDEX idx_name ON users (name)")
    resp = nosql_db.execute("DROP INDEX idx_name")
    assert "successfully" in resp.message.lower()
    assert "idx_name" not in nosql_db.storage.data['nosql_indexes']

def test_create_text_index(nosql_db):
    nosql_db.execute("CREATE COLLECTION docs")
    nosql_db.execute("INSERT INTO docs (id, content) VALUES (1, 'The quick brown fox')")
    resp = nosql_db.execute("CREATE TEXT INDEX idx_content ON docs (content)")
    assert "successfully" in resp.message.lower()
    resp = nosql_db.execute("SELECT * FROM docs WHERE content CONTAINS 'fox'")
    data = resp.data.content
    assert len(data) == 1

def test_drop_text_index(nosql_db):
    nosql_db.execute("CREATE COLLECTION docs")
    nosql_db.execute("CREATE TEXT INDEX idx_content ON docs (content)")
    resp = nosql_db.execute("DROP TEXT INDEX idx_content")
    assert "successfully" in resp.message.lower()
    assert "idx_content" not in nosql_db.storage.data['text_indexes']

def test_stats(nosql_db):
    nosql_db.execute("CREATE COLLECTION users")
    nosql_db.execute("INSERT INTO users (id) VALUES (1)")
    nosql_db.execute("CREATE INDEX idx_id ON users (id)")
    stats = nosql_db.get_stats()
    assert stats['total_collections'] == 1
    assert stats['total_indexes'] == 1
    assert 'users' in stats['collections']
    assert stats['collections']['users']['document_count'] == 1