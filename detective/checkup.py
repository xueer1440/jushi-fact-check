"""体检模式：长文案 → L1 违禁词法条判定（0 检索）→ 剩余事实宣称 Top8 深查 → 风险清单。

三条硬规矩：
1. 词库只定位，不判定；
2. 没跑检索的句子只能标「待核查」，绝不标「违规」；
3. 宣传类断言查不到依据＝不能这么写（广告主负举证责任），这是本产品与词库工具的分水岭。
"""
from __future__ import annotations

import re
import time
from typing import Iterator

from . import claims, config, investigator, lexicon, rules

_LABEL_BY_VERDICT = {
    "false": ("高风险", "high"),
    "misleading": ("误导 · 需修改", "high"),
    "no_source": ("高风险", "high"),
    "unclear": ("需存疑", "mid"),
    "true": ("有依据", "low"),
}


def _level_of(verdict: str, fallback: str) -> str:
    """宣传类：查不到依据也按高风险处理（举证责任在广告主）。"""
    return _LABEL_BY_VERDICT.get(verdict, ("待核查", fallback))[1]


def _verdict_label(verdict: str) -> str:
    from .judge import VERDICT_LABEL

    return VERDICT_LABEL.get(verdict, verdict)


def _prepare(text: str):
    top_n = config.CHECKUP_TOP_N
    sentences = rules.split_sentences(text)
    findings = rules.scan(text)
    lex_hits = lexicon.scan(text)
    return sentences, findings, lex_hits, top_n


def _map_lex_sentences(text: str, sentences: list[str], lex_hits: list) -> list:
    """把违禁词归到「短语级句子」，和 L1 风险句式用同一把尺子。

    词库按句末标点切、L1 按逗号切到短语级，两套粒度会让同一句话被处理两次：
    一次算违禁词，一次又拿去检索。这里统一到短语级，违禁词句才真能"不再核查"。
    """
    from dataclasses import replace

    cursor = 0
    spans: list[tuple[int, int, str]] = []
    for s in sentences:
        idx = text.find(s, cursor)
        if idx == -1:
            idx = cursor
        spans.append((idx, idx + len(s), s))
        cursor = idx + len(s)

    out = []
    for h in lex_hits:
        sent = next((s for a, b, s in spans if a <= h.position < b), "")
        if not sent:
            sent = next((s for _, _, s in spans if h.word in s), h.sentence)
        out.append(replace(h, sentence=sent))
    return out


def _lex_items(lex_hits: list) -> list[dict]:
    """违禁词按句聚合：法条明文，规则即定，一次检索都不做。

    这是体检里唯一「不检索就给结论」的一类——因为它判的不是事实真假，
    而是用词是否违反明文列举，答案在法条里，不在搜索引擎里。
    """
    by_sent: dict[str, list] = {}
    for h in lex_hits:
        by_sent.setdefault(h.sentence, []).append(h)

    out: list[dict] = []
    for sent, hits in by_sent.items():
        hits.sort(key=lambda h: h.level != "high")
        level = "high" if any(h.level == "high" for h in hits) else "mid"
        words = "、".join(h.word for h in hits)
        basis = "；".join(dict.fromkeys(h.basis for h in hits))
        fix = "；".join(dict.fromkeys(h.suggest for h in hits))
        out.append(
            {
                "sentence": sent,
                "label": "违禁词",
                "matched": hits[0].word,
                "why": basis,
                "fix": fix,
                "risk_level": level,
                "verdict": "",
                "verdict_label": "违规用词" if level == "high" else "建议调整",
                "verdict_display": "违规用词" if level == "high" else "建议调整",
                "issue_type": "违禁词",
                "claim_type": "",
                "notes": ["按法条明文判定，无需检索", "改完后可以再体检一遍确认"],
                "confidence": 0,
                "reason": f"命中「{words}」：{basis}",
                "missing": [],
                "evidence": [],
                "usable_count": 0,
                "searches": 0,
                "provider": "rule",
                "demo": False,
                "trace": [],
                "conflict": [],
                "matrix": [],
                "no_search": True,
            }
        )
    out.sort(key=lambda x: x["risk_level"] != "high")
    return out


