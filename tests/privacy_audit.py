#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隐私闸：这个仓库里不允许出现真实数据。

    python tests/privacy_audit.py       # 退出码 0 = 干净

为什么需要一道专门的闸，而不是"提交前看一眼"：
本仓库的输入（盘点表）和输出（图谱 HTML）**都可能含真实经营数据与个人信息**，
而它们在结构上跟无关紧要的生成物长得一模一样，人眼在忙的时候一定会漏。
所以把这件事变成一条命令、一个退出码，让机器每次替你看。

它检查四件事：

  1. **闸门自己能抓得住东西吗** —— 每条规则先打自己的样本，打不中就说明这条
     规则是坏的。不做这一步，闸门会在坏掉之后继续报"通过"。
  2. 全部提交 × 全部 blob —— 不只是工作区。
     删掉的文件仍然留在 history 里，只查工作区等于没查。
  3. 工作区 = 已跟踪的文件 + **未跟踪且未被忽略的文件**。
     后者是关键：`git grep` 只看已跟踪的文件，一个刚丢进仓库、还没 `git add`
     的盘点表它永远看不见 —— 而那恰恰是真实数据最常见的落点。
  4. **数据闸本身有没有失效**：往 sample/ 这类位置丢一个 .xlsx，
     它必须被忽略。"我配了 .gitignore" 和 "它真的挡住了" 是两件事 ——
     本仓库就在这上面栽过一次：早期的规则只挡了两个具体子目录，
     往 sample/ 别处丢数据文件会被 `git add -A` 直接收走。

三条设计铁律（都是踩出来的）：

  * **任何一项检查跑不起来 = 整体失败。** 曾经用过带 `(?<!\\d)` 的规则，
    `git grep` 的 POSIX ERE 不支持前置否定，于是那条规则报了个警告就被跳过了，
    而闸门照样打印"通过"。这种"静默跳过"比没有闸更坏，因为它制造成虚假信心。
  * **判定一律交给 Python 的 `re`。** 历史部分的 `git grep` 只负责捞候选行，
    ERE 与 Python 正则方言不同，同一份模式两边行为不一致就是这个 bug 的根因。
  * **宁可误报，不可漏报。** 这一条不适用于别的测试，只适用于这里 ——
    隐私闸漏掉一次的代价，是这份东西永远收不回来。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = "tests/privacy_audit.py"

# (标签, Python 精确模式, git grep 用的近似模式)
#
# 近似模式不能有前置/后置否定断言 —— git grep 用的是 POSIX ERE。
# 判定用 Python 模式复检，所以近似模式宁可捞宽一点。
PATTERNS: list[tuple[str, str, str | None]] = [
    ("酒店集团 / 门店 / 城市名",
     r"希尔顿|Hilton|苏州|Suzhou|无锡|Wuxi|格芮|杭州|Hangzhou|华住|锦江|洲际|万豪|凯悦",
     None),
    ("本机用户名 / 绝对路径",
     r"C:[/\\]Users|/d/Users|/home/[A-Za-z]",
     None),
    ("中国手机号",
     r"(?<!\d)1[3-9]\d{9}(?!\d)",
     r"1[3-9][0-9]{9}"),
    ("邮箱地址",
     r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}",
     None),
    ("身份证 / 护照 / 工号",
     r"身份证|护照号码|工号|员工编号",
     None),
    ("凭证 / 密钥",
     r"BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
     r"|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|xox[baprs]-",
     None),
]

# 每条规则的自测样本：它必须能被自己的 Python 模式和 grep 模式抓到。
# 这些字符串本身含敏感形状，所以本文件在扫描时被排除（见 SELF）。
SELF_TESTS = {
    "酒店集团 / 门店 / 城市名": "苏州某酒店盘点表",
    "本机用户名 / 绝对路径": r"C:\Users\Somebody\Desktop\x.txt",
    "中国手机号": "联系电话 13812345678",
    "邮箱地址": "zhangsan@example.com",
    "身份证 / 护照 / 工号": "工号 A10086",
    "凭证 / 密钥": "ghp_" + "A" * 24,
}

# 数据闸有效性测试：这些路径**必须**被忽略。
CANARIES = [
    "sample/盘点表.xlsx",
    "sample/新建子目录/out.csv",
    "sample/_smoke/x.html",
    "oe_demo_output/图谱.html",
    "某酒店盘点表.xlsx",
    "out.html",
    "data.csv",
    "backup.xls",
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def self_test() -> list[str]:
    """先证明闸门抓得住东西。返回失败说明列表。"""
    problems = []
    for label, py_pat, grep_pat in PATTERNS:
        sample = SELF_TESTS[label]
        if not re.compile(py_pat).search(sample):
            problems.append(f"规则「{label}」抓不住自己的样本（Python 模式）：{sample!r}")
        if not re.compile(grep_pat or py_pat).search(sample):
            problems.append(f"规则「{label}」抓不住自己的样本（grep 模式）：{sample!r}")
    return problems


# ── 历史：git grep 捞候选，Python re 判定 ──────────────────────────────

def parse_grep_line(line: str, with_rev: bool) -> tuple[str, str, str] | None:
    """git grep 的输出有两种形状：<rev>:<path>:<line>:<text> 和 <path>:<line>:<text>。"""
    parts = line.split(":", 3 if with_rev else 2)
    if with_rev:
        if len(parts) < 4 or not re.fullmatch(r"[0-9a-f]{7,40}", parts[0]):
            return None
        return parts[1], parts[2], parts[3]
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2]


