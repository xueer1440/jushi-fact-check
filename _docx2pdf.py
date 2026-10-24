"""把 作品说明.docx 原样转成 HTML，再用无头 Edge 打印成 PDF。

为什么绕这一圈：机器上 Word COM 被安全策略拦住，装不了 docx2pdf；
而 PDF 必须和 docx 同源，否则交上去的两份文件对不上。
保序遍历 body（段落/表格交错），标题按 outlineLvl 还原层级。

跑法：python _docx2pdf.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent
DOCX = ROOT / "作品说明.docx"
HTML = ROOT / "_作品说明_pdf.html"
PDF = ROOT / "作品说明.pdf"

EDGE = shutil.which("msedge") or next(
    (
        str(p)
        for p in (
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        )
        if p.exists()
    ),
    None,
)

CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: "宋体", "SimSun", serif; font-size: 10.5pt; line-height: 1.7; color: #111; }
h1 { font-family: "黑体","SimHei",serif; font-size: 22pt; font-weight: normal; text-align: center;
     margin: 0 0 18pt; page-break-after: avoid; }
h2 { font-size: 14pt; font-weight: bold; margin: 16pt 0 6pt; page-break-after: avoid; }
h3 { font-size: 12pt; font-weight: bold; margin: 12pt 0 4pt; page-break-after: avoid; }
p  { margin: 0 0 6pt; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt; font-size: 8.5pt;
        table-layout: fixed; }
th, td { border: 1px solid #999; padding: 4pt 6pt; vertical-align: top; text-align: left;
         word-break: break-word; overflow-wrap: anywhere; }
tr { page-break-inside: avoid; }
th { background: #f0f0f0; font-weight: bold; }
blockquote { margin: 6pt 0 6pt 12pt; color: #444; }
"""


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def outline_of(p) -> int | None:
    """从段落的 outlineLvl 拿标题层级（1→h1, 2→h2, 3→h3）。"""
    ppr = p._p.find(qn("w:pPr"))
    if ppr is None:
        return None
    lvl = ppr.find(qn("w:outlineLvl"))
    if lvl is None:
        return None
    try:
        return int(lvl.get(qn("w:val")))
    except (TypeError, ValueError):
        return None


def build_html(doc: Document) -> str:
    out: list[str] = []
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            from docx.text.paragraph import Paragraph

            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            lvl = outline_of(p)
            if lvl is not None and lvl <= 2:
                out.append(f"<h{lvl+1}>{esc(text)}</h{lvl+1}>")
            elif text.startswith(">"):
                out.append(f"<blockquote>{esc(text.lstrip('> '))}</blockquote>")
            else:
                out.append(f"<p>{esc(text)}</p>")
        elif tag == "tbl":
            from docx.table import Table

            t = Table(child, doc)
            out.append("<table>")
            for ri, row in enumerate(t.rows):
                cells = "".join(
                    ("<th>" if ri == 0 else "<td>") + esc(c.text.strip()) + ("</th>" if ri == 0 else "</td>")
                    for c in row.cells
                )
                out.append(f"<tr>{cells}</tr>")
            out.append("</table>")
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>" + "\n".join(out) + "</body></html>"
    )


def main() -> int:
    if not EDGE:
        print("没找到 Edge / Chrome，无法打印 PDF")
        return 1
    doc = Document(str(DOCX))
    HTML.write_text(build_html(doc), encoding="utf-8")
    print(f"HTML 已生成：{HTML}")
    cmd = [
        EDGE,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={PDF}",
        f"file:///{HTML.as_posix()}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        print("打印失败", res.returncode, res.stderr[-300:])
        return 1
    print(f"PDF 已生成：{PDF}（{PDF.stat().st_size//1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
