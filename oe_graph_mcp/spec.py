"""OE 盘点数据契约 —— The OE inventory data contract.

这是整个工具包的"标准"，也是唯一需要冻结的东西。
This module IS the standard: everything else (validate / build / fingerprint)
is derived from it. Freeze this, and the ecosystem can move independently.

设计原则 / Design rules
-----------------------
1. **同行不用改表**。They already have a 盘点表 with their own column names.
   所以每一列都带 `aliases`：中英文/常见叫法自动映射，不必重命名。
2. **能推断的不强制**。金额可以由 数量 x 单价 推出；库龄缺失只降级不报错。
3. **枚举是建议不是关卡**。类别枚举只产生 warning，不产生 error ——
   各家分类习惯不同，硬卡会把人挡在门外。
4. **缺失数据必须显式呈现**。任何被推断/缺失的字段都要回传 flags，
   不许沉默填充 —— 图谱上的每个数字都要能追回原始行。
"""

from __future__ import annotations

SPEC_VERSION = "1.0.0"

# 契约版本升级规则 / bump rules:
#   patch -> 只加 alias 或文档
#   minor -> 加可选列 / 加枚举值
#   major -> 加必填列 / 改类型 / 改语义

# --------------------------------------------------------------------------
# 列定义 / Column definitions
# --------------------------------------------------------------------------
# each: key -> {type, required, aliases, enum?, unit?, note?}
#   required=True 的列缺失，validate 会产出 error 级问题
# --------------------------------------------------------------------------

COLUMNS: dict[str, dict] = {
    # ---- 必填 / required -------------------------------------------------
    "category": {
        "type": "enum", "required": True,
        "aliases": ["类别", "分类", "物料类别", "物资类别", "大类", "品类",
                    "资产类别", "category", "type", "class"],
        "enum": ["不锈钢餐具", "玻璃器皿", "瓷器", "银器", "布草",
                 "厨具", "客房用品", "清洁用品", "其他"],
        "enum_strict": False,
        "note": "枚举外的取值只产生 warning；保留原文以便对照各家口径",
    },
    "item": {
        "type": "str", "required": True,
        "aliases": ["品名", "名称", "物料名称", "物资名称", "物品名称", "资产名称",
                    "item", "name", "item_name", "description"],
        "note": "单个可盘点物件的最小命名单位",
    },
    "outlet": {
        "type": "str", "required": True,
        "aliases": ["营业点", "营业部", "营业部门", "营业点名称", "部门", "使用部门",
                    "归属部门", "存放部门", "存放地点", "所在部门", "所属部门", "点位",
                    "outlet", "department", "dept", "location", "area"],
        "note": "持有/使用该物件的营业点或部门",
    },
    "qty": {
        "type": "float", "required": True,
        "aliases": ["数量", "盘点数量", "实盘数", "实盘数量", "数量(个)", "数量（个）",
                    "qty", "quantity", "count"],
        "unit": "件",
    },
    "unit_price": {
        "type": "float", "required": True,
        "aliases": ["单价", "单价(元)", "单价（元）", "单位成本", "采购单价", "原值单价",
                    "unit_price", "price", "unit cost"],
        "unit": "元/件",
    },
    "vendor": {
        "type": "str", "required": True,
        "aliases": ["供应商", "供货商", "供应商名称", "厂商", "品牌供应商",
                    "vendor", "supplier", "brand"],
        "note": "对外分享时此列应做哈希或替换 —— 供应商与价格属商业敏感信息",
    },

    # ---- 可选 / optional -------------------------------------------------
    "spec": {
        "type": "str", "required": False,
        "aliases": ["规格", "型号", "规格型号", "尺寸", "口径",
                    "spec", "model", "size"],
    },
    "amount": {
        "type": "float", "required": False, "derived": True,
        "aliases": ["金额", "金额(元)", "金额（元）", "总额", "小计", "原值", "账面价值",
                    "amount", "value", "total"],
        "unit": "元",
        "note": "缺失时由 qty x unit_price 推导，并在 flags 中标记 derived",
    },
    "age_months": {
        "type": "float", "required": False,
        "aliases": ["库龄月", "库龄", "库龄(月)", "使用月数", "已用月数", "已使用月数",
                    "age_months", "age", "months_in_use"],
        "unit": "月",
        "note": "用于高库龄识别；缺失只降级，不阻塞",
    },
    "status": {
        "type": "enum", "required": False,
        "aliases": ["状态", "使用状态", "资产状态", "现状", "status", "condition"],
        "enum": ["在用", "闲置", "破损", "报废", "维修中", "在库"],
        "enum_strict": False,
        "note": "用于破损率/闲置率；缺失则该指标不出数并显式标注 N/A",
    },
    "asset_id": {
        "type": "str", "required": False,
        "aliases": ["资产编号", "编号", "卡片号", "资产编码", "台账编号",
                    "asset_id", "id", "tag", "code"],
    },
    "last_count_date": {
        "type": "date", "required": False,
        "aliases": ["最近盘点日", "盘点日期", "上次盘点日", "盘点时间",
                    "last_count_date", "count_date", "last_checked"],
    },
    "custodian": {
        "type": "str", "required": False,
        "aliases": ["责任人", "保管人", "负责人", "使用人", "责任人姓名",
                    "custodian", "owner", "responsible"],
        "sensitive": True,
        "note": "属个人信息。oe_fingerprint 永不输出该列；oe_build 默认移除",
    },
}

REQUIRED_COLUMNS = [k for k, v in COLUMNS.items() if v.get("required")]

