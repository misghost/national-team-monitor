"""数据适配器基类与契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FetchResult:
    dataset: dict[str, Any]
    source: str
    is_demo: bool
    verified: bool                 # 数据源是否已完成可用性与合规核验
    warnings: list[str] = field(default_factory=list)


class BaseAdapter(ABC):
    name: str = "base"
    verified: bool = False

    @abstractmethod
    def fetch(self, as_of: str) -> FetchResult:
        """返回完整数据集，结构见 docs/DATA_DICTIONARY.md。"""
        raise NotImplementedError
