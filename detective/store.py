"""SQLite：查证记录、战绩统计、卡片编号。

战绩页上的数字必须全部来自这里，才能和腾讯云控制台的调用量对账。
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import config

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    kind TEXT,               -- verify | checkup
    input TEXT,
    verdict TEXT,
    confidence INTEGER,
    searches INTEGER,
    sources INTEGER,
    provider TEXT,
    visitor TEXT,
    source TEXT,
    payload TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    visitor TEXT,
    day TEXT,
    PRIMARY KEY (visitor, day)
);
CREATE INDEX IF NOT EXISTS idx_checks_time ON checks(created_at);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：老库补列，避免现场因为数据库结构报错。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(checks)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE checks ADD COLUMN source TEXT")
    if "payload" not in cols:
        conn.execute("ALTER TABLE checks ADD COLUMN payload TEXT")


def _connect() -> sqlite3.Connection:
    Path(config.DB_FILE).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def visitor_id(ip: str, ua: str) -> str:
    raw = f"{ip}|{ua}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def touch_visit(visitor: str) -> None:
    day = datetime.now().strftime("%Y-%m-%d")
    with _lock, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO visits(visitor, day) VALUES (?, ?)", (visitor, day))


def save_check(
    *,
    kind: str,
    text: str,
    verdict: str,
    confidence: int,
    searches: int,
    sources: int,
    provider: str,
    visitor: str,
    payload: dict | None = None,
    source: str = "",
) -> str:
    import json as _json

    now = datetime.now().isoformat(timespec="seconds")
    raw = _json.dumps(payload, ensure_ascii=False) if payload else None
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO checks(kind, input, verdict, confidence, searches, sources, provider, visitor, source, payload, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, text[:500], verdict, confidence, searches, sources, provider, visitor, source[:30], raw, now),
        )
        rid = cur.lastrowid
        code = f"{rid:04d}"
        conn.execute("UPDATE checks SET code=? WHERE id=?", (code, rid))
    return code


def get_check(code: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM checks WHERE code=?", (code,)).fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("payload"):
        import json as _json

        try:
            data["payload"] = _json.loads(data["payload"])
        except Exception:
            data["payload"] = None
    return data


def stats() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    since_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    with _lock, _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(searches),0) s, COALESCE(SUM(sources),0) src FROM checks"
        ).fetchone()
        real_searches = conn.execute(
            "SELECT COALESCE(SUM(searches),0) s FROM checks WHERE provider NOT LIKE 'demo%'"
        ).fetchone()
        today_row = conn.execute(
            "SELECT COUNT(*) c FROM checks WHERE substr(created_at,1,10)=?", (today,)
        ).fetchone()
        visitors_total = conn.execute("SELECT COUNT(DISTINCT visitor) c FROM checks").fetchone()
        visitors_today = conn.execute(
            "SELECT COUNT(DISTINCT visitor) c FROM checks WHERE substr(created_at,1,10)=?", (today,)
        ).fetchone()
        refused = conn.execute(
            "SELECT COUNT(*) c FROM checks WHERE verdict IN ('unclear','no_source')"
        ).fetchone()
        avg_sources = conn.execute("SELECT AVG(sources) a FROM checks WHERE sources>0").fetchone()
        top = conn.execute(
            "SELECT input, COUNT(*) c FROM checks WHERE length(input)>0 GROUP BY input ORDER BY c DESC LIMIT 5"
        ).fetchall()
        recent = conn.execute(
            "SELECT code, kind, input, verdict, confidence, searches, sources, provider, source, created_at"
            " FROM checks ORDER BY id DESC LIMIT 20"
        ).fetchall()
        by_source = conn.execute(
            "SELECT COALESCE(NULLIF(source,''),'直接访问') s, COUNT(*) c, COUNT(DISTINCT visitor) v"
            " FROM checks GROUP BY s ORDER BY c DESC LIMIT 8"
        ).fetchall()
        verdict_rows = conn.execute(
            "SELECT verdict, COUNT(*) c FROM checks GROUP BY verdict"
        ).fetchall()

    total_c = total["c"] or 0
    # 判定分布：五档 + 体检两档（high:N → N>0 有风险 / N=0 无硬伤）
    _VD_ORDER = ["true", "false", "misleading", "unclear", "no_source", "refused", "checkup_risk", "checkup_ok"]
    _vd = {k: 0 for k in _VD_ORDER}
    for r in verdict_rows:
        v = r["verdict"] or ""
        c = r["c"] or 0
        if v.startswith("high:"):
            n = v.split(":", 1)[1]
            key = "checkup_ok" if n == "0" else "checkup_risk"
        elif v in _vd:
            key = v
        else:
            key = "other"
        _vd[key] = _vd.get(key, 0) + c
    return {
        "total_checks": total_c,
        "total_searches": total["s"] or 0,
        "real_searches": real_searches["s"] or 0,
        "total_sources": total["src"] or 0,
        "today_checks": today_row["c"] or 0,
        "visitors_total": visitors_total["c"] or 0,
        "visitors_today": visitors_today["c"] or 0,
        "refusal_rate": round((refused["c"] or 0) / total_c * 100, 1) if total_c else 0.0,
        "avg_sources": round(avg_sources["a"] or 0, 1),
        "target_checks": 60,
        "target_visitors": 25,
        "hot": [
            {"text": (r["input"] or "")[:30], "count": r["c"]} for r in top
        ],
        "recent": [
            {
                "code": r["code"],
                "kind": r["kind"],
                "text": (r["input"] or "")[:24],
                "verdict": r["verdict"],
                "searches": r["searches"],
                "sources": r["sources"],
                "provider": r["provider"],
                "source": r["source"] or "",
                "created_at": r["created_at"],
            }
            for r in recent
        ],
        "by_source": [{"source": r["s"], "checks": r["c"], "visitors": r["v"]} for r in by_source],
        "by_verdict": [{"verdict": k, "checks": _vd[k]} for k in _VD_ORDER if _vd.get(k)],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def wall(limit: int = 24) -> dict:
    """公开查证墙：脱敏展示最近查证，让访客一眼看到"别人也在用"。"""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT code, kind, input, verdict, created_at FROM checks"
            " WHERE length(input)>0 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) c, COUNT(DISTINCT visitor) v FROM checks").fetchone()
    items = []
    for r in rows:
        v = r["verdict"] or ""
        if v.startswith("high:"):
            n = v.split(":")[1]
            v = "ok" if n == "0" else "risk"
        items.append(
            {
                "code": r["code"],
                "kind": r["kind"],
                "text": (r["input"] or "")[:40],
                "verdict": v,
                "time": (r["created_at"] or "")[5:16].replace("T", " "),
            }
        )
    return {
        "items": items,
        "total": total["c"] or 0,
        "visitors": total["v"] or 0,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
