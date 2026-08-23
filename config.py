"""
全局配置 —— 所有可调参数集中在这里，方便修改。
"""
import os
from dotenv import load_dotenv

load_dotenv()  # 加载 .env 文件

# --- 项目路径 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(BASE_DIR, "outputs"))

# --- AI 配置 ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")

# --- Redis ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- 上传限制 ---
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB

# --- 视频处理 ---
FRAME_EXTRACT_INTERVAL = 2  # 每隔 N 秒抽一帧做画面分析
