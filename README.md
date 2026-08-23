# AI 旁白剪辑

上传视频 → AI 生成旁白文案 → TTS 语音合成 → 自动剪辑成片

---

## 项目结构

```
projecte/
├── main.py                  # FastAPI 主入口，启动: python main.py
├── config.py                # 全局配置（API Key、路径等）
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板 → 复制为 .env
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
├── uploads/                 # 上传的视频素材
└── outputs/                 # 生成的音频和视频
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 装 FFmpeg

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

### 3. 配置 API Key（可选，不配置也能跑演示模式）

```bash
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY 或 OPENAI_API_KEY
```

**不配置也能用**：项目内置了演示数据，不接 AI API 也能跑通整个流程。

### 4. 启动

```bash
cd /data/projecte
python main.py
```

浏览器打开 `http://localhost:8000`

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload/video` | 上传视频 |
| GET  | `/api/upload/videos` | 列出已上传视频 |
| POST | `/api/narration/generate` | 生成旁白文案 |
| POST | `/api/narration/synthesize` | 旁白文字 → 语音（可传 `target_duration` 自动匹配视频时长） |
| GET  | `/api/edit/analyze/{video_id}` | 分析视频画面 |
| POST | `/api/edit/compose` | 剪辑合成最终视频（自动剪辑到与旁白时长完全对齐） |
| GET  | `/api/edit/download/{filename}` | 下载生成的视频 |

## 自定义指南

- **换 AI 模型**：改 `config.py` 里的 `AI_MODEL`
- **改旁白 Prompt**：编辑 `services/ai_service.py` 里的 `NARRATION_SYSTEM_PROMPT`
- **换 TTS**：修改 `services/tts_service.py`，换成商业 TTS API
- **改剪辑逻辑**：编辑 `services/ffmpeg_service.py` 的 `compose_video`
- **加数据库**：在 `config.py` 加 DB 连接，在 `models/` 加 ORM 模型

## 时长对齐机制

**语音匹配视频时长**（`synthesize` 的 `target_duration`，前端勾选"⏱ 自动匹配视频时长"）：
1. 按请求语速合成并测量时长；
2. 偏差超容差时按 `实际/目标` 比例自动调速重合成（最多 2 轮），保留最接近的一次；
3. 放到最慢仍不够长时，逐句合成并在句间插入停顿，精确补到目标时长。

**视频自动剪辑匹配语音**（`/api/edit/compose` 自动完成）：
1. 每个分段的画面时长按旁白实际节奏分配——有句级时间点（sidecar）时精确到"下一句起点"切点，否则按 AI 预估比例缩放；
2. 画面不够时最后一段自动延长（素材循环补足）；
3. 成片用 `-t` 精确截到旁白音频时长，视频与语音完全对齐。
