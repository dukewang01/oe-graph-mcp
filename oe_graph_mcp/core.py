"""OE 图谱核心 —— 读取 / 归一化 / 校验 / 建模 / 指纹。

这一层完全不依赖 MCP，可以单独 import、单独测。
本地执行，永不上网。No network calls anywhere in this module.

File stays on the machine. Only :func:`fingerprint` output is designed to
leave, and it is restricted to an allow-list in :mod:`spec`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, date
from typing import Any, Iterable

from . import spec

# --------------------------------------------------------------------------
# 读取 / reading
# --------------------------------------------------------------------------

#: 按顺序尝试的编码。GBK 在中文 Excel 导出里极其常见，放第二位。
ENCODINGS = ("utf-8-sig", "gbk", "utf-16", "utf-8", "latin-1")

_NUM_CLEAN = re.compile(r"[^\d.\-]")


def _try_decode(raw: bytes) -> tuple[str, str]:
    """返回 (文本, 实际编码) / returns (text, encoding_actually_used)."""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _rows_from_csv(raw: bytes) -> tuple[list[list[Any]], str, str | None]:
    text, enc = _try_decode(raw)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return [r for r in reader], enc, None


def _rows_from_xlsx(path: str, sheet: str | None) -> tuple[list[list[Any]], str, str | None]:
    try:
        import openpyxl  # 可选依赖 / optional dependency
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "读取 .xlsx 需要 openpyxl：pip install openpyxl（或先另存为 CSV）"
        ) from exc

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = wb.sheetnames
    chosen = sheet if sheet in sheets else None
    if chosen is None:
        # 选表头命中 alias 最多的那张表 / pick the sheet whose headers match most
        best, best_hits = sheets[0], -1
        for name in sheets:
            hits = 0
            for row in wb[name].iter_rows(min_row=1, max_row=6, values_only=True):
                hits += sum(1 for c in row if c is not None and spec.map_header(c))
                if hits:
                    break
            if hits > best_hits:
                best, best_hits = name, hits
        chosen = best
    rows = [list(r) for r in wb[chosen].iter_rows(values_only=True)]
    wb.close()
    return rows, "xlsx", chosen


def sniff_header_row(rows: list[list[Any]], lookahead: int = 12) -> int:
    """在前若干行里找出表头所在行 / find the header row index.

    真实的盘点表经常有合并标题行、说明行、空行在前。硬取第 0 行是
    最常见的失败原因，所以这里按"命中契约别名的数量"打分。
    """
    best_i, best_score = 0, -1
    for i, row in enumerate(rows[:lookahead]):
        if not row:
            continue
        score = sum(1 for c in row if c is not None and spec.map_header(c))
        # 必填列命中加权，避免把一行无关文字误判成表头
        keys = {spec.map_header(c) for c in row if c is not None and spec.map_header(c)}
        score += 2 * len(keys & set(spec.REQUIRED_COLUMNS))
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def read_table(path: str, sheet: str | None = None) -> dict:
    """读入 CSV/XLSX 为原始二维表 + 元信息。Read a table into rows + meta."""
    if os.path.isdir(path):
        # 传文件夹是最常见的错法之一。不单独处理的话，isfile() 会把它
        # 翻译成"找不到文件"，用户会去反复检查一个明明存在的路径。
        raise IsADirectoryError(f"这是一个文件夹，不是文件 / is a directory: {path}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到文件 / file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)

    if ext in (".xlsx", ".xlsm"):
        rows, encoding, chosen_sheet = _rows_from_xlsx(path, sheet)
    else:
        with open(path, "rb") as fh:
            raw = fh.read()
        rows, encoding, chosen_sheet = _rows_from_csv(raw)

    if not rows:
        raise ValueError("文件是空的 / the file is empty")

    hdr = sniff_header_row(rows)
    headers = [str(c).strip() if c is not None else "" for c in rows[hdr]]
    body = rows[hdr + 1:]

    return {
        "source": os.path.basename(path),
        "path": os.path.abspath(path),
        "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] if size < 64 * 1024 * 1024 else None,
        "size_bytes": size,
        "encoding": encoding,
        "sheet": chosen_sheet,
        "header_row_index": hdr,
        "headers": headers,
        "raw_rows": body,
        "raw_row_count": len(body),
    }


# --------------------------------------------------------------------------
# 归一化 / normalisation
# --------------------------------------------------------------------------

def _to_num(v: Any) -> float | None:
    """把 '1,234.00' / '¥1,234元' / '' / '-' 解析为 float。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return float(v)
    s = unicodedata.normalize("NFKC", str(v)).strip()
    if s in ("", "-", "--", "—", "N/A", "n/a", "NA", "无", "null", "None"):
        return None
    neg = s.startswith("(") and s.endswith(")")   # 会计负数写法 (1,234)
    s = _NUM_CLEAN.sub("", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
                 "%d/%m/%Y", "%m/%d/%Y", "%Y-%m", "%Y年%m月%d日")


