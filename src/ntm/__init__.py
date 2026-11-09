"""国家队资金三层监控看板 (National Team Monitor)。

公开数据无法日频归因到国家队；本系统只做「分层监控 + 带置信度的概率推断」，
不提供个股买卖建议。详见 docs/METHODOLOGY.md。
"""
from .config import VERSION

__version__ = VERSION

__all__ = ["__version__"]
