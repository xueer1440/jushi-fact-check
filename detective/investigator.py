"""多轮调查主循环：搜 → 读 → 判断缺什么 → 再搜。

同一轮的多条查询并发发出（发挥元宝搜索的毫秒级响应），
整轮最多 3 轮、9 次检索、10 秒量级完成。
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from . import claims as claims_mod
from . import config, judge, links, llm, planner, policy, rules, search
from .claims import Claim
from .judge import Evidence, Verdict

_MAX_EVIDENCE = 40
_RAW_PAGE_CHARS = 3000  # 估算：一份网页正文约 3000 字


@dataclass
class Investigation:
    claim: Claim
    evidence: list[Evidence]
    trace: list[dict]
    missing: list[str]
    verdict: Verdict
    matrix: list[dict]
    conflict: list[dict]
    timeline: list[dict]
    origin_note: str
    searches: int
    provider: str
    elapsed: float
    demo: bool = False
    top_evidence: list[Evidence] = field(default_factory=list)
    queried_at: str = ""
    tencent_sources: int = 0
    est: dict = field(default_factory=dict)
    usable_count: int = 0          # 真正进入判定的来源数（与 judge.usable_pool 同口径）

    def as_dict(self) -> dict:
        return {
            "claim": self.claim.as_dict(),
            "verdict": self.verdict.as_dict(),
            "evidence": [e.as_dict() for e in self.evidence],
            "top_evidence": [e.as_dict() for e in self.top_evidence],
            "trace": self.trace,
            "missing": self.missing,
            "matrix": self.matrix,
            "conflict": self.conflict,
            "timeline": self.timeline,
            "origin_note": self.origin_note,
            "searches": self.searches,
            "provider": self.provider,
            "elapsed": round(self.elapsed, 1),
            "demo": self.demo,
            "queried_at": self.queried_at,
            "tencent_sources": self.tencent_sources,
            "est": self.est,
            "usable_count": self.usable_count,
            "disclaimer": config.DISCLAIMER,
        }


def _to_evidence(hit: search.RawHit, idx: int, query: str, round_no: int, claim: Claim) -> Evidence:
    body = f"{hit.title} {hit.passage} {hit.content}"
    return Evidence(
        id=f"E{idx}",
        title=hit.title or hit.url,
        url=hit.url,
        site=hit.site,
        date=hit.date,
        snippet=(hit.content or hit.passage or "")[:220],
        score=hit.score,
        authority=0,  # 由调用方填
        one_hand=rules.is_one_hand(hit.url, hit.title),
        interest=rules.is_interest_related(hit.url, hit.site),
        stance=judge.classify_stance(body, claim),
        query=query,
        round_no=round_no,
    )


def _llm_refine_stance(ev: list[Evidence], claim: Claim) -> int:
    """让模型只给立场标签。返回被修正的条数。"""
    if not ev or not llm.available():
        return 0
    listing = "\n".join(f"{e.id}｜{e.title[:60]}｜{e.snippet[:120]}" for e in ev[:12])
    prompt = (
        "下面是关于一条说法的检索结果。请逐条判断这份材料对该说法的立场。\n"
        f"待核说法：{claim.raw}\n\n"
        f"材料列表：\n{listing}\n\n"
        "判定标准（严格执行）：\n"
        "1. support：材料里明确写出该说法成立（官方认定、原始数据、当事人自认）。\n"
        "2. refute：材料里明确写出该说法不成立（辟谣、否认、查无记录、认定与事实不符）。\n"
        "3. neutral：其余所有情况，包括：\n"
        "   · 讲同一家公司/同一个人的其他成绩、荣誉、产品、融资、招聘、采购、财报；\n"
        "   · 名字相近但不是同一家主体（如「大连亚信科技」与「亚信科技（中国）」）；\n"
        "   · 只是恰好出现相同关键词，实际在谈另一件事；\n"
        "   · 你无法确定。\n"
        "注意：说某公司「有行政处罚」既不能支持也不能反驳「该公司是失信被执行人」，"
        "这是两件不同的事，应判 neutral；讲某公司入选榜单、拿到认证，也不能证明它是否是失信企业。\n"
        "特别注意繁体中文与否定表述：「喝咖啡不會增加患癌風險」「不致癌」「沒有證據表明」"
        "都是 refute，不要因为出现「致癌」「風險」就判 support。\n"
        "宁可判 neutral，也不要猜。\n\n"
        "只输出 JSON：{\"labels\":{\"E1\":\"support|refute|neutral\"}}，不要解释，不要编造不存在的编号。"
    )
    obj = llm.chat_json(prompt, max_tokens=400)
    if not obj or not isinstance(obj.get("labels"), dict):
        return 0
    fixed = 0
    valid = {e.id: e for e in ev}
    for eid, label in obj["labels"].items():
        if eid in valid and label in ("support", "refute", "neutral"):
            if valid[eid].stance != label:
                valid[eid].stance = label
                fixed += 1
    return fixed


def _consensus(evidence: list[Evidence]) -> bool:
    """R1 之后就能定论吗？能就提前收手，省额度。"""
    hq = [e for e in evidence if e.high_quality and e.stance != "neutral"]
    if len(hq) < 2:
        return False
    stances = {e.stance for e in hq}
    return len(stances) == 1 and max(e.authority for e in hq) >= 4


_GAP_STOP = {
    "含量", "标准", "限值", "本身", "具有", "是否", "会", "导致", "超过", "国家",
    "食品", "安全", "直接", "依据", "食用", "摄入", "中的", "相关", "可能",
}


def _gap_tokens(text: str) -> list[str]:
    raw = re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z]{2,}", text)
    out = [t for t in raw if t not in _GAP_STOP and len(t) >= 2]
    extra = []
    for t in out:
        if len(t) >= 4 and re.match(r"[\u4e00-\u9fa5]+$", t):
            extra.extend([t[i : i + 2] for i in range(0, len(t) - 1, 2)])
    return list(dict.fromkeys(out + extra))


def _mentioned(tokens: list[str], haystack: str) -> bool:
    return any(t in haystack for t in tokens)


def _gaps(claim: Claim, evidence: list[Evidence]) -> list[str]:
    hq = [e for e in evidence if e.high_quality]
    gaps: list[str] = []
    for sub in claim.subclaims:
        tokens = _gap_tokens(sub.text)
        if not any(_mentioned(tokens, f"{e.title} {e.snippet}") for e in hq):
            gaps.append(sub.text)
    return gaps[:2]


def _conflict(evidence: list[Evidence]) -> bool:
    stances = {e.stance for e in evidence if e.high_quality and e.stance != "neutral"}
    return len(stances) > 1


def _run_parallel(specs: list[planner.QuerySpec], round_no: int):
    """同一轮并发检索——这是"毫秒级响应"能被感知到的地方。"""

    def one(spec: planner.QuerySpec):
        try:
            return (
                search.search(
                    spec.query,
                    cnt=spec.cnt,
                    mode=spec.mode,
                    site=spec.site,
                    industry=spec.industry,
                    freshness=spec.freshness,
                    round_no=round_no,
                ),
                None,
            )
        except search.SearchUnavailable as exc:
            return None, str(exc)

    if not specs:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(specs))) as pool:
        return list(pool.map(one, specs))


def _count_tencent(evidence: list[Evidence]) -> int:
    n = 0
    for e in evidence:
        host = (urlparse(e.url).hostname or "").lower()
        if "qq.com" in host or "腾讯" in (e.site or ""):
            n += 1
    return n


def _token_est(evidence: list[Evidence]) -> dict:
    sent = sum(len(e.title) + len(e.snippet) for e in evidence[:12])
    raw = len(evidence) * _RAW_PAGE_CHARS
    saved = round((1 - sent / raw) * 100) if raw else 0
    return {
        "sent_chars": sent,
        "raw_chars_est": raw,
        "saved_pct": max(0, min(99, saved)),
        "note": "估算：只把摘要送进模型，不抓原网页",
    }


def _refused_result(claim: Claim, kind: str, hit: str, started: float) -> "Investigation":
    """不受理的两类问题：涉及个人的消息、时政话题。

    在入口就挡掉，一次检索都不做——不是查不到，是这类问题我们根本不查。
    """
    if kind == "personal":
        reason = (
            f"这条涉及具体个人的「{hit}」，不在受理范围。"
            "个人品行、信用与隐私不做核查，也不对个人下任何结论。"
        )
        notes = [
            "受理边界：不核查涉及个人的消息",
            "涉及企业、机构的同类问题可以正常核查",
        ]
        missing = [
            "如为企业信用，请写清公司全称，可查国家企业信用信息公示系统（gsxt.gov.cn）",
            "如涉及个人权益，请咨询律师或通过司法途径核实",
        ]
    else:
        reason = f"这条涉及时政话题（命中「{hit}」），不在受理范围。"
        notes = ["受理边界：不碰时政类话题"]
        missing = ["可核查的范围：消费传言、产品宣称、企业信用、公共政策与科普常识"]

    verdict = judge.Verdict(
        verdict=judge.VERDICT_REFUSED,
        confidence=0,
        reason=reason,
        missing=missing,
        notes=notes,
    )
    return Investigation(
        claim=claim,
        evidence=[],
        trace=[{"round": 0, "tag": "refused", "query": "", "purpose": f"受理边界：{kind}，未检索", "count": 0}],
        missing=missing,
        verdict=verdict,
        matrix=[],
        conflict=[],
        timeline=[],
        origin_note="",
        searches=0,
        provider="policy",
        elapsed=time.time() - started,
        demo=False,
        top_evidence=[],
        queried_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        tencent_sources=0,
        est={},
    )


def _common_knowledge_result(claim: Claim, why: str, started: float) -> "Investigation":
    """常识性事实直接给结论，不检索。

    这是唯一允许"不检索就给答案"的情形，且必须在界面上标明依据是通用知识，
    不能和"检索到的证据"混为一谈。
    """
    verdict = judge.Verdict(
        verdict=judge.VERDICT_TRUE,
        confidence=88,
        reason=f"这是公认的常识性事实（{why or '教科书级知识'}），不必检索。",
        notes=["依据类型：通用知识，非检索结果", "如涉及具体产品、政策或个人，请改用检索核查"],
    )
    return Investigation(
        claim=claim,
        evidence=[],
        trace=[{"round": 0, "tag": "common", "query": claim.core, "purpose": "常识判定：命中，跳过检索", "count": 0}],
        missing=[],
        verdict=verdict,
        matrix=[],
        conflict=[],
        timeline=[],
        origin_note="",
        searches=0,
        provider="knowledge",
        elapsed=time.time() - started,
        demo=False,
        top_evidence=[],
        queried_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        tencent_sources=0,
        est={},
    )


def investigate(
    text: str,
    *,
    use_llm: bool = True,
    check_link: bool = True,
    budget: int | None = None,
    max_rounds: int | None = None,
    skip_common: bool = False,
) -> Investigation:
    started = time.time()

    # 受理边界：涉及个人 / 时政的话题一次检索都不做，直接拒答
    kind, hit = policy.check(text)
    if kind:
        return _refused_result(claims_mod.decompose(text, use_llm=False), kind, hit, started)

    claim = claims_mod.decompose(text, use_llm=use_llm)

    # 常识性事实不必检索：检索只会引入"沾边但不相关"的噪音
    # 但企业信用类不能走常识——"某公司是失信企业"绝不能让模型凭印象判真
    if (
        use_llm
        and not skip_common
        and claim.claim_type not in ("ad_claim", "data", "realtime")
        and not claims_mod.is_enterprise_credit(text)
    ):
        is_common, why = claims_mod.check_common_knowledge(text)
        if is_common:
            return _common_knowledge_result(claim, why, started)
    search_budget = budget or config.MAX_SEARCH_PER_INVESTIGATION
    round_cap = max_rounds or config.MAX_ROUNDS

    evidence: list[Evidence] = []
    trace: list[dict] = []
    seen_urls: set[str] = set()
    searches = 0
    provider = "demo"
    demo_hit = False
    missing: list[str] = []

    for round_no in range(1, round_cap + 1):
        gaps = _gaps(claim, evidence)
        has_conflict = _conflict(evidence)
        specs = planner.plan_round(round_no, claim, gaps, has_conflict)[: max(0, search_budget - searches)]
        if not specs:
            break

        round_hits = 0
        for spec, (result, err) in zip(specs, _run_parallel(specs, round_no)):
            searches += 1
            if result is None:
                demo_hit = True
                provider = "demo"
                trace.append(
                    {
                        "round": round_no,
                        "query": spec.query,
                        "purpose": f"{spec.purpose}（检索不可用：{err}）",
                        "count": 0,
                        "tag": "demo",
                    }
                )
                continue

            provider = result.provider
            new_count = 0
            for hit in result.hits:
                if hit.url in seen_urls:
                    continue
                seen_urls.add(hit.url)
                ev = _to_evidence(hit, len(evidence) + 1, spec.query, round_no, claim)
                ev.authority = rules.authority_score(hit.url, hit.site, hit.authority_level)
                # 官方处罚/查处通报常被门户或自媒体转载，按域名只能给 2 分，
                # 但内容本身是监管机关的定性。对「已查处/已处罚」类证据提权威度，
                # 否则郑州那类 20 万罚单会被当成低权威噪音丢掉。
                _txt = f"{hit.title} {getattr(hit, 'content', '') or ''}"
                if ev.authority < 4 and any(
                    m in _txt for m in ("市场监管局", "市场监管总局", "行政处罚", "处罚决定", "违法广告案", "查处")
                ):
                    ev.authority = 4
                evidence.append(ev)
                new_count += 1
                if len(evidence) >= _MAX_EVIDENCE:
                    break
            round_hits += new_count
            trace.append(
                {
                    "round": round_no,
                    "query": spec.query,
                    "purpose": spec.purpose,
                    "count": new_count,
                    "tag": spec.tag,
                }
            )

        if demo_hit:
            break
        if round_hits == 0 and round_no >= 2:
            trace.append(
                {"round": round_no, "query": "", "purpose": "本轮无新增结果，停止继续检索", "count": 0, "tag": "stop"}
            )
            break
        # R1 就算看起来一致也不提前收手：R2 攻矛盾、R3 溯一手，三轮都要跑完。
        # （原来这里有一处「多条高权威一致就 break」，会让有争议的说法少跑两轮。）
        if round_no == 1 and _consensus(evidence):
            trace.append(
                {"round": 1, "query": "", "purpose": "首轮已有一致结论，仍继续做交叉验证与溯源", "count": 0, "tag": "note"}
            )

    if demo_hit:
        evidence = _demo_evidence(claim)
        provider = "demo"
        trace.append(
            {
                "round": 0,
                "query": "",
                "purpose": "检索服务不可用，已切换到演示数据（真实查询请配置元宝搜索密钥）",
                "count": len(evidence),
                "tag": "demo",
            }
        )
    else:
        if use_llm:
            _llm_refine_stance(evidence, claim)
        # 否定式表述不得当支持：繁体「不會增加患癌風險」曾被模型判成 support
        judge.apply_negation_guard(evidence, claim)
        judge.apply_causal_guard(evidence, claim)
        judge.apply_precision_filters(evidence, claim)
        # 疑问句标题（「真的会致癌吗?」）只是提问，不能当成立场
        judge.apply_question_title_guard(evidence, claim)
        # 辟谣平台/辟谣标题一律按反驳算：模型容易把设问句判成支持
        judge.apply_debunk_guard(evidence, claim)
        # 判「假」必须有主体对得上的证据：「蛋白质吃多了不好」不能否定「鸡蛋含蛋白质」
        judge.apply_subject_guard(evidence, claim)
        # 最后一道闸：把「沾边但不是这件事」的来源清成中性，否则会被算成反证
        judge.demote_irrelevant(evidence, claim)
        if check_link and evidence:
            status = links.check_many([e.url for e in evidence[:16]])
            for ev in evidence:
                if ev.url in status:
                    ok, why = status[ev.url]
                    ev.link_ok = ok
                    if not ok:
                        missing.append(f"{ev.site or '来源'} 链接已失效，已剔除")

    for g in _gaps(claim, evidence):
        if all(g not in m for m in missing):
            missing.append(f"缺少「{g[:26]}」的直接依据")

    # 企业信用类：把「该去哪查」说清楚，别让人以为搜不到就不存在
    if claims_mod.is_enterprise_credit(claim.raw):
        missing.append(
            "企业信用的权威来源是「国家企业信用信息公示系统」(gsxt.gov.cn) 与「信用中国」，"
            "本工具只能检索公开新闻与公告，查不到名单本身不代表没有记录"
        )

    verdict = judge.decide(claim, evidence, missing)
    if use_llm:
        # 规则判定覆盖不了「是国资委直接监管的中央企业」这类表述，
        # 让模型读一遍证据，但必须点名依据哪条、且依据要够权威
        verdict = judge.reconcile_verdict(claim, evidence, verdict, missing)
    usable = judge.usable_pool(claim, evidence)
    support = [e for e in usable if e.stance == "support"]
    refute = [e for e in usable if e.stance == "refute"]
    tl = judge.timeline(evidence)

    # 已经有明确结论时，不要再显示「缺少 XX 的直接依据」——那会跟结论打架
    if verdict.verdict in (judge.VERDICT_TRUE, judge.VERDICT_FALSE):
        final_missing: list[str] = []
    else:
        final_missing = verdict.missing or missing

    return Investigation(
        claim=claim,
        evidence=evidence,
        trace=trace,
        missing=final_missing,
        verdict=verdict,
        matrix=judge.subclaim_matrix(claim, evidence, usable),
        conflict=judge.conflict_table(support, refute) if verdict.conflict else [],
        timeline=tl,
        origin_note=judge.origin_note(tl),
        searches=searches,
        provider=provider,
        elapsed=time.time() - started,
        demo=demo_hit,
        top_evidence=judge.pick_top_evidence(evidence, 3),
        queried_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        tencent_sources=_count_tencent(evidence),
        est=_token_est(evidence),
        usable_count=len(usable),
    )


def _demo_evidence(claim: Claim) -> list[Evidence]:
    from . import demo_data

    return demo_data.build(claim)
