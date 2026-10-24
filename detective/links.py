"""来源链接可用性检查。

规则：404 / 410 / 需登录的一律剔除（宁少不错）；网络超时不剔除，但标记未验证。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}

_DEAD_STATUS = {404, 410, 451}


def check(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """返回 (是否保留, 说明)。"""
    if not url.startswith(("http://", "https://")):
        return False, "非 http 链接"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, stream=True, allow_redirects=True)
        code = resp.status_code
        resp.close()
    except Exception:
        return True, "未验证（请求超时或站点拒绝）"
    if code in _DEAD_STATUS:
        return False, f"链接失效（{code}）"
    if code in (401, 403):
        return True, f"需登录或反爬（{code}），仍保留"
    if code >= 500:
        return True, f"站点异常（{code}），仍保留"
    return True, f"可访问（{code}）"


def check_many(urls: list[str], workers: int = 8) -> dict[str, tuple[bool, str]]:
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, urls))
    return dict(zip(urls, results))
