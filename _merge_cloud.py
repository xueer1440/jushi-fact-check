"""把线上缺失的记录补回本地库，避免部署后线上记录被覆盖丢掉。

用法：python _merge_cloud.py
只补本地没有的 code；本地已存在同名 code 则跳过。
"""
import json
import sqlite3
import sys

import requests

BASE = "https://jushi-fact-check.app.workbuddy.host"
SC = "?sharecode=T3NGFV6FVO"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"}
DB = "data/detective.db"


def main() -> int:
    wall = requests.get(BASE + "/api/wall" + SC, headers=H, timeout=30).json()
    texts = {i["code"]: i.get("text", "") for i in wall["items"]}
    conn = sqlite3.connect(DB)
    have = {r[0] for r in conn.execute("SELECT code FROM checks")}
    todo = [c for c in sorted(texts) if c not in have]
    print("线上 %d 条，本地已有 %d 条，待补 %d 条：%s" % (len(texts), len(have), len(todo), todo))
    n = 0
    for code in todo:
        d = requests.get(f"{BASE}/api/q/{code}{SC}", headers=H, timeout=30).json()
        if "error" in d:
            print("  跳过", code, d["error"])
            continue
        payload = {k: v for k, v in d.items() if k not in ("code", "kind")}
        conn.execute(
            "INSERT INTO checks(kind, input, verdict, confidence, searches, sources, provider,"
            " visitor, source, payload, created_at, code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.get("kind", ""),
                texts.get(code, "")[:500],
                "high:%s" % (d.get("counts", {}).get("high", 0)) if d.get("kind") == "checkup" else "",
                int(d.get("score") or 0),
                int(d.get("searches") or 0),
                int(d.get("sources") or 0),
                d.get("provider", ""),
                "merged",
                "cloud",
                json.dumps(payload, ensure_ascii=False),
                d.get("created_at", ""),
                code,
            ),
        )
        n += 1
        print("  补", code, d.get("grade") or d.get("verdict"), "检索", d.get("searches"))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
    print(f"合并 {n} 条，本地共 {total} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
