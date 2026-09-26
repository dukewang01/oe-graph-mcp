#!/usr/bin/env python3
"""原始 JSON-RPC stdio 握手 —— 直接讲 MCP 协议，不依赖 MCP 客户端 SDK。

为什么不用 SDK 客户端做这个测试：如果测试和服务端用同一个 SDK，
"接得上"只证明这个 SDK 自己跟自己对得上。用裸 JSON-RPC 走一遍，
证明的是**协议层**真的对得上 —— 任何 MCP 客户端都能接。

    python tests/handshake.py <python.exe> <repo>/oe_graph_mcp/server.py [label]

输出：协商到的协议版本、冷启动耗时、工具清单、以及三次真实 tools/call。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 依次尝试；服务端有权回一个它自己支持的版本
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]


class StdioClient:
    """最小 MCP stdio 客户端：换行分隔的 JSON-RPC 2.0。"""

    def __init__(self, cmd: list[str], cwd: str | None = None, env: dict | None = None):
        self.p = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=cwd, env=env,
        )
        self._id = 0
        self._q: queue.Queue = queue.Queue()
        self.stderr_lines: list[str] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self):
        for line in self.p.stdout:
            line = line.strip()
            if line:
                self._q.put(line)
        self._q.put(None)

    def _pump_stderr(self):
        for line in self.p.stderr:
            self.stderr_lines.append(line.rstrip())

    def send(self, method: str, params: dict | None = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return None if notify else self._id

    def recv(self, timeout: float = 30.0) -> dict:
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"等响应超时（{timeout}s）。stderr 尾部：{self.stderr_lines[-5:]}")
        if item is None:
            raise RuntimeError(f"stdout 提前关闭。stderr 尾部：{self.stderr_lines[-8:]}")
        obj = json.loads(item)
        if "error" in obj:
            raise RuntimeError(f"服务端返回错误：{obj['error']}")
        return obj.get("result", obj)

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self.send(method, params)
        return self.recv(timeout)

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def run(python_exe: str, server: str, label: str) -> bool:
    print(f"\n{'='*72}\n[{label}] {python_exe}\n{'='*72}")
    problems: list[str] = []
    c = StdioClient([python_exe, server], cwd=ROOT)

    try:
        # --- 1. 握手：逐个试协议版本 ---
        t0 = time.time()
        init = None
        for ver in PROTOCOL_VERSIONS:
            try:
                init = c.call("initialize", {
                    "protocolVersion": ver,
                    "capabilities": {"roots": {"listChanged": False}},
                    "clientInfo": {"name": "handshake-probe", "version": "1.0.0"},
                }, timeout=25)
                break
            except Exception as e:
                last = e
        init_ms = (time.time() - t0) * 1000
        if init is None:
            print(f"  ✗ 握手失败：{last}")
            return False

        si = init.get("serverInfo", {})
        print(f"  ✓ 握手成功  冷启动 {init_ms:.0f} ms")
        print(f"    协议版本  {init.get('protocolVersion')}")
        print(f"    服务端    {si.get('name')} v{si.get('version')}")
        print(f"    能力      {json.dumps(init.get('capabilities', {}), ensure_ascii=False)}")
        if "tools" not in init.get("capabilities", {}):
            problems.append("initialize 未声明 tools 能力")

        c.send("notifications/initialized", {}, notify=True)

        # --- 2. 工具清单 ---
        tl = c.call("tools/list", {})
        tools = tl.get("tools", [])
        print(f"  ✓ tools/list  共 {len(tools)} 个工具")
        for t in tools:
            desc = (t.get("description") or "").strip().splitlines()[0][:56]
            schema = t.get("inputSchema", {})
            req = schema.get("required", [])
            print(f"    · {t['name']:<15} {desc}")
            print(f"      输入 {list(schema.get('properties', {}).keys())}  必填 {req}")
        names = {t["name"] for t in tools}
        for want in ("oe_spec", "oe_validate", "oe_build", "oe_fingerprint", "oe_demo", "oe_selfcheck"):
            if want not in names:
                problems.append(f"缺少工具 {want}")

        # --- 3. 真实调用：oe_spec ---
        r = c.call("tools/call", {"name": "oe_spec", "arguments": {}})
        text = _text(r)
        s = json.loads(text)
        cols = s["columns"]
        vals = cols.values() if isinstance(cols, dict) else cols
        nopt = sum(1 for v in vals if not (isinstance(v, dict) and v.get("required")))
        print(f"  ✓ tools/call oe_spec        spec_version={s['spec_version']} "
              f"必填 {len(s['required_columns'])} 可选 {nopt} "
              f"指纹白名单 {len(s['fingerprint_fields'])} 项")
        if s["spec_version"] != "1.0.0":
            problems.append("spec_version 不符")
        for k in ("category", "item", "outlet", "qty", "unit_price", "vendor"):
            if k not in s["required_columns"]:
                problems.append(f"契约缺必填列 {k}")

        # --- 4. 真实调用：oe_demo（生成样例 + 出图） ---
        outdir = os.path.join(ROOT, "sample", "_handshake", label.replace(" ", "_"))
        r = c.call("tools/call", {"name": "oe_demo", "arguments": {"out_dir": outdir, "rooms": 320}},
                   timeout=60)
        d = json.loads(_text(r))
        hp = str(d.get("html_path") or "")
        hsz = os.path.getsize(hp) if os.path.exists(hp) else 0
        st = d.get("stats", {}) or {}
        print(f"  ✓ tools/call oe_demo        csv={os.path.basename(str(d.get('sample_path')))} "
              f"html={hsz} bytes")
        print(f"      stats={json.dumps({k: st.get(k) for k in ('row_count', 'node_count', 'link_count', 'item_count', 'vendor_count', 'outlet_count', 'total_amount') if k in st}, ensure_ascii=False)}")
        print(f"      headers_inferred={json.dumps(d.get('headers_inferred'), ensure_ascii=False)}")
        if not d.get("ok"):
            problems.append("oe_demo 未成功")
        if not hp or not os.path.exists(hp):
            problems.append("oe_demo 声称生成的 HTML 不存在")

        # --- 5. 真实调用：oe_fingerprint ---
        r = c.call("tools/call", {"name": "oe_fingerprint",
                                  "arguments": {"path": d["sample_path"], "rooms": 320}})
        fp = json.loads(_text(r))
        leaked = [k for k, v in fp.items() if isinstance(v, str) and len(v) > 24 and k != "_note"]
        print(f"  ✓ tools/call oe_fingerprint {len(fp)} 字段，无长字符串泄漏={not leaked} "
              f"标量档位={[k for k in fp if k.endswith(('band', 'bucket'))][:3]}…")
        print(f"     指纹 = {json.dumps({k: v for k, v in fp.items() if not k.startswith('_')}, ensure_ascii=False)}")

        # --- 6. 真实调用：oe_selfcheck（审计自己） ---
        r = c.call("tools/call", {"name": "oe_selfcheck", "arguments": {}})
        sc = json.loads(_text(r))
        print(f"  ✓ tools/call oe_selfcheck   {sc['network_verdict']}  sdk={sc['mcp_sdk']} {sc['mcp_sdk_version']}")

        # --- 7. 错误必须变成结构化错误，而不是崩溃 ---
        r = c.call("tools/call", {"name": "oe_validate", "arguments": {"path": "Z:/nope/missing.csv"}})
        t = _text(r)
        if r.get("isError"):
            print(f"  ✓ 错误路径：不存在文件 -> isError，消息可读（未崩进程）")
            print(f"      {t[:100]}")
        else:
            print(f"  ⚠ 不存在文件未标记 isError：{t[:80]}")

        # stdout 纯净性：stderr 有日志，stdout 只有 JSON-RPC
        print(f"  · stderr 日志 {len(c.stderr_lines)} 行（stdout 全程只有 JSON-RPC，未污染）")
        if c.stderr_lines:
            print(f"    例：{c.stderr_lines[0][:80]}")

    except Exception as e:
        print(f"  ✗ 异常：{type(e).__name__}: {e}")
        problems.append(str(e))
    finally:
        c.close()

    if problems:
        print(f"  ✗ {len(problems)} 个问题：{problems}")
        return False
    print("  ✓ 全部通过")
    return True


def _text(result: dict) -> str:
    """从 tools/call 结果里取文本（兼容 content[] 与 structuredContent）。"""
    if "content" in result:
        for part in result["content"]:
            if part.get("type") == "text":
                return part["text"]
    if "structuredContent" in result:
        return json.dumps(result["structuredContent"], ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    python_exe, server = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else os.path.basename(python_exe)
    ok = run(python_exe, server, label)
    print(f"\n结果 / RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
