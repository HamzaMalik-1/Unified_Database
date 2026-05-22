# backend/catalog/database_catalog.py
import os
from storage.storage import Storage
from engine.sql_engine import SQLDatabase
from engine.nosql_engine import NoSQLDatabase
from typing import List, Dict, Any

class DatabaseCatalog:
    _instance = None
    _databases = {}
    _current_db = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._databases = {}
            cls._instance._current_db = None
            cls._instance._load_existing_databases()   # <-- load on startup
        return cls._instance

    def _load_existing_databases(self):
        """Scan data/ folder and load any existing databases."""
        data_dir = "data"
        if not os.path.exists(data_dir):
            return
        for db_name in os.listdir(data_dir):
            db_path = os.path.join(data_dir, db_name)
            if os.path.isdir(db_path):
                sql_file = os.path.join(db_path, "sql_data.json")
                nosql_file = os.path.join(db_path, "nosql_data.json")
                if os.path.exists(sql_file) and os.path.exists(nosql_file):
                    self._load_database(db_name)

    def _load_database(self, name: str):
        """Load a database from disk into memory."""
        if name in self._databases:
            return
        os.makedirs(f"data/{name}", exist_ok=True)
        sql_storage = Storage(f"data/{name}/sql_data.json")
        nosql_storage = Storage(f"data/{name}/nosql_data.json")
        sql_engine = SQLDatabase(sql_storage)
        nosql_engine = NoSQLDatabase(nosql_storage)
        sql_engine.set_nosql_engine(nosql_engine)
        self._databases[name] = {
            "sql": sql_engine,
            "nosql": nosql_engine
        }

    def create_database(self, name: str) -> str:
        if name in self._databases:
            return f"Database '{name}' already exists"
        self._load_database(name)   # reuse loading logic
        return f"Database '{name}' created"

    def use_database(self, name: str) -> str:
        # If database exists on disk but not loaded, load it
        if name not in self._databases:
            db_path = f"data/{name}"
            if os.path.exists(db_path) and os.path.exists(f"{db_path}/sql_data.json"):
                self._load_database(name)
            else:
                return f"Database '{name}' does not exist. Create it first with DATABASE CREATE {name}"
        self._current_db = name
        return f"Now using database '{name}'"

    def get_current_sql_engine(self):
        if self._current_db is None:
            raise Exception("No database selected. Use 'DATABASE USE <name>' first.")
        return self._databases[self._current_db]["sql"]

    def get_current_nosql_engine(self):
        if self._current_db is None:
            raise Exception("No database selected. Use 'DATABASE USE <name>' first.")
        return self._databases[self._current_db]["nosql"]

    def get_current_database_name(self) -> str:
        if self._current_db is None:
            raise Exception("No database selected. Use 'DATABASE USE <name>' first.")
        return self._current_db

    def get_all_database_names(self) -> List[str]:
        return list(self._databases.keys())

    def get_database_stats(self, db_name: str) -> Dict[str, Any]:
        if db_name not in self._databases:
            raise ValueError(f"Database '{db_name}' does not exist")
        sql_engine = self._databases[db_name]["sql"]
        nosql_engine = self._databases[db_name]["nosql"]
        return {
            "sql": sql_engine.get_stats(),
            "nosql": nosql_engine.get_stats()
        }
        
        
    def get_current_database_name(self) -> str:
        if self._current_db is None:
            raise Exception("No database selected. Use 'DATABASE USE <name>' first.")
        return self._current_db