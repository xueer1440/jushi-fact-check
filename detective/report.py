"""深度调查报告：把一次核查的完整证据链导出成一份自包含的 HTML。

设计要点：
1. 自包含——样式内联，不依赖站点的 css，拷走就能打开。
2. 可打印——带 @media print，浏览器另存为 PDF 就是一份正式报告。
3. 只呈现真实存在的东西——没有检索就不写检索，没有依据就不编依据。
"""
from __future__ import annotations

import html
import json
from datetime import datetime

from . import config

VERDICT_TONE = {
    "true": "low",
    "false": "high",
    "unclear": "mid",
    "no_source": "pending",
    "refused": "refused",
}

_ROUND_NAME = {0: "预处理", 1: "第一轮 · 广泛取证", 2: "第二轮 · 交叉验证", 3: "第三轮 · 溯源一手"}
_TAG_NAME = {
    "general": "广泛检索",
    "refute": "反向检索",
    "authority": "官方口径",
    "tencent": "腾讯生态信源",
    "data": "研究数据",
    "primary": "一手材料",
    "credit": "信用公示",
    "realtime": "实时直查",
    "common": "常识判定",
    "refused": "受理边界",
    "demo": "检索不可用",
    "stop": "提前收手",
}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _rows(items: list[dict], cols: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    head = "".join(f"<th>{_e(t)}</th>" for _, t in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(it.get(k, ''))}</td>" for k, _ in cols) + "</tr>"
        for it in items
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_verify_report(payload: dict, code: str = "", created_at: str = "") -> str:
    v = payload.get("verdict") or {}
    claim = payload.get("claim") or {}
    raw = claim.get("raw") or payload.get("input") or ""
    verdict = v.get("verdict") or ""
    tone = VERDICT_TONE.get(verdict, "pending")
    label = v.get("label") or ""
    if payload.get("claim", {}).get("claim_type") == "realtime":
        tone, label = "pending", "实时信息"

    evidence = payload.get("evidence") or []
    top = payload.get("top_evidence") or evidence[:6]
    usable = payload.get("usable_count")

    ev_html = "".join(
        f"""<div class="src">
      <div class="src-h"><span class="tag {_e(e.get('stance',''))}">{_e(_STANCE.get(e.get('stance',''), ''))}</span>
        <span class="auth">权威度 {_e(e.get('authority','-'))}/5</span>
        <span class="site">{_e(e.get('site') or e.get('url') or '')}</span>
        {f"<span class='date'>{_e(e.get('date'))}</span>" if e.get("date") else ""}
      </div>
      <a class="title" href="{_e(e.get('url'))}" target="_blank" rel="noopener">{_e(e.get('title'))}</a>
      {f"<p class='snip'>{_e((e.get('snippet') or '')[:220])}</p>" if e.get("snippet") else ""}
      <div class="url">{_e(e.get('url'))}</div>
    </div>"""
        for e in top
    ) or '<p class="empty">本次没有可采信的来源，所以没有下结论。</p>'

    trace_html = _rows(
        payload.get("trace") or [],
        [("round", "轮次"), ("tag", "检索方式"), ("query", "查询语句"), ("count", "命中"), ("purpose", "目的")],
    )
    if trace_html:
        trace_html = trace_html.replace("<td>0</td>", "<td>0</td>")

    matrix_html = _rows(
        payload.get("matrix") or [],
        [("index", "#"), ("text", "子主张"), ("support", "支持"), ("refute", "反驳"), ("state", "判定")],
    )

    conflict_html = ""
    cf = payload.get("conflict") or []
    if cf:
        conflict_html = (
            "<h3>来源之间的分歧</h3>"
            + "".join(
                f"<div class='src'><div class='src-h'><span class='site'>{_e(c.get('site',''))}</span></div>"
                f"<a class='title' href='{_e(c.get('url'))}' target='_blank' rel='noopener'>{_e(c.get('title'))}</a></div>"
                for c in cf
            )
        )

    timeline_html = _rows(
        payload.get("timeline") or [],
        [("date", "时间"), ("title", "事件"), ("site", "来源")],
    )

    missing = payload.get("missing") or []
    missing_html = (
        "<h3>还缺什么</h3><ul>" + "".join(f"<li>{_e(m)}</li>" for m in missing) + "</ul>"
        if missing
        else ""
    )

    notes = v.get("notes") or []
    notes_html = (
        "<h3>判定说明</h3><ul>" + "".join(f"<li>{_e(n)}</li>" for n in notes) + "</ul>"
        if notes
        else ""
    )

    chips = [
        f"置信度 {v.get('confidence', 0)}",
        f"检索 {payload.get('searches', 0)} 次",
        f"命中 {len(evidence)} 条",
        f"可用 {usable if usable is not None else '—'} 条",
        f"耗时 {payload.get('elapsed', 0)} 秒",
    ]
    if payload.get("tencent_sources"):
        chips.append(f"腾讯生态信源 {payload['tencent_sources']} 条")
    if payload.get("provider") == "knowledge":
        chips = ["通用知识 · 未检索"]
    if payload.get("provider") == "policy":
        chips = ["未检索 · 不在受理范围"]

    on_topic = [e for e in evidence if e.get("on_topic")]
    off_cnt = len(evidence) - len(on_topic)
    rows = "".join(
        f"<tr><td>{_e(e.get('site') or '—')}</td><td>{_e((e.get('date') or '')[:10])}</td>"
        f"<td>{_e(_STANCE.get(e.get('stance',''), '未表态'))}</td><td>{_e(e.get('authority','-'))}/5</td>"
        f"<td><a href='{_e(e.get('url'))}' target='_blank' rel='noopener'>{_e(e.get('title'))}</a></td></tr>"
        for e in on_topic[:20]
    )
    all_ev_html = (
        f"<h3>全部主题相关来源（{len(on_topic)} 条，列出前 {min(len(on_topic),20)} 条）</h3>"
        "<p class='note'>只统计标题确实在谈这句话的页面；同一主体的另一件事不计入（见「异常与排除」）。每条都可点开原文复核。</p>"
        "<table><thead><tr><th>站点</th><th>日期</th><th>立场</th><th>权威度</th><th>标题（点击可核验）</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p class='note'>另有 {off_cnt} 条命中因未直接涉及本条说法而排除，不计入判定。</p>"
    ) if on_topic else ""

    return _shell(
        title=f"核查报告 · {raw[:24]}",
        body=f"""
    <div class="rhead">
      <div class="brand">据实 · 发布前事实核查</div>
      <h1>{_e(raw)}</h1>
      <div class="bar">
        <span class="badge {tone}">{_e(label or verdict)}</span>
        <div class="chips">{''.join(f'<span class="chip">{_e(c)}</span>' for c in chips)}</div>
      </div>
      <p class="reason">{_e(v.get('reason',''))}</p>
    </div>

    <h2>一、结论依据</h2>
    {ev_html}
    {all_ev_html}

    {notes_html}
    {missing_html}

    <h2>二、取证过程</h2>
    <p class="note">三轮检索：广泛取证 → 交叉验证 → 溯源一手材料。下表是每一次检索实际发出的查询与命中条数，可复核。</p>
    {trace_html or '<p class="empty">本次未检索。</p>'}

    <h2>三、子主张拆解</h2>
    <p class="note">把一句话拆成可分别验证的小断言，哪一条没有依据一眼可见。</p>
    {matrix_html or '<p class="empty">本次未做子主张拆解。</p>'}

    {f"<h2>四、来源分歧</h2>{conflict_html}" if conflict_html else ""}

    {f"<h2>{'五' if conflict_html else '四'}、时间线</h2>{timeline_html}" if timeline_html else ""}

    <h2>附：口径说明</h2>
    <ul class="foot">
      <li><b>置信度</b>是规则档位而非概率：权威度越高、依据越硬，档位越高，上限 90。</li>
      <li><b>命中</b>是检索返回的全部条目；<b>可用</b>是通过一手性、权威度、时效、利益相关四维筛选后可进入判定的来源。</li>
      <li>「可用」不等于「能定论」：只有明确表态的来源才用于给出真或假，其余按未表态处理。</li>
      <li>本报告只呈现检索到的公开来源，未检索到的部分一律标注「没查到」，不做推测。</li>
    </ul>

    <div class="rfoot">
      <span>核查编号 #{_e(code)}</span>
      <span>生成时间 {_e(created_at or datetime.now().strftime('%Y-%m-%d %H:%M'))}</span>
      <span>{_e(config.OWNER_NAME)}{(' · ' + _e(config.OWNER_TAGLINE)) if config.OWNER_TAGLINE else ''}</span>
    </div>
    <div class="disc">{_e(payload.get('disclaimer') or config.DISCLAIMER)}<br>{_e(config.CONTEST_LINE)}</div>""",
    )


_STANCE = {"support": "支持", "refute": "反驳", "neutral": "未表态"}


def build_checkup_report(payload: dict, code: str = "", created_at: str = "") -> str:
    items = payload.get("items") or []
    summary = payload.get("summary") or {}
    grade = summary.get("grade") or payload.get("grade") or ""
    score = summary.get("score") if "score" in summary else payload.get("score")
    # grade 存储格式为「D · 不建议发布」；展示改为「D 级 · 不建议发布」，
    # 徽章配色按等级字母（A/B 绿、C 琥珀、D 红）而不是整串文本查表。
    _g_letter = grade.split(" ")[0] if grade else ""
    _g_rest = grade.split("·", 1)[1].strip() if "·" in grade else ""
    grade_display = f"{_g_letter} 级 · {_g_rest}" if _g_rest else grade
    grade_cls = _GRADE.get(_g_letter, "pending")

    rows = []
    for it in items:
        is_rule = bool(it.get("no_search"))
        rows.append(
            f"""<div class="item">
      <div class="item-h">
        <span class="badge {_LEVEL.get(it.get('risk_level',''), 'pending')}">{_e(it.get('risk_label') or it.get('risk_level') or '')}</span>
        <span class="chip">{_e(it.get('issue_type') or '')}</span>
        {f"<span class='chip'>规则判定 · 未检索</span>" if is_rule else f"<span class='chip'>检索 {it.get('searches', 0)} 次</span>"}
        <span class="sent">{_e(it.get('sentence') or it.get('text') or '')}</span>
      </div>
      {f"<p class='why'>{_e(it.get('verdict_display') or '')}：{_e(it.get('reason') or '')}</p>" if it.get("reason") else ""}
      {f"<p class='fix'><b>改法：</b>{_e(it.get('suggestion') or it.get('fix') or '')}</p>" if (it.get("suggestion") or it.get("fix")) else ""}
      {_evlist(it.get("evidence") or [])}
    </div>"""
        )

    return _shell(
        title="文案体检报告",
        body=f"""
    <div class="rhead">
      <div class="brand">据实 · 文案体检</div>
      <h1>文案体检报告</h1>
      <div class="bar">
        <span class="badge {grade_cls}">{_e(grade_display)}</span>
        <div class="chips">
          <span class="chip">得分 {_e(score if score is not None else '—')}</span>
          <span class="chip">检查 {len(items)} 句</span>
          <span class="chip">检索 {payload.get('searches', 0) if isinstance(payload.get('searches'), int) else ''} 次</span>
        </div>
      </div>
      {f"<p class='reason'>{_e(summary.get('headline') or '')}</p>" if summary.get("headline") else ""}
    </div>

    <h2>逐句体检结果</h2>
    {''.join(rows) or '<p class="empty">没有发现需要提示的句子。</p>'}

    <h2>附：口径说明</h2>
    <ul class="foot">
      <li><b>违禁词</b>依据广告法与相关规定的明文表述判定，不做自由裁量。</li>
      <li><b>事实宣称</b>实行举证责任倒置：拿不出可举证的材料，即按风险处理，而不是当作没风险。</li>
      <li>本报告只提示风险与改法方向，不构成法律意见。</li>
    </ul>

    <div class="rfoot">
      <span>报告编号 #{_e(code)}</span>
      <span>生成时间 {_e(created_at or datetime.now().strftime('%Y-%m-%d %H:%M'))}</span>
      <span>{_e(config.OWNER_NAME)}{(' · ' + _e(config.OWNER_TAGLINE)) if config.OWNER_TAGLINE else ''}</span>
    </div>
    <div class="disc">{_e(payload.get('disclaimer') or config.DISCLAIMER)}<br>{_e(config.CONTEST_LINE)}</div>""",
    )


_LEVEL = {"high": "high", "mid": "mid", "low": "low"}
_GRADE = {"A": "low", "B": "low", "C": "mid", "D": "high"}


def _evlist(items: list[dict]) -> str:
    if not items:
        return ""
    lis = "".join(
        f"<li><a href='{_e(e.get('url'))}' target='_blank' rel='noopener'>{_e(e.get('title'))}</a>"
        f"<span class='site'> · {_e(e.get('site') or '')}</span></li>"
        for e in items[:4]
    )
    return f"<ul class='evul'>{lis}</ul>"


def build(row: dict) -> str:
    """从一条落库记录生成报告。"""
    payload = row.get("payload") or {}
    code = row.get("code") or ""
    created_at = (row.get("created_at") or "")[:16].replace("T", " ")
    if row.get("kind") == "checkup":
        return build_checkup_report(payload, code, created_at)
    return build_verify_report(payload, code, created_at)


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:32px 20px;background:#fff;color:#1b1f24;
 font-family:-apple-system,"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
 font-size:14px;line-height:1.75}}
