#!/usr/bin/env python3
"""端到端冒烟：从 GBK 盘点表到交互图谱 HTML。

    python tests/smoke.py

不起 MCP，直接调 core/render —— 用来定位"是契约的问题还是协议的问题"。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oe_graph_mcp import core, render, server  # noqa: E402

OUT = os.path.join(ROOT, "sample", "_smoke")
fails: list[str] = []


def check(cond: bool, label: str, detail: str = ""):
    print(f"   {'✓' if cond else '✗'} {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def call(f, *a, **k):
    """FastMCP/MCPServer 装饰后仍返回原函数；保险起见兼容 .fn。"""
    return getattr(f, "fn", f)(*a, **k)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)

    print("=== 1. 合成样例（GBK 编码，模拟中文 Excel 导出）===")
    csv_path = core.write_sample(os.path.join(OUT, "oe_sample.csv"))
    raw = open(csv_path, "rb").read()
    check(raw[:3] != b"\xef\xbb\xbf" and b"\r\n" in raw, "写出的是 GBK 字节流", f"{len(raw)} B")

    print("=== 2. 读取：编码与表头行探测 ===")
    t = core.read_table(csv_path)
    check(t["encoding"] == "gbk", "GBK 被正确识别", t["encoding"])
    check(t["header_row_index"] == 0, "表头行 = 第 0 行", str(t["header_row_index"]))
    check(len(t["headers"]) == 9, "读到 9 个列头", str(t["headers"]))

    print("=== 3. 归一化：别名映射 ===")
    n = core.normalize(t)
    check(len(n["colmap"]) == 9, "9 个列头全部映射到规范键", json.dumps(n["colmap"], ensure_ascii=False))
    check(n["colmap"].get("outlet") == "营业部门", "「营业部门」→ outlet", n["colmap"].get("outlet", ""))
    check(n["colmap"].get("qty") == "盘点数量", "「盘点数量」→ qty", n["colmap"].get("qty", ""))
    check(not n["unmapped_headers"], "无未识别列头")
    check(len(n["records"]) == 33, "33 行记录", str(len(n["records"])))
    check(n["derived_amount_count"] == 33, "金额缺失时自动推导", str(n["derived_amount_count"]))

    print("=== 3b. 包含关系回退（改过名的表）===")
    alt = os.path.join(OUT, "alt_headers.csv")
    with open(alt, "w", encoding="utf-8") as f:
        f.write("本店资产类别,物料品名,存放地点,实盘数,采购单价,供货商\n瓷器,汤碗,中餐厅,40,36,丙供应商\n")
    na = core.normalize(core.read_table(alt))
    check(na["colmap"].get("outlet") == "存放地点", "「存放地点」→ outlet", na["colmap"].get("outlet", ""))
    check(na["colmap"].get("qty") == "实盘数", "「实盘数」→ qty", na["colmap"].get("qty", ""))
    match_mode = "fuzzy（包含回退）" if na["fuzzy_headers"] else "exact（完全一致）"
    check(na["colmap"].get("category") == "本店资产类别", "「本店资产类别」→ category",
          f"命中方式={match_mode}")
    check("category" in na["fuzzy_headers"], "fuzzy 匹配被显式记录，未静默通过",
          json.dumps(na["fuzzy_headers"], ensure_ascii=False))

    print("=== 4. 校验 ===")
    v = core.validate(n, t)
    check(v["verdict"] in ("pass", "warn"), "verdict", v["verdict"])
    check(not v["errors"], "无错误")
    codes = [w["code"] for w in v["warnings"]]
    check("W_PRICE_OUTLIER" in codes, "单价异常（故意埋的 59000）被抓出", str(codes))

    print("=== 5. 建模 ===")
    g = core.build_graph(n)
    check(len(g["nodes"]) == 55, "节点数", str(len(g["nodes"])))
    check(len(g["links"]) == 89, "边数", str(len(g["links"])))
    check(abs(g["stats"]["total_amount"] - 470010.0) < 0.01, "金额合计", f"¥{g['stats']['total_amount']}")
    check(g["stats"]["abc"] == {"A": 11, "B": 7, "C": 10}, "ABC 分级", str(g["stats"]["abc"]))
    check(len(g["risks"]) > 0, "风险项被识别", f"{len(g['risks'])} 条")
    node_ids = {x["id"] for x in g["nodes"]}
    check(all(l["source"] in node_ids and l["target"] in node_ids for l in g["links"]),
          "所有边两端都存在（无悬空边）")

    print("=== 6. 指纹：白名单 + 无泄漏 ===")
    fp = core.fingerprint(n, g, rooms=320)
    META = {"_digest", "_note", "_not_submitted"}   # 工具追加的、不含数据的元字段
    extra = set(fp) - set(core.spec.FINGERPRINT_FIELDS) - META
    missing = set(core.spec.FINGERPRINT_FIELDS) - set(fp)
    check(not extra and not missing, "指纹字段 = 白名单 ∪ 元字段（无多无少）",
          f"多出={extra or '无'} 缺少={missing or '无'}")
    scalars = [k for k, v in fp.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    check(not scalars, "指纹里没有任何裸数字（只有档位标签与比率）", str(scalars) or "无")
    blob = json.dumps(fp, ensure_ascii=False)
    for secret in ([r["vendor"] for r in n["records"][:6]] + [r["item"] for r in n["records"][:6]]):
        if secret and secret in blob:
            check(False, f"指纹泄漏了「{secret}」")
            break
    else:
        check(True, "指纹不含任何供应商名/品名")
    check(all(k.endswith(("band", "bucket", "mix", "version", "column", "note", "submitted", "digest"))
              or k.startswith("_") or k in ("category_mix", "row_count_bucket") for k in fp),
          "指纹只由比率/档位/占比构成")

    print("=== 7. 渲染：离线自足 ===")
    doc = render.render_html(g, v)
    hp = os.path.join(OUT, "oe_sample_graph.html")
    open(hp, "w", encoding="utf-8").write(doc)
    check("<script src" not in doc, "无外部 <script src>")
    check("http://" not in doc and "https://" not in doc, "无任何 http(s) 引用")
    check("</" not in doc.split("const DATA=")[1].split(";\n")[0] if "const DATA=" in doc else True,
          "注入的 JSON 已转义 </（不可逃逸出 script 标签）")
    check(len(doc) > 20000, "HTML 体量合理", f"{len(doc)} chars")
    check(os.path.getsize(hp) > 20000, "HTML 已落盘", f"{os.path.getsize(hp)} B")

    print("=== 8. 工具层（进程内直调）===")
    s = json.loads(call(server.oe_spec))
    check(s["spec_version"] == "1.0.0", "oe_spec 版本", s["spec_version"])
    b = json.loads(call(server.oe_build, csv_path, os.path.join(OUT, "via_tool.html")))
    check(b["ok"] and os.path.exists(b["out_path"]), "oe_build 落盘", b.get("out_path", ""))
    sc = json.loads(call(server.oe_selfcheck))
    check(sc["network_verdict"].startswith("clean"), "oe_selfcheck：无网络调用", sc["network_verdict"])

    print("=== 9. 错误路径必须分级，不能崩 ===")
    bad = os.path.join(OUT, "bad.csv")
    open(bad, "w", encoding="utf-8").write("品名,数量\n测试,5\n")
    vb = json.loads(call(server.oe_validate, bad))
    check(vb["verdict"] == "fail", "缺必填列 → fail", vb["verdict"])
    check(all(e["code"] == "E_MISSING_REQUIRED_COLUMN" for e in vb["errors"]), "错误码正确",
          str([e["code"] for e in vb["errors"]]))
    junk = os.path.join(OUT, "with_title_rows.csv")
    with open(junk, "w", encoding="utf-8") as f:
        f.write("某某酒店 2026年度运营物资盘点表\n制表:财务部  日期:2026-09-26\n\n"
                "类别,品名,营业点,数量,单价,供应商\n瓷器,餐盘,中餐厅,20,50,甲供应商\n")
    vj = json.loads(call(server.oe_validate, junk))
    check(vj["header_row_index"] == 3, "前置标题行被跳过", f"header_row_index={vj['header_row_index']}")
    check(vj["usable_row_count"] == 1, "可用行数", str(vj["usable_row_count"]))
    r = json.loads(call(server.oe_validate, "Z:/nope/missing.csv"))
    check(r.get("error", {}).get("code") == "E_FILE_NOT_FOUND",
          "文件不存在 → 结构化错误而非异常", str(r)[:120])
    r2 = json.loads(call(server.oe_validate, OUT))
    check(r2.get("error", {}).get("code") == "E_IS_DIRECTORY",
          "传了文件夹 → 结构化错误", str(r2)[:120])
    r3 = json.loads(call(server.oe_fingerprint, "Z:/nope/missing.csv"))
    check(r3.get("error", {}).get("code") == "E_FILE_NOT_FOUND",
          "指纹工具同样不抛异常", str(r3)[:120])

    print()
    if fails:
        print(f"FAIL — {len(fails)} 项未通过：{fails}")
        return 1
    print("SMOKE OK — 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