def _to_date(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    s = unicodedata.normalize("NFKC", str(v)).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize(table: dict) -> dict:
    """映射表头 + 类型转换，产出规范记录。Map headers and coerce types."""
    colmap: dict[str, str] = {}      # canonical -> actual header text
    unmapped: list[str] = []
    duplicates: list[str] = []
    fuzzy: dict[str, str] = {}       # canonical -> header that was *inferred*

    for h in table["headers"]:
        if not h:
            continue
        key, mode = spec.map_header_ex(h)
        if key is None:
            unmapped.append(h)
        elif key in colmap:
            duplicates.append(h)
        else:
            colmap[key] = h
            if mode == "fuzzy":
                fuzzy[key] = h

    idx = {h: i for i, h in enumerate(table["headers"]) if h}
    records: list[dict] = []
    derived_amount = 0
    dropped_blank = 0

    for r_i, row in enumerate(table["raw_rows"]):
        rec: dict[str, Any] = {"_row": table["header_row_index"] + 2 + r_i}

        for key, header in colmap.items():
            i = idx.get(header)
            raw_v = row[i] if (i is not None and i < len(row)) else None
            ctype = spec.COLUMNS[key]["type"]
            if ctype == "float":
                rec[key] = _to_num(raw_v)
            elif ctype == "date":
                rec[key] = _to_date(raw_v)
            else:
                s = "" if raw_v is None else str(raw_v).strip()
                rec[key] = unicodedata.normalize("NFKC", s)

        # 全空行不算记录 / skip fully blank rows
        if not any(rec.get(k) not in (None, "") for k in ("item", "category", "outlet")):
            dropped_blank += 1
            continue

        # 推导金额 / derive amount when absent
        if not rec.get("amount") and rec.get("qty") is not None and rec.get("unit_price") is not None:
            rec["amount"] = round(rec["qty"] * rec["unit_price"], 2)
            rec["_amount_derived"] = True
            derived_amount += 1
        else:
            rec.setdefault("amount", None)
            rec["_amount_derived"] = False

        records.append(rec)

    return {
        "records": records,
        "colmap": colmap,
        "unmapped_headers": unmapped,
        "fuzzy_headers": fuzzy,
        "duplicate_headers": duplicates,
        "derived_amount_count": derived_amount,
        "dropped_blank_rows": dropped_blank,
        "spec_version": spec.SPEC_VERSION,
    }


# --------------------------------------------------------------------------
# 校验 / validation
# --------------------------------------------------------------------------

def validate(norm: dict, table: dict) -> dict:
    """产出分级问题清单。errors 会阻塞，warnings 不会。"""
    errors: list[dict] = []
    warnings: list[dict] = []
    info: list[dict] = []

    colmap = norm["colmap"]
    records = norm["records"]

    for key in spec.REQUIRED_COLUMNS:
        if key not in colmap:
            meta = spec.COLUMNS[key]
            errors.append({
                "code": "E_MISSING_REQUIRED_COLUMN",
                "column": key,
                "message": f"缺少必填列「{key}」，可接受的写法有：{'、'.join(meta['aliases'][:5])}…",
            })

    if not records:
        errors.append({"code": "E_NO_RECORDS", "message": "表头之后没有可用的数据行"})
        return {"errors": errors, "warnings": warnings, "info": info,
                "verdict": "fail", "spec_version": spec.SPEC_VERSION}

    # ---- 枚举值 / enum membership ---------------------------------------
    for key, meta in spec.COLUMNS.items():
        if meta.get("type") != "enum" or key not in colmap:
            continue
        bad = Counter()
        for rec in records:
            v = (rec.get(key) or "").strip()
            if v and v not in meta["enum"]:
                bad[v] += 1
        if bad:
            top = bad.most_common(6)
            (errors if meta.get("enum_strict") else warnings).append({
                "code": "W_UNKNOWN_ENUM" if not meta.get("enum_strict") else "E_UNKNOWN_ENUM",
                "column": key,
                "message": f"「{key}」有 {len(bad)} 个取值不在建议枚举内（共 {sum(bad.values())} 行）",
                "samples": [f"{v}×{n}" for v, n in top],
                "hint": "保留原文不影响出图；若要参与行业分位对比，建议归并到标准枚举",
            })

    # ---- 空值 / missing values -----------------------------------------
    for key in spec.REQUIRED_COLUMNS:
        if key not in colmap:
            continue
        miss = sum(1 for r in records if r.get(key) in (None, ""))
        if miss:
            errors.append({
                "code": "E_REQUIRED_VALUE_MISSING",
                "column": key,
                "rows": miss,
                "message": f"必填列「{key}」有 {miss} 行为空",
                "samples": [r["_row"] for r in records if r.get(key) in (None, "")][:8],
            })

    for key in ("status", "age_months"):
        if key not in colmap:
            warnings.append({
                "code": "W_OPTIONAL_COLUMN_ABSENT",
                "column": key,
                "message": f"未提供「{key}」，相关指标将显示为 N/A 而不是猜测值",
            })

    # ---- 数值异常 / numeric anomalies ----------------------------------
    neg_qty = [r["_row"] for r in records if (r.get("qty") or 0) < 0]
    if neg_qty:
        errors.append({"code": "E_NEGATIVE_QTY", "rows": len(neg_qty),
                       "message": f"{len(neg_qty)} 行数量为负", "samples": neg_qty[:8]})

    zero_price = sum(1 for r in records if not r.get("unit_price"))
    if zero_price:
        warnings.append({"code": "W_ZERO_PRICE", "rows": zero_price,
                         "message": f"{zero_price} 行单价为空或 0，金额将偏低"})

    outlier_rows = []
    prices = [r["unit_price"] for r in records if r.get("unit_price")]
    if len(prices) >= 8:
        med = statistics.median(prices)
        if med > 0:
            for r in records:
                p = r.get("unit_price")
                if p and (p > med * 50 or p < med / 50):
                    outlier_rows.append(r["_row"])
    if outlier_rows:
        warnings.append({"code": "W_PRICE_OUTLIER", "rows": len(outlier_rows),
                         "message": f"{len(outlier_rows)} 行单价与中位数相差 50 倍以上（可能是千分位/单位错误）",
                         "samples": outlier_rows[:8]})

    if norm["derived_amount_count"]:
        info.append({"code": "I_AMOUNT_DERIVED",
                     "rows": norm["derived_amount_count"],
                     "message": f"{norm['derived_amount_count']} 行金额由 数量×单价 推导（原表无金额列）"})

    # ---- 重复 / duplicates ---------------------------------------------
    if "asset_id" in colmap:
        ids = Counter(r["asset_id"] for r in records if r.get("asset_id"))
        dup = [k for k, n in ids.items() if n > 1]
        if dup:
            warnings.append({"code": "W_DUPLICATE_ASSET_ID", "rows": len(dup),
                             "message": f"资产编号有 {len(dup)} 个重复值", "samples": dup[:8]})
    else:
        key_cols = ("item", "outlet")
        if all(k in colmap for k in key_cols):
            seen = Counter((r.get("item"), r.get("outlet"), r.get("spec")) for r in records)
            dup = sum(n - 1 for n in seen.values() if n > 1)
            if dup:
                info.append({"code": "I_POSSIBLE_DUPLICATE_LINES", "rows": dup,
                             "message": f"{dup} 行在「品名+营业点+规格」上重复，已按明细保留"})

    # ---- 敏感列 / sensitive columns ------------------------------------
    present_sensitive = [k for k, m in spec.COLUMNS.items() if m.get("sensitive") and k in colmap]
    if present_sensitive:
        warnings.append({
            "code": "W_SENSITIVE_COLUMN_PRESENT",
            "column": ",".join(present_sensitive),
            "message": "表中含个人信息列（责任人）。生成图谱默认移除；请勿把原始表直接发给第三方",
        })

    if norm["fuzzy_headers"]:
        info.append({"code": "I_HEADER_INFERRED",
                     "message": f"{len(norm['fuzzy_headers'])} 个列头是按包含关系推断出来的（你的表不用改名）",
                     "samples": [f"{k} ← 「{v}」" for k, v in norm["fuzzy_headers"].items()]})

    if norm["unmapped_headers"]:
        info.append({"code": "I_UNMAPPED_HEADERS",
                     "message": f"{len(norm['unmapped_headers'])} 个列头不在契约内，已忽略",
                     "samples": norm["unmapped_headers"][:12]})

    verdict = "fail" if errors else ("warn" if warnings else "pass")
    return {"errors": errors, "warnings": warnings, "info": info,
            "verdict": verdict, "spec_version": spec.SPEC_VERSION}


# --------------------------------------------------------------------------
# 建模 / graph construction
# --------------------------------------------------------------------------

def _short(text: str, n: int = 60) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def build_graph(norm: dict, *, include_vendors: bool = True,
                include_custodian: bool = False, title: str | None = None) -> dict:
    """把规范记录建成节点/边。产出与 v9r3 同构的 schema。

    聚合层级：品名(+规格) = item 节点；营业点、类别、供应商为维度节点。
    An OE 盘点表 has many rows per physical line; nodes aggregate by
    (item, spec) so the graph reflects *what* is held, not spreadsheet rows.
    """
    records = norm["records"]

    items: dict[str, dict] = {}
    outlets: dict[str, dict] = {}
    cats: dict[str, dict] = {}
    vendors: dict[str, dict] = {}
    edges: dict[tuple, dict] = defaultdict(lambda: {"val": 0.0, "qty": 0.0, "rows": 0})

    def _eid(*parts) -> str:
        return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]

    for rec in records:
        item_name = _short(rec.get("item") or "(未命名)")
        sp = _short(rec.get("spec") or "", 24)
        cat = (rec.get("category") or "未分类").strip() or "未分类"
        outlet = (rec.get("outlet") or "未指定").strip() or "未指定"
        vendor = (rec.get("vendor") or "未指定").strip() or "未指定"
        amt = rec.get("amount") or 0.0
        q = rec.get("qty") or 0.0

        iid = "i" + _eid(item_name, sp)
        oid = "o" + _eid(outlet)
        cid = "c" + _eid(cat)
        vid = "v" + _eid(vendor)

        it = items.setdefault(iid, {
            "id": iid, "name": item_name, "type": "oe_item", "spec": sp,
            "cat": cat, "val": 0.0, "qty": 0.0, "rows": 0,
            "outlets": set(), "vendors": set(),
            "oldest_age": None, "bad_status": 0,
        })
        it["val"] += amt
        it["qty"] += q
        it["rows"] += 1
        it["outlets"].add(outlet)
        it["vendors"].add(vendor)
        if rec.get("age_months") is not None:
            a = rec["age_months"]
            it["oldest_age"] = a if it["oldest_age"] is None else max(it["oldest_age"], a)
        if (rec.get("status") or "") in ("破损", "报废"):
            it["bad_status"] += 1

        ov = outlets.setdefault(oid, {"id": oid, "name": outlet, "type": "oe_outlet",
                                      "val": 0.0, "qty": 0.0, "items": 0, "cats": Counter()})
        ov["val"] += amt
        ov["qty"] += q
        ov["cats"][cat] += 1

        cv = cats.setdefault(cid, {"id": cid, "name": cat, "type": "oe_category",
                                   "val": 0.0, "qty": 0.0, "items": 0})
        cv["val"] += amt
        cv["qty"] += q

        if include_vendors:
            vv = vendors.setdefault(vid, {"id": vid, "name": vendor, "type": "oe_vendor",
                                          "val": 0.0, "qty": 0.0, "items": 0})
            vv["val"] += amt
            vv["qty"] += q

        edges[(cid, iid, "listed_in")]["val"] += amt
        edges[(cid, iid, "listed_in")]["qty"] += q
        edges[(cid, iid, "listed_in")]["rows"] += 1

        edges[(iid, oid, "held_at")]["val"] += amt
        edges[(iid, oid, "held_at")]["qty"] += q
        edges[(iid, oid, "held_at")]["rows"] += 1

        if include_vendors:
            edges[(iid, vid, "supplied_by")]["val"] += amt
            edges[(iid, vid, "supplied_by")]["qty"] += q
            edges[(iid, vid, "supplied_by")]["rows"] += 1

    for it in items.values():
        it["item_count"] = len(it["outlets"])
        it["outlets"] = sorted(it["outlets"])
        it["vendors"] = sorted(it["vendors"])
        it["_outlets_n"] = it.pop("item_count", None)
    for cv in cats.values():
        cv["items"] = sum(1 for it in items.values() if it["cat"] == cv["name"])
    for ov in outlets.values():
        ov["items"] = sum(1 for it in items.values() if ov["name"] in it["outlets"])
        ov["cats"] = dict(ov["cats"].most_common(6))
    for vv in vendors.values():
        vv["items"] = sum(1 for it in items.values() if vv["name"] in it["vendors"])

    nodes = list(cats.values()) + list(outlets.values()) + list(vendors.values()) + list(items.values())
    links = [{"source": s, "target": t, "kind": k, **v} for (s, t, k), v in edges.items()]

    # ---- 帕累托与 ABC / Pareto + ABC on item value ----------------------
    ranked = sorted(items.values(), key=lambda x: -x["val"])
    total_val = sum(x["val"] for x in ranked) or 1.0
    cum = 0.0
    pareto = []
    for rank, it in enumerate(ranked, 1):
        cum += it["val"]
        share = cum / total_val
        grade = "A" if share <= 0.70 else ("B" if share <= 0.90 else "C")
        it["grade"] = grade
        pareto.append({"rank": rank, "name": it["name"], "val": round(it["val"], 2),
                       "share": round(it["val"] / total_val, 5), "cum": round(share, 5),
                       "grade": grade, "cat": it["cat"]})

    # ---- 风险 / risk items ---------------------------------------------
    risks = []
    for it in items.values():
        flags = []
        if it.get("oldest_age") is not None and it["oldest_age"] >= 36:
            flags.append("高库龄")
        if it["bad_status"]:
            flags.append("破损/报废")
        if it["val"] >= 0 and it["qty"] <= 0:
            flags.append("数量为零")
        if flags:
            risks.append({"id": it["id"], "name": it["name"], "cat": it["cat"],
                          "val": round(it["val"], 2), "qty": round(it["qty"], 2),
                          "flags": flags, "age": it.get("oldest_age")})
    risks.sort(key=lambda r: -r["val"])

    # ---- 统计 / stats ---------------------------------------------------
    statuses = Counter((r.get("status") or "").strip() for r in records)
    ages = [r["age_months"] for r in records if r.get("age_months") is not None]
    cat_val = Counter()
    for it in items.values():
        cat_val[it["cat"]] += it["val"]

    stats = {
        "row_count": len(records),
        "item_count": len(items),
        "outlet_count": len(outlets),
        "vendor_count": len(vendors),
        "category_count": len(cats),
        "total_amount": round(total_val, 2),
        "total_qty": round(sum(x["qty"] for x in items.values()), 2),
        "amount_derived_rows": norm["derived_amount_count"],
        "has_status": bool(statuses and any(statuses.values())),
        "has_age": bool(ages),
        "age_median_months": round(statistics.median(ages), 1) if ages else None,
        "age_max_months": round(max(ages), 1) if ages else None,
        "old_stock_count": sum(1 for a in ages if a >= 36),
        "defect_qty": sum(statuses.get(k, 0) for k in ("破损", "报废")),
        "idle_qty": statuses.get("闲置", 0),
        "status_breakdown": dict(statuses.most_common()),
        "category_value": {k: round(v, 2) for k, v in cat_val.most_common()},
        "abc": {
            "A": sum(1 for p in pareto if p["grade"] == "A"),
            "B": sum(1 for p in pareto if p["grade"] == "B"),
            "C": sum(1 for p in pareto if p["grade"] == "C"),
        },
        "top_outlets": [
            {"name": o["name"], "val": round(o["val"], 2), "items": o["items"]}
            for o in sorted(outlets.values(), key=lambda x: -x["val"])[:8]
        ],
    }

    return {
        "meta": {
            "title": title or "OE 运营物资图谱",
            "spec_version": spec.SPEC_VERSION,
            "generator": "oe-graph-mcp",
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": norm.get("source") or "",
            "source_sha256_16": norm.get("source_sha256") or "",
            "privacy": "本地生成 / built locally",
        },
        "nodes": nodes,
        "links": links,
        "pareto": pareto[:40],
        "risks": risks[:60],
        "stats": stats,
    }