.wrap{{max-width:860px;margin:0 auto}}
.printbar{{position:fixed;right:18px;bottom:18px;display:flex;gap:8px;z-index:9}}
.printbar button{{font-family:inherit;font-size:13px;padding:9px 16px;border-radius:8px;border:1px solid #0052d9;background:#0052d9;color:#fff;cursor:pointer}}
.printbar a{{font-family:inherit;font-size:13px;padding:9px 16px;border-radius:8px;border:1px solid #cfd6e0;background:#fff;color:#333;text-decoration:none}}
@media print{{.printbar{{display:none!important}} body{{padding:0}} .wrap{{max-width:none}}}}

.rhead{{border-bottom:2px solid #0052d9;padding-bottom:18px;margin-bottom:26px}}
.brand{{font-size:12px;letter-spacing:.16em;color:#0052d9;font-weight:700;margin-bottom:10px}}
h1{{font-size:22px;line-height:1.5;margin:0 0 14px}}
.bar{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}
.badge{{font-size:14px;font-weight:700;padding:5px 14px;border-radius:6px}}
.badge.low{{background:#e8f5ec;color:#1a7f37}}
.badge.high{{background:#fdecec;color:#c0392b}}
.badge.mid{{background:#fff4e0;color:#a8671a}}
.badge.pending{{background:#f0f2f5;color:#5a6270}}
.badge.refused{{background:#eef0f4;color:#5a6270;border:1px solid #d8dde5}}
.chips{{display:flex;gap:8px;flex-wrap:wrap}}
.chip{{font-size:12px;color:#4b5260;background:#f3f5f8;border-radius:5px;padding:3px 9px}}
.reason{{font-size:15px;color:#222;margin:0}}
h2{{font-size:16px;margin:30px 0 12px;padding-left:10px;border-left:4px solid #0052d9}}
h3{{font-size:14px;margin:20px 0 8px;color:#333}}
p.note{{font-size:12.5px;color:#767c86;margin:0 0 10px}}
p.empty{{font-size:13px;color:#767c86}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:6px}}
th,td{{border:1px solid #e3e6eb;padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#f7f8fa;font-weight:600;color:#333}}
.src{{border:1px solid #e3e6eb;border-radius:7px;padding:12px 14px;margin-bottom:10px}}
.src-h{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12px;color:#767c86;margin-bottom:6px}}
.tag{{font-weight:700;padding:2px 8px;border-radius:5px;font-size:11.5px}}
.tag.support{{background:#e8f5ec;color:#1a7f37}}
.tag.refute{{background:#fdecec;color:#c0392b}}
.tag.neutral{{background:#f0f2f5;color:#5a6270}}
.auth{{color:#0052d9;font-weight:600}}
a.title{{color:#1b1f24;font-weight:600;text-decoration:none;font-size:14px}}
a.title:hover{{color:#0052d9;text-decoration:underline}}
.snip{{margin:7px 0;font-size:13px;color:#4b5260}}
.url{{font-size:11.5px;color:#9aa0a9;word-break:break-all}}
.item{{border:1px solid #e3e6eb;border-radius:7px;padding:13px 15px;margin-bottom:10px}}
.item-h{{display:flex;gap:9px;align-items:flex-start;flex-wrap:wrap;margin-bottom:6px}}
.sent{{flex:1;min-width:220px;font-weight:600}}
.why{{margin:4px 0;font-size:13px;color:#4b5260}}
.fix{{margin:4px 0;font-size:13px;color:#1a7f37}}
ul.foot,ul.evul{{font-size:12.5px;color:#5a6270;padding-left:20px}}
.rfoot{{margin-top:30px;padding-top:14px;border-top:1px solid #e3e6eb;
 display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#767c86}}
.disc{{margin-top:8px;font-size:11.5px;color:#9aa0a9;line-height:1.7}}
@media print{{
 body{{padding:0;font-size:12px}}
 .wrap{{max-width:none}}
 a.title{{color:#000}}
 @page{{margin:16mm}}
}}
</style></head>
<body><div class="wrap">{body}</div>
<div class="printbar"><button onclick="window.print()">打印 / 另存为 PDF</button><a href="/">回首页</a></div>
<script>window.addEventListener("load",function(){{document.title={json.dumps(title, ensure_ascii=False)}}});
</script></body></html>"""
