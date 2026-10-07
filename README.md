# AI 旁白剪辑

上传视频 → AI 生成旁白文案 → TTS 语音合成 → 自动剪辑成片

---

## 项目结构

```
projecte/
├── main.py                  # FastAPI 主入口
├── start.sh                 # 一键启动脚本（推荐使用）
├── config.py                # 全局配置（API Key、路径等）
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板（不含真实 Key，可安全提交）
│
├── models/
│   └── schemas.py           # 数据模型（请求/响应格式）
│
├── services/
│   ├── ai_service.py        # AI 服务（调 Claude/OpenAI 生成文案+分析画面）
│   ├── tts_service.py       # TTS 语音合成（Edge-TTS 免费）
│   └── ffmpeg_service.py    # FFmpeg 视频处理（抽帧、剪辑、拼接）
│
├── routers/
│   ├── upload.py            # 上传路由
│   ├── narration.py         # 旁白路由（生成文案 + 合成语音）
│   └── edit.py              # 剪辑路由（合成视频 + 画面分析）
│
├── static/
│   └── index.html           # 前端页面（4 步操作流程）
│
├── uploads/                 # 上传的视频素材（运行时生成，不提交）
└── outputs/                 # 生成的音频和视频（运行时生成，不提交）
```

## 快速开始

### 方式一：一键脚本（推荐）

前提：装好 **Python 3.10+** 和 **FFmpeg**（安装命令见下文）。

```bash
bash start.sh
```

脚本会自动完成：环境检查 → 首次运行创建虚拟环境并安装依赖 → 生成 `.env` → 启动服务。
之后每次启动同样一条命令，依赖已装好时秒开。

### 方式二：手动启动

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # 可选：编辑填入 API Key
./venv/bin/python main.py
```

浏览器打开 `http://localhost:8000`

### 安装 FFmpeg（视频处理必需）

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

## 配置 API Key（可选，不配置也能跑演示模式）

> ⚠️ **Key 属于个人凭证，不要提交到仓库。** `.env` 已被 `.gitignore` 忽略，请只填在自己本地的 `.env` 里，不要填进 `.env.example` 或代码中——否则所有 clone 仓库的人都会拿到并使用你的 Key（费用算你的）。

`start.sh` 首次运行会自动从 `.env.example` 复制生成 `.env`，编辑它填入**你自己的** Key：

**方式 1：Claude API**（从 https://console.anthropic.com 获取）

```bash
ANTHROPIC_API_KEY=sk-ant-xxxx
AI_MODEL=claude-sonnet-4-20250514
```

**方式 2：OpenAI 兼容接口**（DeepSeek / 千问等，每人用自己的 Key，互不影响）

```bash
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.deepseek.com
AI_MODEL=deepseek-chat
```

两种方式填一种即可，都填了优先用 Claude。

**不配置也能用**：项目内置了演示数据，不接 AI API 也能跑通整个流程（上传 → 生成 → 配音 → 剪辑）。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload/video` | 上传视频 |
| GET  | `/api/upload/videos` | 列出已上传视频 |
| POST | `/api/narration/generate` | 生成旁白文案 |
| POST | `/api/narration/synthesize` | 旁白文字 → 语音 |
| GET  | `/api/edit/analyze/{video_id}` | 分析视频画面 |
| POST | `/api/edit/compose` | 剪辑合成最终视频 |
| GET  | `/api/edit/download/{filename}` | 下载生成的视频 |

## 自定义指南

- **换 AI 模型**：改 `config.py` 里的 `AI_MODEL`
- **改旁白 Prompt**：编辑 `services/ai_service.py` 里的 `NARRATION_SYSTEM_PROMPT`
- **换 TTS**：修改 `services/tts_service.py`，换成商业 TTS API
- **改剪辑逻辑**：编辑 `services/ffmpeg_service.py` 的 `compose_video`
- **加数据库**：在 `config.py` 加 DB 连接，在 `models/` 加 ORM 模型