# --------------------------------------------------------------------------
# 指纹 / fingerprint — 唯一设计成可以外发的输出
# --------------------------------------------------------------------------

def fingerprint(norm: dict, graph: dict, *, rooms: float | None = None,
                outlets_declared: int | None = None) -> dict:
    """产出可外发的脱敏指纹。允许外发的字段由 spec.FINGERPRINT_FIELDS 白名单定义。

    Never returns names, amounts, prices, vendor identities or people.
    Ratios, shares and band labels only.

    ``rooms``(客房数) 是可选的：给了才能算"单位客房持有额"，
    但输出仍然只是分位档位，不是金额本身。
    """
    st = graph["stats"]
    recs = norm["records"]
    n = len(recs) or 1

    # 类别占比（按行数占比，不是金额分摊；金额本身不出本机）
    cat_mix = Counter((r.get("category") or "未分类").strip() for r in recs)
    category_mix = {k: round(v / n, 4) for k, v in cat_mix.most_common()}

    defect_ratio = (st["defect_qty"] / n) if st["has_status"] else None
    idle_ratio = (st["idle_qty"] / n) if st["has_status"] else None
    old_share = (st["old_stock_count"] / n) if st["has_age"] else None

    value_per_room = None
    if rooms and rooms > 0:
        value_per_room = st["total_amount"] / rooms

    out = {
        "spec_version": spec.SPEC_VERSION,
        "row_count_bucket": spec.to_band("row_count_bucket", st["row_count"]),
        "category_mix": category_mix,
        "value_per_room_band": spec.to_band("value_per_room_band", value_per_room),
        "defect_rate_band": spec.to_band("defect_rate_band", defect_ratio),
        "idle_rate_band": spec.to_band("idle_rate_band", idle_ratio),
        "old_stock_share_band": spec.to_band("old_stock_share_band", old_share),
        "outlet_count_bucket": spec.to_band(
            "outlet_count_bucket",
            outlets_declared if outlets_declared is not None else st["outlet_count"]),
        "vendor_count_bucket": spec.to_band("vendor_count_bucket", st["vendor_count"]),
        "has_status_column": st["has_status"],
        "has_age_column": st["has_age"],
        "_not_submitted": True,
        "_note": "本地计算结果。是否上报由你决定；默认不外发、无网络请求。",
    }
    out = {k: v for k, v in out.items() if k in spec.FINGERPRINT_FIELDS or k.startswith("_")}

    # 自检：白名单之外的字段绝不允许溜出去
    leaked = [k for k in out if not k.startswith("_") and k not in spec.FINGERPRINT_FIELDS]
    if leaked:
        raise AssertionError(f"指纹白名单自检失败 / allow-list violation: {leaked}")

    blob = json.dumps({k: v for k, v in out.items() if not k.startswith("_")},
                      sort_keys=True, ensure_ascii=False)
    out["_digest"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return out


# --------------------------------------------------------------------------
# 样例 / synthetic sample — 零真实数据，用于首次跑通
# --------------------------------------------------------------------------

SAMPLE_ROWS = [
    # category, item, spec, outlet, qty, unit_price, vendor, age_months, status
    ("玻璃器皿", "红酒杯", "240ml", "中餐厅", 180, 32.5, "A供应商", 8, "在用"),
    ("玻璃器皿", "红酒杯", "240ml", "西餐厅", 120, 32.5, "A供应商", 14, "在用"),
    ("玻璃器皿", "水杯", "280ml", "中餐厅", 240, 18.0, "A供应商", 30, "在用"),
    ("玻璃器皿", "水杯", "280ml", "宴会厅", 400, 18.0, "A供应商", 42, "闲置"),
    ("玻璃器皿", "威士忌杯", "300ml", "大堂吧", 96, 45.0, "B供应商", 20, "在用"),
    ("瓷器", "骨瓷餐盘", '9"', "西餐厅", 150, 78.0, "C供应商", 18, "在用"),
    ("瓷器", "骨瓷餐盘", '9"', "中餐厅", 160, 78.0, "C供应商", 26, "破损"),
    ("瓷器", "骨瓷汤碗", '5"', "中餐厅", 140, 56.0, "C供应商", 40, "在用"),
    ("瓷器", "咖啡杯碟", "标准", "大堂吧", 200, 38.0, "C供应商", 12, "在用"),
    ("瓷器", "咖啡杯碟", "标准", "行政酒廊", 80, 38.0, "C供应商", 55, "在用"),
    ("不锈钢餐具", "主餐刀", "标准", "西餐厅", 150, 42.0, "D供应商", 22, "在用"),
    ("不锈钢餐具", "主餐叉", "标准", "西餐厅", 150, 40.0, "D供应商", 22, "在用"),
    ("不锈钢餐具", "汤勺", "标准", "中餐厅", 220, 26.0, "D供应商", 34, "在用"),
    ("不锈钢餐具", "服务勺叉", "标准", "宴会厅", 60, 88.0, "D供应商", 16, "在用"),
    ("不锈钢餐具", "甜品叉", "标准", "西餐厅", 130, 22.0, "D供应商", 48, "闲置"),
    ("银器", "银质茶壶", "标准", "中餐厅", 6, 3200.0, "E供应商", 60, "在用"),
    ("银器", "银质托盘", "大号", "中餐厅", 8, 2400.0, "E供应商", 60, "在用"),
    ("银器", "银质烛台", "标准", "宴会厅", 10, 1800.0, "E供应商", 72, "闲置"),
    ("布草", "床单", "大床", "客房部", 600, 68.0, "F供应商", 10, "在用"),
    ("布草", "床单", "双床", "客房部", 700, 62.0, "F供应商", 10, "在用"),
    ("布草", "枕套", "标准", "客房部", 1200, 18.0, "F供应商", 14, "在用"),
    ("布草", "浴巾", "标准", "客房部", 900, 42.0, "F供应商", 20, "在用"),
    ("布草", "浴巾", "标准", "健身房", 150, 42.0, "F供应商", 28, "破损"),
    ("布草", "台布", "圆桌", "宴会厅", 120, 130.0, "F供应商", 36, "在用"),
    ("布草", "口布", "标准", "宴会厅", 400, 24.0, "F供应商", 36, "在用"),
    ("厨具", "不锈钢汤桶", "50L", "中厨房", 12, 680.0, "G供应商", 44, "在用"),
    ("厨具", "不粘平底锅", "28cm", "西厨房", 18, 320.0, "G供应商", 30, "在用"),
    ("厨具", "砧板", "标准", "中厨房", 24, 95.0, "G供应商", 50, "破损"),
    ("客房用品", "吹风机", "标准", "客房部", 220, 180.0, "H供应商", 24, "在用"),
    ("客房用品", "电热水壶", "1.2L", "客房部", 210, 150.0, "H供应商", 24, "在用"),
    ("客房用品", "衣架", "标准", "客房部", 1600, 12.0, "H供应商", 40, "在用"),
    ("清洁用品", "布草车", "标准", "客房部", 14, 1200.0, "I供应商", 56, "在用"),
    ("清洁用品", "吸尘器", "标准", "公区", 8, 2600.0, "I供应商", 38, "维修中"),
]

SAMPLE_HEADERS = ["资产类别", "物品名称", "规格型号", "营业部门", "盘点数量",
                  "单价(元)", "供应商名称", "使用月数", "使用状态"]


def write_sample(path: str, *, rooms: int = 320) -> str:
    """写出行级样例（GBK 编码，模拟中文 Excel 导出）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="gbk", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SAMPLE_HEADERS)
        w.writerows(SAMPLE_ROWS)
    return os.path.abspath(path)


def sample_note(rooms: int = 320) -> str:
    return (
        f"合成样例，非真实数据。饭店假设 {rooms} 间客房。\n"
        f"生成方式：oe-graph-mcp 内置合成器 / synthetic, generated by oe-graph-mcp.\n"
        "注意表头故意写成「营业部部门」「盘点数量」「使用月数」等非契约写法，\n"
        "用以验证别名映射是否生效。"
    )