#: 别名 -> 规范键 的反向索引（大小写不敏感、忽略空白）
ALIAS_INDEX: dict[str, str] = {}
for _key, _meta in COLUMNS.items():
    for _a in [_key, *_meta.get("aliases", [])]:
        ALIAS_INDEX[_a.strip().lower()] = _key

# 中文列名常见的不可见字符/全角括号，归一化时清掉
import re as _re
_NOISE = _re.compile(r"[\s\u3000_\-/\\()（）\[\]【】:：*]+")


def norm_header(raw: str) -> str:
    """归一化表头以便查别名 / normalise a header before alias lookup."""
    return _NOISE.sub("", str(raw or "").strip().lower())


_NORM_INDEX = {norm_header(k): v for k, v in ALIAS_INDEX.items()}


def map_header_ex(raw: str) -> tuple[str | None, str]:
    """把一个原始表头映射为规范键，并说明匹配强度。

    Returns ``(key, mode)`` where mode is:
      ``"exact"``   —— 表头归一化后与某个别名完全一致
      ``"fuzzy"``   —— 表头包含某个别名（如「营业部门」含「营业部」）
      ``"none"``    —— 认不出，返回 ``(None, "none")``

    Fuzzy matching exists because every hotel names its columns slightly
    differently ("营业部门" / "营业部" / "使用部门" / "点位"). Requiring an
    exact rename is the single biggest adoption blocker for a free tool, so
    we resolve the common variants automatically and *report* which ones were
    inferred rather than silently guessing.
    """
    n = norm_header(raw)
    if not n:
        return None, "none"
    exact = _NORM_INDEX.get(n)
    if exact:
        return exact, "exact"

    # 子串回退：只取最长匹配；同样长但有歧义时宁可放弃，不猜
    cands: list[tuple[int, str]] = [
        (len(alias), key)
        for alias, key in _NORM_INDEX.items()
        if len(alias) >= 2 and alias in n
    ]
    if not cands:
        return None, "none"
    longest = max(c[0] for c in cands)
    best = {key for length, key in cands if length == longest}
    if len(best) == 1:
        return best.pop(), "fuzzy"
    return None, "none"


def map_header(raw: str) -> str | None:
    """把一个原始表头映射为规范键；认不出返回 None。"""
    return map_header_ex(raw)[0]


# --------------------------------------------------------------------------
# 指纹输出字段 / fingerprint fields — the ONLY fields allowed to leave a machine
# --------------------------------------------------------------------------
# 白名单机制：只有这里列出的聚合量可以被回传。
# Allow-list, not deny-list: anything not named here can never be transmitted.
# 全部为比率、分位或区间，不含绝对值、不含名称、不含人。
# --------------------------------------------------------------------------
FINGERPRINT_FIELDS = [
    "spec_version",
    "row_count_bucket",          # 行数量级档位，如 "500-1000"，不是精确值
    "category_mix",              # 类别占比（比率，非金额）
    "value_per_room_band",       # 单位客房持有额所在分位档
    "defect_rate_band",          # 破损率档
    "idle_rate_band",            # 闲置率档
    "old_stock_share_band",      # 高库龄占比档（>36 月）
    "outlet_count_bucket",       # 营业点数量档
    "vendor_count_bucket",       # 供应商数量档（数量，不含名称）
    "has_status_column",         # 布尔
    "has_age_column",            # 布尔
]

#: 明令禁止出现在指纹输出中的内容（自检用）
FINGERPRINT_FORBIDDEN = [
    "vendor", "supplier", "custodian", "责任人", "供应商",
    "unit_price", "amount", "asset_id", "item",
]


def bands() -> dict[str, list[tuple[float, float, str]]]:
    """分位档位定义 / band definitions used by fingerprint output.

    Why bands and not raw numbers: a single hotel's absolute figure is
    commercially sensitive, but "P50-P75 of comparable hotels" is not.
    """
    return {
        "row_count_bucket": [
            (0, 250, "<250"), (250, 500, "250-500"), (500, 1000, "500-1000"),
            (1000, 2000, "1000-2000"), (2000, 5000, "2000-5000"),
            (5000, float("inf"), ">5000"),
        ],
        "outlet_count_bucket": [
            (0, 4, "1-3"), (4, 8, "4-7"), (8, 15, "8-14"), (15, float("inf"), "15+"),
        ],
        "vendor_count_bucket": [
            (0, 6, "1-5"), (6, 15, "6-14"), (15, 30, "15-29"), (30, float("inf"), "30+"),
        ],
        "defect_rate_band": [
            (0, 0.01, "0-1%"), (0.01, 0.03, "1-3%"), (0.03, 0.06, "3-6%"),
            (0.06, 0.10, "6-10%"), (0.10, float("inf"), ">10%"),
        ],
        "idle_rate_band": [
            (0, 0.02, "0-2%"), (0.02, 0.05, "2-5%"), (0.05, 0.10, "5-10%"),
            (0.10, float("inf"), ">10%"),
        ],
        "old_stock_share_band": [
            (0, 0.05, "0-5%"), (0.05, 0.15, "5-15%"), (0.15, 0.30, "15-30%"),
            (0.30, float("inf"), ">30%"),
        ],
        "value_per_room_band": [
            (0, 500, "<500"), (500, 1500, "500-1500"), (1500, 3000, "1500-3000"),
            (3000, 6000, "3000-6000"), (6000, float("inf"), "6000+"),
        ],
    }


def to_band(field: str, value: float | None) -> str | None:
    """把一个数值映射到档位标签 / map a number into its band label."""
    if value is None:
        return None
    for lo, hi, label in bands().get(field, []):
        if lo <= value < hi:
            return label
    return None
