"""碎片句守卫 _fragment 的边界用例。

背景：双栏海报 OCR 会把左右两栏读成一行，再按句号切开，
于是出现「1适合这样的企业」「希望尽快见效」这类半截话。
它们不是完整宣称，拿去检索只会匹配到毫不相干的页面，然后被判成
"与公开来源不符"——那是误杀，不是核查。

但守卫太狠也会漏检：数字开头的真实功效宣称（「1次见效」「3步根治」）
必须照常核查。所以这里把两边都钉住，改代码后必跑。

用法：.venv/Scripts/python _test_fragment.py
"""

from detective.checkup import _fragment

# (句子, 期望是否为碎片)
CASES = [
    # ---- 应当判为碎片：OCR 串栏切出来的半截话 ----
    ("1适合这样的企业", True),      # 栏目标号和正文粘在一起
    ("01我能提供什么", True),
    ("2我可以帮你做什么", True),
    ("03先看效果", True),
    ("希望尽快见效", True),          # 愿望性开头、无对象、极短
    ("最终落不了地", True),          # 「最终」是承接副词，不是绝对化宣称
    ("然后就能上线", True),
    ("并且不用改流程", True),
    ("另外还有培训", True),
    ("比如说落地场景", True),
    ("尤其省人工成本", True),
    ("以及后续维护", True),
    ("同时降低成本", True),
    ("5大优势", True),
    ("1对1专属顾问", True),

    # ---- 不应判为碎片：有硬内容、有标点或带真实宣称 ----
    ("本店已开业五年", False),       # 自述事实，要核
    ("除醛率99%", False),            # 数据宣称
    ("我们的服务覆盖全市", False),
    ("国家免检产品", False),         # 违禁/认证类
    ("包治百病", False),             # 疗效宣称
    ("24小时响应", False),           # 数字 + 单位，是真数字
    ("12年专业经验", False),
    ("3天见效", False),
    ("100%有效", False),
    ("2小时上门服务", False),
    ("1次见效", False),              # 数字开头但含功效词 → 必须照常核（10-24 修的漏检）
    ("3步根治颈椎病", False),
    ("全网最低价", False),           # 绝对化用语
]


def main() -> int:
    failed = 0
    for i, (text, expect) in enumerate(CASES, 1):
        got = _fragment(text)
        ok = got == expect
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {i:>2}. {text:<22} -> {'碎片' if got else '正常'}  (期望 {'碎片' if expect else '正常'})")
    print("-" * 62)
    print(f"共 {len(CASES)} 例，失败 {failed} 例")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
