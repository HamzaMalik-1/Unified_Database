from typing import Any

class DataType:
    @staticmethod
    def validate(value: Any, type_name: str) -> Any:
        if type_name.upper() == "INT":
            if isinstance(value, int):
                return value
            elif isinstance(value, str) and value.lstrip('-').isdigit():
                return int(value)
            else:
                raise ValueError(f"Invalid INT value: {value}")
        elif type_name.upper() == "TEXT":
            return str(value)
        elif type_name.upper() == "REAL":
            try:
                return float(value)
            except:
                raise ValueError(f"Invalid REAL value: {value}")
        else:
            raise ValueError(f"Unknown data type: {type_name}")