"""命令行入口：快速自测与回归。

用法：
    python cli.py verify "隔夜菜亚硝酸盐超标会致癌"
    python cli.py checkup "本店祖传秘方，纯天然零添加，包治颈椎病"
    python cli.py regress
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from detective import checkup, config, investigator

CASES = Path(__file__).resolve().parent / "regress" / "cases.json"


def _fmt_verify(d: dict) -> str:
    v = d["verdict"]
    lines = [
        f"结论：{v['label']}（置信度 {v['confidence']}）",
        f"理由：{v['reason']}",
        f"检索 {d['searches']} 次 / 来源 {len(d['evidence'])} 条 / 引擎 {d['provider']} / 耗时 {d['elapsed']}s",
    ]
    if d["claim"]["subclaims"]:
        lines.append("子主张：" + "；".join(s["text"] for s in d["claim"]["subclaims"]))
    if v["conflict"]:
        lines.append("⚠ 来源存在分歧，已按四维判决")
    if d["missing"]:
        lines.append("还差：" + "；".join(d["missing"]))
    lines.append("证据：")
    for e in d["top_evidence"] or d["evidence"][:3]:
        lines.append(f"  [{e['id']}] {e['site']} {e['date']} 权威{e['authority']} {e['stance']} → {e['url']}")
    return "\n".join(lines)


def _fmt_checkup(r: dict) -> str:
    c = r["counts"]
    lines = [
        r["headline"],
        f"句子 {c['sentences']} / 深查 {c['checked']} / 高 {c['high']} 中 {c['mid']} 低 {c['low']} / 待核查 {c['pending']}",
        f"检索 {r['searches']} 次 / 来源 {r['sources']} 条 / 引擎 {r['provider']}",
    ]
    for it in r["items"]:
        lines.append(f"- [{it['risk_level']}] {it['label']}：{it['sentence'][:34]} → {it.get('verdict_display') or it['verdict_label']}")
        lines.append(f"    改：{it['fix']}")
    return "\n".join(lines)


def cmd_verify(text: str) -> None:
    inv = investigator.investigate(text)
    print(_fmt_verify(inv.as_dict()))


def cmd_checkup(text: str) -> None:
    r = checkup.run_checkup(text)
    print(_fmt_checkup(r))


def cmd_regress() -> int:
    # 回归必须跑在固定证据上，否则每次真实检索结果不同，无法判断"有没有漂移"
    config.RUN_MODE = "demo"
    print("[回归] 强制演示模式：验证判定算法，不验证线上检索质量")
    data = json.loads(CASES.read_text(encoding="utf-8"))
    print(f"{'案例':<4}{'期望':<12}{'实际':<26}结果")
    print("-" * 62)
    failed = 0
    for case in data["cases"]:
        if case["kind"] == "verify":
            d = investigator.investigate(case["input"]).as_dict()
            actual = d["verdict"]["verdict"]
            label = d["verdict"]["label"]
        else:
            r = checkup.run_checkup(case["input"])
            verdicts = {it["verdict"] for it in r["items"]}
            levels = {it["risk_level"] for it in r["items"]}
            if case["expect_verdict"] in verdicts:
                actual, label = case["expect_verdict"], f"命中 {len(r['items'])} 句"
            elif case["expect_verdict"] in levels:
                actual, label = case["expect_verdict"], f"命中风险等级"
            else:
                actual, label = "|".join(sorted(verdicts | levels)) or "none", "无匹配"
        ok = actual == case["expect_verdict"]
        if not ok:
            failed += 1
        print(f"{case['id']:<5}{case['expect_verdict']:<12}{actual:<20}{'PASS' if ok else 'FAIL'}  {label}")
    print("-" * 62)
    print(f"共 {len(data['cases'])} 例，失败 {failed} 例")
    return 1 if failed else 0


def cmd_lint() -> int:
    """检查四个页面的 JS 语法。改前端后必跑，防"一处手滑整页失效"。"""
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        print("没装 node，跳过 JS 语法检查")
        return 0
    bad = 0
    for page in ("index.html", "card.html", "stats.html", "wall.html"):
        path = Path(__file__).resolve().parent / "web" / page
        if not path.exists():
            print(f"{page}: 不存在")
            continue
        html = path.read_text(encoding="utf-8")
        blocks = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
        for idx, block in enumerate(blocks, 1):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
                fh.write(block)
                tmp = fh.name
            res = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            os.unlink(tmp)
            if res.returncode != 0:
                bad += 1
                first = res.stderr.strip().splitlines()
                print(f"{page} 第{idx}块: FAIL {' | '.join(first[:2])}")
        if blocks:
            print(f"{page}: {len(blocks)} 块检查完毕")

    # 引用完整性：JS 里 $("#xxx") / querySelector("#xxx") 的元素必须真的存在
    # （曾因误删一个状态条 div，导致整个上传功能静默失效）
    ref_root = Path(__file__).resolve().parent / "web"
    miss_total = 0
    for page in ("index.html", "card.html", "wall.html", "stats.html"):
        path = ref_root / page
        if not path.exists():
            continue
        html = path.read_text(encoding="utf-8")
        defined = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
        used = set(re.findall(r'\$\("#([A-Za-z0-9_]+)"\)', html)) | set(
            re.findall(r'querySelector\("#([A-Za-z0-9_]+)"\)', html)
        )
        missing = sorted(used - defined)
        if missing:
            miss_total += len(missing)
            print(f"{page}: 引用了不存在的元素 {missing}")
    print("引用完整性 OK" if miss_total == 0 else f"有 {miss_total} 处引用缺失")
    if miss_total:
        bad += miss_total

    print("OK" if bad == 0 else f"有 {bad} 处问题")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(0)
    cmd = sys.argv[1]
    if cmd == "verify":
        cmd_verify(" ".join(sys.argv[2:]))
    elif cmd == "checkup":
        cmd_checkup(" ".join(sys.argv[2:]))
    elif cmd == "regress":
        raise SystemExit(cmd_regress())
    elif cmd == "lint":
        raise SystemExit(cmd_lint())
    else:
        print(__doc__)
