"""校验 作品说明.docx 与 作品说明.pdf 内容是否完全一致。

用法：python _check_docx_pdf.py [docx] [pdf]
判定：docx 的每个字符都必须出现在 PDF 里；PDF 允许多出项目符号（\\uf0b7）。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from docx import Document
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent


def docx_text(path: Path) -> str:
    d = Document(str(path))
    body = "".join(p.text for p in d.paragraphs)
    cells = "".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    return body + cells


def pdf_text(path: Path) -> str:
    return "".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def norm(s: str) -> str:
    return re.sub(r"\s", "", s)


def main() -> int:
    docx = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "01-提交评委材料/作品说明.docx"
    pdf = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "01-提交评委材料/作品说明.pdf"
    a, b = norm(docx_text(docx)), norm(pdf_text(pdf))
    ca, cb = Counter(a), Counter(b)
    missing = ca - cb
    extra = cb - ca
    print(f"{docx.name}: {len(a)} 字")
    print(f"{pdf.name}: {len(b)} 字（{len(PdfReader(str(pdf)).pages)} 页）")
    print("docx 有而 PDF 没有:", dict(missing.most_common(10)) or "无")
    print("PDF 有而 docx 没有:", dict(extra.most_common(10)) or "无")
    ok = not missing and set(extra) <= {"\uf0b7"}   # 仅允许项目符号差异
    print("结论:", "一致 ✅" if ok else "不一致 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
