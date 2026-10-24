"""从线上查证记录生成《调查报告》（Markdown + DOCX + PDF）。

数据来源：/api/q/<code> 的原始 payload，不做任何补写。
用法：python _gen_report.py 0160 0159
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "03-进阶报告"
COOKIE = r"C:\Users\zengyan\AppData\Local\Temp\jushi_cookie.txt"
BASE = "https://jushi-fact-check.app.workbuddy.host"


def fetch(code: str) -> dict:
    local = ROOT / "data" / "detective.db"
    import sqlite3

    conn = sqlite3.connect(local)
    row = conn.execute("SELECT payload FROM checks WHERE code=?", (code,)).fetchone()
    if row:
        return json.loads(row[0])
    out = subprocess.run(
        ["curl", "-s", "-b", COOKIE, f"{BASE}/api/q/{code}"],
        capture_output=True, text=True, encoding="utf-8").stdout
    return json.loads(out)


def stance_cn(s: str) -> str:
    return {"support": "支持", "refute": "反驳", "neutral": "中性"}.get(s, s or "中性")


def report_md(d: dict) -> str:
    v = d.get("verdict") or {}
    claim = d.get("claim") or {}
    trace = d.get("trace") or []
    ev = d.get("evidence") or []
    top = d.get("top_evidence") or []
    matrix = d.get("matrix") or []
    code = d.get("code")

    lines = [
        f"# 调查报告：{claim.get('raw','')}",
        "",
        f"> 查证编号 **{code}** ｜ 查询时间 {d.get('queried_at','')} ｜ 检索 {d.get('searches','?')} 次 ｜ 耗时 {d.get('elapsed','?')} 秒 ｜ 引擎 {d.get('provider','')}",
        f"> 在线复核：{BASE}/?sharecode=T3NGFV6FVO → 查证记录 → 搜编号 {code}",
        "",
        "## 一、结论",
        "",
        f"- **判定：{v.get('label','')}**（置信度 {v.get('confidence','')}）",
        f"- 理由：{v.get('reason','')}",
        f"- 可采信来源：{d.get('usable_count','?')} 条（全部为公开可访问页面，见第三节）",
        f"- 未解决缺口：{'无' if not d.get('missing') else '；'.join(d.get('missing') or [])}",
        "",
        "## 二、检索过程（逐条可复现）",
        "",
        "| 轮次 | 查询词 | 目的 | 本轮新增 |",
        "|---|---|---|---|",
    ]
    for t in trace:
        lines.append(f"| 第 {t.get('round','')} 轮 | `{t.get('query','')}` | {t.get('purpose','')} | {t.get('count','')} |")

    lines += ["", "## 三、证据链", "", "### 3.1 判定所依据的关键来源", ""]
    if top:
        lines += ["| # | 标题 | 站点 | 日期 | 立场 | 权威度 | 链接 |", "|---|---|---|---|---|---|---|"]
        for i, e in enumerate(top, 1):
            lines.append(
                f"| {i} | {e.get('title','')} | {e.get('site','')} | {e.get('date','')} | "
                f"{stance_cn(e.get('stance'))} | {e.get('authority','')}/5 | [原文]({e.get('url','')}) |")
    else:
        lines.append("（无关键来源）")

    on_topic = [e for e in ev if e.get("on_topic")]
    off_topic = [e for e in ev if not e.get("on_topic")]
    lines += ["", f"### 3.2 主题相关的全部命中（{len(on_topic)} 条）", ""]
    if on_topic:
        lines += ["| # | 标题 | 站点 | 日期 | 立场 | 权威度 | 一手 | 链接 |", "|---|---|---|---|---|---|---|---|"]
        for i, e in enumerate(on_topic, 1):
            lines.append(
                f"| E{i} | {e.get('title','')} | {e.get('site','')} | {e.get('date','')} | "
                f"{stance_cn(e.get('stance'))} | {e.get('authority','')}/5 | {'是' if e.get('one_hand') else '否'} | "
                f"[原文]({e.get('url','')}) |")
    lines += ["", f"### 3.3 已排除的命中（{len(off_topic)} 条，仅列前 10）", "",
              "排除理由：标题未直接涉及本条说法、或属于同一主体的另一件事（主体/主题校验未通过），按规则不计入判定。", ""]
    for i, e in enumerate(off_topic[:10], 1):
        lines.append(f"{i}. {e.get('title','')}（{e.get('site','')}，{e.get('date','')}）— {e.get('url','')}")

    lines += ["", "## 四、子主张拆解", ""]
    if matrix:
        lines += ["| # | 子主张 | 支持 | 反证 | 判定 |", "|---|---|---|---|---|"]
        for m in matrix:
            lines.append(f"| {m.get('index','')} | {m.get('text','')} | {m.get('support',0)} | {m.get('refute',0)} | {m.get('state','')} |")
    else:
        lines.append("（本条未拆分子主张）")

    lines += ["", "## 五、采信规则（判定依据）", "",
              "1. **权威优先**：不数来源条数，只看最高权威层级是否给出定论；来源打架时明示为分歧，不硬判。",
              "2. **十道闸**：否定式守卫、因果降级、转述降级、同站矛盾清零、同站去重、转载合并、疑问句降级、辟谣标记、主体校验；转载同一篇只算一条。",
              "3. **相关性命中**：标题未直接涉及本条说法的页面只进证据池、不进判定（见 3.3）。",
              "4. **能点开**：每条进入判定的来源都附原始链接，可逐条复核。",
              "",
              "## 六、边界与声明", "",
              f"- {d.get('disclaimer','')}",
              "- 检索为实时进行，本报告对应编号 " + str(code) + " 的那一次查询；同一句话在不同时间重查，证据集可能变化。",
              "",
              "---",
              f"报告生成：据实 · 发布前事实核查 ｜ 生成时间由记录时间确定：{d.get('queried_at','')}",
              ]
    return "\n".join(lines)


def md_to_docx(md_text: str, dst: Path, title: str):
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Cm(2.4); sec.bottom_margin = Cm(2.4)
    sec.left_margin = Cm(2.6); sec.right_margin = Cm(2.6)
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"; normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def set_font(run, font="宋体", size=10.5, bold=False):
        run.font.name = font; run.font.size = Pt(size); run.bold = bold
        rpr = run._element.get_or_add_rPr()
        rpr.rFonts.set(qn("w:eastAsia"), font)

    def split_row(line: str):
        return [c.strip() for c in line.strip().strip("|").split("|")]

    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            doc.add_paragraph(); i += 1; continue

        # 表格：连续以 | 开头的行，第二行是分隔符
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = split_row(line)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append(split_row(lines[j])); j += 1
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            for k, cell in enumerate(header):
                cell_obj = table.rows[0].cells[k]
                para = cell_obj.paragraphs[0]
                set_font(para.add_run(cell), "宋体", 9, True)
            for row in rows:
                cells = table.add_row().cells
                for k in range(len(header)):
                    val = row[k] if k < len(row) else ""
                    # 链接写法 [文字](url) 只保留文字
                    val = re.sub(r"\[([^\]]+)\]\(([^)]*)\)", r"\1（\2）", val)
                    set_font(cells[k].paragraphs[0].add_run(val), "宋体", 9)
            doc.add_paragraph()
            i = j
            continue

        if line.startswith("# "):
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            set_font(p.add_run(line[2:].strip()), "黑体", 18, True)
        elif line.startswith("## "):
            p = doc.add_paragraph(); set_font(p.add_run(line[3:].strip()), "宋体", 14, True)
        elif line.startswith("### "):
            p = doc.add_paragraph(); set_font(p.add_run(line[4:].strip()), "宋体", 12, True)
        elif line.lstrip().startswith("> "):
            p = doc.add_paragraph(); set_font(p.add_run(line.lstrip()[2:].strip()), "宋体", 9.5)
        elif re.match(r"^\s*[-*]\s", line):
            p = doc.add_paragraph(); set_font(p.add_run("· " + re.sub(r"^\s*[-*]\s+", "", line)), "宋体", 10.5)
        elif re.match(r"^\s*\d+\.\s", line):
            p = doc.add_paragraph(); set_font(p.add_run(line.strip()), "宋体", 10.5)
        else:
            p = doc.add_paragraph(); set_font(p.add_run(line.strip()), "宋体", 10.5)
        i += 1
    doc.save(str(dst))


def main():
    OUT.mkdir(exist_ok=True)
    for code in sys.argv[1:]:
        d = fetch(code)
        md = report_md(d)
        name = f"调查报告-{code}-{(d.get('claim') or {}).get('raw','')[:14]}"
        name = name.replace("/", "／").replace("\\", "＼")
        md_path = OUT / f"{name}.md"
        md_path.write_text(md, encoding="utf-8")
        docx_path = OUT / f"{name}.docx"
        md_to_docx(md, docx_path, name)
        print("已生成:", md_path.name, "|", docx_path.name, "|", len(md), "字")


if __name__ == "__main__":
    main()
