"""临时：调本地 /api/checkup/stream 跑一条文案体检，打印摘要。

用法：python _run_checkup.py <文本文件>
"""
import json
import sys
import time

import requests

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5057"
URL = BASE + "/api/checkup/stream"
if "workbuddy.host" in BASE:
    URL += "?sharecode=T3NGFV6FVO"

text = open(sys.argv[1], encoding="utf-8").read().strip()
t0 = time.time()
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"}
with requests.post(URL, json={"text": text}, headers=HEADERS, stream=True, timeout=300) as r:
    r.encoding = "utf-8"
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        kind = ev.get("type") or ev.get("kind")
        if kind == "done":
            d = ev.get("result") or ev.get("data") or ev
            c = d.get("counts", {})
            print("耗时 %.1fs" % (time.time() - t0))
            print("编号", d.get("code"), "|", d.get("grade"), d.get("score"), "分")
            print("句子", c.get("sentences"), "深查", c.get("checked"), "高", c.get("high"),
                  "中", c.get("mid"), "低", c.get("low"), "待核", c.get("pending"))
            print("检索", d.get("searches"), "次 / 来源", d.get("sources"), "条 / 引擎", d.get("provider"))
            print("结语:", d.get("headline"))
            for it in d.get("items", []):
                print("  [%s] %-8s %s" % (it.get("risk_level"), it.get("issue_type") or it.get("label"),
                                          it.get("sentence", "")[:40]))
        elif kind == "error":
            print("ERROR", ev)
