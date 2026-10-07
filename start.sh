#!/usr/bin/env bash
# ============================================
# 一键启动脚本 —— AI 旁白剪辑
# 用法: bash start.sh   (或 ./start.sh)
# 自动完成: 环境检查 → 创建 venv → 装依赖 → 配置 .env → 启动服务
# ============================================
set -e

cd "$(dirname "$0")"   # 切到脚本所在目录（从任何地方执行都行）

# --- 1. 检查 Python ---
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi
echo "✅ Python: $(python3 --version)"

# --- 2. 检查 ffmpeg（视频处理必需）---
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "❌ 未找到 ffmpeg（视频处理必需），请先安装："
    echo "   Ubuntu/Debian: sudo apt install ffmpeg"
    echo "   macOS:         brew install ffmpeg"
    exit 1
fi
echo "✅ ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"

# --- 3. 创建虚拟环境并安装依赖（仅首次，或依赖有更新时）---
if [ ! -d venv ]; then
    echo "⏳ 首次运行，创建虚拟环境并安装依赖（约 1-2 分钟）..."
    python3 -m venv venv
    ./venv/bin/pip install -q --upgrade pip
    ./venv/bin/pip install -q -r requirements.txt
    touch venv/.deps_installed
    echo "✅ 依赖安装完成"
elif [ requirements.txt -nt venv/.deps_installed ]; then
    echo "⏳ requirements.txt 有更新，重新安装依赖..."
    ./venv/bin/pip install -q -r requirements.txt
    touch venv/.deps_installed
fi

# --- 4. 配置 .env（仅首次）---
if [ ! -f .env ]; then
    cp .env.example .env
    echo "ℹ️  已从 .env.example 生成 .env"
    echo "   如需 AI 真实生成（非演示数据），请编辑 .env 填入 API Key"
fi

# --- 5. 创建运行时目录 ---
mkdir -p uploads outputs

# --- 6. 启动 ---
echo "🚀 启动服务: http://localhost:8000"
./venv/bin/python main.py
