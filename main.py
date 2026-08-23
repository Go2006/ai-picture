"""
FastAPI 主入口 —— 启动: python main.py
"""
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import UPLOAD_DIR, OUTPUT_DIR

from routers import upload, narration, edit

# --- 创建应用 ---
app = FastAPI(
    title="AI 旁白剪辑",
    description="上传视频 → AI 生成旁白 → TTS 配音 → 自动剪辑成片",
    version="0.1.0",
)

# 跨域（前端开发时需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(upload.router)
app.include_router(narration.router)
app.include_router(edit.router)

# 静态文件（前端页面）
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- 首页 ---
@app.get("/")
async def index():
    """重定向到前端页面"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")


# --- 启动 ---
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # 代码改动自动重启
    )
