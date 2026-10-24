import sqlite3, json
c = sqlite3.connect(r"D:\自动化报表\一人公司\1024侦探\data\detective.db")
for code in ["0142", "0143"]:
    p = json.loads(c.execute("SELECT payload FROM checks WHERE code=?", (code,)).fetchone()[0])
    v = p.get("verdict") or {}
    claim = p.get("claim") or {}
    c.execute(
        "UPDATE checks SET input=?, verdict=?, confidence=?, searches=?, sources=?, provider=? WHERE code=?",
        (claim.get("raw") or claim.get("core") or "", v.get("verdict"), v.get("confidence"),
         p.get("searches") or p.get("est"), len(p.get("evidence") or []), p.get("provider") or "", code))
    print(code, "修正为:", v.get("verdict"), v.get("confidence"), "| 检索", p.get("searches"))
c.commit()

# 分布校验
dist = {}
for (v,) in c.execute("SELECT verdict FROM checks"):
    if v and v.startswith("high:"):
        k = "checkup_ok" if v.split(":")[1] == "0" else "checkup_risk"
    elif v in ("true", "false", "misleading", "unclear", "no_source", "refused"):
        k = v
    else:
        k = "other"
    dist[k] = dist.get(k, 0) + 1
total = c.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
print("分布:", dist, "| 合计:", sum(dist.values()), "| 总数:", total, "| other:", dist.get("other", 0))