_RULE_SPEC = {
    "service_guarantee": {
        "label": "保证性承诺",
        "why": "对服务/培训效果作了保证，《广告法》禁止对教育、培训效果作保证性承诺；"
               "其他行业的效果保证也容易构成虚假宣传",
        "fix": "删掉效果保证，改成可核实的内容：写明服务范围、交付物、已完成案例；"
               "效果部分用「以实际为准」限定",
        "notes": ["按法条判定，无需检索", "服务范围本身可以写，删掉的是「保证效果」"],
        "hint": "对效果作了保证",
        "level": "mid",
    },
    "credential": {
        "label": "资质自称",
        "why": "个人资质（证书、职称）在公开检索里核不到——查不到不等于假；"
               "但读者也无从验证，属于需要补证明的说法",
        "fix": "在文案里注明证书全称与颁发机构（如「XX 认证高级人工智能训练师」），"
               "或删掉无法证明的头衔",
        "notes": ["个人资质不在公开检索范围内，本项不判真假", "补上证书名称即可消掉这一项"],
        "hint": "自称持有资质",
        "level": "mid",
    },
    "self_claim": {
        "label": "自述信息",
        "why": "第一人称的经营细节（开了几年、几小时、人均多少钱），网上查不到，"
               "但属于可自证内容——广告主负举证责任，靠留存材料履行，不必事前公开",
        "fix": "不用改文案；请留存可举证材料（进货单、工时记录、租赁合同、价目表），"
               "被质疑时能拿出来即可",
        "notes": ["可自证内容，本项不判真假", "留好材料比改文案更重要"],
        "hint": "经营细节，可自证",
        "level": "low",
    },
}


def _guarantee_items(
    sentences: list[str], lex_sent: set[str], types: dict[str, str] | None = None
) -> list[dict]:
    """保证性承诺 / 资质自称 / 自述信息：都不检索，按规则直接给结论与改法。

    这几类判的不是"网上能不能查到"，所以不该占用检索预算、也不该判成
    "无依据的高风险"。但它们确实需要提示，所以照旧出现在结果里。
    """
    out: list[dict] = []
    for s in sentences:
        if s in lex_sent:
            continue
        t = types.get(s) if types else claims.classify_claim(s)
        spec = _RULE_SPEC.get(t)
        if not spec:
            continue
        hit = next((g for g in claims._GUARANTEE_WORDS if g in s), "")
        out.append(
            {
                "sentence": s,
                "label": spec["label"],
                "matched": hit,
                "why": spec["why"],
                "fix": spec["fix"],
                "risk_level": spec.get("level", "mid"),
                "verdict": "",
                "verdict_label": spec["label"],
                "verdict_display": spec["label"],
                "issue_type": spec["label"],
                "claim_type": t,
                "notes": spec["notes"],
                "confidence": 0,
                "reason": spec["hint"],
                "missing": [],
                "evidence": [],
                "usable_count": 0,
                "searches": 0,
                "provider": "rule",
                "demo": False,
                "trace": [],
                "conflict": [],
                "matrix": [],
                "no_search": True,
            }
        )
    return out


def _hard_claim(text: str) -> bool:
    """这句话本身有没有「能被外部证据推翻」的硬内容。

    服务类文案里的碎片（「无需上大系统」「能答客户」「说清你的场景，免费」）
    描述的是交付方式与能力范围，公开渠道查不到第三方证据；
    但只要有数字、绝对化、疗效、产地、认证这类硬内容，就必须照常检索。
    """
    if not text:
        return False
    # 「最终」是承接副词（"最终落不了地"），不是绝对化宣称，先摘掉再判断
    if any(w in text.replace("最终", "") for w in ("%", "％", "倍", "第一", "唯一", "最")):
        return True
    if any(w in text for w in (
        "包治", "根治", "治愈", "疗效", "致癌", "超标", "无毒", "无添加", "零添加",
        "进口", "引进", "原装", "专利", "认证", "持证", "权威", "国家", "唯一",
    )):
        return True
    if re.search(r"\d", text):
        return True
    if re.search(r"(是|属于|均为|都是|全部)", text):
        return True
    return False


_FRAG_HEAD = (
    "最终", "希望", "然后", "接着", "并且", "而且", "因此", "所以", "另外",
    "同时", "比如", "尤其", "以及", "还有", "从而", "进而", "此外", "不然",
)
# 出现这些词说明句子有明确的对象/主体，是完整的话，不是半截
_FRAG_SUBJECT = ("你", "您", "我", "他", "她", "它", "我们", "客户", "用户",
                 "员工", "团队", "企业", "公司", "学员", "学生", "患者", "店", "家")
