"""API 响应模型（Pydantic v2）。

契约与前端严格对应；字段命名不得随意变更，见 docs/DATA_DICTIONARY.md。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Condition(BaseModel):
    met: bool
    label: str
    detail: str


class Signal(BaseModel):
    date: str
    state: str
    state_label: str
    confidence: str
    confidence_label: str
    is_confirmed: bool = Field(
        default=False,
        description="永远为 False：公开数据无法确权到具体国家队主体",
    )
    attribution: str
    summary: str
    conditions: dict[str, Condition]
    caveat: str


class Overview(BaseModel):
    as_of: str
    is_demo: bool
    signal: Signal
    layers: list[dict]
    disclaimer: str


class Health(BaseModel):
    status: str
    version: str
    is_demo: bool
