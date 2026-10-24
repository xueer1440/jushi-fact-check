"""断言抽取与子主张拆分。

设计取舍：一句话常混着 2–4 个子主张，混在一起判必错。
规则版先把句子切碎，LLM 可用时再让它改写成更利于检索的子问题。
两条路都不影响后续判定，因为判定只看证据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm

# 只保留有信息量的停用词，避免过度切词把关键实体删掉
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "也", "都", "就", "还", "又", "很", "太",
    "这", "那", "个", "吗", "呢", "吧", "啊", "呀", "什么", "怎么", "为什么",
    "一定", "千万", "千万不", "据说", "听说", "转发", "注意", "紧急", "速看",
    "真的", "到底", "请问", "大家", "朋友", "群里", "消息", "文章", "视频",
}

_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_CLAUSE_RE = re.compile(r"[，,、]+")

_CAUSAL_HINTS = ("导致", "引起", "会造成", "会致", "能治", "可以治", "治愈", "诱发", "使")

_AD_WORDS = (
    "唯一", "顶级", "国家级", "最佳", "首创", "首款", "独家",
    "纯天然", "零添加", "无添加", "百分百", "100%", "祖传", "秘方", "特效",
    "包治", "根治", "治愈", "药到病除", "立竿见影", "永久", "永不", "销量第一", "全网最低",
    "一次见效", "见效", "无副作用", "母婴安全", "安全无害", "包好",
    "采用", "配方", "工艺", "必备", "首选", "推荐", "适用人群",
    # 产地 / 功效类广告语（实测：「日本引进」「一盒让三年疼痛消失」原本被判成传言，
    # 走了三轮传言检索；这是广告语，应该按宣传宣称走举证责任倒置）
    "引进", "进口", "原装", "专利", "止痛", "疼痛消失", "无痛", "根除", "消除症状",
)
_NEWS_WORDS = ("今天", "昨日", "刚刚", "最新", "截至", "目前", "本周", "本月", "发布")
_DATA_WORDS = ("%", "％", "倍", "增长", "下降", "上升", "平均", "统计", "占比", "亿元", "万人")


@dataclass
class SubClaim:
    index: int
    text: str
    kind: str = "general"      # fact / causal / numeric / efficacy
    query_hint: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "kind": self.kind,
            "query_hint": self.query_hint,
        }


@dataclass
class Claim:
    raw: str
    core: str
    claim_type: str = "rumor"      # rumor | ad_claim | news | data
    subclaims: list[SubClaim] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "raw": self.raw,
            "core": self.core,
            "claim_type": self.claim_type,
            "subclaims": [s.as_dict() for s in self.subclaims],
        }


def normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^[【\[]?(转|转发|紧急|重要|提醒|速看)[】\]]?[:：]?", "", text)
    # 拆掉引号书名号：广告语常以「日本引进」这种带引号的形式出现，
    # 引号会污染二字片（「日/本引/进」），让后面的主体匹配全部落空。
    text = re.sub(r"[「」『』“”\"'《》〈〉]", "", text)
    return text.strip()


def extract_keywords(text: str, limit: int = 6) -> list[str]:
    """粗粒度关键词：中文按 2–6 字片段 + 英文/数字保留。"""
    words: list[str] = []
    for token in re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fa5]{2,6}", text or ""):
        if token in _STOPWORDS:
            continue
        if token not in words:
            words.append(token)
    return words[:limit]


# 自我推广的说话方式：第一人称 + 业务动词，基本就是在宣传自己
_SELF_WORDS = (
    "我司", "我们", "本公司", "本店", "本机构", "本工作室", "本单位", "本人",
    "我是", "我帮你", "帮你把", "我的团队", "团队专注", "欢迎咨询", "欢迎致电", "欢迎到店",
)
_PROMO_MARKERS = (
    "专注", "专做", "提供服务", "承接", "价格", "优惠", "套餐", "免费体验",
    "合作", "招商", "加盟", "代运营", "培训", "课程", "落地", "解决方案",
    "顾问", "咨询", "定制", "一对一", "上门",
    # 资质类自称：属于需要证据的宣称（证书/职称可核验），不能当传闻放过
    "训练师", "资质", "证书", "持证", "认证", "讲师", "导师",
)

# 服务动作词：第一人称 + 这类动词 = 在说明「我能提供什么服务」，
# 属于服务范围描述，不是可核验的事实宣称。
# 注意：不要放「提供」「开发」「培训」这类通用词——「某公司提供虚假检测报告」
# 会因此被误判成服务说明，那就成了漏网。
_SERVICE_VERBS = (
    "帮你", "帮您", "为你", "为您", "协助你", "协助您", "帮助你", "帮助您",
    "我们提供", "我司提供", "本公司提供", "我可以提供", "我能提供",
    "可提供", "可以提供", "为你提供", "为您提供",
    "承接", "定制开发", "代运营", "陪跑", "咨询我",
    "教你", "教您", "带你", "带您", "帮你把", "帮您把", "做出来",
    "上门服务", "落地服务", "陪跑服务",
)

# 保证性 / 效果承诺：这类才真的有合规风险（广告法禁止对效果作保证性承诺），
# 需要提示，但同样不需要检索——判断依据在法条，不在搜索结果里。
_GUARANTEE_WORDS = (
    "保证", "确保", "一定能", "肯定能", "必能", "包你", "包您", "包会", "包过",
    "绝对", "100%", "100％", "百分之百", "零风险", "无效退款", "无效退",
    "学完就能", "上手就能", "立刻见效", "马上见效", "立竿见影", "无条件",
    "永久", "永不",
)

# 痛点钩子：提问、描述行业困境，不是宣称。不该占用深查名额。
_HOOK_HINTS = (
    "不会用", "买了闲置", "闲置", "是不是你", "有没有", "为什么", "怎么办",
    "如何", "太难", "搞不定", "踩坑", "用不起来", "白买了",
)

# 经营细节词：年限、价格、工序、时间、地址——这些是「可自证」的内容，
# 广告主的举证责任靠留存材料（进货单、工时记录、合同）履行，不必先公开。
_DETAIL_WORDS = (
    "年", "开了", "人均", "元", "地址", "营业", "每天", "早市", "小时", "分钟",
    "公里", "平方", "斤", "克", "份", "碗", "台", "位", "次", "现买", "现做",
    "手工", "熬", "现磨", "当天",
)
# 但带优越性/效果数据时，就不只是自述了——那是需要外部证据的宣称
_SUPERIORITY = (
    "最", "第一", "唯一", "顶级", "独家", "国家级", "销量", "保证", "百分百",
    "100%", "100％", "倍", "率", "增长", "下降", "提升",
)


def looks_like_own_business(text: str) -> bool:
    """判断一句是否只是「描述自家怎么经营的」——可自证，不需要检索。"""
    return any(d in text for d in _DETAIL_WORDS) and not any(
        s in text for s in _SUPERIORITY
    )


# 实时信息类：要的是"现在/明天多少"，不是"某个说法真假"
_REALTIME_WORDS = (
    "天气", "气温", "气温", "下雨", "降雨", "降雪", "台风", "空气质量", "雾霾",
    "汇率", "股价", "油价", "金价", "房价", "限行", "航班", "高铁", "路况",
    "票房", "比分", "赛果", "开盘", "收盘", "涨停", "跌停", "最新价格", "多少钱",
)


def classify_claim(text: str) -> str:
    low = (text or "").lower()
    # 先判实时信息：否则「最低气温」会被当成绝对化用语
    # 但「某市已取消限行政策」这类是政策陈述，属于待核实说法，不算实时查询
    if (
        any(w in text for w in _REALTIME_WORDS)
        and len(text) <= 30
        and not any(w in text for w in ("我司", "本店", "我们", "本公司"))
        and not any(
            w in text
            for w in ("取消", "发布", "实施", "宣布", "规定", "政策", "新规", "通知", "调整", "废止", "恢复")
        )
    ):
        return "realtime"
    # 「最新」「最近」等是时间词，不是广告法里的绝对化用语；先剥掉再查「最X」，
    # 否则「临保车位最新是500元」这类转述会被误判成广告文案、劝用户切错入口
    probe = re.sub(r"最[新近终后初迟]", "", text or "")
    probe = probe.replace("第一次", "")
    if re.search(r"最[^\s，。；]{0,4}", probe) or "第一" in probe:
        return "ad_claim"
    if any(w in text for w in _AD_WORDS):
        return "ad_claim"
    # ---- 服务类文案：先区分「说明服务」和「保证效果」----
    # 商家/个人介绍自己能做什么（「我帮你把 AI 落到业务里」）是服务范围，
    # 公开渠道天然查不到第三方证据，按事实宣称去检索只会误杀正常文案。
    # 但如果顺手许了效果（「保证学会」「学完就能用」），那是广告法明令禁止的
    # 保证性承诺——这类要提示，依据在法条，不需要检索。
    has_service = any(v in text for v in _SERVICE_VERBS)
    # 个人资质自称：证书真假在公开检索里查不到（不是"网上没有=假的"），
    # 应提示"补充证书名称/颁发机构"，而不是当成无依据的高风险宣称。
    if any(w in text for w in ("我是", "本人", "持有", "持证")) and any(
        c in text for c in ("训练师", "证书", "持证", "认证", "资质", "工程师", "讲师", "导师", "评估师", "咨询师")
    ):
        return "credential"
    # 效果保证独立判定：不要求出现服务动词（「学完就能上手」这类也常单独成句）
    if any(g in text for g in _GUARANTEE_WORDS):
        return "service_guarantee"
    # 邀约/联系方式句（「欢迎中午来吃」「扫码加我」）不是宣称，检索它没有意义
    if any(w in text for w in ("欢迎", "扫码", "私信", "加微信", "加我", "联系我", "来电", "预约", "咨询我")) and not any(
        w in text for w in ("最", "第一", "唯一", "保证", "100%", "百分百")
    ):
        return "service"
    # 痛点/提问钩子：问句、描述困境，不是宣称
    if ("？" in text or "?" in text) and not any(
        w in text for w in ("%", "％", "倍", "第一", "唯一")
    ):
        return "hook"
    if any(h in text for h in _HOOK_HINTS) and not any(
        w in text for w in _SELF_WORDS + ("我们", "本店", "本公司")
    ):
        return "hook"
    if has_service:
        return "service"
    # 自我定位短句：「专注中小企业AI落地」「深耕本地餐饮十年」——讲的是业务范围。
    # 加长度与问号限定，避免「专注力训练有效吗」这类问题被当成自我介绍。
    if len(text) <= 20 and "？" not in text and "?" not in text and re.match(
        r"^(专注|专做|深耕|致力于|只做)", text
    ):
        return "service"
    # 服务范围限定：「只服务中小企业」「仅面向本地客户」——是业务边界，不是事实断言
    if re.search(r"(只|仅|专|主要)(服务|做|接|面向|针对)", text):
        return "service"
    # 自我身份短标签：「AI赋能顾问」「提效工作室」——名词短语，无宣称内容。
    # 但带「我是/本人/持证/证书/认证」的资质自称要留下核查（可能需出示证书）。
    if (
        len(text) <= 12
        and re.search(r"(顾问|专家|服务商|工作室|教练)$", text)
        and not any(w in text for w in ("我是", "本人", "持证", "证书", "认证", "资质"))
    ):
        return "service"
    # ---- 服务「交付方式」：讲怎么做，不讲效果 ----
    # 「不用懂代码」「零基础也能上手」「按你的流程定做」「AI培训：按岗位实操训练」
    # 描述的是服务怎么交付、门槛多高，不是可验证的事实宣称。
    # 拿它去检索必然查不到，只会把正常文案判成"无依据"（2026-10-24 海报实测事故）。
    # 三条硬约束，防止借这个口子放过真正的宣称：
    #   ① 不含百分比/倍数/绝对化用语；② 不是效果保证（保证在上面已单独拦）；
    #   ③ 不含疗效/功效动词（治/根治/消除/降/减）。
    _eff = any(w in text for w in ("治", "根治", "消除", "降", "减", "瘦", "白", "祛"))
    if not _eff and not any(w in text for w in ("%", "％", "倍", "第一", "唯一", "100%", "百分百")):
        if re.search(r"(不用|无需|不需)(懂|会|学|自己|有)", text):
            return "service"
        if "零基础" in text and len(text) <= 24:
            return "service"
        if re.search(r"(只|仅)(讲|教|聊|给|做|接|服务|面向|针对)", text):
            return "service"
        if re.search(r"(按|根据|照着)(你的|您的|你|您)", text):
            return "service"
        if re.search(r"(手把手|一对一|上门服务|陪跑)", text) and len(text) <= 30:
            return "service"
        # 服务项目小标题：「AI培训：按岗位实操训练」「提效工具定制：…」
        if "：" in text and re.search(r"(培训|定制|开发|诊断|陪跑|交付|代运营)", text) and len(text) <= 30:
            return "service"
        # 碎片小标题（常来自海报 OCR）：以服务项目名收尾
        if len(text) <= 10 and re.search(r"(培训|定制|开发|诊断|陪跑|咨询)$", text):
            return "service"
        # 「也能直接开发提效工具」这类：能+服务动作，讲的是能力范围
        if len(text) <= 30 and re.search(r"(能|可|会)(直接)?(培训|定制|开发|交付|陪跑|诊断)", text):
            return "service"

    # 第一人称 + 业务话术 = 商家/个人在介绍自己能提供什么，属于"宣称"而非"传言"
    if any(w in text for w in _SELF_WORDS) and any(m in text for m in _PROMO_MARKERS):
        return "ad_claim"
    if any(w in low for w in ("%", "％", "倍")) or any(w in text for w in _DATA_WORDS):
        return "data"
    if any(w in text for w in _NEWS_WORDS):
        return "news"
    return "rumor"


def build_core_query(text: str) -> str:
    """把口语消息收敛成一个可检索的短查询。"""
    norm = normalize(text)
    clauses = [c for c in _CLAUSE_RE.split(_SPLIT_RE.split(norm)[0]) if c.strip()]
    core = clauses[0] if clauses else norm
    core = re.sub(r"^(据说|听说|网传|有人说|都说了?|专家说)", "", core).strip()
    if len(core) > 30:
        kws = extract_keywords(norm, limit=4)
        if kws:
            return " ".join(kws)
        core = core[:30]
    return core or norm


def _kind_of(clause: str) -> str:
    if any(h in clause for h in _CAUSAL_HINTS):
        return "causal"
    if any(w in clause for w in ("%", "％", "倍", "超标", "含量")):
        return "numeric"
    if any(w in clause for w in ("治", "有效", "功效", "预防", "增强", "改善")):
        return "efficacy"
    return "fact"


def _rule_decompose(core: str, raw: str) -> list[SubClaim]:
    """无 LLM 时的拆分：先按标点切，切不动再套模板。"""
    first = _SPLIT_RE.split(normalize(raw))[0]
    parts = [p.strip() for p in _CLAUSE_RE.split(first) if len(p.strip()) >= 4]

    if len(parts) < 2 and any(h in core for h in _CAUSAL_HINTS):
        # 因果句：拆成"前提是否成立"和"因果是否成立"
        for h in _CAUSAL_HINTS:
            if h in core:
                left, _, right = core.partition(h)
                parts = [left.strip(), f"{left.strip()}{h}{right.strip()}"]
                break

    if len(parts) < 2:
        parts = [core, f"{core} 是否有权威来源支持"]

    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return [
        SubClaim(index=i + 1, text=t, kind=_kind_of(t), query_hint=t)
        for i, t in enumerate(seen[:4])
    ]


def _llm_decompose(core: str, raw: str) -> list[SubClaim] | None:
    prompt = (
        "把下面这条说法拆成 2–4 个可以分别核查的子主张，每个子主张都必须能独立用搜索引擎验证。\n"
        "要求：只输出 JSON，格式为 {\"subclaims\":[{\"text\":\"...\",\"kind\":\"fact|causal|numeric|efficacy\"}]}\n"
        f"原说法：{raw}\n核心：{core}"
    )
    obj = llm.chat_json(prompt)
    if not obj:
        return None
    items = obj.get("subclaims")
    if not isinstance(items, list) or not items:
        return None
    out: list[SubClaim] = []
    for i, item in enumerate(items[:4]):
        if isinstance(item, dict) and item.get("text"):
            text = str(item["text"]).strip()
            kind = str(item.get("kind") or "fact")
            if kind not in ("fact", "causal", "numeric", "efficacy"):
                kind = "fact"
            out.append(SubClaim(index=i + 1, text=text, kind=kind, query_hint=text))
    return out or None


def decompose(raw: str, *, use_llm: bool = True) -> Claim:
    text = normalize(raw)
    core = build_core_query(text)
    claim = Claim(raw=text, core=core, claim_type=classify_claim(text))
    subs = _llm_decompose(core, text) if use_llm else None
    claim.subclaims = subs or _rule_decompose(core, text)
    return claim

_COMMON_PROMPT = """判断这句话是否属于「教科书级常识」——不需要查资料、任何受过基础教育的人都认同的事实。

