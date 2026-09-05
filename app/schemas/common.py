"""公共通用 schemas — 分页、错误响应等。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """所有 list 接口统一返回结构。

    约定：
    - items 为当前页数据，长度 <= limit
    - total 为符合条件的总记录数（用于前端计算总页数）
    - offset 为本次请求的起始偏移量（= page_index * limit）
    - limit 为本次请求的 page size
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T] = Field(default_factory=list, description="当前页数据")
    total: int = Field(0, ge=0, description="符合条件的总记录数")
    limit: int = Field(50, ge=1, le=500, description="每页数量")
    offset: int = Field(0, ge=0, description="当前页起始偏移量")


class ErrorResponse(BaseModel):
    """统一错误响应（FastAPI HTTPException 会自动生成类似结构，
    此 schema 主要用于业务异常的结构化返回）。"""

    detail: str = Field(..., description="错误描述")
    error_code: str | None = Field(None, description="业务错误码")
    retryable: bool = Field(False, description="是否可重试")