def scan_history(revs: list[str]) -> list[tuple[str, str, str, str]]:
    """扫全部提交的全部 blob。任何规则跑不起来直接抛异常。"""
    exclude = f":(exclude){SELF}"
    hits: list[tuple[str, str, str, str]] = []
    for label, py_pat, grep_pat in PATTERNS:
        proc = run(["git", "grep", "-nIE", "-e", grep_pat or py_pat]
                   + revs + ["--", ".", exclude])
        if proc.returncode not in (0, 1):      # 1 = 没有命中，正常
            raise RuntimeError(
                f"规则「{label}」没能执行，闸门无法给出结论：{proc.stderr.strip()}"
            )
        precise = re.compile(py_pat)
        for line in proc.stdout.splitlines():
            parsed = parse_grep_line(line, True)
            if not parsed:
                continue
            path, lineno, text = parsed
            if precise.search(text):           # ERE 只负责捞，判定用 Python 复检
                hits.append((label, path, lineno, text.strip()))
    return hits


# ── 工作区：纯 Python 读盘，覆盖未跟踪文件 ───────────────────────────

def iter_worktree_files() -> list[str]:
    """已跟踪的文件 + 未跟踪且没被忽略的文件（相对仓库根的路径）。"""
    tracked = run(["git", "ls-files"]).stdout.splitlines()
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines()
    return sorted({p for p in tracked + untracked if p and p != SELF})


def scan_worktree() -> list[tuple[str, str, str, str]]:
    compiled = [(label, re.compile(py_pat)) for label, py_pat, _ in PATTERNS]
    hits: list[tuple[str, str, str, str]] = []
    for rel in iter_worktree_files():
        try:
            raw = (ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:              # 二进制，跳过
            continue
        text = raw.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, rx in compiled:
                if rx.search(line):
                    hits.append((label, rel, str(lineno), line.strip()))
    return hits


def dedupe(hits: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """同一处问题在多个提交里会重复出现，按 (标签, 路径, 原文) 去重。"""
    seen: set[tuple[str, str, str]] = set()
    out = []
    for label, path, lineno, text in hits:
        key = (label, path, text)
        if key not in seen:
            seen.add(key)
            out.append((label, path, lineno, text))
    return out


def main() -> int:
    print("隐私闸 / privacy gate")
    print(f"仓库: {ROOT}\n")

    problems: list[str] = []

    print("[1/4] 闸门自测（每条规则必须先抓住自己的样本）...")
    st = self_test()
    if st:
        problems.extend(st)
        for p in st:
            print(f"  [!!] {p}")
    else:
        print(f"  [ok] {len(PATTERNS)} 条规则全部有效")

    commits = run(["git", "rev-list", "--all"]).stdout.split()
    if not commits:
        print("  [警告] 尚无提交，只检查工作区")

    print(f"\n[2/4] 扫描全部提交 × 全部 blob（{len(commits)} 个提交）...")
    try:
        hist = dedupe(scan_history(commits)) if commits else []
    except RuntimeError as exc:
        print(f"  [!!] {exc}")
        return 1
    if not hist:
        print("  [ok] 历史干净")

    files = iter_worktree_files()
    print(f"\n[3/4] 扫描工作区（{len(files)} 个文件：已跟踪 + 未跟踪未被忽略）...")
    work = dedupe(scan_worktree())
    if not work:
        print("  [ok] 工作区干净")

    all_hits = dedupe(hist + work)
    if all_hits:
        print(f"\n  [!!] 发现 {len(all_hits)} 处疑似真实数据：")
        for label, path, lineno, text in all_hits:
            print(f"      [{label}] {path}:{lineno}")
            print(f"          {text[:160]}")

    print("\n[4/4] 数据闸有效性测试（丢一个数据文件进去，必须被忽略）...")
    gaps = [p for p in CANARIES if run(["git", "check-ignore", "-q", "--", p]).returncode != 0]
    if gaps:
        print("  [!!] 这些路径没被忽略，可以被 git add 收走：")
        for path in gaps:
            print(f"          {path}")
    else:
        print(f"  [ok] {len(CANARIES)} 个探针全部被忽略")

    print("\n提交者（人工过一眼，确认没有真实邮箱）：")
    authors = run(["git", "log", "--all", "--pretty=format:%an <%ae>"]).stdout
    for a in sorted({x for x in authors.splitlines() if x.strip()}):
        print(f"      {a}")

    if problems or all_hits or gaps:
        print("\n结果：不通过 —— 先清干净再提交。")
        return 1
    print("\n结果：通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
