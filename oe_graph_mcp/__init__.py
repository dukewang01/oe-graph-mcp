"""oe-graph-mcp —— 酒店 OE 运营物资盘点：本地校验、建模、出图、脱敏指纹。

Free and local. No network calls anywhere. Your file never leaves your machine.

工具 / tools
------------
oe_spec       列头契约（这个包里的"标准"）
oe_validate   用契约体检一张盘点表，产出分级问题清单
oe_build      本地生成单文件交互图谱 HTML
oe_fingerprint 脱敏指纹（唯一设计成可外发的输出，白名单约束）
oe_demo       生成合成样例并出图，用于 30 秒内确认装好了

许可证 / license: MIT
"""

from .spec import SPEC_VERSION  # noqa: F401

__version__ = "0.1.0"
__all__ = ["SPEC_VERSION", "__version__"]
