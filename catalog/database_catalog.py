# backend/catalog/database_catalog.py
import os
from storage.storage import Storage
from engine.sql_engine import SQLDatabase
from engine.nosql_engine import NoSQLDatabase

class DatabaseCatalog:
    _instance = None
    _databases = {}
    _current_db = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._databases = {}
            cls._instance._current_db = None
        return cls._instance

    def create_database(self, name: str) -> str:
        if name in self._databases:
            return f"Database '{name}' already exists"
        # Create folder for this database
        os.makedirs(f"data/{name}", exist_ok=True)
        sql_storage = Storage(f"data/{name}/sql_data.json")
        nosql_storage = Storage(f"data/{name}/nosql_data.json")
        self._databases[name] = {
            "sql": SQLDatabase(sql_storage),
            "nosql": NoSQLDatabase(nosql_storage)
        }
        return f"Database '{name}' created"

    def use_database(self, name: str) -> str:
        if name not in self._databases:
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