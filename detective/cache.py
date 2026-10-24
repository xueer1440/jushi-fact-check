"""结果缓存与限流：保护免费额度。"""
from __future__ import annotations

import hashlib
import threading
import time

from . import config

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()

_hits: dict[str, list[float]] = {}
_hit_lock = threading.Lock()


def key_of(prefix: str, text: str) -> str:
    norm = "".join((text or "").split())
    return prefix + ":" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:20]


def get(key: str):
    hit = get_with_age(key)
    return hit[0] if hit else None


def get_with_age(key: str):
    """返回 (结果, 距今秒数)；过期或没有则返回 None。"""
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, value = item
        age = time.time() - ts
        if age > config.CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return value, int(age)


def put(key: str, value) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), value)
        if len(_cache) > 800:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:200]
            for k, _ in oldest:
                _cache.pop(k, None)


def allow(ip: str) -> tuple[bool, int]:
    """每小时限额。返回 (是否放行, 剩余次数)。"""
    now = time.time()
    with _hit_lock:
        bucket = [t for t in _hits.get(ip, []) if now - t < 3600]
        if len(bucket) >= config.RATE_LIMIT_PER_HOUR:
            _hits[ip] = bucket
            return False, 0
        bucket.append(now)
        _hits[ip] = bucket
        return True, config.RATE_LIMIT_PER_HOUR - len(bucket)
