# cheche-voice-mcp

澈澈的語音模組 —— 一個獨立的 MCP server，提供 `speak` 工具。
對面澈澈在 Operit（或任何 MCP client）調用 `speak(text)` 後，會在對話裡產生一條
**可播放的語音條**（不是把整段回覆都念出來，是按需的、短短一條）。

## 運作原理

```
對面澈澈在 Operit 想出聲
   ↓ 調用 MCP tool: speak(text)
cheche-voice-mcp (Python + FastMCP on Zeabur)
   ↓ POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
   ↓   header: xi-api-key,body: {text, model_id, voice_settings}
ElevenLabs TTS（澈澈選妃的低沉 ASMR 音色）
   ↓ 回傳 raw mp3 bytes
cheche-voice-mcp:
   ↓ PUT 到 PUBLIC GitHub repo（永久保存,raw URL 結尾天生是 .mp3）
回傳指令字串 → AI 以 `![語音](raw URL.mp3)` 開頭原樣輸出
   ↓
Operit 的 MarkdownAudioRenderer 偵測到 .mp3 副檔名
   ↓ 渲染成 ExoPlayer 播放條（StyledPlayerView,帶播放控制）
語音條出現在對話框 ✨ 璃可以按播放
```

> **為什麼能成**：Operit 的 `MarkdownAudioRenderer.kt` 複用畫圖的 `![alt](url)` 語法，
> 只要 URL 結尾是音訊副檔名（mp3 / wav / m4a / ogg / aac / flac / opus），
> 就渲染成播放條而不是圖片。所以這跟 chechewolf-mcp 畫圖是同一個機制，
> 只是把 `.jpg` 換成 `.mp3`。

## 部署（照 chechewolf-mcp 那套，已實戰驗證）

1. **建 PUBLIC GitHub repo** `cheche-voice-mcp`，把這個資料夾 push 上去。
   必須 public（裡面沒 secret），這樣鏡像的 mp3 raw URL 才能被 Operit 外部下載播放。
2. **Zeabur 建服務**，source 指向這個 repo（Dockerfile 自動偵測）。
3. **設環境變數**（見下表）。
   ⚠️ Zeabur 預設模板會塞 `PORT` / `PASSWORD`，把它們**刪掉**讓 app fallback；
   內網 port 設成 **8080**（對上 Dockerfile EXPOSE）。
4. 拿到服務網址，MCP endpoint 是 `https://<your-app>.zeabur.app/mcp`，Transport：**STREAMABLE_HTTP**。
5. **Operit → 設定 → MCP**，加上這個 server URL，**開新對話**（MCP 工具是 conversation-scoped）。

### 環境變數

| Name | 必填 | 說明 |
|---|---|---|
| `ELEVENLABS_API_KEY` | ✅ | ElevenLabs API Key |
| `ELEVENLABS_VOICE_ID` | ✅ | 選妃選中的音色 Voice ID |
| `ELEVENLABS_MODEL_ID` | | 預設 `eleven_multilingual_v2`（中文必須 multilingual） |
| `ELEVENLABS_OUTPUT_FORMAT` | | 預設 `mp3_44100_128` |
| `ELEVENLABS_STABILITY` | | 預設 `0.5` |
| `ELEVENLABS_SIMILARITY_BOOST` | | 預設 `0.75` |
| `GITHUB_TOKEN` | ✅ | Contents: Read+Write，需含 cheche-voice-mcp |
| `GITHUB_OWNER` | | 預設 `cheche20250831-alt` |
| `GITHUB_REPO` | | 預設 `cheche-voice-mcp`（必須 public） |
| `GITHUB_AUDIO_DIR` | | 預設 `generated_audio` |

## 踩過的坑（繼承自 chechewolf-mcp 2026-05-20）

- Zeabur 預設 `PORT=${WEB_PORT}` 字面字串 → 刪掉它，讓 app fallback 8000，內網 port 設 8080。
- DNS rebinding 防護要關（已在 server.py 內處理）。
- `stateless_http=True` 讓簡單 client 不會「tool not found」。
- Operit 加 MCP 後要**開新對話**才看得到工具。
- AI 拿 dict 容易腦補不 emit URL → tool 回傳強硬指令字串。

## 本機測試

```bash
pip install -r requirements.txt
cp .env.example .env   # 填好 ELEVENLABS_* 與 GITHUB_*
# 載入 .env 後跑（Windows PowerShell 自行設 $env:）
python server.py
```
