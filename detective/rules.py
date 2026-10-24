"""规则层：来源权威度打分 + L1 广告法风险快筛。

核心原则（写进作品说明也用得上）：
**词库只负责"定位哪句值得查"，绝不负责"判定它违法"。**
判定必须拿到检索证据，否则一律标"待核查"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# ---------------- 来源权威度 ----------------

_AUTH_RULES: list[tuple[str, int]] = [
    ("gov.cn", 5),
    ("who.int", 5),
    ("nhc.gov.cn", 5),
    ("samr.gov.cn", 5),
    ("cac.gov.cn", 5),
    ("court.gov.cn", 5),
    ("spp.gov.cn", 5),
    ("cdc", 4),
    ("edu.cn", 4),
    ("cnki.net", 4),
    ("wanfangdata", 4),
    ("kepuchina.cn", 4),
    ("piyao.org.cn", 5),
    ("12377.cn", 5),
    ("cca.org.cn", 4),
    ("cfda.gov.cn", 5),
    ("cast.org.cn", 4),
    ("xinhuanet.com", 4),
    ("people.com.cn", 4),
    ("news.cn", 4),
    ("cctv.com", 4),
    ("cnr.cn", 4),
    ("gmw.cn", 4),
    ("chinanews.com.cn", 3),
    ("ce.cn", 3),
    ("thepaper.cn", 3),
    ("caixin.com", 3),
    ("jiemian.com", 3),
    ("qq.com", 3),
    ("sina.com.cn", 2),
    ("163.com", 2),
    ("sohu.com", 2),
    ("baidu.com", 2),
    ("zhihu.com", 2),
    ("weixin.qq.com", 1),
    ("xiaohongshu.com", 1),
    ("douyin.com", 1),
    ("kuaishou.com", 1),
    ("taobao.com", 1),
    ("tmall.com", 1),
    ("jd.com", 1),
    ("1688.com", 1),
    ("bilibili.com", 2),
]

_INTEREST_HINTS = ("旗舰店", "官方商城", "自营", "招商", "代理", "加盟", "旗舰", "buy", "shop", "mall", "product")
_ONE_HAND_HINTS = ("通报", "公告", "决定书", "判决", "批复", "标准", "指南", "白皮书", "论文", "研究", "抽检")


def authority_score(url: str, site: str = "", authority_level: int | None = None) -> int:
    """0–5。有官方 authority_level 时优先用它。"""
    if isinstance(authority_level, int) and authority_level > 0:
        return min(5, max(0, authority_level))
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    best = 2  # 未知站点默认中性偏弱
    for pattern, score in _AUTH_RULES:
        if host == pattern or host.endswith("." + pattern) or pattern in host:
            best = max(best, score)
    if not host:
        best = 0
    return best


def is_one_hand(url: str, title: str = "") -> bool:
    """是否接近一手材料：官方通报、标准、论文、判决。"""
    host = (urlparse(url).hostname or "").lower()
    if authority_score(url) >= 4:
        return True
    text = f"{title}"
    return any(h in text for h in _ONE_HAND_HINTS)


def is_interest_related(url: str, site: str = "") -> bool:
    """是否利益相关方（商家自测、带货、招商页）。"""
    host = (urlparse(url).hostname or "").lower()
    text = f"{host} {site}"
    if any(h in text.lower() for h in _INTEREST_HINTS):
        return True
    if any(d in host for d in ("taobao.com", "tmall.com", "jd.com", "1688.com", "xiaohongshu.com", "douyin.com")):
        return True
    return False


# ---------------- L1 快筛：只定位，不判定 ----------------


@dataclass
class RiskPattern:
    key: str
    label: str
    pattern: str
    level: str          # high | mid
    why: str
    fix: str


RISK_PATTERNS: list[RiskPattern] = [
    RiskPattern("superlative", "绝对化用语",
                r"(最好|最佳|最优|最强|最大|最高级|最先进|最正宗|最专业|最便宜|最低价|最实惠|最美味|最健康|最安全"
                r"|第一品牌|全国第一|世界第一|独一无二|仅此一家|顶级|极品|国家级|世界级|唯一)",
                "high", "《广告法》第九条禁止使用最高级、最佳等绝对化用语", "改成可验证的相对表述，如「较早」「较优」「本地常见选择之一」"),
    RiskPattern("cure", "功效承诺", r"(包治|根治|治愈|药到病除|立竿见影|一次见效|永久(有效|去除)|彻底(解决|根除)|100%有效)",
                "high", "普通商品/服务不得宣称治疗功效，医疗类也不得作功效保证", "改成「有助于」「可改善」并注明适用范围，或直接删掉"),
    RiskPattern("purity", "绝对纯净宣称", r"(纯天然|零添加|无添加|纯手工|0添加|不含任何(化学|添加))",
                "high", "「纯天然/零添加」属绝对化宣称，需能逐项举证，否则构成虚假宣传", "改成「未额外添加防腐剂」这类可举证的具体表述"),
    RiskPattern("origin", "产地/身份宣称", r"(进口|原装进口|日本|德国|韩国|美国|法国|瑞士)[^\s，。]{0,4}(技术|原料|配方|品牌|进口)",
                "high", "产地与品牌背景属可查事实，不实即构成虚假广告（已有 20 万罚单先例）", "删掉无法出示报关单/授权的产地表述"),
    RiskPattern("rank", "排名/销量宣称", r"(销量第一|全网最低|全网最低价|行业领先|市占率第一|排名第一|冠军|no\.?1)",
                "high", "排名与价格宣称需有第三方数据支撑，否则属虚假宣传", "改成「本店热销」或标注数据来源与统计时间"),
    RiskPattern("authority", "权威背书", r"(国家(级)?认证|权威认证|官方指定|指定用品|专家推荐|央视上榜|获奖品牌)",
                "high", "未经授权的机构背书属虚假宣传，且涉嫌违反《广告法》第九条", "仅保留可出示证书的表述，并写明证书名称与编号"),
    RiskPattern("heritage", "祖传/秘方暗示", r"(祖传|秘方|宫廷|古方|百年传承|世代相传)",
                "mid", "用祖传/秘方暗示功效，属无法举证的主观宣称，易被认定为虚假宣传", "改成可核实的事实，如「本店经营八年，方子是老板自己调的」"),
    RiskPattern("fear", "恐吓式表述", r"(千万别|再不看就|赶紧|马上删|致癌|有毒|致命|毒过|吃了会死)",
                "mid", "恐吓式营销易被认定为误导，也是谣言传播的典型句式", "换成陈述句并给出具体依据"),
    RiskPattern("vague", "模糊数据", r"(\d+%|百分之[一二三四五六七八九十]+)[^\s，。]{0,6}(有效|满意|提升|改善)",
                "mid", "百分比效果宣称需说明样本量、统计口径与时间", "补上「基于 XX 位用户 X 年 X 月反馈」"),
]

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_RISK_COMPILED = [(p, re.compile(p.pattern)) for p in RISK_PATTERNS]


@dataclass
class RiskFinding:
    sentence: str
    sentence_index: int
    pattern_key: str
    label: str
    level: str
    matched: str
    why: str
    fix: str

    def as_dict(self) -> dict:
        return {
            "sentence": self.sentence,
            "sentence_index": self.sentence_index,
            "pattern_key": self.pattern_key,
            "label": self.label,
            "level": self.level,
            "matched": self.matched,
            "why": self.why,
            "fix": self.fix,
        }


def split_sentences(text: str) -> list[str]:
    """切成"可单独判定"的最小单位。

    先按句末标点切；句子太长再按逗号切到短语级——
    老板要的是"哪一句有问题"，短语级定位才用得上。
    """
    out: list[str] = []
    for part in _SENTENCE_SPLIT.split(text or ""):
        part = part.strip()
        if not part:
            continue
        if len(part) <= 12:
            if len(part) >= 4:
                out.append(part)
            continue
        for frag in re.split(r"[，,]", part):
            frag = frag.strip()
            if not frag:
                continue
            if len(frag) < 4 and out:
                out[-1] = out[-1] + "，" + frag
                continue
            if len(frag) >= 4:
                out.append(frag)
    return out


def scan(text: str) -> list[RiskFinding]:
    """L1 快筛：找出值得深查的句子。不产生任何结论。"""
    findings: list[RiskFinding] = []
    for idx, sentence in enumerate(split_sentences(text)):
        for pat, compiled in _RISK_COMPILED:
            m = compiled.search(sentence)
            if not m:
                continue
            findings.append(
                RiskFinding(
                    sentence=sentence,
                    sentence_index=idx,
                    pattern_key=pat.key,
                    label=pat.label,
                    level=pat.level,
                    matched=m.group(0),
                    why=pat.why,
                    fix=pat.fix,
                )
            )
    # 一句只留最高等级的一条，避免一屏全是同一个词
    best: dict[str, RiskFinding] = {}
    order = {"high": 0, "mid": 1}
    for f in findings:
        cur = best.get(f.sentence)
        if cur is None or order[f.level] < order[cur.level]:
            best[f.sentence] = f
    ranked = sorted(best.values(), key=lambda f: (order[f.level], f.sentence_index))
    return ranked


def rank_for_checkup(findings: list[RiskFinding], top_n: int = 5) -> list[RiskFinding]:
    """挑最值得深查的 top_n：先看等级，再看是否命中多条。"""
    return findings[:top_n]
