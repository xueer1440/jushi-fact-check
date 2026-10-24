"""判定层：四维判决 + 冲突检测 + 拒答闸门。

**结论由算法算出，不由模型自由发挥。**
LLM 只能给单条证据打立场标签（support/refute/neutral），标签错了最多影响权重，
不会直接生成一句"结论"。同输入同输出，可复现、可回归测试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import llm, rules
from .claims import Claim, is_enterprise_credit

VERDICT_TRUE = "true"
VERDICT_FALSE = "false"
VERDICT_MISLEADING = "misleading"
VERDICT_UNCLEAR = "unclear"
VERDICT_NO_SOURCE = "no_source"
VERDICT_REFUSED = "refused"

VERDICT_LABEL = {
    VERDICT_TRUE: "真",
    VERDICT_FALSE: "假",
    VERDICT_MISLEADING: "误导",
    VERDICT_UNCLEAR: "说不准",
    VERDICT_NO_SOURCE: "没查到可靠来源",
    VERDICT_REFUSED: "不在受理范围",
}

VERDICT_TONE = {
    VERDICT_TRUE: "true",
    VERDICT_FALSE: "false",
    VERDICT_MISLEADING: "misleading",
    VERDICT_UNCLEAR: "unclear",
    VERDICT_NO_SOURCE: "no_source",
    VERDICT_REFUSED: "refused",
}

# 误导档识别：权威来源在「纠正归因」而不是「定性造假」
_MISLEADING_FIX = (
    "并非", "而不是", "实为", "误读", "误传", "误传为", "被误", "夸大", "以偏概全",
    "移花接木", "张冠李戴", "原研究", "针对的是", "指的是", "移出", "实际情况",
)
# 这些词说明来源已直接定性造假，归「假」而不是「误导」
_MISLEADING_FLAT = (
    "谣言", "不属实", "虚假", "纯属", "伪科学", "无科学依据", "没有依据", "错误", "伪造",
)


def _misleading_signal(top: "Evidence") -> bool:
    text = f"{top.title} {top.snippet}"
    if any(w in text for w in _MISLEADING_FLAT):
        return False
    return any(w in text for w in _MISLEADING_FIX)


_REFUTE_WORDS = (
    "辟谣", "谣言", "不实", "假的", "错误", "误导", "不存在", "无科学依据", "没有依据",
    "已否认", "处罚", "罚款", "查处", "虚假宣传", "违法", "澄清", "系谣言", "纯属",
    "不成立", "无稽之谈", "驳回", "停止发布", "责令",
)
_SUPPORT_WORDS = (
    "证实", "确实", "研究表明", "数据显示", "检测出", "超标", "确认", "有依据",
    "属实", "检出", "通报称", "已查明", "确有其事",
)


@dataclass
class Evidence:
    id: str
    title: str
    url: str
    site: str
    date: str
    snippet: str
    score: float
    authority: int
    one_hand: bool
    interest: bool
    stance: str = "neutral"        # support | refute | neutral
    query: str = ""
    round_no: int = 0
    link_ok: bool = True
    on_topic: bool = True          # 是否真的在讲这个断言（主体一致 + 主题相关）

    @property
    def weight(self) -> float:
        return self.authority * self._time_factor() * (0.5 if self.interest else 1.0)

    def _time_factor(self) -> float:
        dt = parse_date(self.date)
        if dt is None:
            return 0.9
        years = (datetime.now() - dt).days / 365.0
        if years <= 2:
            return 1.0
        if years <= 5:
            return 0.85
        return 0.7

    @property
    def high_quality(self) -> bool:
        return self.authority >= 3 and not self.interest and self.link_ok

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "site": self.site,
            "date": self.date,
            "snippet": self.snippet,
            "score": round(self.score, 3),
            "authority": self.authority,
            "one_hand": self.one_hand,
            "interest": self.interest,
            "stance": self.stance,
            "query": self.query,
            "round": self.round_no,
            "high_quality": self.high_quality,   # 权威 + 无利益 + 链接可达
            "link_ok": self.link_ok,
            "on_topic": self.on_topic,           # 是否真的在讲这件事（主体一致）
            "weight": round(self.weight, 2),
        }


def parse_date(text: str) -> datetime | None:
    if not text:
        return None
    cleaned = text.strip().replace("年", "-").replace("月", "-").replace("日", "")
    cleaned = re.sub(r"[/.T]", "-", cleaned)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", cleaned)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{1,2})", cleaned)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    return None


def classify_stance(text: str, claim: Claim) -> str:
    """规则版立场识别。LLM 可用时由 investigator 覆盖。"""
    body = normalize_text(text)
    refute = sum(1 for w in _REFUTE_WORDS if w in body)
    support = sum(1 for w in _SUPPORT_WORDS if w in body)
    if refute > support:
        return "refute"
    if support > refute:
        return "support"
    return "neutral"


# ---- 繁体转简体 + 否定式守卫 ----
# 场景：检索会返回港台媒体，标题是繁体（「喝咖啡不會增加患癌風險」）。
# 规则词表和模型都容易把「不會…風險」里的「致癌/風險」当成支持，必须单独兜住。
_TRAD2SIMP = str.maketrans({
    "會": "会", "風": "风", "險": "险", "證": "证", "據": "据", "沒": "没", "無": "无",
    "這": "这", "個": "个", "專": "专", "們": "们", "對": "对", "於": "于", "與": "与",
    "為": "为", "來": "来", "後": "后", "說": "说", "麼": "么", "樣": "样", "還": "还",
    "開": "开", "關": "关", "現": "现", "實": "实", "際": "际", "體": "体", "點": "点",
    "數": "数", "營": "营", "養": "养", "醫": "医", "藥": "药", "學": "学", "種": "种",
    "類": "类", "發": "发", "報": "报", "導": "导", "認": "认", "確": "确", "檢": "检",
    "測": "测", "標": "标", "準": "准", "據": "据", "題": "题", "訪": "访", "談": "谈",
})


def normalize_text(text: str) -> str:
    """把常见繁体字折成简体，避免立场词表漏判。"""
    return (text or "").translate(_TRAD2SIMP)


# 「不会增加…风险」「不致癌」「没有证据表明」「并非…而是」
_NEGATION_REFUTE = re.compile(
    r"(不|没|沒|无|無)(会|會)?(增加|提高|导致|導致|引起|存在|含|是|致)"
    r"|不致癌|不会致癌|不會致癌"
    r"|(没有|沒有|无|無)(证据|證據|依据|依據|科学依据|科學依據)"
    r"|(并非|並非|不是)[^。，,；;]{0,12}(而是|导致|導致|致癌|引起)"
)


def apply_negation_guard(ev: list[Evidence], claim: Claim) -> int:
    """否定式表述一律不得算「支持」；确认是明确否定的，直接改判 refute。

    只处理「待核说法本身是肯定式」的情况——若说法本身就是否定句
    （「咖啡不致癌」），否定式证据反而可能是支持，交给模型与规则原判。
    """
    raw = normalize_text(getattr(claim, "raw", "") or "")
    if any(w in raw for w in ("不", "没", "沒", "无", "無", "未")):
        return 0
    fixed = 0
    for e in ev:
        body = normalize_text(f"{e.title} {e.snippet}")
        if not _NEGATION_REFUTE.search(body):
            continue
        if e.stance == "support":
            e.stance = "refute"
            fixed += 1
    return fixed


@dataclass
class Verdict:
    verdict: str
    confidence: int
    reason: str
    risk_level: str = ""          # high | mid | low（宣传类才用）
    missing: list[str] = field(default_factory=list)
    conflict: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "label": VERDICT_LABEL[self.verdict],
            "tone": VERDICT_TONE[self.verdict],
            "confidence": self.confidence,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "missing": self.missing,
            "conflict": self.conflict,
            "notes": self.notes,
        }


def _bigrams(text: str) -> list[str]:
    """中文按二字切片，比整段匹配稳。"""
    out = []
    for seg in re.findall(r"[\u4e00-\u9fa5]+", text or ""):
        out.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    out.extend(re.findall(r"[A-Za-z]{2,}", text or ""))
    return list(dict.fromkeys(out))


def _strong_relevant(e: Evidence, claim: Claim) -> bool:
    """比 _relevant 更严：核心断言的二字片至少命中 3 个。

    「鸡蛋的 8 个误区」这类文章只沾了实体，没谈断言本身，不该算作对断言的否定。
    数字类断言再加一道：标题必须点到那个数字，否则多半在讲同一主体的另一件事
    （实测事故：「高血压的诊断标准是 140/90」被一篇《别让这些高血压误区…》
    判成"假 90"，而那篇文章根本没提 140/90）。
    """
    title = e.title or ""
    core = claim.core or ""
    nums = [n for n in re.findall(r"\d+(?:\.\d+)?", core) if len(n) >= 2]
    if nums and e.stance == "refute" and not any(n in title for n in nums):
        return False
    toks = _bigrams(core)
    if not toks:
        return True
    # A) 标题里出现了断言的主语（前 3 个字），视为在谈这件事
    if len(core) >= 4 and core[:3] in title:
        return True
    # B) 二字片覆盖
    # 宣传/数据类放宽到「标题 + 摘要」，并把门槛降到 2 片：
    # 处罚通报的标题常是「XX公司违法广告案」，广告原文只出现在摘要里
    # （实测：「日本引进」「一盒让三年疼痛消失」把 3 条处罚报道全判成不相关）。
    if claim.claim_type in ("ad_claim", "data"):
        text = f"{title} {e.snippet}"
        hit = sum(1 for t in toks if t in text)
        need = 2
        if any(m in text for m in _PUNISH_MARKERS):
            need = 1          # 有处罚/查处字样时，命中一个主体词即可
        return hit >= need
    hit = sum(1 for t in toks if t in title)
    return hit >= max(2, int(len(toks) * 0.25))


def _relevant(e: Evidence, claim: Claim) -> bool:
    """这条证据是否真的在讲这件事（防止"含关键词但无关"的页面混进来）。"""
    toks = _bigrams(claim.core)
    if not toks:
        return True
    text = f"{e.title} {e.snippet}"
    hit = sum(1 for t in toks if t in text)
    need = 2 if len(toks) >= 4 else 1
    return hit >= need


_PUNISH_MARKERS = ("罚款", "处罚", "立案", "查处", "责令", "没收", "违法")


# ---------- 主体一致性 ----------
# 「大连亚信科技」不能给「亚信科技（中国）」当证据：同名不同法人是检索最常见的坑。

_ORG_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z（）()·]{2,24}?"
    r"(?:股份有限公司|有限责任公司|有限公司|分公司|集团公司|集团|公司|银行|大学|医院|研究院))"
)
_ORG_SUFFIX = ("股份有限公司", "有限责任公司", "有限公司", "分公司", "集团公司", "集团", "公司")
_REGION_WORDS = (
    "北京", "上海", "广州", "深圳", "成都", "大连", "怀化", "杭州", "南京", "武汉",
    "西安", "天津", "重庆", "苏州", "无锡", "青岛", "济南", "郑州", "长沙", "合肥",
    "福州", "厦门", "南昌", "昆明", "贵阳", "南宁", "海口", "太原", "石家庄", "哈尔滨",
    "长春", "沈阳", "兰州", "西宁", "银川", "乌鲁木齐", "呼和浩特", "拉萨", "雄安",
    "中国", "香港", "澳门", "台湾", "国际", "海外", "亚太", "华东", "华南", "华北",
    "华中", "西北", "西南", "东北",
)


def _org_names(text: str) -> list[str]:
    return [m.group(1) for m in _ORG_RE.finditer(text or "")]


def _org_core(name: str) -> str:
    """去掉括号注记与公司后缀，留下识别主体的核心名。"""
    core = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    for suf in _ORG_SUFFIX:
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    return core.strip()


def _org_region(name: str) -> str:
    m = re.search(r"[（(]([^）)]{2,6})[）)]", name or "")
    return m.group(1).strip() if m else ""


def subject_conflicts(title: str, claim: Claim) -> bool:
    """标题里点名的公司，是不是另一家同名不同法人的公司。

    只处理能明确看出来的两种情况，看不出来一律当作同一主体（少拦不错拦）：
      A) 括号注记不同：亚信科技（中国） vs 亚信科技（成都）
      B) 核心名前面多了地域词：大连亚信科技 vs 亚信科技
    """
    others = _org_names(title)
    wants = _org_names(claim.raw)
    if not others or not wants:
        return False
    want_core, want_region = _org_core(wants[0]), _org_region(wants[0])
    if not want_core:
        return False
    for name in others:
        core, region = _org_core(name), _org_region(name)
        if core == want_core:
            if want_region and region and want_region != region:
                return True
            continue
        if core.endswith(want_core) and core[: -len(want_core)] in _REGION_WORDS:
            return True
        if want_core.endswith(core) and want_core[: -len(core)] in _REGION_WORDS:
            return True
    return False



# 同义主体：断言写「隔夜菜」，证据标题常写「剩菜/剩饭」
_SUBJECT_ALIASES = {
    "隔夜菜": ("剩菜", "剩饭", "隔夜饭", "过夜菜"),
    "剩菜": ("隔夜菜", "剩饭"),
    "微波炉": ("微波",),
    "咖啡": ("咖啡因",),
}

# 从说法里抠出「主体名词」：先剥掉时间副词和动词，再取到谓语前
# 「每天喝咖啡会致癌」→ 咖啡；「鸡蛋含有蛋白质」→ 鸡蛋
_SUBJECT_RE = re.compile(
    r"^(?:每天|天天|经常|长期|最近|听说|网上说|有人说|有研究(?:说|称|表明)?|都说|据传|传言|据说)?"
    r"[，,]?\s*(?:多|常)?(?:喝|吃|用|服用|使用|买|做)?"
    r"([\u4e00-\u9fa5]{2,4}?)(?:是|为|属于|会|能|含有|含|导致|引起|致癌|是否|真的|不|没|可以|的话|$)"
)


def _subject_hit(title: str, claim: Claim) -> bool:
    """标题里有没有点到这条说法的主体。

    防的是「恰好提到同一个词就当成反证」：搜「鸡蛋含有蛋白质」会返回
    《蛋白质吃得越多越好……是真是假?》，那讲的是另一件事，不能拿来否定这句话。

    另一边也不能太严：原来只拿 core 前 2 字（「每天喝咖啡会致癌」→「喝咖」），
    结果《喝咖啡会致癌? 真相是这样的》这种明确辟谣的标题一条都过不了，
    反驳证据被全部清成中性。现在先抽主体名词（咖啡 / 鸡蛋 / 微波炉）。
    """
    t = title or ""
    core = (claim.core or "").strip()
    m = _SUBJECT_RE.match(core)
    subject = m.group(1) if m else ""
    if subject and subject in t:
        return True
    head = core[:2]
    if head and head in t:
        return True
    for key, aliases in _SUBJECT_ALIASES.items():
        if key in core and (key in t or any(a in t for a in aliases)):
            return True
    return False


def apply_subject_guard(evidence: list[Evidence], claim: Claim) -> int:
    """反驳类证据必须点到同一主体；没点到的降为中性。

    判「假」的代价最高，所以这一关只拦 refute，不拦 support
    （支持类证据的标题常是综述式，误伤成本更大）。
    """
    fixed = 0
    for e in evidence:
        if e.stance == "refute" and not _subject_hit(e.title, claim):
            e.stance = "neutral"
            fixed += 1
    return fixed


def _decide_ad(claim: Claim, live: list[Evidence], missing: list[str]) -> Verdict:
    """宣传类断言：能举证才算有依据，拿不出依据就按高风险处理。"""
    hq = [e for e in live if e.high_quality and e.on_topic]
    # 处罚决定书在广告语境下是「这句话被认定违法」，归为反驳，而不是确认
    punished = [e for e in hq if any(m in f"{e.title} {e.snippet}" for m in _PUNISH_MARKERS)]
    # 区分「直接定性」与「处罚先例」：处罚同类表述只能算间接近似，不能给到直接定性的置信度
    direct = [e for e in hq if evidence_kind(e, claim) == "denial" and e not in punished]
    denials = list(direct)
    for e in punished:
        if e not in denials:
            denials.append(e)
    confirms = [
        e for e in hq
        if evidence_kind(e, claim) == "confirm" and e not in punished
    ]
    note = _ad_burden_note(claim)

    if len(denials) >= 2:
        top = max(denials, key=lambda e: e.weight)
        if direct:
            conf, why = 70, f"{len(denials)} 条来源显示同类表述已被认定不实或被查处"
        else:
            # 只有处罚先例、没有对该句的直接定性 → 置信度压到 60（间接依据）
            conf, why = 60, (
                f"{len(denials)} 条来源显示同类表述曾被查处（处罚先例，属间接依据；"
                f"未检索到针对这句话的直接定性）"
            )
        return Verdict(
            verdict=VERDICT_FALSE,
            confidence=conf,
            reason=(
                f"{why}"
                f"（如 {top.site or '来源'}）。这句话建议改写。"
            ),
            risk_level="high",
            notes=[note],
        )
    if len(confirms) >= 2:
        top = max(confirms, key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_TRUE,
            confidence=65,
            reason=f"{len(confirms)} 条官方/一手材料可以支撑这句话（如 {top.site or '来源'}）。",
            risk_level="low",
            notes=[note],
        )
    return Verdict(
        verdict=VERDICT_NO_SOURCE,
        confidence=20,
        reason=(
            f"检索到 {len(live)} 条结果，但没有一条能直接证明这句话成立"
            "（检测报告、备案、统计原始数据、第三方测评都算）。"
        ),
        risk_level="high",
        missing=missing or ["自家检测报告、备案文件、第三方测评或统计数据"],
        notes=[
            "拿不到依据就不建议这么写：广告主对内容真实性负举证责任。",
            note,
        ],
    )


_OFFICIAL_HOSTS = (
    "gov.cn", "gsxt.gov.cn", "creditchina.gov.cn", "court.gov.cn",
    "spp.gov.cn", "csrc.gov.cn", "npc.gov.cn", "samr.gov.cn",
)
_OFFICIAL_SITE_WORDS = (
    "人民政府", "人民法院", "人民检察院", "市场监督管理", "发展和改革",
    "信用中国", "纪委", "监察", "证监会", "统计局", "海关", "税务",
)


def is_official_source(e: Evidence) -> bool:
    """政府或司法来源——信用类断言只看这一类。"""
    url = (e.url or "").lower()
    site = e.site or ""
    if any(h in url for h in _OFFICIAL_HOSTS):
        return True
    return any(w in site for w in _OFFICIAL_SITE_WORDS)


def demote_irrelevant(evidence: list[Evidence], claim: Claim) -> dict:
    """把「沾边但不是在说这件事」的来源降为中性——判定前最后一道闸。

    三种误判都出在这里，不拦就会变成「被反驳」：
      1) 标题根本没在讲这个断言：「光明区安全生产违法行为典型案例」被当成失信反证
      2) 点名的不是同一家法人：「大连亚信科技」给「亚信科技（中国）」作证
      3) 企业信用类：媒体与商业数据库对「是否失信」没有证明力，只认政府与司法公示
    """
    stats = {"off_topic": 0, "other_subject": 0, "not_official": 0}
    credit = is_enterprise_credit(claim.raw)
    for e in evidence:
        if credit and not is_official_source(e):
            e.on_topic = False
            e.stance = "neutral"
            stats["not_official"] += 1
            continue
        if subject_conflicts(e.title, claim):
            e.on_topic = False
            e.stance = "neutral"
            stats["other_subject"] += 1
            continue
        if not _strong_relevant(e, claim):
            e.on_topic = False
            e.stance = "neutral"
            stats["off_topic"] += 1
    return stats



def usable_pool(claim: Claim, evidence: list[Evidence]) -> list[Evidence]:
    """允许参与「立场」计数的来源——与 decide 同一口径。

    主结论与子主张矩阵必须看同一批证据，否则会出现
    「上面说查不到可靠来源、下面却说被反驳」的自相矛盾。
    """
    live = [e for e in evidence if e.link_ok and e.on_topic]
    if is_enterprise_credit(claim.raw):
        return [e for e in live if is_official_source(e) and _strong_relevant(e, claim)]
    return [e for e in live if e.high_quality]


def _verdict_by_authority(
    claim: Claim,
    live: list[Evidence],
    missing: list[str],
) -> Verdict | None:
    """权威优先裁决：先看最高权威那一层说了什么，不按条数投票。

    口径：最有权威的机构已经下了定论，就不用再看别的来源。
    只有最高权威层级里两边同时有声音，才算真分歧，才退回「说不准」。
    权威度不到 4（政府/法院/主管机关）的单一来源，不足以定论，交给后续分支。
    """
    pool = [e for e in live if e.on_topic and _strong_relevant(e, claim) and e.stance != "neutral"]
    if not pool:
        return None
    sup = [e for e in pool if e.stance == "support"]
    ref = [e for e in pool if e.stance == "refute"]
    top_s = max((e.authority for e in sup), default=0)
    top_r = max((e.authority for e in ref), default=0)
    if top_s == 0 and top_r == 0:
        return None

    if top_s > 0 and top_r > 0 and top_s == top_r:
        s_top = max([e for e in sup if e.authority == top_s], key=lambda e: e.weight)
        r_top = max([e for e in ref if e.authority == top_r], key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=50,
            reason=(
                f"权威度同为 {top_s}/5 的两方结论相反："
                f"{s_top.site or '来源'}《{s_top.title[:24]}》认为成立，"
                f"{r_top.site or '来源'}《{r_top.title[:24]}》认为不成立。"
                "这是来源本身的分歧，不是我们查不到。"
            ),
            conflict=True,
            missing=missing or ["需要更高级别的权威定性，或补充适用条件"],
            notes=["最高权威层出现对立结论，按分歧处理"],
        )

    if top_s > top_r:
        if top_s < 4:
            return None            # 低权威的单一声音不足以定论
        top = max([e for e in sup if e.authority == top_s], key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_TRUE,
            confidence=min(90, 55 + top_s * 7),
            reason=(
                f"最高权威来源（{top.site or '来源'}，权威度 {top_s}/5）明确支持该说法："
                f"《{top.title[:32]}》。"
            ),
            notes=["按权威优先裁决：只看最高权威层级，不按来源条数投票"],
        )

    if top_r < 4:
        return None
    top = max([e for e in ref if e.authority == top_r], key=lambda e: e.weight)
    # 误导档：权威来源是在「纠正归因」（并非/实为/夸大/原研究针对的是…），
    # 而不是直接定性「谣言/不属实」——说法有影子但被安错了对象或条件，
    # 与纯粹造假分开。识别不出来时保守退回「假」。
    if _misleading_signal(top):
        return Verdict(
            verdict=VERDICT_MISLEADING,
            confidence=min(85, 50 + top_r * 7),
            reason=(
                f"最高权威来源（{top.site or '来源'}，权威度 {top_r}/5）指出该说法存在失真："
                f"《{top.title[:32]}》。说法有影子但被安错了对象或条件，属于误导性表述，"
                "与凭空捏造的假消息分开。"
            ),
            notes=["误导档：说法有出处但被歪曲，改写时把对象和条件补准即可"],
        )
    return Verdict(
        verdict=VERDICT_FALSE,
        confidence=min(90, 55 + top_r * 7),
        reason=(
            f"最高权威来源（{top.site or '来源'}，权威度 {top_r}/5）明确否定该说法："
            f"《{top.title[:32]}》。"
        ),
        notes=["按权威优先裁决：只看最高权威层级，不按来源条数投票"],
    )


_CREDIT_HIT = ("失信被执行人", "失信名单", "被列入失信", "限制高消费", "被执行人名单")
_CREDIT_CLEAR = ("未列入", "未被列入", "无失信记录", "不在失信", "不是失信", "查无", "未查询到", "无相关记录")


def _credit_signal(e: Evidence) -> str:
    """信用类只看正文里的直接表述，不看立场词。

    「高管被开除党籍」「公司被罚款」和「公司是失信被执行人」是三件不同的事，
    前者既不能证明后者，也不能否定后者——所以这里只认名单类的直接表述。
    """
    text = f"{e.title} {e.snippet}"
    if any(w in text for w in _CREDIT_CLEAR):
        return "clear"
    if any(w in text for w in _CREDIT_HIT):
        return "hit"
    return ""


def _decide_credit(claim: Claim, live: list[Evidence], missing: list[str]) -> Verdict:
    """企业/个人信用类：只认政府与司法公示里「名单」本身。

    这条口径单独写，不走通用裁决——通用裁决看的是「支持/反驳」的立场，
    而一条「高管被开除党籍」的纪委通报在通用逻辑里会被算成「反驳失信」，
    得出「这家公司不是失信企业」的荒谬结论。
    """
    official = [
        e for e in live
        if e.on_topic and is_official_source(e) and _strong_relevant(e, claim)
    ]
    # 这三个入口固定给出：让用户知道该去哪查，而不是以为「查不到=没有」
    entries = [
        "国家企业信用信息公示系统（gsxt.gov.cn）",
        "信用中国（creditchina.gov.cn）",
        "中国执行信息公开网（zxgk.court.gov.cn）",
    ]
    if not official:
        return Verdict(
            verdict=VERDICT_NO_SOURCE,
            confidence=10,
            reason=(
                "企业信用记录以政府公示为准。本次没有检索到直接对应的政府或法院公示页面，"
                "新闻与商业数据库不作为判断依据，所以不下结论。"
            ),
            missing=entries,
            notes=["信用类断言只采信政府与司法来源，媒体与第三方数据库一律不采信"],
        )

    hits = [e for e in official if _credit_signal(e) == "hit"]
    clears = [e for e in official if _credit_signal(e) == "clear"]

    if hits and clears:
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=50,
            reason="政府公示里既有名单记录也有「未列入」的表述，需要按具体名单和时间再核。",
            conflict=True,
            missing=entries,
            notes=["信用类：名单本身互相矛盾，不下结论"],
        )
    if hits:
        top = max(hits, key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_TRUE,
            confidence=80,
            reason=(
                f"{top.site or '政府公示'} 出现了该主体与「失信被执行人」相关的记录："
                f"《{top.title[:32]}》。"
            ),
            notes=["依据类型：政府/司法公示", "信用类只认名单本身，不看媒体报道"],
        )
    if clears:
        top = max(clears, key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_FALSE,
            confidence=75,
            reason=f"{top.site or '政府公示'} 明确写明该主体未列入失信名单：《{top.title[:32]}》。",
            notes=["依据类型：政府/司法公示"],
        )
    return Verdict(
        verdict=VERDICT_NO_SOURCE,
        confidence=15,
        reason=(
            f"检索到 {len(official)} 条政府或司法页面，但没有一条是失信名单本身"
            "（多是处罚通报、人事处理或政策解读），据此既不能认定也不能否定，所以不下结论。"
        ),
        missing=entries,
        notes=["信用类只认名单本身；处罚通报与人事处理不等于失信记录"],
    )


def decide(claim: Claim, evidence: list[Evidence], missing: list[str]) -> Verdict:
    """判定原则：只有"官方定性/一手证据"才能给出真或假。

    媒体报道只算"间接线索"，永远不足以下"真/假"——因为媒体可以报道一个说法，
    报道本身不等于支持该说法。这条规则牺牲一点覆盖率，换"绝不把报道当结论"。
    """
    live = [e for e in evidence if e.link_ok and e.on_topic]

    # 企业/个人信用类：只认政府与法院公示里的名单本身，单独一套口径
    if is_enterprise_credit(claim.raw):
        return _decide_credit(claim, live, missing)




    # 宣传类断言单独走：查得到依据才能用，查不到按举证责任判高风险
    # 实时信息类：本工具不提供实时数据，也不假装判断真假
    if claim.claim_type == "realtime":
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=40,
            reason=(
                "这是实时信息类问题，要的是「此刻/明天的实际数据」，不是「某句话说法的真假」。"
                "下面列出的是刚检索到的最新来源，请直接以来源页面的发布内容为准。"
            ),
            notes=[
                "实时信息类：不做真假判定",
                "本工具的口径是核实「某个说法有没有依据」，不是查询实时数据",
            ],
        )

    # 宣传类与数据类宣称同一套口径：拿不出依据就是风险，而不是"没事"
    if claim.claim_type in ("ad_claim", "data"):
        return _decide_ad(claim, live, missing)

    # 先按权威优先裁一遍：最高权威那一层有定论就直接采纳，不数条数
    by_auth = _verdict_by_authority(claim, live, missing)
    if by_auth is not None:
        return by_auth

    hq = [e for e in live if e.high_quality]

    denials = [e for e in hq if evidence_kind(e, claim) == "denial"]
    confirms = [e for e in hq if evidence_kind(e, claim) == "confirm"]
    media = [e for e in hq if e not in denials and e not in confirms and _relevant(e, claim)]

    burden = [_ad_burden_note(claim)] if claim.claim_type in ("ad_claim", "data") else []

    # ---- 1) 有官方定性：这才是能下结论的场景 ----
    sites_d = {(e.site or e.url) for e in denials}
    sites_c = {(e.site or e.url) for e in confirms}

    if len(denials) == 1 and not confirms:
        d = denials[0]
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=60,
            reason=f"只有 1 条权威来源定性为假（{d.site or '来源'}），单一来源不足以定论。",
            missing=missing or ["需要第二条独立来源印证"],
            notes=["单一来源不下结论"],
        )
    if len(confirms) == 1 and not denials:
        c = confirms[0]
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=55,
            reason=f"只有 1 条官方材料（{c.site or '来源'}）看起来支持该说法，单一来源不足以定论。",
            missing=missing or ["需要第二个独立来源印证"],
            notes=["单一来源不下结论"],
        )
    if denials and not confirms:
        # 有明确"支持"立场的权威来源时，不能只凭"带辟谣字眼的文章"判假
        support_side = [e for e in hq if e.stance == "support" and _strong_relevant(e, claim)]
        if len(support_side) >= 3:
            top = max(denials, key=lambda e: e.weight)
            return Verdict(
                verdict=VERDICT_UNCLEAR,
                confidence=45,
                reason=(
                    f"来源分歧：有 {len(denials)} 条来源提到「辟谣/不实」"
                    f"（如 {top.site or '来源'}），但同时有 {len(support_side)} 条权威来源支持该说法。"
                    "两边都站得住，不下绝对结论。"
                ),
                missing=missing or ["需要一份直接针对该说法的官方定性或检测报告"],
                conflict=True,
                notes=["正反两方都有权威来源，按冲突处理", *burden],
            )
        top = max(denials, key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_FALSE,
            confidence=min(90, 62 + 8 * len(denials)),
            reason=(
                f"{len(denials)} 条权威来源明确定性该说法不成立"
                f"（如 {top.site or '来源'}），没有找到支持它的官方证据。"
            ),
            risk_level="high" if claim.claim_type in ("ad_claim", "data") else "",
            notes=["依据类型：官方/权威定性（辟谣、不实、澄清）", *burden],
        )
    if confirms and not denials:
        refute_side = [e for e in hq if e.stance == "refute" and _strong_relevant(e, claim)]
        if len(refute_side) >= 3:
            top = max(refute_side, key=lambda e: e.weight)
            return Verdict(
                verdict=VERDICT_UNCLEAR,
                confidence=45,
                reason=(
                    f"来源分歧：有 {len(refute_side)} 条来源持反驳立场"
                    f"（如 {top.site or '来源'}），虽然也有支持性材料，但两边不一致。"
                ),
                missing=missing or ["需要一份直接针对该说法的官方定性或检测报告"],
                conflict=True,
                notes=["正反两方都有权威来源，按冲突处理", *burden],
            )
        top = max(confirms, key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_TRUE,
            confidence=min(90, 62 + 8 * len(confirms)),
            reason=(
                f"{len(confirms)} 条官方/一手材料支持该说法（如 {top.site or '来源'}），"
                "且没有找到否定的权威来源。"
            ),
            notes=["依据类型：官方通报/检测结果", *burden],
        )
    if denials and confirms:
        d = max(denials, key=lambda e: e.weight)
        c = max(confirms, key=lambda e: e.weight)
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=45,
            reason=(
                f"两边都有官方性质的来源：{d.site or '一方'}定性为假，"
                f"{c.site or '另一方'}提供了支持性材料。这种情况要按条件判断，不能给绝对结论。"
            ),
            missing=missing or ["需要一份能同时解释两种口径的一手文件"],
            conflict=True,
            notes=["依据类型：官方定性 vs 官方通报，存在口径冲突"],
        )

    # ---- 2) 只有媒体报道：最多到"说不准"，绝不给真/假 ----
    if len(media) >= 2:
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=30,
            reason=(
                f"找到 {len(media)} 条相关报道，但都不是官方定性或一手证据。"
                "媒体报道一个说法，不等于这个说法成立，所以不下真/假结论。"
            ),
            missing=missing or ["需要官方定性、标准原文、检测报告或原始研究"],
            notes=["拒绝把媒体报道当结论", *burden],
        )
    if media:
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=20,
            reason=f"只找到 1 条间接相关的报道（{media[0].site or '来源'}），单条不足以判断。",
            missing=missing or ["需要至少两条互相独立的可采信来源"],
            notes=["拒绝把单条报道当结论", *burden],
        )

    # ---- 3) 什么都没找到 ----
    reachable = [e for e in evidence if e.link_ok]
    dropped = len(reachable) - len(live)
    if not reachable:
        reason = "没有检索到任何可访问的来源。"
    elif not live:
        reason = (
            f"检索到 {len(reachable)} 条结果，但都不是在讲这条说法"
            "（同名不同主体、或只是关键词重合），没有一条能用来判断。"
        )
    else:
        reason = (
            f"检索到 {len(reachable)} 条结果（其中 {dropped} 条与这条说法无关），"
            "剩下没有一条是能支撑或否定该说法的一手材料。"
        )
    return Verdict(
        verdict=VERDICT_NO_SOURCE,
        confidence=0 if not live else 10,
        reason=reason,
        missing=missing or ["需要官方通报、标准原文或原始研究"],
        risk_level="high" if claim.claim_type in ("ad_claim", "data") else "",
        notes=["拒答：没有可采信的一手材料，不做任何推测。"],
    )


_ASSESS_PROMPT = (
    "你是事实核查员。请只根据下列检索材料，判断待核说法是否成立。\n\n"
    "待核说法：{claim}\n\n"
    "材料（编号｜站点（权威度）｜标题｜摘要）：\n{listing}\n\n"
    "判定规则：\n"
    "1. true：材料里明确写出该说法成立。例如材料写「XX 是国资委直接监管的中央企业」，"
    "则「XX 是国企」可判 true。\n"
    "2. false：材料里明确写出该说法不成立（辟谣、否认、查无记录、明确否定）。\n"
    "3. unclear：材料不足以判断；只有间接线索（讲该主体的其他成绩、其他事件）也算 unclear。\n"
    "4. 不要用材料之外的知识补充。材料里没写，就判 unclear。\n"
    "5. evidence_ids 必须是上面出现过的编号，且必须真正支撑你的结论；引不出编号就只能 unclear。\n"
    "6. 问答页里，提问者的话不算结论，只有答复方的话才算。\n\n"
    '只输出 JSON：{"verdict":"true|false|unclear","evidence_ids":["E1"],"reason":"不超过50字"}'
)


def llm_assess(
    claim: Claim,
    evidence: list[Evidence],
    *,
    min_authority: int = 4,
    min_citations: int = 2,
) -> Verdict | None:
    """让模型读证据下判定——但必须点名依据哪条证据，且依据要够权威。

    词表覆盖不了「是国资委直接监管的中央企业」这类表述，硬套词表只会把
    正确答案判成「说不准」。这一步把判读交回模型，同时用三道校验防止它乱来：
    引用的编号必须真实存在、权威度要达标、且至少两条（单一来源不足以定论）。
    """
    if not llm.available():
        return None
    pool = [e for e in evidence if e.link_ok and e.on_topic]
    if not pool:
        return None
    pool.sort(key=lambda e: -e.weight)
    pool = pool[:10]
    listing = "\n".join(
        f"{e.id}｜{e.site or '未知站点'}（权威度{e.authority}）｜{e.title[:50]}｜{e.snippet[:170]}"
        for e in pool
    )
    obj = llm.chat_json(
        _ASSESS_PROMPT.replace("{claim}", claim.raw).replace("{listing}", listing),
        max_tokens=500,
    )
    if not obj:
        return None
    verdict = str(obj.get("verdict") or "").lower()
    if verdict not in (VERDICT_TRUE, VERDICT_FALSE, VERDICT_UNCLEAR):
        return None
    if verdict == VERDICT_UNCLEAR:
        return None                      # 交回算法的拒答分支，不制造新结论
    ids = {str(i) for i in (obj.get("evidence_ids") or [])}
    cited = [e for e in pool if e.id in ids]
    strong = [e for e in cited if e.authority >= min_authority and not e.interest]
    if len(strong) < min_citations:
        return None                      # 引不出足够权威的依据，不采纳
    top = max(strong, key=lambda e: e.weight)
    reason = str(obj.get("reason") or "").strip()[:80]
    return Verdict(
        verdict=verdict,
        confidence=72,
        reason=(
            f"{reason}（依据：{top.site or '来源'}《{top.title[:26]}》）"
            if reason
            else f"依据 {top.site or '来源'}《{top.title[:26]}》。"
        ),
        notes=["模型读证据判读，已校验引用来源确实存在、权威度达标且不少于两条"],
    )


def reconcile_verdict(
    claim: Claim,
    evidence: list[Evidence],
    algo: Verdict,
    missing: list[str],
) -> Verdict:
    """算法与模型各判一次：一致就采纳；冲突就退回「说不准」。

    宣传类、数据类、实时类、企业信用类不走这一步——它们各自有明确的口径
    （举证责任 / 不判真假 / 只认政府公示），不能由模型一句话推翻。
    算法已经判「来源互相矛盾」时也不推翻：这是真实分歧，不该由模型单方面定调。
    """
    if claim.claim_type in ("ad_claim", "data", "realtime") or is_enterprise_credit(claim.raw):
        return algo
    if algo.verdict == VERDICT_UNCLEAR and algo.conflict:
        algo.notes = list(algo.notes) + ["来源确实互相矛盾，不由单一判定方向覆盖"]
        return algo
    if algo.verdict not in (VERDICT_UNCLEAR, VERDICT_NO_SOURCE):
        return algo              # 算法已有明确结论，不再多调一次模型
    llm_verdict = llm_assess(claim, evidence)
    if llm_verdict is None:
        return algo
    if algo.verdict == llm_verdict.verdict:
        algo.notes = list(algo.notes) + ["算法与模型判定一致"]
        return algo
    if algo.verdict in (VERDICT_TRUE, VERDICT_FALSE):
        return Verdict(
            verdict=VERDICT_UNCLEAR,
            confidence=45,
            reason=(
                f"两种判定方向不一致（规则判定：{VERDICT_LABEL[algo.verdict]}，"
                f"读证据判定：{VERDICT_LABEL[llm_verdict.verdict]}），按保守原则不下结论。"
            ),
            missing=missing or ["需要更权威的一手材料来定论"],
            notes=["算法与模型交叉校验未通过，宁可不下结论"],
        )
    # 算法说不出所以然（unclear / no_source），而模型能点名权威证据 → 采纳模型
    return llm_verdict


def _ad_burden_note(claim: Claim) -> str:
    if claim.claim_type != "ad_claim":
        return ""
    return (
        "宣传类断言按举证责任倒置处理：广告主对广告内容真实性负责，"
        "拿不出依据就不建议这样写（《广告法》第四条、第二十八条）。"
    )


_CAUSAL_MARKERS = ("致癌", "导致", "引起", "诱发", "造成", "使得")
_EFFECT_WORDS = ("致癌", "癌症", "肿瘤", "癌变", "患病", "中毒", "风险", "危害")


def apply_causal_guard(evidence: list[Evidence], claim: Claim) -> int:
    """因果类断言：只提到"前提"、没提到"后果"的证据，不算支持。

    例：「隔夜菜亚硝酸盐超标会致癌」——只说"亚硝酸盐升高"的文章不能当支持证据。
    返回被降级的条数。
    """
    if not any(m in claim.core for m in _CAUSAL_MARKERS):
        return 0
    fixed = 0
    for e in evidence:
        if e.stance == "support" and not any(w in f"{e.title} {e.snippet}" for w in _EFFECT_WORDS):
            e.stance = "neutral"
            fixed += 1
    return fixed


_REPORT_MARKERS = ("称", "表示", "据悉", "网传", "流传", "报道", "专家", "研究", "消息")
_QUESTION_TITLE = re.compile(r"[？?]")


def apply_question_title_guard(evidence: list[Evidence], claim: Claim) -> int:
    """标题是疑问句的，本身没有表态，不能算支持也不能算反驳。

    「隔夜菜真的会致癌吗?」「到底还能不能吃?」这类标题，正文多半是辟谣，
    但只看标题等于把「提问」当成了「主张」——它曾把《隔夜菜真的会致癌吗?》
    判成支持，硬生生把一条能判假的说法拖成「说不准」。
    问号出现在标题中间也要算（《喝咖啡会致癌? 真相是这样的》）。
    """
    fixed = 0
    for e in evidence:
        if e.stance in ("support", "refute") and _QUESTION_TITLE.search(e.title or ""):
            # 只有「通篇是问句」才算没表态：标题以问号收尾。
            # 中文标题常把问号放中间当设问（「国产筋骨贴冒充日本货?罚款20万!」），
            # 后半句已经把答案说了，这类不能降级——实测中它把 3 条处罚报道全判成了中性。
            if not (e.title or "").strip().rstrip("！!。. ").endswith(("?", "？")):
                continue
            e.stance = "neutral"
            fixed += 1
    return fixed


# 辟谣类标记：出现在标题里，说明这篇是在否定某说法
_DEBUNK_MARKERS = (
    "辟谣", "辟謠", "谣言", "謠言", "不实", "不實", "假的", "别再信", "別再信",
    "真相是", "真相来了", "真相來了", "别被骗", "別被騙", "误区", "誤區",
    "误读", "誤讀", "夸大", "誇大", "以偏概全", "纯属", "純屬", "并非", "並非",
)


def apply_debunk_guard(evidence: list[Evidence], claim: Claim) -> int:
    """辟谣平台 / 带辟谣标记的稿子，立场只能是反驳。

    模型只看 120 字摘要，遇到「喝咖啡会致癌? 真相是这样的」这种设问式标题，
    会当成在说「致癌」而标成 support——最高权威一旦被标成支持，整条结论就翻。
    这里用站点性质 + 标题标记兜底，不依赖模型。
    """
    fixed = 0
    for e in evidence:
        title = normalize_text(e.title or "")
        site = normalize_text(e.site or "")
        debunk_site = any(w in site for w in ("辟谣", "较真"))
        if not (debunk_site or any(w in title for w in _DEBUNK_MARKERS)):
            continue
        if e.stance != "refute":
            e.stance = "refute"
            fixed += 1
    return fixed


_PUNCT_RE = re.compile(r"[\s　，。！？；：“”‘’\"'（）()《》〈〉【】\[\]…—\-·|/、?!,.;:]+")
_SYNDICATE_PREFIXES = ("腾讯新闻", "新浪财经", "网易", "搜狐", "凤凰网", "央视新闻", "人民日报", "澎湃新闻")


def _title_key(title: str) -> str:
    """去掉标点和转载前缀，得到可比较的标题指纹。"""
    t = _PUNCT_RE.sub("", normalize_text(title or ""))
    for pre in _SYNDICATE_PREFIXES:
        if t.startswith(pre):
            t = t[len(pre):]
    return t


def _same_story(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    # 一方完整出现在另一方里，且长度够长，视为同一篇（避免「咖啡」这种短词误合并）
    return len(short) >= 10 and short in long


def _collapse_syndicated(evidence: list[Evidence]) -> int:
    """同一立场下标题重复的来源，只留权重最高的一条。

    多家媒体转载同一篇稿子，会在计数上变成「多条来源互相印证」，
    实际只有一份原始材料——这是最典型的假共识来源。
    """
    kept: list[Evidence] = []
    collapsed = 0
    for e in sorted(evidence, key=lambda x: x.weight, reverse=True):
        if e.stance not in ("support", "refute"):
            continue
        key = _title_key(e.title)
        dup = next(
            (k for k in kept if k.stance == e.stance and _same_story(key, _title_key(k.title))),
            None,
        )
        if dup is not None:
            e.stance = "neutral"
            collapsed += 1
        else:
            kept.append(e)
    return collapsed


def apply_precision_filters(evidence: list[Evidence], claim: Claim) -> dict:
    """真实语料上的三个降噪动作。返回统计，便于观察效果。

    1) 转述型降级：媒体"报道某个研究说 X 致癌"不等于"支持 X 致癌"——只算中性。
    2) 同站矛盾清零：同一站点既支持又反驳，说明我们判错了，双方都不算数。
    3) 站内单一来源不加权：同一站点多条只保留权重最高的一条，防止一个站刷爆权重。
    """
    stats = {"demoted_report": 0, "demoted_same_site": 0, "deduped_site": 0, "collapsed_syndicated": 0}

    # 1) 转述型降级（只降 support，不降 refute：辟谣本身常带"称"字）
    for e in evidence:
        if e.stance != "support":
            continue
        title = e.title or ""
        if any(m in title for m in _REPORT_MARKERS):
            e.stance = "neutral"
            stats["demoted_report"] += 1

    # 2) 同站矛盾清零
    by_site: dict[str, set[str]] = {}
    for e in evidence:
        if e.stance in ("support", "refute"):
            by_site.setdefault(e.site or e.url, set()).add(e.stance)
    conflicted = {s for s, stances in by_site.items() if len(stances) > 1}
    for e in evidence:
        if (e.site or e.url) in conflicted and e.stance in ("support", "refute"):
            e.stance = "neutral"
            stats["demoted_same_site"] += 1

    # 3) 同站去重（留权重最高的一条）
    best: dict[tuple[str, str], Evidence] = {}
    for e in evidence:
        if e.stance not in ("support", "refute"):
            continue
        key = (e.site or e.url, e.stance)
        if key not in best or e.weight > best[key].weight:
            if key in best:
                best[key].stance = "neutral"
                stats["deduped_site"] += 1
            best[key] = e
        else:
            e.stance = "neutral"
            stats["deduped_site"] += 1

    # 4) 转载合并：同一篇稿子被多家转载，不能算多条独立佐证
    #    （对标 bullshit-detector 的 collapse syndicated results to their origin）
    stats["collapsed_syndicated"] = _collapse_syndicated(evidence)

    return stats


_DENIAL_MARKERS = (
    "辟谣", "谣言", "不实", "假的", "澄清", "别信", "并非", "不会致癌", "不致癌",
    "没有依据", "误读", "误区", "夸大", "无科学依据", "说法不成立", "骗局",
)
_CONFIRM_MARKERS = (
    "通报", "检出", "超标", "确认", "查处", "检测出", "曝光", "立案", "处罚决定",
    "召回", "停售通知", "公告",
)


def evidence_kind(e: Evidence, claim: Claim | None = None) -> str:
    """区分"官方定性"和"媒体报道"——这是不把报道当结论的关键。

    传了 claim 时，还会要求这条来源真的在谈这个断言（_strong_relevant），
    否则「鸡蛋的 8 个误区」这种只沾了实体的文章会被误当成对断言的否定。
    """
    text = f"{e.title} {e.snippet}"
    if any(m in text for m in _DENIAL_MARKERS) and e.authority >= 4:
        if claim is None or _strong_relevant(e, claim):
            return "denial"
    # 只有政府级来源（authority 5）才算法定"确认"；媒体转述不算
    if any(m in text for m in _CONFIRM_MARKERS) and e.authority >= 5:
        if claim is None:
            return "confirm"
        core = claim.core or ""
        title = e.title or ""
        # 标题必须同时点到「谁」和「怎么了」：只沾话题不算确认
        # 否则「法院用失信彩铃打击老赖」会变成「某公司是失信企业」的佐证
        subject_ok = bool(core[:4]) and core[:4] in title
        claim_word = core[-3:-1] if len(core) >= 6 else ""
        verdict_ok = bool(claim_word) and claim_word in title
        if subject_ok and verdict_ok:
            return "confirm"
    return "media"


def _conflict_reason(winner: list[Evidence], loser: list[Evidence], verdict: str) -> str:
    w = max(winner, key=lambda e: e.weight)
    l = max(loser, key=lambda e: e.weight)
    bits = [f"采纳「{VERDICT_LABEL[verdict]}」，因为 {w.site or '来源'} 更可采信"]
    if w.one_hand and not l.one_hand:
        bits.append("它是一手材料")
    if w.authority > l.authority:
        bits.append(f"权威度 {w.authority} 高于 {l.authority}")
    if w.interest and not l.interest:
        bits.append("对立来源属利益相关方")
    elif l.interest and not w.interest:
        bits.append("对立来源是利益相关方，已降权")
    wd, ld = parse_date(w.date), parse_date(l.date)
    if wd and ld and wd > ld:
        bits.append("时间更新")
    return "，".join(bits) + f"；不采纳 {l.site or '对立来源'}（权威度 {l.authority}）。"


def conflict_table(support: list[Evidence], refute: list[Evidence]) -> list[dict]:
    """四维对比表。adopted 表示该来源与最终采纳方向一致。"""
    rows: list[dict] = []
    for group, stance_label, adopted in (
        (support, "支持该说法", sup_wins(support, refute)),
        (refute, "反驳该说法", not sup_wins(support, refute)),
    ):
        for e in group:
            rows.append(
                {
                    "stance": stance_label,
                    "evidence_id": e.id,
                    "site": e.site or "未知站点",
                    "title": e.title,
                    "url": e.url,
                    "date": e.date,
                    "authority": e.authority,
                    "one_hand": "一手材料" if e.one_hand else "二手转述",
                    "interest": "利益相关（已降权）" if e.interest else "无",
                    "weight": round(e.weight, 2),
                    "adopted": adopted,
                }
            )
    rows.sort(key=lambda r: -r["weight"])
    return rows


def sup_wins(support: list[Evidence], refute: list[Evidence]) -> bool:
    return sum(e.weight for e in support) >= sum(e.weight for e in refute)


def subclaim_matrix(
    claim: Claim,
    evidence: list[Evidence],
    usable: list[Evidence] | None = None,
) -> list[dict]:
    """每个子主张单独看有没有证据，一眼看出哪一环断链。

    usable 是「可采信的证据池」，与主判定同一口径。不传时退化成
    「on_topic 且链接可达」，避免出现「主结论说查不到、子主张却显示被反驳」的自相矛盾。
    """
    pool = usable if usable is not None else [e for e in evidence if e.on_topic and e.link_ok]
    rows: list[dict] = []
    for sub in claim.subclaims:
        tokens = [t for t in re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z]{2,}", sub.text) if len(t) >= 2]
        matched = [e for e in pool if _overlap(tokens, f"{e.title} {e.snippet}")]
        if not matched and pool:
            # 子主张是改写过的说法，措辞常与证据对不上（证据说「中央企业」，子主张写「国有企业」）。
            # 这时按「整条说法」的证据池算，避免出现「主结论说真、子主张却说无直接依据」的矛盾。
            matched = pool
        sup = [e for e in matched if e.stance == "support"]
        ref = [e for e in matched if e.stance == "refute"]
        if not matched:
            state, tone = "无直接依据", "none"
        elif not sup and not ref:
            state, tone = "来源未表态", "none"
        elif len(sup) > len(ref):
            state, tone = "有支持", "support"
        elif len(ref) > len(sup):
            state, tone = "反证占多", "refute"
        else:
            state, tone = "分歧", "mixed"
        rows.append(
            {
                "index": sub.index,
                "text": sub.text,
                "kind": sub.kind,
                "support": len(sup),
                "refute": len(ref),
                "high_quality": len(matched),
                "state": state,
                "tone": tone,
            }
        )
    return rows


def _overlap(tokens: list[str], haystack: str) -> bool:
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in haystack)
    return hits >= max(1, len(tokens) // 3)


def timeline(evidence: list[Evidence], limit: int = 8) -> list[dict]:
    dated = [(parse_date(e.date), e) for e in evidence]
    dated = [(d, e) for d, e in dated if d is not None]
    dated.sort(key=lambda pair: pair[0])
    rows = []
    for d, e in dated[:limit]:
        rows.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "title": e.title,
                "site": e.site,
                "url": e.url,
                "stance": e.stance,
                "authority": e.authority,
            }
        )
    return rows


def origin_note(rows: list[dict]) -> str:
    if not rows:
        return "未能追溯到最初出处：检索结果里没有可用的发布日期。"
    first = rows[0]
    return f"可追溯的最早记录：{first['date']}，{first['site'] or '未知站点'}。更早的源头未找到。"


def pick_top_evidence(evidence: list[Evidence], n: int = 3) -> list[Evidence]:
    pool = [e for e in evidence if e.link_ok]
    pool.sort(
        key=lambda e: (
            0 if e.stance != "neutral" else 1,
            0 if e.high_quality else 1,
            0 if e.one_hand else 1,
            -e.weight,
            -e.score,
        )
    )
    return pool[:n]
