"""演示数据：没有密钥或接口异常时的兜底。

这里放的都是**真实存在、可点开**的公开页面，方便现场断网时依然能演示完整链路。
真实参赛查询请配置元宝搜索密钥，走真检索。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import config, rules
from .claims import Claim
from .judge import Evidence


@dataclass
class Scenario:
    key: str
    keywords: list[str]
    items: list[dict]


SCENARIOS: list[Scenario] = [
    Scenario(
        key="coffee_cancer",
        keywords=["咖啡", "致癌"],
        items=[
            {
                "title": "喝太烫容易致癌？是的！——科普中国",
                "url": "https://www.kepuchina.cn/kppd/dtz/201606/t20160621_12641.shtml",
                "site": "科普中国",
                "date": "2016-06-21",
                "snippet": "温度超过 65℃ 的过烫饮品被列入 2A 类致癌物；由于致癌性证据不足，咖啡已被从 2B 类致癌物名单中移除，挪到第 3 类。",
                "stance": "refute",
                "score": 0.91,
            },
            {
                "title": "喝咖啡致癌吗？——中国互联网联合辟谣平台",
                "url": "https://www.piyao.org.cn/2021-03/23/c_1211079114.htm",
                "site": "中国互联网联合辟谣平台",
                "date": "2021-03-23",
                "snippet": "咖啡中含丙烯酰胺，但并不能推导出喝咖啡导致人类患癌；人体暴露量很低，目前没有食品中丙烯酰胺的限量标准。",
                "stance": "refute",
                "score": 0.89,
            },
            {
                "title": "经常喝 65℃ 以上的水真会致癌？——中国互联网联合辟谣平台",
                "url": "https://www.piyao.org.cn/2020-06/29/c_1210681797.htm",
                "site": "中国互联网联合辟谣平台",
                "date": "2020-06-29",
                "snippet": "该说法源自 2016 年《柳叶刀肿瘤学》关于咖啡、马黛茶与很热饮品的评估，被误读成「咖啡致癌」；原文指向的是 65℃ 以上热饮。",
                "stance": "refute",
                "score": 0.86,
            },
            {
                "title": "咖啡致癌是真的吗——有来医生",
                "url": "https://m.youlai.cn/sgdz/jkzx/8E8E46aq.html",
                "site": "有来医生",
                "date": "2022-05-11",
                "snippet": "鉴定结果：假的。按 60kg 成人体重测算，需每天约 30 杯才能达到致癌剂量。",
                "stance": "refute",
                "score": 0.55,
            },
        ],
    ),
    Scenario(
        key="leftover_nitrite",
        keywords=["隔夜", "亚硝酸盐"],
        items=[
            {
                "title": "剩菜反复加热会致癌？关键不在「热」，在「放」——哈密市人民政府",
                "url": "https://www.hami.gov.cn/hami/c120182/202610/b1d0d853fcea4ed594879c16b72a6760.shtml",
                "site": "哈密市人民政府",
                "date": "2026-10-17",
                "snippet": "冷藏一夜、未翻动的剩菜，亚硝酸盐通常只有每公斤几毫克，离急性中毒线相差悬殊；但室温久放的叶菜会明显飙升，部分超过 100mg/kg。",
                "stance": "refute",
                "score": 0.93,
            },
            {
                "title": "隔夜菜到底能不能吃？他们做了个实验——泉州市市场监督管理局",
                "url": "https://scjgj.quanzhou.gov.cn/xxgk/ztzl/xfts/scjggzxkt/202312/t20231219_2983021.htm",
                "site": "泉州市市场监督管理局",
                "date": "2023-12-19",
                "snippet": "8 份样品实测：冷藏 24 小时后均未检出亚硝酸盐；常温组 3 份含蔬菜样品检出亚硝酸盐，蒜蓉小白菜与酸菜鱼超过 80mg/kg。",
                "stance": "support",
                "score": 0.9,
            },
            {
                "title": "每日热点：隔夜饭菜会致癌不能吃，有道理吗——四川省疾病预防控制中心",
                "url": "https://sccdc.cn/Article/View?id=33618",
                "site": "四川省疾病预防控制中心",
                "date": "2021-05-16",
                "snippet": "4℃ 冷藏条件下 12、24、48 小时后，白菜、胡萝卜、牛肉的亚硝酸盐含量均低于国家规定的残留量上限。",
                "stance": "refute",
                "score": 0.88,
            },
            {
                "title": "食品安全｜剩菜反复加热会致癌——腾讯新闻",
                "url": "https://new.qq.com/rain/a/20261017A088YD00",
                "site": "腾讯新闻",
                "date": "2026-10-17",
                "snippet": "风险分两层：亚硝酸盐的长期积累与细菌毒素的急性中毒；规范冷藏的剩菜可以吃，但室温久放的叶菜风险明显上升。",
                "stance": "neutral",
                "score": 0.72,
            },
        ],
    ),
    Scenario(
        key="ad_precedent",
        keywords=[
            "最低价", "销量第一", "纯天然", "零添加", "包治", "根治", "一次见效",
            "祖传", "第一", "顶级", "进口", "99%",
        ],
        items=[
            {
                "title": "行政处罚决定书：抖音视频广告使用绝对化用语，罚款 35000 元",
                "url": "http://www.wuyuan.gov.cn/zfxxgk/fdzdgknr/cfqzxx/xzcfyxzqzjd/202511/t20251122_599482.html",
                "site": "五原县人民政府",
                "date": "2025-11-22",
                "snippet": "当事人通过微信视频、抖音发布互联网广告，使用《广告法》第九条禁止的绝对化用语；法定 20 万至 100 万元，因积极配合、及时改正减轻处罚，罚款 35000 元。",
                "stance": "neutral",
                "score": 0.92,
            },
            {
                "title": "网店宣传使用「最新款」被举报，市场监管部门已立案处理",
                "url": "https://huarong.gov.cn/33157/61165/61166/content_2331857.html",
                "site": "华容县人民政府",
                "date": "2024-11-08",
                "snippet": "因抖音商城网店宣传「最新款」被群众举报，涉嫌违反《广告法》第九条；因初次违法、危害轻微最终不予处罚，但已立案调查。",
                "stance": "neutral",
                "score": 0.86,
            },
        ],
    ),
]


def _pick(text: str) -> Scenario | None:
    best: tuple[int, Scenario] | None = None
    for sc in SCENARIOS:
        hits = sum(1 for kw in sc.keywords if kw in text)
        if hits and (best is None or hits > best[0]):
            best = (hits, sc)
    return best[1] if best else None


def build(claim: Claim) -> list[Evidence]:
    """按关键词匹配演示场景；匹配不到就返回空证据（走拒答）。"""
    sc = _pick(claim.raw)
    if sc is None:
        return []
    out: list[Evidence] = []
    for i, item in enumerate(sc.items):
        url = item["url"]
        out.append(
            Evidence(
                id=f"E{i + 1}",
                title=item["title"],
                url=url,
                site=item["site"],
                date=item["date"],
                snippet=item["snippet"],
                score=float(item.get("score", 0.6)),
                authority=rules.authority_score(url, item["site"]),
                one_hand=rules.is_one_hand(url, item["title"]),
                interest=rules.is_interest_related(url, item["site"]),
                stance=item["stance"],
                query="(演示数据)",
                round_no=1,
            )
        )
    return out


def dump_scenarios(path: str | None = None) -> str:
    payload = [{"key": s.key, "keywords": s.keywords, "count": len(s.items)} for s in SCENARIOS]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    target = path or str(config.DEMO_FILE)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    return target
