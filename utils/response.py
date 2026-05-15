# utils/response.py
from typing import Any, List, Optional, Union
from dataclasses import dataclass, asdict

@dataclass
class APIResponse:
    success: bool
    status: int
    message: str
    data: 'ResponseData'

    def to_dict(self):
        return asdict(self)

@dataclass
class ResponseData:
    type: str   # 'table', 'json', 'text', 'error'
    content: Any
    columns: Optional[List[str]] = None
    row_count: Optional[int] = None
    execution_time_ms: Optional[float] = None   # <-- new field

class ResponseFactory:
    @staticmethod
    def table(content: List[dict], columns: List[str] = None, message: str = "", time_ms: float = None) -> APIResponse:
        if not columns and content:
            columns = list(content[0].keys())
        return APIResponse(
            success=True,
            status=200,
            message=message,
            data=ResponseData(
                type="table",
                content=content,
                columns=columns,
                row_count=len(content),
                execution_time_ms=time_ms
            )
        )

    @staticmethod
    def json(content: Union[dict, list], message: str = "", time_ms: float = None) -> APIResponse:
        return APIResponse(
            success=True,
            status=200,
            message=message,
            data=ResponseData(
                type="json",
                content=content,
                row_count=len(content) if isinstance(content, list) else None,
                execution_time_ms=time_ms
            )
        )

    @staticmethod
    def text(content: str, message: str = "", time_ms: float = None) -> APIResponse:
        return APIResponse(
            success=True,
            status=200,
            message=message or content,
            data=ResponseData(type="text", content=content, execution_time_ms=time_ms)
        )

    @staticmethod
    def error(message: str, status: int = 400, time_ms: float = None) -> APIResponse:
        return APIResponse(
            success=False,
            status=status,
            message=message,
            data=ResponseData(type="error", content=message, execution_time_ms=time_ms)
        )