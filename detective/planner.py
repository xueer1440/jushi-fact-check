"""多轮查询规划。

每轮目的不同，不做无脑重复搜：
R1 广搜摸盘 → R2 攻缺口与矛盾 → R3 溯源找一手材料。
总量受 config.MAX_SEARCH_PER_INVESTIGATION 约束。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import re

from .claims import Claim, is_enterprise_credit


@dataclass
class QuerySpec:
    query: str
    purpose: str
    round_no: int
    mode: int = 2
    cnt: int = 20
    site: str | None = None
    industry: str | None = None
    freshness: str | None = None
    tag: str = "general"
    meta: dict = field(default_factory=dict)


def _credit_subject(text: str) -> str:
    """从「某公司是失信企业」里抽出主体名，别把判断词也塞进查询。"""
    subj = re.split(r"是一家|是一个|属于|被列为|被列入|为失信|是失信|已经|已", text, maxsplit=1)[0]
    return (subj or text).strip(" ，,。的")


_OWNERSHIP_HINTS = (
    "是国企", "是央企", "是国有企业", "是中央企业", "是上市公司", "是事业单位",
    "是外资企业", "是民营企业", "是私营企业", "是公立",
)


def is_ownership_claim(text: str) -> bool:
    """机构性质类：「X 是国企/央企/上市公司」。要的是登记信息，不是传言源头。"""
    return any(h in (text or "") for h in _OWNERSHIP_HINTS)


def _ownership_subject(text: str) -> str:
    subj = re.split(
        r"是国企|是央企|是国有企业|是中央企业|是上市公司|是事业单位|"
        r"是外资企业|是民营企业|是私营企业|是公立|是一家|是一个|属于",
        text,
        maxsplit=1,
    )[0]
    return (subj or text).strip(" ，,。的")


def round1(claim: Claim) -> list[QuerySpec]:
    core = claim.core
    if is_enterprise_credit(claim.raw):
        subj = _credit_subject(claim.raw)
        return [
            QuerySpec(query=f"{subj} 失信被执行人", purpose="信用：查失信被执行人名单", round_no=1, cnt=10, tag="credit"),
            QuerySpec(query=f"{subj} 行政处罚", purpose="信用：查行政处罚记录", round_no=1, cnt=10, tag="credit"),
            QuerySpec(query=f"{subj} 经营异常 信用中国", purpose="信用：查信用中国公示", round_no=1, cnt=10, tag="credit"),
        ]
    if claim.claim_type == "realtime":
        return [
            QuerySpec(query=core, purpose="实时信息：查最新播报", round_no=1, cnt=20, freshness="d1", tag="general"),
            QuerySpec(query=f"{core} 最新", purpose="实时信息：查权威发布", round_no=1, cnt=10, freshness="d3", tag="authority"),
        ]
    out = [
        QuerySpec(query=core, purpose="广搜：先看全网怎么说", round_no=1, cnt=20, tag="general"),
        QuerySpec(
            query=f"{core} 辟谣 真相",
            purpose="反向：找有没有人辟过这条",
            round_no=1,
            cnt=10,
            tag="refute",
        ),
        QuerySpec(
            query=core,
            purpose="权威：只看政府与官方口径",
            round_no=1,
            cnt=10,
            industry="gov",
            tag="authority",
        ),
        QuerySpec(
            query=core,
            purpose="生态：调取腾讯内容生态信源（腾讯新闻/腾讯医典等）",
            round_no=1,
            cnt=10,
            site="news.qq.com",
            tag="tencent",
        ),
    ]
    if claim.claim_type in ("ad_claim", "data"):
        out[0].query = f"{core} 市场监管 处罚 虚假宣传"
        out[0].purpose = "广搜：先看同类宣传有没有被罚的先例"
    return out


def round2(claim: Claim, gaps: list[str], conflicts: bool) -> list[QuerySpec]:
    if claim.claim_type == "realtime" or is_enterprise_credit(claim.raw):
        return []
    core = claim.core
    out: list[QuerySpec] = []
    if conflicts:
        out.append(
            QuerySpec(
                query=f"{core} 研究 数据 结论",
                purpose="攻矛盾：找原始研究与数据口径",
                round_no=2,
                cnt=20,
                tag="data",
            )
        )
        out.append(
            QuerySpec(
                query=f"{core} 不实 谣言 官方回应",
                purpose="攻矛盾：找官方对这条的定性",
                round_no=2,
                cnt=10,
                industry="gov",
                tag="authority",
            )
        )
    else:
        # 只说了不算，找原始研究与官方处置
        out.append(
            QuerySpec(
                query=f"{core} 研究 论文 数据",
                purpose="深挖：找一手研究或统计数据",
                round_no=2,
                cnt=20,
                tag="data",
            )
        )
        out.append(
            QuerySpec(
                query=f"{core} 官方 通报 标准",
                purpose="深挖：找主管部门的通报或标准",
                round_no=2,
                cnt=10,
                industry="gov",
                tag="authority",
            )
        )
    # 针对最薄弱的子主张补一路
    if gaps:
        out.append(
            QuerySpec(
                query=gaps[0],
                purpose=f"补缺：单独核查子主张「{gaps[0][:18]}」",
                round_no=2,
                cnt=10,
                tag="subclaim",
            )
        )
    return out


def round3(claim: Claim, conflicts: bool) -> list[QuerySpec]:
    if claim.claim_type == "realtime" or is_enterprise_credit(claim.raw):
        return []
    # 机构性质类没有「传言源头」可溯——答案在主管机关名录和工商登记里
    if is_ownership_claim(claim.raw):
        subj = _ownership_subject(claim.raw)
        return [
            QuerySpec(
                query=f"{subj} 央企名录 国资委",
                purpose="溯源一手：查主管机关名录与监管关系",
                round_no=3,
                cnt=10,
                industry="gov",
                tag="primary",
            )
        ]
    core = claim.core
    out = [
        QuerySpec(
            query=f"{core} 最早 出处 传言 起源",
            purpose="溯源：找这条说法最早从哪来",
            round_no=3,
            cnt=20,
            freshness="y5",
            tag="origin",
        )
    ]
    if conflicts:
        out.append(
            QuerySpec(
                query=core,
                purpose="找一手材料：政府与学术源原话",
                round_no=3,
                cnt=10,
                industry="gov",
                tag="primary",
            )
        )
    return out


def plan_round(round_no: int, claim: Claim, gaps: list[str], conflicts: bool) -> list[QuerySpec]:
    if round_no == 1:
        return round1(claim)
    if round_no == 2:
        return round2(claim, gaps, conflicts)
    if round_no == 3:
        return round3(claim, conflicts)
    return []
