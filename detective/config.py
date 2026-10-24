"""全局配置：环境变量、运行模式、署名信息。

设计原则：没有密钥也必须能完整运行（走 demo 数据），
这样现场网络出问题时不会白屏。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """极简 .env 解析，不引入 python-dotenv 依赖。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()

# ---- 元宝搜索（腾讯云 wsa）----
# 两条通道任选其一，配了 API KEY 就优先走它：
# 1. 服务 API KEY —— 控制台创建，Bearer 直连，赛事赠送额度通常挂在这上面
# 2. SecretId/SecretKey —— 标准签名，走 SDK
TENCENTCLOUD_WSA_APIKEY = os.getenv("TENCENTCLOUD_WSA_APIKEY", "")
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY", "")
WSA_ENDPOINT = os.getenv("WSA_ENDPOINT", "wsa.tencentcloudapi.com")
WSA_APIKEY_HOST = os.getenv("WSA_APIKEY_HOST", "https://api.wsa.cloud.tencent.com/SearchPro")
WSA_VERSION = "2025-05-08"

# ---- 大模型（OpenAI 兼容；只用来识别立场和写改写建议，不负责下结论）----
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))

# ---- 运行模式：auto 有密钥走真实接口、异常自动降级 demo ----
RUN_MODE = os.getenv("RUN_MODE", "auto").lower()  # auto | real | demo

# ---- 署名（入门砖的核心：卡片上必须看得出是谁做的）----
OWNER_NAME = os.getenv("OWNER_NAME", "曾艳")
OWNER_TAGLINE = os.getenv("OWNER_TAGLINE", "帮小微企业把 AI 用起来")
OWNER_WECHAT = os.getenv("OWNER_WECHAT", "")
OWNER_QRCODE = os.getenv("OWNER_QRCODE", "/assets/owner_qr.png")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
# 是否在页面上显示二维码与联系方式。参赛期若担心作品页被公开截图，设为 false（只留名字）
SHOW_OWNER_QR = os.getenv("SHOW_OWNER_QR", "true").lower() in ("1", "true", "yes", "on")

# ---- 限额与缓存 ----
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "30"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(2 * 3600)))
MAX_SEARCH_PER_INVESTIGATION = int(os.getenv("MAX_SEARCH_PER_INVESTIGATION", "9"))
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3"))
CHECKUP_TOP_N = int(os.getenv("CHECKUP_TOP_N", "8"))
# 体检深查的时间预算（秒）。平台网关约 100 秒会截断长请求，
# 一张 600+ 字的海报会排进 8 句深查（24 次检索），实测超过 100 秒 →
# 用户看到的是错误页。到点即停，没查的句子标「待核查」，绝不给没查过的句子下结论。
CHECKUP_TIME_BUDGET = int(os.getenv("CHECKUP_TIME_BUDGET", "55"))
# 文案体检输入上限：控制检索成本与耗时，超长文案建议分段
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "1000"))
# 事实核查针对"一条说法"，越长越查不准
MAX_VERIFY_CHARS = int(os.getenv("MAX_VERIFY_CHARS", "200"))

DISCLAIMER = "本结果基于权威、可公开核验来源的核查与风险提示，不构成法律意见。"

# 参赛信息与检索能力致谢（页面页脚、分享卡、报告页统一露出）
CONTEST_LINE = (
    "检索能力：腾讯元宝搜索　|　"
    "1024超能Coding周末 · 侦探赛道参赛作品　|　"
    "WaytoAGI · AI切磋大会"
)

DEMO_FILE = BASE_DIR / "demo" / "queries.json"
DB_FILE = BASE_DIR / "data" / "detective.db"


def has_search_key() -> bool:
    return bool(TENCENTCLOUD_WSA_APIKEY or (TENCENT_SECRET_ID and TENCENT_SECRET_KEY))


def search_channel() -> str:
    """当前走哪条通道：apikey（Bearer 直连）还是 standard（SDK 签名）。"""
    return "apikey" if TENCENTCLOUD_WSA_APIKEY else "standard"


def has_llm_key() -> bool:
    return bool(LLM_API_KEY)


def resolved_mode() -> str:
    """决定本次进程实际的检索模式。"""
    if RUN_MODE == "demo":
        return "demo"
    if RUN_MODE == "real":
        return "real"
    return "real" if has_search_key() else "demo"