# 数字后面跟这些，是量词/单位（24小时、10-200人），不是排版编号
_FRAG_UNIT = ("小时", "天", "年", "月", "周", "分", "秒", "人", "个", "元", "万",
              "千", "%", "％", "倍", "×", "*", "-", "、", "／", "/")
# 编号后面带这些词，说明这半截话里藏着一条要核的宣称（功效/认证/数据），
# 不能当 OCR 碎片丢掉——否则「1次见效」会整句跳过检索。
_FRAG_EFFECT = ("见效", "起效", "根治", "治愈", "治好", "止痛", "祛", "美白", "瘦",
                "减肥", "增高", "康复", "痊愈", "不复发", "认证", "专利", "进口",
                "原装", "第一", "唯一", "国家级", "保证", "承诺")


def _fragment(text: str) -> bool:
    """是不是图片识别（OCR）把两栏/两行读串后切出来的半截话。

    双栏海报 OCR 会把左右两栏读成一行，再按句号切开，
    于是出现「1适合这样的企业」「希望尽快见效」「最终落不了地」这类碎片。
    它们不是一句完整的宣称，拿去检索只会匹配到毫不相干的页面，
    然后被判成"与公开来源不符"——这是误杀，不是核查。

    判定要很克制，只在三件事同时成立时才认：
      1. 没有硬内容（无数字、无绝对化、无疗效/产地/认证词）——有就照常查；
      2. 没有标点——半截话通常切在逗号处，读出来是光秃秃一串字；
      3. 满足碎片特征之一：数字序号和正文粘在一起，或以连接/承接词开头。
    """
    s = (text or "").strip()
    if not s:
        return False
    # 「1适合这样的企业」「01我能提供什么」：OCR 把栏目标号和正文读到了一起。
    # 这里的数字是排版编号，不是宣称数据，所以要先于硬内容判断。
    # 排除带单位的情况（24小时 / 7×24 / 10-200人）——那是真数字，得照常查。
    m = re.match(r"^(\d{1,2})([^\d\s])", s)
    if m and int(m.group(1)) <= 12 and m.group(2) not in _FRAG_UNIT and not any(
        u in s for u in _FRAG_UNIT
    ):
        # 数字是排版编号没错，但如果后半截带着功效/数据宣称（「1次见效」「3步根治」），
        # 那它就是一句要核的话，不是碎片——不能因为开头有个编号就整个放过。
        # （10-24 自查发现：原实现把这类句子整个跳过，造成漏检。）
        if any(w in s for w in _FRAG_EFFECT):
            return False
        return True
    if _hard_claim(s):
        return False
    if re.search(r"[，。！？、；：,.!?;:”\"）)]", s):
        return False
    # 「希望尽快见效」「最终落不了地」：承接/愿望性开头、没有对象、又短，
    # 基本都是上一句的后半截。有明确主体（你/客户/企业…）的按正常句子处理。
    if s.startswith(_FRAG_HEAD) and len(s) <= 8 and not any(w in s for w in _FRAG_SUBJECT):
        return True
    return False


def _pending(
    rule_findings: list[rules.RiskFinding],
    unchecked: list[rules.RiskFinding],
    checked: list[dict],
) -> list[dict]:
    """没排上深查的句子分两种，别混成一个"待核查"。"""
    done = {c["sentence"] for c in checked}
    out: list[dict] = []

    def row(f: "rules.RiskFinding", needs_search: bool) -> dict:
        return {
            "sentence": f.sentence,
            "label": f.label,
            "level": f.level,
            "fix": f.fix,
            "needs_search": needs_search,
            "state": "待核查" if needs_search else "规则判定",
            "note": (
                "这句含事实宣称，需要检索才知道真假；本次未排上，所以不给结论。"
                if needs_search
                else "属表述风险，规则即可判定，按建议调整即可。"
            ),
        }

    for f in rule_findings:
        if f.sentence not in done:
            out.append(row(f, False))
    for f in unchecked:
        if f.sentence not in done:
            out.append(row(f, True))
    out.sort(key=lambda x: (not x["needs_search"], x["level"] != "high"))
    return out


