"""Flask 服务：单端口，前端三个页面。

上线时只需要一个端口，便于用任何 PaaS / 反代发布。
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from detective import cache, checkup, config, investigator, links, report, store

app = Flask(__name__, static_folder=None)
store.init()


# ---------------- 基础工具 ----------------


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def _visitor() -> str:
    return store.visitor_id(_client_ip(), request.headers.get("User-Agent", ""))


def _json_body() -> dict:
    return request.get_json(force=True, silent=True) or {}


def _page(name: str):
    """页面一律不缓存：改前端后用户刷新就能拿到新代码，不用教他清缓存。"""
    resp = send_from_directory(str(config.BASE_DIR / "web"), name)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ---------------- 页面 ----------------


@app.get("/")
def index():
    return _page("index.html")


@app.get("/stats")
def stats_page():
    return _page("stats.html")


@app.get("/wall")
def wall_page():
    return _page("wall.html")


@app.get("/q/<code>")
def card_page(code: str):
    return _page("card.html")


@app.get("/r/<code>")
def report_page(code: str):
    """深度调查报告：自包含 HTML，浏览器另存为 PDF 即一份正式报告。"""
    row = store.get_check(code)
    if not row or not row.get("payload"):
        return Response("没有找到这条核查记录", status=404, mimetype="text/plain; charset=utf-8")
    html = report.build(row)
    resp = Response(html, mimetype="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/assets/<path:filename>")
def assets(filename: str):
    """静态资源同样不缓存：改了样式刷新就生效，不用教用户清缓存。"""
    resp = send_from_directory(str(config.BASE_DIR / "assets"), filename)
    resp.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/assets/owner_qr.png")
def owner_qr():
    """优先用你上传的微信二维码；没有才生成占位码。"""
    uploaded = config.BASE_DIR / "assets" / "owner_qr.png"
    if uploaded.exists():
        return send_from_directory(str(uploaded.parent), uploaded.name)

    target = config.PUBLIC_BASE_URL or config.OWNER_WECHAT or config.OWNER_NAME
    try:
        import qrcode

        img = qrcode.make(target or "1024")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(buf.read(), mimetype="image/png")
    except Exception:
        return Response(b"", mimetype="image/png")


# ---------------- 接口 ----------------


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "mode": config.resolved_mode(),
            "has_search_key": config.has_search_key(),
            "has_llm_key": config.has_llm_key(),
            "searches_this_process": __import__("detective.search", fromlist=["call_count"]).call_count(),
            "owner": {
                "name": config.OWNER_NAME,
                "tagline": config.OWNER_TAGLINE,
                "show_qr": config.SHOW_OWNER_QR,
            },
        }
    )


@app.get("/api/stats")
def api_stats():
    return jsonify(store.stats())


@app.get("/api/wall")
def api_wall():
    return jsonify(store.wall())


@app.post("/api/verify")
def api_verify():
    body = _json_body()
    text = (body.get("text") or "").strip()
    if len(text) < 4:
        return jsonify({"error": "内容太短，至少给一句话"}), 400
    truncated = len(text) > config.MAX_VERIFY_CHARS
    if truncated:
        text = text[: config.MAX_VERIFY_CHARS]

    ok, _ = cache.allow(_client_ip())
    if not ok:
        return jsonify({"error": "本小时查询次数已用完，请稍后再试"}), 429

    visitor = _visitor()
    store.touch_visit(visitor)

    key = cache.key_of("verify", text)
    force = bool(body.get("force"))
    if not force:
        hit = cache.get_with_age(key)
        if hit:
            value, age = hit
            value["cached"] = True
            value["cached_age"] = age        # 秒；前端据此提示"复用的是多久前的结果"
            return jsonify(value)

    inv = investigator.investigate(
        text,
        use_llm=body.get("use_llm", True),
        skip_common=bool(body.get("skip_common")),
    )
    payload = inv.as_dict()
    payload["cached"] = False
    payload["cached_age"] = 0
    payload["truncated"] = truncated
    code = store.save_check(
        kind="verify",
        text=text,
        verdict=inv.verdict.verdict,
        confidence=inv.verdict.confidence,
        searches=inv.searches,
        sources=len(inv.evidence),
        provider=inv.provider,
        visitor=visitor,
        payload=payload,
        source=(body.get("source") or "")[:30],
    )
    payload["code"] = code
    cache.put(key, payload)
    return jsonify(payload)


@app.post("/api/checkup")
def api_checkup():
    body = _json_body()
    text = (body.get("text") or "").strip()
    if len(text) < 6:
        return jsonify({"error": "文案太短，贴一段完整的宣传语试试"}), 400
    truncated = len(text) > config.MAX_INPUT_CHARS
    if truncated:
        text = text[: config.MAX_INPUT_CHARS]

    ok, _ = cache.allow(_client_ip())
    if not ok:
        return jsonify({"error": "本小时查询次数已用完，请稍后再试"}), 429

    visitor = _visitor()
    store.touch_visit(visitor)

    result = checkup.run_checkup(text, use_llm=body.get("use_llm", True))
    result["truncated"] = truncated
    code = store.save_check(
        kind="checkup",
        text=text,
        verdict=f"high:{result['counts']['high']}",
        confidence=100 - result["counts"]["high"] * 15,
        searches=result["searches"],
        sources=result["sources"],
        provider=result["provider"],
        visitor=visitor,
        payload=result,
        source=(body.get("source") or "")[:30],
    )
    result["code"] = code
    return jsonify(result)


@app.get("/api/q/<code>")
def api_q(code: str):
    row = store.get_check(code)
    if not row or not row.get("payload"):
        return jsonify({"error": "没有找到这条记录"}), 404
    payload = dict(row["payload"])
    payload["code"] = code
    payload["created_at"] = row.get("created_at")
    return jsonify(payload)


@app.post("/api/checkup/stream")
def api_checkup_stream():
    body = _json_body()
    text = (body.get("text") or "").strip()
    truncated = len(text) > config.MAX_INPUT_CHARS
    if truncated:
        text = text[: config.MAX_INPUT_CHARS]
    # 先把请求上下文里的东西取出来，生成器里不能再碰 request
    ip = _client_ip()
    visitor = _visitor()
    use_llm = body.get("use_llm", True)
    source = (body.get("source") or "")[:30]

    def gen():
        if len(text) < 6:
            yield _sse({"type": "error", "message": "文案太短，贴一段完整的宣传语试试"})
            return
        ok, left = cache.allow(ip)
        if not ok:
            yield _sse({"type": "error", "message": "本小时查询次数已用完，请稍后再试"})
            return
        store.touch_visit(visitor)
        items: list[dict] = []
        final: dict | None = None
        try:
            for event, data in checkup.iter_checkup(text, use_llm=use_llm):
                if event == "start":
                    yield _sse(
                        {
                            "type": "start",
                            "plan": data.get("plan", []),
                            "findings": data.get("findings", 0),
                            "sentences": data.get("sentences", 0),
                            "left": left,
                        }
                    )
                elif event == "stage":
                    yield _sse({"type": "stage", "stage": data})
                elif event == "item":
                    items.append(data)
                    yield _sse({"type": "item", "item": data})
                elif event == "done":
                    final = data
            if final is not None:
                final["truncated"] = truncated
                code = store.save_check(
                    kind="checkup",
                    text=text,
                    verdict=f"high:{final['counts']['high']}",
                    confidence=100 - final["counts"]["high"] * 15,
                    searches=final["searches"],
                    sources=final["sources"],
                    provider=final["provider"],
                    visitor=visitor,
                    payload=final,
                    source=source,
                )
                final["code"] = code
                yield _sse({"type": "done", "result": final})
            else:
                yield _sse({"type": "error", "message": "没有产生结果，请重试"})
        except Exception as exc:  # 决不让前端白屏
            yield _sse({"type": "error", "message": f"处理时出错：{exc}"})

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(data: dict) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


# ---------------- 图片 / 文本上传 → 取出文字 ----------------

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".txt", ".md"}
_ocr_lock = threading.Lock()
_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _ocr_engine = RapidOCR()
        return _ocr_engine


@app.post("/api/extract")
def api_extract():
    """把海报图片/截图/文本文件里的字取出来，回填到输入框。"""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "没有收到文件"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        return jsonify({"error": f"暂不支持 {ext}，请传图片(png/jpg)或 txt"}), 400

    data = file.read()
    if len(data) > 8 * 1024 * 1024:
        return jsonify({"error": "文件太大，请压到 8MB 以内"}), 413

    if ext in (".txt", ".md"):
        text = None
        for enc in ("utf-8", "gbk", "utf-16"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        text = (text or data.decode("utf-8", "ignore")).strip()
        return jsonify({"kind": "text", "text": text, "chars": len(text)})

    try:
        engine = _get_ocr()
    except Exception as exc:
        return jsonify({"error": f"图片识别不可用（{exc}），请把文字复制粘贴过来"}), 503

    tmp = None
    upscaled = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as fh:
            fh.write(data)
            tmp = fh.name
        result, _ = engine(tmp)
        lines = [row[1] for row in (result or []) if len(row) > 1]
        text = "\n".join(lines).strip()

        # 没认出来就放大 2 倍再试一次（小图/压缩图常见）
        if not text:
            try:
                from PIL import Image

                img = Image.open(tmp).convert("RGB")
                img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
                upscaled = tmp + "_2x.png"
                img.save(upscaled)
                result2, _ = engine(upscaled)
                lines2 = [row[1] for row in (result2 or []) if len(row) > 1]
                text = "\n".join(lines2).strip()
            except Exception:
                pass
    except Exception as exc:
        return jsonify({"error": f"识别过程出错：{exc}"}), 500
    finally:
        for path in (tmp, upscaled):
            if path and os.path.exists(path):
                os.unlink(path)

    if not text:
        return jsonify(
            {
                "error": "这张图没认出文字。可能是纯图片/花纹底/字太小——"
                "把文字复制粘贴进来最快，或者截图时只截文字部分"
            }
        ), 422

    return jsonify(
        {
            "kind": "image",
            "text": text,
            "chars": len(text),
            "note": "图片识别可能有错字，请核对后再开始",
        }
    )


@app.get("/api/check-links")
def api_check_links():
    urls = request.args.get("urls", "")
    items = [u for u in urls.split("|") if u.startswith("http")][:10]
    return jsonify(links.check_many(items))


if __name__ == "__main__":
    print(f"[1024侦探] 模式={config.resolved_mode()} 检索密钥={'有' if config.has_search_key() else '无（演示模式）'}")
    # 云端沙箱通过 PORT 环境变量注入端口；本地默认 5000
    _port = int(os.environ.get("PORT", "5000"))
    print(f"[1024侦探] 打开 http://127.0.0.1:{_port}")
    app.run(host="0.0.0.0", port=_port, debug=False, threaded=True)
