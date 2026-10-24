"""腾讯云元宝搜索（wsa）封装。

只做三件事：发请求、解析 Pages、把错误翻译成人话。
真实调用失败时抛 SearchUnavailable，由上层降级到 demo 数据。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from . import config


class SearchUnavailable(RuntimeError):
    """检索不可用（无密钥 / 额度耗尽 / 网络异常），上层应降级。"""


@dataclass
class RawHit:
    title: str = ""
    url: str = ""
    site: str = ""
    date: str = ""
    passage: str = ""
    content: str = ""
    score: float = 0.0
    authority_level: int | None = None
    pics: list = field(default_factory=list)


@dataclass
class SearchResult:
    query: str
    hits: list[RawHit]
    round: int = 0
    note: str = ""
    provider: str = "wsa"


_counter_lock = threading.Lock()
_call_counter = {"total": 0}


def call_count() -> int:
    with _counter_lock:
        return _call_counter["total"]


def _bump(n: int = 1) -> None:
    with _counter_lock:
        _call_counter["total"] += n


def _parse_pages(pages: Any) -> list[RawHit]:
    hits: list[RawHit] = []
    if not isinstance(pages, list):
        return hits
    for item in pages:
        try:
            obj = json.loads(item) if isinstance(item, str) else item
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        url = (obj.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            RawHit(
                title=(obj.get("title") or "").strip(),
                url=url,
                site=(obj.get("site") or "").strip(),
                date=(obj.get("date") or "").strip(),
                passage=(obj.get("passage") or "").strip(),
                content=(obj.get("content") or "").strip(),
                score=float(obj.get("score") or 0.0),
                authority_level=obj.get("authority_level"),
                pics=obj.get("pics") or [],
            )
        )
    return hits


def _search_by_apikey(params: dict, query: str, round_no: int) -> SearchResult:
    """服务 API KEY 通道：Bearer 直连 api.wsa.cloud.tencent.com/SearchPro。

    和 SDK 签名走的是同一个 SearchPro 能力，只是鉴权与端点不同。
    赛事赠送的额度通常挂在 API KEY 上，所以配了它就优先走这条。
    """
    import urllib.error
    import urllib.request

    body = json.dumps(params, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        config.WSA_APIKEY_HOST,
        data=body,
        headers={
            "Authorization": "Bearer " + config.TENCENTCLOUD_WSA_APIKEY,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        code = ""
        try:
            code = (json.loads(detail).get("Response", {}).get("Error", {}).get("Code") or "")
        except Exception:
            pass
        hint = {
            "UnauthorizedOperation": "API KEY 无效或未开通服务",
            "ResourceNotFound": "未开通元宝搜索服务",
            "ResourceUnavailable": "账号欠费或资源不可用",
            "RequestLimitExceeded": "超过频率限制",
        }.get(code, detail or str(exc))
        raise SearchUnavailable(f"元宝搜索调用失败：{hint}") from exc
    except Exception as exc:
        raise SearchUnavailable(f"元宝搜索调用失败：{exc}") from exc

    _bump()
    body2 = data.get("Response") if isinstance(data.get("Response"), dict) else data
    if isinstance(body2.get("Error"), dict):
        err = body2["Error"]
        raise SearchUnavailable(f"元宝搜索返回错误：{err.get('Code')} {err.get('Message', '')}")
    return SearchResult(
        query=query,
        hits=_parse_pages(body2.get("Pages")),
        round=round_no,
        note=(body2.get("Msg") or "").strip(),
        provider="wsa-apikey",
    )


def search(
    query: str,
    *,
    cnt: int = 20,
    mode: int = 2,
    site: str | None = None,
    industry: str | None = None,
    freshness: str | None = None,
    round_no: int = 0,
) -> SearchResult:
    """调用元宝搜索 SearchPro。无密钥或失败时抛 SearchUnavailable。"""
    if not config.has_search_key() or config.resolved_mode() == "demo":
        raise SearchUnavailable("未配置元宝搜索密钥，已切到演示数据")

    # 接口只接受 10 的倍数（1-20），传 8 会直接 400，这里兜一下
    cnt = min(20, max(10, int(round(cnt / 10.0)) * 10))
    params: dict[str, Any] = {"Query": query, "Mode": mode, "Cnt": cnt}
    if site:
        params["Site"] = site
    if industry:
        params["Industry"] = industry
    if freshness:
        params["Freshness"] = freshness

    if config.search_channel() == "apikey":
        return _search_by_apikey(params, query, round_no)

    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.wsa.v20250508 import models, wsa_client
    except ImportError as exc:  # pragma: no cover
        raise SearchUnavailable("缺少 tencentcloud-sdk-python，请先安装依赖") from exc

    try:
        cred = credential.Credential(config.TENCENT_SECRET_ID, config.TENCENT_SECRET_KEY)
        http_profile = HttpProfile()
        http_profile.endpoint = config.WSA_ENDPOINT
        http_profile.reqTimeout = 10
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client = wsa_client.WsaClient(cred, "", client_profile)

        req = models.SearchProRequest()
        req.from_json_string(json.dumps(params))
        resp = client.SearchPro(req)
        data = json.loads(resp.to_json_string())
    except Exception as exc:  # 网络/鉴权/额度，一律降级
        raise SearchUnavailable(f"元宝搜索调用失败：{exc}") from exc

    _bump()
    # SDK 不同版本回包结构不同：有的包一层 Response，有的直接平铺，两种都兼容
    body = data.get("Response") if isinstance(data.get("Response"), dict) else data
    hits = _parse_pages(body.get("Pages"))
    return SearchResult(
        query=query,
        hits=hits,
        round=round_no,
        note=(body.get("Msg") or "").strip(),
        provider=f"wsa-{body.get('Version') or 'standard'}",
    )
