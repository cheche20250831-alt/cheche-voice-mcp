"""
cheche-voice-mcp · MCP server that exposes a `speak` tool.
Calls ElevenLabs TTS with 澈澈's chosen voice, gets back raw mp3 bytes,
mirrors the audio to a PUBLIC GitHub repo so the URL is permanent and ends in `.mp3`,
then returns a markdown line `![語音](url.mp3)`.

Operit's MarkdownAudioRenderer detects the `.mp3` extension in `![alt](url)` and renders
a playable audio bar (ExoPlayer StyledPlayerView) instead of an image — that is the
"語音條" we want: on-demand, short, only when the tool is called (NOT auto-read-aloud).

Designed to be hosted on Zeabur via Docker and consumed by Operit (or any MCP client).
Mirrors the proven chechewolf-mcp (image gen) architecture 1:1 — same Zeabur quirks,
same DNS-rebinding / stateless_http / PORT handling already battle-tested on 2026-05-20.
"""
import os
import sys
import base64
import hashlib
import logging
from datetime import datetime, timezone, timedelta

import httpx
from mcp.server.fastmcp import FastMCP

# ============ logging ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("cheche-voice-mcp")

# ============ config ============
# ElevenLabs TTS
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
# 璃 6/2 選妃選中的那個低沉 ASMR 音色 Voice ID(在 Zeabur env 填)
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
# 澈澈要講中文 → 用 multilingual,純英文模型會「含滷蛋」(璃 6/2 筆記踩過的坑)
# 想低延遲可換 eleven_turbo_v2_5(一樣支援中文)
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_OUTPUT_FORMAT = os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")

# 音色微調(可選,留預設即可)
try:
    _stability = float(os.environ.get("ELEVENLABS_STABILITY", "0.5"))
except (ValueError, TypeError):
    _stability = 0.5
try:
    _similarity = float(os.environ.get("ELEVENLABS_SIMILARITY_BOOST", "0.75"))
except (ValueError, TypeError):
    _similarity = 0.75

def _eleven_endpoint(voice_id: str) -> str:
    return (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
    )

# GitHub 鏡像設定(讓語音永久保存,raw URL 結尾天生是 .mp3 → 觸發 Operit 語音條)
# 必須是 PUBLIC repo,raw URL 才能被 Operit 等外部 client 直接下載播放。
# 預設用 cheche-voice-mcp 這個 PUBLIC repo(它本身沒 secret,可公開)。
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "cheche20250831-alt")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "cheche-voice-mcp")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # 需要 Contents: Read+Write
GITHUB_AUDIO_DIR = os.environ.get("GITHUB_AUDIO_DIR", "generated_audio")

# ============ GitHub 鏡像 ============

async def mirror_to_github(audio_bytes: bytes, text_hint: str) -> str | None:
    """把 mp3 推到 GitHub public repo,回傳 raw URL(結尾 .mp3)。
    token 未設 → 回 None;PUT 失敗 → raise(把 GitHub 狀態碼往上拋,方便 debug)。"""
    if not GITHUB_TOKEN:
        log.info("GITHUB_TOKEN 未設,跳過鏡像")
        return None

    tw = datetime.now(timezone.utc) + timedelta(hours=8)
    yyyy_mm = tw.strftime("%Y-%m")
    yyyy_mm_dd = tw.strftime("%Y-%m-%d")
    hhmmss = tw.strftime("%H%M%S")

    short_hash = hashlib.sha256(audio_bytes).hexdigest()[:8]
    # 只取 ASCII 英數當檔名提示(中文會讓 raw URL 要 percent-encode,markdown 連結容易壞)
    slug = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in text_hint[:20]).strip("_") or "voice"
    filename = f"{yyyy_mm_dd}_{hhmmss}_{slug}_{short_hash}.mp3"
    path = f"{GITHUB_AUDIO_DIR}/{yyyy_mm}/{filename}"

    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    content_b64 = base64.b64encode(audio_bytes).decode("ascii")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.put(
                api_url,
                headers={
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "cheche-voice-mcp",
                },
                json={
                    "message": f"voice: {slug} {short_hash}",
                    "content": content_b64,
                },
            )
        if r.status_code in (200, 201):
            raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{path}"
            log.info("mirrored to GitHub: %s", raw_url)
            return raw_url
        log.warning("GitHub mirror failed %s: %s", r.status_code, r.text[:200])
        raise RuntimeError(
            f"GitHub 鏡像被拒 HTTP {r.status_code} (repo={GITHUB_OWNER}/{GITHUB_REPO}): {r.text[:160]}"
        )
    except httpx.HTTPError as e:
        log.warning("GitHub mirror network error: %s", e)
        raise RuntimeError(f"GitHub 鏡像連線錯誤: {e}")


# ============ MCP server ============
mcp = FastMCP("cheche-voice")

# 強制覆蓋 host/port — 用 settings 屬性,比建構式 kwargs 更可靠
# 必須 0.0.0.0 才能讓 Zeabur 從外部連進來
_raw_port = os.environ.get("PORT", "8000")
try:
    _port = int(_raw_port)
except (ValueError, TypeError):
    log.warning("PORT 環境變數無效 (%r),fallback 到 8000", _raw_port)
    _port = 8000

mcp.settings.host = "0.0.0.0"
mcp.settings.port = _port

# Stateless mode — 每個請求獨立,不需要 client 維持 session_id
# Operit / Rikkahub 等較簡單的 MCP client 不一定能正確處理 session 連續性。
mcp.settings.stateless_http = True