必须同时满足才判 true：
1. 没有时效性（不涉及今天/明天/最新/最近）
2. 没有争议（学界与监管没有不同口径）
3. 不含数字承诺、排名、承诺效果
4. 不涉及具体机构、个人、地名

示例：
- "鸡蛋含有蛋白质" → true
- "水在标准大气压下100℃沸腾" → true
- "隔夜菜致癌" → false（有争议）
- "微波炉加热食物致癌" → false（有争议）
- "某市已经取消限行政策" → false（有时效性且涉及具体政府）
- "这款喷雾除醛率99%" → false（数字承诺）

只输出 JSON：{"common": true 或 false, "reason": "不超过20字的理由"}

待判断："""


# 这些特征出现，一律不当常识处理——宁可多检索一次，也不凭印象给结论
_COMMON_BLOCKERS = (
    "公司", "集团", "银行", "大学", "医院", "研究院", "法院", "政府", "委员会",
    "国企", "央企", "上市公司", "失信", "被执行人", "董事长", "总经理", "教授",
    "%", "％", "倍", "亿元", "万元", "万人", "第一", "最高", "最低", "唯一", "顶级", "排名",
    "今天", "明天", "昨天", "今年", "明年", "最近", "最新", "截至", "目前", "本月",
    "致癌", "治疗", "治愈", "副作用", "超标", "有毒", "无毒", "安全",
)


def _common_blocker(text: str) -> str:
    for w in _COMMON_BLOCKERS:
        if w in (text or ""):
            return w
    return ""


def check_common_knowledge(text: str) -> tuple[bool, str]:
    """用模型判断是不是常识性事实。任何异常都按「不是」处理，保证不影响正常流程。

    两道关：先过规则黑名单（涉及具体机构、数字、时效、健康因果的一律不短路），
    再让模型判断。规则兜底是为了防止模型把「某公司是国企」这类具体事实当常识放行。
    """
    from . import llm

    blocker = _common_blocker(text)
    if blocker:
        return False, f"含「{blocker}」，不按常识处理"
    data = llm.chat_json(_COMMON_PROMPT + text, max_tokens=120)
    if not isinstance(data, dict):
        return False, ""
    return bool(data.get("common")), str(data.get("reason") or "")[:40]

# 企业信用类断言：答案在信用公示系统里，不在新闻里
_CREDIT_WORDS = (
    "失信", "被执行人", "经营异常", "严重违法", "吊销", "注销", "老赖",
    "被处罚", "行政处罚", "失信黑名单", "信用中国",
)
_ORG_WORDS = ("公司", "企业", "集团", "分公司", "子公司", "机构", "工厂", "门店", "银行", "事务所")


def is_enterprise_credit(text: str) -> bool:
    """判断是不是「某企业失信/违法」这类需要查工商与信用系统的断言。"""
    return any(w in text for w in _CREDIT_WORDS) and any(w in text for w in _ORG_WORDS)