def _summary(
    sentences: list[str],
    checked: list[dict],
    pending: list[dict],
    lex_total: int = 0,
    clean_total: int = 0,
) -> dict:
    high = sum(1 for c in checked if c["risk_level"] == "high")
    mid = sum(1 for c in checked if c["risk_level"] == "mid")
    low = sum(1 for c in checked if c["risk_level"] == "low")
    guar = sum(1 for c in checked if c["issue_type"] == "保证性承诺")
    cred = sum(1 for c in checked if c["issue_type"] == "资质自称")
    lex_high = sum(1 for c in checked if c["issue_type"] == "违禁词" and c["risk_level"] == "high")
    false_cnt = sum(1 for c in checked if c.get("verdict") in ("false", "misleading"))
    fact_high = high - lex_high
    score, grade = _score(high, mid, low, false_cnt)
    # 判了「假 / 误导」的文案，不能给 A、B。若被判假的句子占已核查句子的一半以上，
    # 说明这段文案的主体就是假的 → 直接 D（不建议发布），分数也压到 60 以下。
    if false_cnt:
        judged = max(1, sum(1 for c in checked if c.get("verdict")))
        if false_cnt / judged >= 0.5:
            grade = "D · 不建议发布"
            score = min(score, 54)
        elif grade[:1] in ("A", "B"):
            grade = "C · 需修改"
    if high:
        parts = []
        if lex_high:
            parts.append(f"{lex_high} 句含违禁词")
        if false_cnt:
            parts.append(f"{false_cnt} 句与公开来源不符")
        unclear_high = fact_high - false_cnt
        if unclear_high > 0:
            parts.append(f"{unclear_high} 句事实宣称拿不出依据")
        headline = f"这段文案有 {'、'.join(parts)}，建议先改标红的几句。"
    elif mid:
        bits = []
        if guar:
            bits.append(f"{guar} 处效果保证")
        if cred:
            bits.append(f"{cred} 处资质待补证明")
        rest_mid = mid - guar - cred
        if rest_mid:
            bits.append(f"{rest_mid} 处说法尚拿不出依据")
        headline = f"未见明显硬伤；有 {'、'.join(bits) or str(mid) + ' 处待改'}，建议改完再发。"
    elif checked:
        headline = "命中的风险点都能拿出依据，结论暂不用改；建议留存本次核查记录。"
    else:
        headline = "未发现需要修改的表述，可以发布；建议留存本次核查记录。"
    by_type: dict[str, int] = {}
    for c in checked:
        by_type[c["issue_type"]] = by_type.get(c["issue_type"], 0) + 1
    return {
        "headline": headline,
        "score": score,
        "grade": grade,
        "counts": {
            "sentences": len(sentences),
            "checked": len(checked),
            "high": high,
            "mid": mid,
            "low": low,
            "pending": len(pending),
            "clean": clean_total,      # 未命中风险句式、无需深查的句子
            "lex": lex_total,
            "lex_high": lex_high,
            "by_type": by_type,
        },
    }


_CHECK_DISPLAY = {
    "false": "与公开来源不符",
    "no_source": "尚无可举证材料",
    "unclear": "依据不足",
    "true": "有依据支撑",
}


def _display(verdict: str, claim_type: str) -> str:
    """体检报告用"能不能这么写"的语言，不用"真/假"。"""
    return _CHECK_DISPLAY.get(verdict, "待核查")


def _score(high: int, mid: int, low: int, false_cnt: int = 0) -> tuple[int, str]:
    """100 分制。判「假 / 误导」的句子额外多扣 10 分：
    这不是"拿不出依据"，是"与公开来源相反"，性质更重——
    否则一句话的文案判了假还能拿 82 分 B 级，和事实核查页的「假」对不上。
    """
    score = 100 - high * 18 - mid * 8 - false_cnt * 10
    score = max(0, min(100, score))
    if score >= 85:
        grade = "A · 可发布"
    elif score >= 70:
        grade = "B · 小幅修改"
    elif score >= 55:
        grade = "C · 需修改"
    else:
        grade = "D · 不建议发布"
    return score, grade


_FIX_BY_VERDICT = {
    "false": "与公开来源不符：删去这句，或改为与来源一致的表述。",
    "misleading": "该表述有误导性：改为限定明确的说法（说清对象、条件、数据出处），不留夸大空间。",
    "no_source": "拿不出依据就不建议这么写：删去这句，或换成可举证的表述（附检测报告、备案编号、统计数据出处）。",
    "unclear": "依据不足：先补上可核验的出处；补不上就改为留有余地的表述（如「据我们统计」并注明口径）。",
    "true": "有依据支撑：建议在这句旁注明出处（报告编号、数据来源），既合规也更可信。",
}


