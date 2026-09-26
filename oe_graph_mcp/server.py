"""MCP 工具层 —— the MCP surface.

四个工具，全部在调用方机器上执行：

    oe_spec         列头契约（标准本体，不含任何数据）
    oe_validate     体检一张盘点表，产出分级问题清单
    oe_build        本地生成单文件交互图谱 HTML
    oe_fingerprint  脱敏指纹（唯一设计成可外发的输出）

加一个 oe_demo 用于 30 秒内确认"装好了"。

设计承诺 / guarantees
----------------------
* **无网络**。本模块不 import requests/urllib/socket，不做任何网络调用。
  可以用 `grep -nE "urllib|requests|socket|http" oe_graph_mcp/*.py` 自查。
* **默认不外发**。只有 oe_fingerprint 的返回值被设计成可以离开机器，
  且受 spec.FINGERPRINT_FIELDS 白名单约束；它自己也不发送任何东西。
* **不写回源文件**。任何工具都不修改输入文件；oe_build 只写新文件。
* **stdout 是协议通道**。所有诊断信息走 stderr —— 往 stdout 打一个 print
  就会破坏 JSON-RPC 流，这是本地 MCP server 最常见的自伤方式。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

if __package__ in (None, ""):
    # 允许两种启动方式：
    #   python oe_graph_mcp/server.py       ← MCP 客户端通常直接指向 .py 文件
    #   python -m oe_graph_mcp.server       ← 包方式
    # 不做这一步的话，直接指 .py 会 ImportError: attempted relative import
    # with no known parent package —— 而且是在客户端里静默失败。
    import os as _os
    import sys as _sys
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from oe_graph_mcp import core, render, spec

    _DIRECT_SCRIPT = True
else:
    from . import core, render, spec

    _DIRECT_SCRIPT = False

# --------------------------------------------------------------------------
# 兼容层 / compatibility shim
# --------------------------------------------------------------------------
# MCP SDK 在 2.0 把 mcp.server.fastmcp 换成了 mcp.server.mcpserver。
# 这台机器上就同时存在两个版本：Hermes venv = 2.0.0（FastMCP 已删除），
# 系统 Python 3.14 = 1.26.0。所以在这个分界期里，兼容层不是洁癖，是必需。
# --------------------------------------------------------------------------

def _make_server(name: str, instructions: str):
    try:                                    # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer
        # serverInfo.version：1.x 默认填 SDK 自己的版本号，2.x 会留空白。
        # 显式给一个，让客户端界面上显示的是这个包的版本。
        # 注意与 pyproject.toml 的 version 保持一致。
        try:
            return MCPServer(name=name, instructions=instructions, version="0.1.0")
        except TypeError:
            return MCPServer(name=name, instructions=instructions)
    except ImportError:
        pass
    try:                                    # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        try:
            return FastMCP(name=name, instructions=instructions, version="0.1.0")
        except TypeError:
            return FastMCP(name=name, instructions=instructions)
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未找到 MCP SDK。请安装：pip install 'mcp>=1.26'（或 uvx 直接运行本包）"
        ) from exc


INSTRUCTIONS = (
    "酒店 OE（运营物资）盘点工具包。全部在本地执行，不上传、不联网。"
    "先调 oe_spec 拿列头契约，再用 oe_validate 体检客人的盘点表，"
    "然后 oe_build 出图、oe_fingerprint 得到可外发的脱敏指纹。"
    "若客人没有数据文件，先调 oe_demo 生成合成样例跑通全流程。"
)

app = _make_server("oe-graph", INSTRUCTIONS)


def _j(obj: Any) -> str:
    """统一以 JSON 文本返回。

    为什么返回字符串而不是 dict：MCP 1.x/2.x 对结构化返回的处理不同，
    而文本在**任何**客户端上都能读。一个要免费发给陌生环境的工具，
    兼容性优先于优雅。
    """
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _log(msg: str) -> None:
    """诊断只走 stderr，绝不污染 stdout 的 JSON-RPC 流。"""
    print(f"[oe-graph] {msg}", file=sys.stderr, flush=True)


def _no_network_audit() -> list[str]:
    """自查：本包源码里不允许出现网络调用。"""
    banned = ("urllib", "requests", "httpx", "socket", "aiohttp", "http.client")
    hits: list[str] = []
    here = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(here, fn), "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                s = line.strip()
                if s.startswith("#") or s.startswith('"') or s.startswith("*"):
                    continue
                for b in banned:
                    if f"import {b}" in s or f"from {b}" in s:
                        hits.append(f"{fn}:{i}: {s[:80]}")
    return hits


# --------------------------------------------------------------------------
# 错误边界 / error boundary
# --------------------------------------------------------------------------
# 对陌生人环境里的免费工具，"抛异常"等于客户端只看到一句 traceback，
# 调用方 agent 分不清是路径写错了、格式不对，还是工具本身坏了。
# 所以所有工具统一在这里兜一层：任何异常都变成结构化 JSON。
#
# 做法是在实例上替换 app.tool，而不是给六个工具各加一个装饰器 ——
# 好处是以后新增的工具自动继承这个边界，不会漏。
# （已实测 mcp 1.26.0 / 2.0.0 两个 SDK 的 app.tool 都允许实例级赋值。）
# --------------------------------------------------------------------------

import functools as _functools      # noqa: E402  就近导入，仅本区块使用
import traceback as _traceback      # noqa: E402


def _safe(fn):
    """把工具体内的任何异常转成结构化 JSON 错误。"""
    @_functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError as exc:
            return _j({"error": {"code": "E_FILE_NOT_FOUND", "message": str(exc),
                                 "hint": "检查路径；Windows 上把 \\ 写成 \\\\ 或直接用 /。"}})
        except IsADirectoryError as exc:
            return _j({"error": {"code": "E_IS_DIRECTORY", "message": str(exc),
                                 "hint": "path 要给到文件，不是文件夹。"}})
        except PermissionError as exc:
            return _j({"error": {"code": "E_PERMISSION", "message": str(exc),
                                 "hint": "文件可能正被 Excel/WPS 打开，先关掉再试。"}})
        except (ValueError, KeyError, TypeError) as exc:
            return _j({"error": {"code": "E_BAD_INPUT",
                                 "message": f"{type(exc).__name__}: {exc}"}})
        except Exception as exc:                    # noqa: BLE001
            _log("UNEXPECTED " + _traceback.format_exc().replace("\n", " | "))
            return _j({"error": {"code": "E_INTERNAL",
                                 "message": f"{type(exc).__name__}: {exc}",
                                 "hint": "这是工具自身的缺陷，不是你的表的问题。"
                                         "请把这条消息连同表头（不要数据）反馈给作者。"}})
    return wrapper


_orig_tool = app.tool


def _tool_with_boundary(*args, **kwargs):
    deco = _orig_tool(*args, **kwargs)

    def wrap(fn):
        return deco(_safe(fn))
    return wrap


app.tool = _tool_with_boundary


# --------------------------------------------------------------------------
# 工具 / tools
# --------------------------------------------------------------------------

@app.tool()
def oe_spec(format: str = "json") -> str:
    """返回 OE 盘点数据契约（列头契约 / 标准本体）。

    这是这个工具包里唯一需要冻结的东西：别的工具都从它派生。
    拿到契约后，客人的 agent 就知道该怎么准备表格 —— 通常是**不用改**
    现有盘点表，因为契约认别名。

    Args:
        format: "json" 返回机读契约；"md" 返回人读的 Markdown 说明。

    Returns:
        契约内容。**不含任何数据**，可以安全地给任何人。
    """
    if format == "md":
        lines = [f"# OE 盘点数据契约 v{spec.SPEC_VERSION}", ""]
        lines.append("## 必填列 / required")
        lines.append("")
        lines.append("| 规范键 | 含义 | 可接受的列头写法（部分） |")
        lines.append("|---|---|---|")
        for k in spec.REQUIRED_COLUMNS:
            m = spec.COLUMNS[k]
            lines.append(f"| `{k}` | {m.get('note') or k} | {'、'.join(m['aliases'][:8])} … |")
        lines.append("")
        lines.append("## 可选列 / optional")
        lines.append("")
        lines.append("| 规范键 | 含义 | 说明 |")
        lines.append("|---|---|---|")
        for k, m in spec.COLUMNS.items():
            if m.get("required"):
                continue
            note = m.get("note") or ("枚举：" + "、".join(m.get("enum", []))) or ""
            lines.append(f"| `{k}` | {'、'.join(m['aliases'][:4])} | {note} |")
        lines.append("")
        lines.append("## 可外发的指纹字段（白名单）")
        lines.append("")
        for f in spec.FINGERPRINT_FIELDS:
            lines.append(f"- `{f}`")
        lines.append("")
        lines.append("白名单之外的任何字段都不会出现在指纹输出中 —— "
                     "这是一个 allow-list，不是 deny-list。")
        return "\n".join(lines)

    return _j({
        "spec_version": spec.SPEC_VERSION,
        "columns": {
            k: {kk: vv for kk, vv in v.items() if kk != "enum" or True}
            for k, v in spec.COLUMNS.items()
        },
        "required_columns": spec.REQUIRED_COLUMNS,
        "aliases_resolved_automatically": True,
        "fingerprint_fields": spec.FINGERPRINT_FIELDS,
        "fingerprint_forbidden": spec.FINGERPRINT_FORBIDDEN,
        "bands": {k: [b[2] for b in v] for k, v in spec.bands().items()},
        "notes": [
            "列头不用改：契约按别名匹配，「营业部门」「盘点数量」「使用月数」都能认。",
            "金额缺失时由 数量×单价 推导，并在结果里标记 derived。",
            "类别枚举是建议不是关卡：取值不在枚举内只产生 warning。",
            "含「责任人」等个人信息列时，图谱默认移除该列，并给出提醒。",
        ],
    })


@app.tool()
def oe_validate(path: str, sheet: str | None = None) -> str:
    """用契约体检一张盘点表，产出分级问题清单（错误 / 警告 / 信息）。

    文件留在本机：本工具只读取，不修改、不上传。

    Args:
        path: CSV 或 XLSX 绝对路径。中文 Excel 导出的 GBK 编码会自动识别。
        sheet: XLSX 的工作表名；省略时自动选表头命中最多的一张。

    Returns:
        JSON：{verdict, spec_version, colmap, errors[], warnings[], info[]}

    verdict 取值：pass / warn / fail。fail 表示必填列缺失或必填值为空，
    出图结果会失真，应先修表。
    """
    try:
        table = core.read_table(path, sheet)
        norm = core.normalize(table)
        issues = core.validate(norm, table)
        return _j({
            "verdict": issues["verdict"],
            "spec_version": issues["spec_version"],
            "source": table["source"],
            "encoding_detected": table["encoding"],
            "sheet": table["sheet"],
            "header_row_index": table["header_row_index"],
            "raw_row_count": table["raw_row_count"],
            "usable_row_count": len(norm["records"]),
            "column_mapping": norm["colmap"],
            "headers_inferred": norm["fuzzy_headers"],
            "headers_unmapped": norm["unmapped_headers"],
            "errors": issues["errors"],
            "warnings": issues["warnings"],
            "info": issues["info"],
        })
    except Exception as exc:
        # 这里只记日志，不在这里编错误格式 —— 格式统一由 _safe 边界产出。
        _log("tool error: " + traceback.format_exc().replace("\n", " | "))
        raise


@app.tool()
def oe_build(path: str, out: str | None = None, title: str | None = None,
             sheet: str | None = None, include_vendors: bool = True) -> str:
    """本地生成单文件交互图谱 HTML。

    产出是一个离线 HTML：无 CDN、无外部字体、无任何网络请求，双击即可打开，
    也可以直接拷给别人。包含图谱 / 帕累托(ABC) / 风险 / 数据质量 四个视图。

    Args:
        path: 输入 CSV/XLSX 路径。
        out: 输出 HTML 路径；省略时写在输入文件同目录。
        title: 图谱标题；省略时用默认名。
        sheet: XLSX 工作表名，省略时自动选择。
        include_vendors: 是否把供应商建成节点。**对外分享前建议设为 False**。

    Returns:
        JSON：{ok, out_path, stats, issues_verdict, note}
    """
    try:
        table = core.read_table(path, sheet)
        norm = core.normalize(table)
        issues = core.validate(norm, table)

        if any(e.get("code") == "E_NO_RECORDS" for e in issues["errors"]):
            return _j({"ok": False, "error": "表头之后没有数据行",
                       "issues_verdict": issues["verdict"]})

        graph = core.build_graph(norm, include_vendors=include_vendors,
                                 title=title or f"OE 运营物资图谱 · {table['source']}")
        graph["meta"]["source_sha256_16"] = (table.get("sha256") or "")[:16]

        html_doc = render.render_html(graph, issues, title=title)

        if not out:
            base = os.path.splitext(path)[0]
            out = base + "_图谱.html"
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html_doc)

        _log(f"built {out} ({len(html_doc)} bytes)")
        return _j({
            "ok": True,
            "out_path": out,
            "size_bytes": len(html_doc.encode("utf-8")),
            "stats": graph["stats"],
            "issues_verdict": issues["verdict"],
            "issues": {"errors": len(issues["errors"]),
                       "warnings": len(issues["warnings"]),
                       "info": len(issues["info"])},
            "note": "文件留在本机。拷贝 HTML 给他人时，" + (
                "注意图中含供应商名称。" if include_vendors else ""),
        })
    except Exception as exc:
        # 这里只记日志，不在这里编错误格式 —— 格式统一由 _safe 边界产出，
        # 否则同一类错误在不同工具里会变成不同形状，调用方 agent 没法统一处理。
        _log("tool error: " + traceback.format_exc().replace("\n", " | "))
        raise


@app.tool()
def oe_fingerprint(path: str, rooms: float | None = None,
                   outlets: int | None = None, sheet: str | None = None) -> str:
    """计算脱敏指纹 —— 唯一被设计成可以外发的输出。

    输出只含比率、占比与档位标签（例如"单位客房持有额 1500-3000 元"），
    **不含**任何供应商名、品名、单价、金额、责任人、资产编号。
    受 spec.FINGERPRINT_FIELDS 白名单约束，并有运行时自检。

    ⚠️ 本工具**不发送任何数据**。它只是把结果算出来给你看。
       是否上报由你决定，默认不外发、无网络请求。

    Args:
        path: 输入 CSV/XLSX 路径。
        rooms: 客房数。给了才能算"单位客房持有额"分档，不给则该档为 null。
        outlets: 营业点数量；省略时用文件里出现的数量。
        sheet: XLSX 工作表名。

    Returns:
        JSON：脱敏指纹 + `_digest`（指纹自身的摘要，供去重与版本追踪）。
    """
    try:
        table = core.read_table(path, sheet)
        norm = core.normalize(table)
        graph = core.build_graph(norm)
        fp = core.fingerprint(norm, graph, rooms=rooms, outlets_declared=outlets)
        # 刻意**不**输出源文件的行数和哈希：
        #   · 精确行数会让人把 row_count_bucket 的档位反推成更窄的区间，
        #     档位就没意义了；
        #   · 源文件哈希是"源文件本身的指纹" —— 外发之后，谁猜到文件内容
        #     都能拿它验证一次。指纹之所以能被外发，前提就是它只由
        #     档位和比率构成。这个口子不能开。
        return _j(fp)
    except Exception as exc:
        # 这里只记日志，不在这里编错误格式 —— 格式统一由 _safe 边界产出，
        # 否则同一类错误在不同工具里会变成不同形状，调用方 agent 没法统一处理。
        _log("tool error: " + traceback.format_exc().replace("\n", " | "))
        raise


@app.tool()
def oe_demo(out_dir: str | None = None, rooms: int = 320) -> str:
    """生成合成样例并出一张图 —— 用来在 30 秒内确认工具装好了。

    样例**完全是合成数据**（A供应商…I供应商，无真实酒店、无真人），
    表头故意写成「营业部门」「盘点数量」「使用月数」等非契约写法，
    用来验证别名映射确实生效。

    Args:
        out_dir: 输出目录；省略时用当前目录下的 oe_demo_output。
        rooms: 样例假设的客房数（只影响指纹里的单位客房持有额分档）。

    Returns:
        JSON：{ok, sample_path, html_path, stats, hint}
    """
    try:
        out_dir = os.path.abspath(out_dir or os.path.join(os.getcwd(), "oe_demo_output"))
        os.makedirs(out_dir, exist_ok=True)
        csv_path = os.path.join(out_dir, "oe_sample.csv")
        core.write_sample(csv_path, rooms=rooms)
        with open(os.path.join(out_dir, "README_样例说明.txt"), "w", encoding="utf-8") as fh:
            fh.write(core.sample_note(rooms) + "\n")

        table = core.read_table(csv_path)
        norm = core.normalize(table)
        issues = core.validate(norm, table)
        graph = core.build_graph(
            norm, title=f"OE 运营物资图谱 · 合成样例（{rooms} 间客房）")
        html_path = os.path.join(out_dir, "oe_sample_图谱.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(render.render_html(graph, issues))

        fp = core.fingerprint(norm, graph, rooms=rooms)
        return _j({
            "ok": True,
            "sample_path": csv_path,
            "sample_encoding": "gbk（模拟中文 Excel 导出）",
            "html_path": html_path,
            "headers_inferred": norm["fuzzy_headers"],
            "stats": graph["stats"],
            "fingerprint_preview": {k: v for k, v in fp.items() if not k.startswith("_")},
            "hint": "打开 html_path 即为交互图谱。把 sample_path 换成你的盘点表，"
                    "依次调用 oe_validate / oe_build / oe_fingerprint 即可。",
        })
    except Exception as exc:
        # 这里只记日志，不在这里编错误格式 —— 格式统一由 _safe 边界产出，
        # 否则同一类错误在不同工具里会变成不同形状，调用方 agent 没法统一处理。
        _log("tool error: " + traceback.format_exc().replace("\n", " | "))
        raise


@app.tool()
def oe_selfcheck() -> str:
    """自检：确认这个包没有网络调用代码，并报告运行环境。

    免费工具的信任成本全在这里：让调用方**自己验证**"它不上传"，
    而不是要求对方相信我们。任何人拿到源码都能复现这个检查。

    Returns:
        JSON：网络调用审计结果、MCP SDK 版本与 API 形态、Python 版本。
    """
    import platform
    hits = _no_network_audit()
    sdk_kind = "unknown"
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
        sdk_kind = "mcp.server.mcpserver.MCPServer (SDK 2.x)"
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP  # noqa: F401
            sdk_kind = "mcp.server.fastmcp.FastMCP (SDK 1.x)"
        except ImportError:
            sdk_kind = "MCP SDK 未找到"
    try:
        import importlib.metadata as md
        mcp_ver = md.version("mcp")
    except Exception:
        mcp_ver = "unknown"
    return _j({
        "network_calls_found": hits,
        "network_verdict": "clean — 没有任何网络调用" if not hits else "FOUND — 请审查",
        "audited_files": sorted(f for f in os.listdir(os.path.dirname(os.path.abspath(__file__)))
                                if f.endswith(".py")),
        "mcp_sdk": sdk_kind,
        "mcp_sdk_version": mcp_ver,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "spec_version": spec.SPEC_VERSION,
        "how_to_reproduce": "grep -nE 'urllib|requests|httpx|socket|aiohttp' oe_graph_mcp/*.py",
    })


# --------------------------------------------------------------------------
# 入口 / entry point
# --------------------------------------------------------------------------

def main() -> None:
    """以 stdio 方式运行 —— 本地 MCP server 的标准形态。"""
    _log(f"oe-graph-mcp starting · spec v{spec.SPEC_VERSION} · python {sys.version.split()[0]}")
    hits = _no_network_audit()
    _log(f"network audit: {'clean' if not hits else 'FOUND ' + str(hits)}")
    app.run()


if __name__ == "__main__":
    main()
