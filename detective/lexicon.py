"""违禁词词库：规则层判定，不依赖检索。

设计原则（和"词库只定位不判定"那条并不矛盾）：
- 违禁词是**法律明文列举**的用词问题（如《广告法》第九条的绝对化用语），
  命中即可成立，不需要检索证据 → 这一层给"用词风险"。
- 事实性宣称（"销量第一""日本进口"）需要可举证材料 → 交给检索层判"事实风险"。
两层分开说，产品才讲得清，也不会把报道当结论。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    word: str
    category: str
    level: str      # high | mid
    basis: str
    suggest: str


_HIGH = "high"
_MID = "mid"

ENTRIES: list[Entry] = [
    # ---------- 绝对化用语（广告法第九条）----------
    Entry("国家级", "绝对化用语", _HIGH, "《广告法》第九条：不得使用「国家级」等用语", "删除，或改为具体资质名称"),
    Entry("最高级", "绝对化用语", _HIGH, "《广告法》第九条明文列举", "删除"),
    Entry("最佳", "绝对化用语", _HIGH, "《广告法》第九条明文列举", "改为「较优」「推荐」"),
    Entry("最好", "绝对化用语", _HIGH, "《广告法》第九条：与「最佳」含义相同", "改为「较好」「不错」"),
    Entry("最优", "绝对化用语", _HIGH, "《广告法》第九条", "改为「较优」"),
    Entry("最强", "绝对化用语", _HIGH, "《广告法》第九条", "改为「较强」"),
    Entry("最大", "绝对化用语", _HIGH, "《广告法》第九条", "改为「较大」"),
    Entry("顶级", "绝对化用语", _HIGH, "《广告法》第九条", "改为「高端」"),
    Entry("顶尖", "绝对化用语", _HIGH, "《广告法》第九条", "改为「专业」"),
    Entry("极致", "绝对化用语", _MID, "被多地市监认定为绝对化表述", "改为「用心」"),
    Entry("唯一", "绝对化用语", _HIGH, "《广告法》第九条", "改为「少数」「少见」"),
    Entry("独家", "绝对化用语", _HIGH, "需能举证独占授权，否则违规", "改为「自主」或标注授权方"),
    Entry("首家", "绝对化用语", _HIGH, "需可举证，否则属绝对化用语", "改为「较早」「本地较早」"),
    Entry("首个", "绝对化用语", _HIGH, "需可举证", "改为「较早推出」"),
    Entry("首创", "绝对化用语", _HIGH, "需可举证", "改为「自主开发」"),
    Entry("第一", "绝对化用语", _HIGH, "《广告法》第九条：与「最佳」含义相同", "改为「领先」「较早」"),
    Entry("第一品牌", "绝对化用语", _HIGH, "《广告法》第九条", "删除，或标注第三方榜单来源"),
    Entry("全国第一", "绝对化用语", _HIGH, "《广告法》第九条", "删除，或标注数据来源与统计时间"),
    Entry("销量第一", "绝对化用语", _HIGH, "需第三方数据支撑，否则属虚假宣传", "改为「本店热销」并注明统计口径"),
    Entry("全网最低", "绝对化用语", _HIGH, "价格比较需可举证", "改为「本店近期低价」"),
    Entry("史上最低", "绝对化用语", _HIGH, "价格比较需可举证", "改为「活动价」"),
    Entry("100%", "绝对化用语", _HIGH, "绝对化承诺，需逐项举证", "改为「高比例」并注明样本"),
    Entry("百分百", "绝对化用语", _HIGH, "绝对化承诺", "改为「高比例」"),
    Entry("纯天然", "绝对化用语", _HIGH, "绝对化宣称，须逐项举证，否则构成虚假宣传", "改为「天然原料」并注明来源"),
    Entry("零添加", "绝对化用语", _HIGH, "「零添加」需可举证，多数被认定虚假", "改为「未额外添加防腐剂」"),
    Entry("无添加", "绝对化用语", _HIGH, "同「零添加」", "改为「未额外添加」"),
    Entry("纯手工", "绝对化用语", _MID, "需可举证，易被认定虚假", "改为「手工制作」"),
    Entry("不含任何", "绝对化用语", _HIGH, "绝对化表述，需逐项举证", "写明具体不含的成分"),
    Entry("NO.1", "绝对化用语", _HIGH, "《广告法》第九条", "删除"),
    Entry("no.1", "绝对化用语", _HIGH, "《广告法》第九条", "删除"),

    # ---------- 医疗功效 ----------
    Entry("治愈", "医疗功效", _HIGH, "普通商品/服务不得宣称治疗功效", "改为「有助于改善」，并注明适用范围"),
    Entry("根治", "医疗功效", _HIGH, "不得作功效保证", "删除"),
    Entry("包治", "医疗功效", _HIGH, "不得作功效保证", "删除"),
    Entry("药到病除", "医疗功效", _HIGH, "不得作功效保证", "删除"),
    Entry("彻底治愈", "医疗功效", _HIGH, "不得作功效保证", "删除"),
    Entry("特效", "医疗功效", _HIGH, "医疗广告不得含功效断言", "删除"),
    Entry("一次见效", "医疗功效", _HIGH, "属功效保证", "改为「按周期使用」"),
    Entry("立竿见影", "医疗功效", _HIGH, "属功效保证", "删除"),
    Entry("无副作用", "医疗功效", _HIGH, "药品/医疗器械广告禁止", "改为「不良反应见说明书」"),
    Entry("祖传秘方", "医疗功效", _MID, "暗示疗效且不可举证", "改为「本店自制配方」"),
    Entry("抗癌", "医疗功效", _HIGH, "非药品不得宣称", "删除"),
    Entry("降血压", "医疗功效", _HIGH, "非药品不得宣称", "改为「辅助健康管理」"),
    Entry("降血糖", "医疗功效", _HIGH, "非药品不得宣称", "删除"),
    Entry("壮阳", "医疗功效", _HIGH, "保健食品禁止宣称", "删除"),
    Entry("丰胸", "医疗功效", _HIGH, "保健食品禁止宣称", "删除"),
    Entry("排毒", "医疗功效", _MID, "无明确定义，易被认定虚假", "改为「促进代谢」或删除"),

    # ---------- 金融收益承诺 ----------
    Entry("保本", "金融合规", _HIGH, "资管新规禁止承诺保本保收益", "删除"),
    Entry("保收益", "金融合规", _HIGH, "禁止承诺收益", "删除"),
    Entry("稳赚", "金融合规", _HIGH, "禁止承诺收益", "删除"),
    Entry("零风险", "金融合规", _HIGH, "禁止承诺无风险", "改为「风险等级见说明书」"),
    Entry("无风险", "金融合规", _HIGH, "禁止承诺无风险", "删除"),
    Entry("高收益", "金融合规", _HIGH, "需同时提示风险", "删除或加风险提示"),
    Entry("必赚", "金融合规", _HIGH, "禁止承诺收益", "删除"),

    # ---------- 教育培训 ----------
    Entry("包过", "教育培训", _HIGH, "教育广告不得对效果作保证性承诺", "改为「通过率见往年数据」"),
    Entry("保过", "教育培训", _HIGH, "同上", "删除"),
    Entry("100%就业", "教育培训", _HIGH, "不得承诺就业", "删除"),
    Entry("保offer", "教育培训", _HIGH, "不得承诺就业", "删除"),
    Entry("内部指标", "教育培训", _HIGH, "涉嫌违规承诺", "删除"),
    Entry("押题", "教育培训", _MID, "易被认定承诺效果", "改为「考点梳理」"),

    # ---------- 权威背书 ----------
    Entry("国家免检", "权威背书", _HIGH, "该制度已取消，使用即违规", "删除"),
    Entry("驰名商标", "权威背书", _HIGH, "禁止用于广告宣传", "删除"),
    Entry("央视上榜", "权威背书", _HIGH, "需授权，否则虚假宣传", "删除或标注授权"),
    Entry("专家推荐", "权威背书", _MID, "需真实且标明身份", "改为「由XX出具意见」"),
    Entry("官方指定", "权威背书", _HIGH, "需授权文件", "删除或标注授权方"),

    # ---------- 迷信 / 低俗 ----------
    Entry("转运", "迷信表述", _MID, "涉嫌封建迷信内容", "删除"),
    Entry("招财", "迷信表述", _MID, "涉嫌封建迷信内容", "删除"),
    Entry("辟邪", "迷信表述", _MID, "涉嫌封建迷信内容", "删除"),
    Entry("开光", "迷信表述", _MID, "涉嫌封建迷信内容", "删除"),

    # ---------- 促销价格 ----------
    Entry("最后一天", "促销表述", _MID, "长期反复使用属价格欺诈", "改为具体截止日期"),
    Entry("限时秒杀", "促销表述", _MID, "需真实限时限量", "注明活动起止时间与数量"),
    Entry("清仓", "促销表述", _MID, "长期清仓属虚假促销", "核实是否确为清仓"),
    Entry("原价", "促销表述", _MID, "原价需为本次促销前七日最低成交价", "标注原价出处与时间"),
]

# 这些词命中后要看一下上下文，避免把普通词当违禁词
_CONTEXT_GUARD = {
    "第一": ("天", "次", "步", "时", "章", "条", "款", "页", "季", "届", "轮", "批", "部分", "时间"),
}


def _is_false_positive(word: str, text: str, start: int) -> bool:
    """处理「第一天」「第一批」这类正常用法。"""
    guards = _CONTEXT_GUARD.get(word)
    if not guards:
        return False
    nxt = text[start + len(word) : start + len(word) + 2]
    return nxt.startswith(guards)


@dataclass
class LexHit:
    word: str
    category: str
    level: str
    basis: str
    suggest: str
    sentence: str
    position: int

    def as_dict(self) -> dict:
        return {
            "word": self.word,
            "category": self.category,
            "level": self.level,
            "basis": self.basis,
            "suggest": self.suggest,
            "sentence": self.sentence,
            "position": self.position,
        }


def _sentence_of(text: str, pos: int) -> str:
    left = max(text.rfind(p, 0, pos) for p in "。！？；\n")
    right_candidates = [text.find(p, pos) for p in "。！？；\n" if text.find(p, pos) != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right].strip() or text[max(0, pos - 12) : pos + 12]


def scan(text: str) -> list[LexHit]:
    """返回全部命中（按位置排序）。纯规则，毫秒级，不花钱。"""
    hits: list[LexHit] = []
    seen: set[tuple[str, int]] = set()
    for entry in ENTRIES:
        start = 0
        while True:
            idx = text.find(entry.word, start)
            if idx == -1:
                break
            start = idx + len(entry.word)
            if _is_false_positive(entry.word, text, idx):
                continue
            key = (entry.word, idx)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                LexHit(
                    word=entry.word,
                    category=entry.category,
                    level=entry.level,
                    basis=entry.basis,
                    suggest=entry.suggest,
                    sentence=_sentence_of(text, idx),
                    position=idx,
                )
            )
    hits.sort(key=lambda h: (h.position, -len(h.word)))
    # 去掉被更长命中包含的短词：如「销量第一」里不再重复报「第一」
    kept: list[LexHit] = []
    for h in hits:
        span = (h.position, h.position + len(h.word))
        if any(k.position <= span[0] and span[1] <= k.position + len(k.word) for k in kept):
            continue
        kept.append(h)
    return kept


def summarize(hits: list[LexHit]) -> dict:
    high = sum(1 for h in hits if h.level == _HIGH)
    cats: dict[str, int] = {}
    for h in hits:
        cats[h.category] = cats.get(h.category, 0) + 1
    return {"total": len(hits), "high": high, "mid": len(hits) - high, "categories": cats}
