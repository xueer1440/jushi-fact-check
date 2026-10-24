"""把 作品说明.md 重新生成 作品说明.docx。

排版规范（用户 2026-10-23 指定）：
- 最大标题（# 一级）：黑体，二号（22pt），不加粗
- 其他标题（## / ###）：宋体，四号（14pt），加粗
- 正文（含表格、列表）：宋体，五号（10.5pt）
- 中文相邻的空格全部去掉（"33 次" → "33次"）

跑法：python _md2docx.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parent
MD = ROOT / "作品说明.md"
DOCX = ROOT / "作品说明.docx"

CJK = r"\u4e00-\u9fff\u3001-\u303f\uff00-\uffef\u2014\u2026\u2018\u2019\u201c\u201d"


def tighten(text: str) -> str:
    """中文两侧的空格去掉；英文/数字内部保留；间隔号两侧不留空格。"""
    text = re.sub(r"\s*·\s*", "·", text)
    text = re.sub(rf"[ \u3000]+(?=[{CJK}])", "", text)
    text = re.sub(rf"[ \u3000]+(?<=[{CJK}])", "", text)
    return text


def set_font(run, *, ascii_font: str, size: float, bold: bool = False):
    run.font.name = ascii_font
    run.font.size = Pt(size)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), ascii_font)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)


INLINE = re.compile(r"(\*\*.+?\*\*|`.+?`)")


def add_rich(paragraph, text: str, *, font: str, size: float, bold: bool = False):
    for seg in INLINE.split(text):
        if not seg:
            continue
        if seg.startswith("**") and seg.endswith("**"):
            r = paragraph.add_run(tighten(seg[2:-2]))
            set_font(r, ascii_font=font, size=size, bold=True)
        elif seg.startswith("`") and seg.endswith("`"):
            r = paragraph.add_run(tighten(seg[1:-1]))
            set_font(r, ascii_font="Consolas", size=size - 0.5, bold=bold)
        else:
            r = paragraph.add_run(tighten(seg))
            set_font(r, ascii_font=font, size=size, bold=bold)


def split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def set_outline(paragraph, level: int):
    """给段落写大纲级别（0=一级标题），Word 左侧导航窗格靠它生成目录树。
    只加 outlineLvl，不改字体字号，所以排版规格不受影响。"""
    ppr = paragraph._p.get_or_add_pPr()
    for old in ppr.findall(qn("w:outlineLvl")):
        ppr.remove(old)
    el = ppr.makeelement(qn("w:outlineLvl"), {qn("w:val"): str(level)})
    ppr.append(el)
    paragraph.paragraph_format.keep_with_next = True


def main():
    md = MD.read_text(encoding="utf-8").splitlines()
    if DOCX.exists():
        shutil.copy2(DOCX, ROOT / "作品说明.backup-生成前.docx")

    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Cm(2.54)
    sec.bottom_margin = Cm(2.54)
    sec.left_margin = Cm(2.8)
    sec.right_margin = Cm(2.8)

    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    i = 0
    while i < len(md):
        line = md[i].rstrip()
        if not line.strip():
            i += 1
            continue

        # 表格
        if line.lstrip().startswith("|") and i + 1 < len(md) and re.match(r"^\s*\|[\s:|-]+\|\s*$", md[i + 1]):
            header = split_row(line)
            rows = []
            j = i + 2
            while j < len(md) and md[j].lstrip().startswith("|"):
                rows.append(split_row(md[j]))
                j += 1
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for k, cell in enumerate(header):
                p = table.rows[0].cells[k].paragraphs[0]
                add_rich(p, cell, font="宋体", size=9.5, bold=True)
            for row in rows:
                cells = table.add_row().cells
                for k in range(len(header)):
                    val = row[k] if k < len(row) else ""
                    p = cells[k].paragraphs[0]
                    add_rich(p, val, font="宋体", size=9.5)
            doc.add_paragraph()
            i = j
            continue

        if line.startswith("# "):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(12)
            set_outline(p, 0)
            add_rich(p, line[2:].strip(), font="黑体", size=22, bold=False)
        elif line.startswith("## "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(6)
            set_outline(p, 1)
            add_rich(p, line[3:].strip(), font="宋体", size=14, bold=True)
        elif line.startswith("### "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(4)
            set_outline(p, 2)
            add_rich(p, line[4:].strip(), font="宋体", size=14, bold=True)
        elif line.lstrip().startswith(("- ", "* ")):
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.line_spacing = 1.5
            add_rich(p, line.lstrip()[2:].strip(), font="宋体", size=10.5)
        elif re.match(r"^\d+\.\s", line.lstrip()):
            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.5
            add_rich(p, line.lstrip(), font="宋体", size=10.5)
        elif line.lstrip().startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.left_indent = Cm(0.5)
            add_rich(p, line.lstrip()[1:].strip(), font="宋体", size=10, bold=False)
        elif line.strip() == "---":
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
        else:
            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.first_line_indent = Cm(0)
            add_rich(p, line.strip(), font="宋体", size=10.5)
        i += 1

    doc.save(DOCX)
    print("已生成:", DOCX, "| 段落数:", len(doc.paragraphs), "| 表格数:", len(doc.tables))


if __name__ == "__main__":
    main()