def _fix_for(verdict: str, base_fix: str) -> str:
    """检索类条目没有现成改法时，按判定结果给一句诚实的修改建议。"""
    return base_fix or _FIX_BY_VERDICT.get(verdict, "")


def iter_checkup(
    text: str, *, use_llm: bool = True, top_n: int | None = None
) -> Iterator[tuple[str, dict]]:
    """流式体检：逐条吐出结果，前端边出边显示。"""
    sentences, findings, lex_hits, default_n = _prepare(text)
    top_n = top_n or default_n
    lex_hits = _map_lex_sentences(text, sentences, lex_hits)
    lex_summary = lexicon.summarize(lex_hits)
    lex_sent = {h.sentence for h in lex_hits}

    # ---- 句子类型预判 ----
    # 全文出现第一人称时，句子里只写经营细节的（开了几年、熬几小时、人均多少），
    # 属于「可自证内容」：广告主的举证责任靠留存材料履行，不必先公开。
    # 这类不该走检索、更不该判成"网上查不到 = 高危"。
    self_ctx = any(w in text for w in ("我们", "我司", "本店", "本公司", "本人", "我帮", "我家", "我店"))
    types: dict[str, str] = {}
    for s in sentences:
        t = claims.classify_claim(s)
        if t in ("rumor", "news", "ad_claim", "data") and self_ctx and claims.looks_like_own_business(s):
            t = "self_claim"
        # 整段是「商家自述服务」文脉时，没有硬内容的碎片句按服务描述处理：
        # 不检索、不计风险。依据：这类句子讲的是交付方式与能力范围，
        # 公开渠道查不到第三方证据，按事实宣称检索只会误杀正常文案。
        if self_ctx and t in ("rumor", "news") and not _hard_claim(s):
            t = "service"
        types[s] = t

    # ---- 第零层：剔除 OCR 断句碎片 ----
    # 图片识别把双栏读成一行后切出的半截话不是完整宣称，不检索、不计风险，
    # 只留一份清单备查（报告里并不展示为"干净文案"，避免虚报）。
    fragments = [s for s in sentences if _fragment(s)]
    frag_set = set(fragments)

    # ---- 第一层：违禁词。法条明文，规则即定，一次检索都不做 ----
    checked: list[dict] = list(_lex_items(lex_hits))
    # 保证性承诺：判断依据在法条（广告法禁止对效果作保证承诺），不在搜索结果里，
    # 所以同样零检索——只提示改法，不浪费一次调用。
    checked.extend(_guarantee_items(sentences, lex_sent, types))

    # ---- 第二层：剩下的句子里，挑「可核宣称」去检索 ----
    # 注意：不能只从规则命中里挑——规则没覆盖的句式（如"效率提升300%"）
    # 也是事实宣称，漏掉就会静默溜进"干净句"。
    # 可核宣称含四类：广告宣称/数据/口碑传闻/新闻式说法——rumor/news 以前
    # 被直接丢进"未核查"，即使深查队列没满也不查，违背"最多 8 句深查"承诺。
    CHECK_TYPES = ("ad_claim", "data", "rumor", "news")
    rest = [f for f in findings if f.sentence not in lex_sent and f.sentence not in frag_set]
    flagged = {f.sentence for f in rest}
    for idx, s in enumerate(sentences):
        if s in lex_sent or s in frag_set or s in flagged:
            continue
        if types.get(s) in CHECK_TYPES:
            rest.append(
                rules.RiskFinding(
                    sentence=s,
                    sentence_index=idx,
                    pattern_key="fact_claim",
                    label="事实宣称",
                    level="low",
                    matched=s,
                    why="这句话含可核验的事实宣称，需要检索验证",
                    fix="",
                )
            )
    claim_findings = [f for f in rest if types.get(f.sentence) in CHECK_TYPES]
    claim_ids = {id(f) for f in claim_findings}
    rule_findings = [f for f in rest if id(f) not in claim_ids]
    to_check = claim_findings[:top_n]

    yield (
        "start",
        {
            "kind": "checkup",
            "sentences": len(sentences),
            "findings": len(findings),
            "lex_total": lex_summary["total"],
            "lex_high": lex_summary["high"],
            "lex_sentences": len(checked),
            "fact_total": len(claim_findings),
            "rule_total": len(rule_findings),
            "plan": [f"{f.label}@第{f.sentence_index + 1}句" for f in to_check],
        },
    )

    # 违禁词项直接吐出：不用等检索，用户一眼先看到确定的问题
    for item in checked:
        yield ("item", item)

    # 时间预算：平台网关约 100 秒会截断长请求（实测 674 字海报触发 524）。
    # 到点就停，没查的句子进「待核查」——宁可少查几句，也不能给没查过的句子下结论。
    t0 = time.monotonic()
    budget = config.CHECKUP_TIME_BUDGET
    done_n = 0
    for i, f in enumerate(to_check):
        if i and time.monotonic() - t0 > budget:
            break
        done_n = i + 1
        yield ("stage", {"sentence": f.sentence, "label": f.label, "step": "正在核查这句话"})
        inv = investigator.investigate(
            f.sentence, use_llm=use_llm, budget=3, max_rounds=1
        )
        v = inv.verdict
        item = {
            "sentence": f.sentence,
            "label": f.label,
            "matched": f.matched,
            "why": f.why,
            "fix": _fix_for(v.verdict, f.fix),
            "risk_level": _level_of(v.verdict, f.level),
            "verdict": v.verdict,
            "verdict_label": _verdict_label(v.verdict),
            "verdict_display": _display(v.verdict, inv.claim.claim_type),
            "issue_type": "事实宣称" if inv.claim.claim_type in ("ad_claim", "data", "rumor", "news") else "表述风险",
            "no_search": False,
            "claim_type": inv.claim.claim_type,
            "notes": v.notes,
            "confidence": v.confidence,
            "reason": v.reason,
            "missing": v.missing,
            "evidence": [e.as_dict() for e in inv.top_evidence],
            "usable_count": inv.usable_count,
            "searches": inv.searches,
            "provider": inv.provider,
            "demo": inv.demo,
            "trace": inv.trace,
            "conflict": inv.conflict,
            "matrix": inv.matrix,
        }
        checked.append(item)
        yield ("item", item)

    # 超预算没跑到的句子，与「没排进 Top8」的句子同样处理：标待核查，不给结论。
    skipped = list(to_check[done_n:])
    pending = _pending(rule_findings, claim_findings[top_n:] + skipped, checked)
    # 规则没覆盖、也不是 ad_claim/data 的待核实说法（如"权威机构认证"
    # "政府指定合作单位"）：不能算干净句，放进折叠区标"未核查"。
    done_sentences = {c["sentence"] for c in checked} | {p["sentence"] for p in pending}
    for idx, s in enumerate(sentences):
        if s in lex_sent or s in done_sentences:
            continue
        if types.get(s) in ("rumor", "news"):
            pending.append(
                {
                    "sentence": s,
                    "label": "事实宣称",
                    "level": "low",
                    "fix": "",
                    "needs_search": True,
                    "state": "未核查",
                    "note": "这句是待核实的事实宣称，本次体检未深查；要验证可在「事实核查」里单独查。",
                }
            )
            done_sentences.add(s)
    # 干净句 = 既没检查过、也不在折叠区里的句子。
    # 之前用规则 findings 排除，会把违禁词句、深查句都错算成"干净"。
    handled = {c["sentence"] for c in checked} | {p["sentence"] for p in pending}
    # 碎片句既不算"已核查"，也不算"干净"——它压根不是一句完整的话。
    clean = [s for s in sentences if s not in handled and s not in frag_set]
    summary = _summary(sentences, checked, pending, lex_summary["total"], len(clean))
    yield (
        "done",
        {
            "kind": "checkup",
            "headline": summary["headline"],
            "score": summary["score"],
            "grade": summary["grade"],
            "counts": summary["counts"],
            "items": checked,
            "pending": pending,
            "clean": clean,
            "fragments": fragments,
            "lex": [h.as_dict() for h in lex_hits],
            "lex_summary": lex_summary,
            "searches": sum(c["searches"] for c in checked),
            "sources": sum(len(c["evidence"]) for c in checked),
            "provider": next((c["provider"] for c in checked if c["provider"] != "rule"), "rule"),
            "demo": any(c["demo"] for c in checked),
            "disclaimer": config.DISCLAIMER,
        },
    )


def run_checkup(text: str, *, use_llm: bool = True, top_n: int | None = None) -> dict:
    final: dict = {}
    for event, data in iter_checkup(text, use_llm=use_llm, top_n=top_n):
        if event == "done":
            final = data
    return final
