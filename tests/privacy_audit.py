#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隐私闸：这个仓库里不允许出现真实数据。

    python tests/privacy_audit.py       # 退出码 0 = 干净

为什么需要一道专门的闸，而不是"提交前看一眼"：
本仓库的输入（盘点表）和输出（图谱 HTML）**都可能含真实经营数据与个人信息**，
而它们在结构上跟无关紧要的生成物长得一模一样，人眼在忙的时候一定会漏。
所以把这件事变成一条命令、一个退出码，让机器每次替你看。

它检查三件事：

  1. 全部提交 × 全部 blob —— 不只是工作区。
     删掉的文件仍然留在 history 里，只查工作区等于没查。
  2. 工作区。
  3. **数据闸本身有没有失效**：往 sample/ 这类位置丢一个 .xlsx，
     它必须被忽略。"我配了 .gitignore" 和 "它真的挡住了" 是两件事，
     我们自己就在这上面栽过一次 —— 早期的规则只挡了两个具体子目录，
     往 sample/ 别处丢数据文件会被 `git add -A` 直接收走。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = "tests/privacy_audit.py"

# 只查「数据形状」的东西。
#
# 刻意**不含**「姓名 / 联系人 / 手机」这类中文词：spec.py 里的列头别名表
# （"责任人""保管人""负责人"）是词汇表，不是数据，命中它属于设计如此。
# 闸门只认真实数据的形状 —— 真手机号、真邮箱、真凭证、真店名。
PATTERNS: list[tuple[str, str]] = [
    ("酒店集团 / 门店 / 城市名",
     r"希尔顿|Hilton|苏州|Suzhou|无锡|Wuxi|格芮|杭州|Hangzhou|华住|锦江|洲际|万豪|凯悦"),
    ("本机用户名 / 绝对路径", r"C:[/\\]Users|/d/Users|/home/[A-Za-z]"),
    ("中国手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ("邮箱地址", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    ("身份证 / 护照 / 工号", r"身份证|护照号码|工号|员工编号"),
    ("凭证 / 密钥",
     r"BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
     r"|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|xox[baprs]-"),
]

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


def scan(revs: list[str]) -> list[tuple[str, str, str]]:
    """返回 [(标签, 位置, 命中行)]。

    排除本文件自身：它里面写着这些模式，否则会永远自命中。
    """
    hits: list[tuple[str, str, str]] = []
    exclude = f":(exclude){SELF}"
    for label, pat in PATTERNS:
        args = ["git", "grep", "-nIE", "-e", pat] + revs + ["--", ".", exclude]
        proc = run(args)
        if proc.returncode not in (0, 1):      # 1 = 没有命中，正常
            print(f"  [警告] git grep 在「{label}」上异常: {proc.stderr.strip()}")
            continue
        for line in proc.stdout.splitlines():
            if line.strip():
                loc = line.split(":", 2)
                hits.append((label, loc[0] if loc else "?", line))
    return hits


def dedupe(hits: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """同一个文件同一行在多个提交里会重复，按"去掉提交哈希后的内容"去重。"""
    seen: set[str] = set()
    out = []
    for label, _loc, line in hits:
        # line 形如 <sha>:<path>:<lineno>:<text>
        parts = line.split(":", 3)
        key = ":".join(parts[1:]) if len(parts) > 3 else line
        if key not in seen:
            seen.add(key)
            out.append((label, parts[1] if len(parts) > 1 else "?", parts[3] if len(parts) > 3 else line))
    return out


def main() -> int:
    print("隐私闸 / privacy gate")
    print(f"仓库: {ROOT}\n")

    commits = run(["git", "rev-list", "--all"]).stdout.split()
    if not commits:
        print("  [警告] 尚无提交，只检查工作区")

    print(f"[1/3] 扫描全部提交 × 全部 blob（{len(commits)} 个提交）...")
    hist = dedupe(scan(commits)) if commits else []

    print("[2/3] 扫描工作区...")
    work = dedupe(scan([]))

    # 工作区与历史命中往往重叠，合并成一个清单
    all_hits = dedupe(scan(commits) if commits else []) + [h for h in work if h not in hist]

    if all_hits:
        print(f"\n  [!!] 发现 {len(all_hits)} 处疑似真实数据：")
        for label, path, text in all_hits:
            print(f"      [{label}] {path}")
            print(f"          {text[:160]}")
    else:
        print("  [0] 零命中 —— 提交内容与工作区都没有真实数据的形状\n")

    print("[3/3] 数据闸有效性测试（丢一个数据文件进去，必须被忽略）...")
    gaps = []
    for path in CANARIES:
        proc = run(["git", "check-ignore", "-q", "--", path])
        if proc.returncode != 0:
            gaps.append(path)
    if gaps:
        print(f"  [!!] 这些路径没被忽略，可以被 git add 收走：")
        for path in gaps:
            print(f"          {path}")
    else:
        print(f"  [ok] {len(CANARIES)} 个探针全部被忽略\n")

    print("提交者（人工过一眼，确认没有真实邮箱）：")
    authors = run(["git", "log", "--all", "--pretty=format:%an <%ae>"]).stdout
    for a in sorted(set(authors.splitlines())):
        if a.strip():
            print(f"      {a}")

    if all_hits or gaps:
        print("\n结果：不通过。先清干净再提交。")
        return 1
    print("\n结果：通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
