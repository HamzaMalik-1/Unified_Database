import json
import os

class Storage:
    def __init__(self, filename: str):
        self.filename = filename
        self.data = {}
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r') as f:
                self.data = json.load(f)
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

    def set_table_data(self, table_name: str, rows):
        self.data["tables"][table_name] = rows
        self.save()