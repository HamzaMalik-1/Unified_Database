# backend/storage/storage.py
import json
import os

class Storage:
    def __init__(self, filename: str):
        self.filename = filename
        self.data = {}
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.data = {"schemas": {}, "tables": {}}
        else:
            self.data = {"schemas": {}, "tables": {}}

    def save(self):
        with open(self.filename, 'w') as f:
            json.dump(self.data, f, indent=2)

    def get_schema(self, table_name: str):
        return self.data["schemas"].get(table_name)

    def set_schema(self, table_name: str, schema):
        self.data["schemas"][table_name] = schema
        self.save()

    def get_table_data(self, table_name: str):
        return self.data["tables"].get(table_name, [])

    def set_table_data(self, table_name: str, rows, skip_save: bool = False):
        self.data["tables"][table_name] = rows
        if not skip_save:
            self.save()