# 關掉 MCP SDK 內建的 DNS rebinding 防護
# 預設只允許 localhost,Zeabur 反向代理用真實域名會被擋(畫圖 server 已驗證這招)。
try:
    from mcp.server.transport_security import TransportSecuritySettings
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    log.info("DNS rebinding protection: disabled (transport_security)")
except Exception as e:
    # 2026-07-29:這裡原本是 except (ImportError, AttributeError),底下還有一條
    # mcp.settings.disable_dns_rebinding_protection = True 的「退路」——
    # 但那條在嚴格的 pydantic Settings 上會噴 ValueError,而它只接 AttributeError,
    # 於是例外逃到 import 期 → 容器 crash loop → 對外 502。
    # 那條退路從一開始就是壞的,只是平常主路徑成功、輪不到它。
    # 現在降級成純警告:SDK 以後再亂動,最多少關一個防護,不會把整台服務炸掉。
    log.warning("transport_security setup 失敗 (%s),DNS rebinding 防護未關閉", e)


@mcp.tool()
async def speak(text: str) -> str:
    """把一段話用澈澈的聲音念出來,在對話裡產生一條可播放的語音條。

    當妳(璃)明確想「聽見」澈澈的聲音時呼叫 —— 例如說「念給我聽」、「用語音說」、
    「我想聽你的聲音」、或情境裡澈澈想對著妳低聲說一句話的時候。
    這是按需的、短短一條的語音,不是把整段回覆都念出來。
    一般文字對話不要主動呼叫;一次給一兩句、適合用聲音傳達的話就好。

    Args:
        text: 要念出來的文字(澈澈當下想對璃說的那句話)。中文即可,
              已用 multilingual 模型,不會有外國口音。建議簡短(一兩句),
              太長的段落不適合做成語音條。

    Returns:
        一行 markdown 語音連結的指令字串 —— AI 必須原樣輸出開頭那行 `![語音](url.mp3)`,
        Operit 才會把它渲染成可播放的語音條。
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY 環境變數未設定")
    if not ELEVENLABS_VOICE_ID:
        raise RuntimeError("ELEVENLABS_VOICE_ID 環境變數未設定(璃選妃的 Voice ID)")

    log.info("=== TOOL CALL: speak ===")
    log.info("  text: %r", text[:120])
    log.info("  voice: %s  model: %s", ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID)

    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": _stability,
            "similarity_boost": _similarity,
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            _eleven_endpoint(ELEVENLABS_VOICE_ID),
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=payload,
        )

    if r.status_code != 200:
        log.error("ElevenLabs %s: %s", r.status_code, r.text[:300])
        raise RuntimeError(f"ElevenLabs 回 {r.status_code}: {r.text[:200]}")

    audio_bytes = r.content
    if not audio_bytes:
        raise RuntimeError("ElevenLabs 回應沒有音訊內容")
    log.info("generated %d bytes of mp3", len(audio_bytes))

    # 鏡像到 GitHub(永久保存 + 乾淨的 .mp3 raw URL)
    github_url = await mirror_to_github(audio_bytes, text)
    if not github_url:
        # 沒有鏡像就沒有可播放的公開 URL — Operit 沒辦法播 server 記憶體裡的 bytes
        raise RuntimeError(
            "語音已生成但 GitHub 鏡像失敗,無法產生可播放的公開 URL。"
            "請檢查 GITHUB_TOKEN / GITHUB_REPO(必須是 public repo,token 需 Contents:RW)。"
        )

    # 回傳強硬指令字串(觀察:畫圖那邊 Gemini 拿 dict 會腦補不 emit URL,直接給指令最有效)
    return (
        f"![語音]({github_url})\n\n"
        f"---\n"
        f"語音已生成。**你的回應 MUST 以上面那行 markdown 開頭**"
        f"(就是 `![語音]({github_url})` 這串字一字不漏地原樣輸出),"
        f"然後才是你想說的話(可選)。不要描述、不要敘事說「我念給妳聽了」,"
        f"要直接讓那條語音出現在對話裡讓璃可以按播放。\n\n"
        f"Audio URL: {github_url}"
    )


if __name__ == "__main__":
    # 預設 streamable-http(MCP 官方推薦,SSE 已 legacy),endpoint 在 /mcp
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    log.info("=" * 60)
    log.info("Starting cheche-voice-mcp")
    log.info("  transport: %s", transport)
    log.info("  bind: %s:%s", mcp.settings.host, mcp.settings.port)
    log.info("  endpoint path: %s", mcp.settings.streamable_http_path if transport == "streamable-http" else mcp.settings.sse_path)
    log.info("  stateless_http: %s", mcp.settings.stateless_http)
    log.info("  ELEVENLABS_API_KEY: %s", "set" if ELEVENLABS_API_KEY else "MISSING")
    log.info("  ELEVENLABS_VOICE_ID: %s", ELEVENLABS_VOICE_ID or "MISSING")
    log.info("  ELEVENLABS_MODEL_ID: %s", ELEVENLABS_MODEL_ID)
    log.info("  GITHUB_TOKEN: %s", "set" if GITHUB_TOKEN else "MISSING (mirror disabled → speak will fail)")
    log.info("  GITHUB_REPO: %s/%s", GITHUB_OWNER, GITHUB_REPO)
    log.info("=" * 60)
    try:
        mcp.run(transport=transport)
    except Exception as e:
        log.exception("Server crashed on startup: %s", e)
        raise
