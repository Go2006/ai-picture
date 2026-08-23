"""
上传路由 —— 视频文件上传和管理。
"""
import os
import uuid
import aiofiles
from fastapi import APIRouter, UploadFile, File
from config import UPLOAD_DIR
from services.ffmpeg_service import _get_video_duration

router = APIRouter(prefix="/api/upload", tags=["上传"])

os.makedirs(UPLOAD_DIR, exist_ok=True)


async def _probe_duration(filepath: str) -> float:
    """探测视频时长（秒），失败返回 0"""
    try:
        return round(await _get_video_duration(filepath), 1)
    except Exception:
        return 0.0


@router.post("/video")
async def upload_video(file: UploadFile = File(...)):
    """
    上传视频文件。
    返回 video_id，后续操作都用这个 ID。
    """
    # 生成唯一 ID
    video_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    filename = f"{video_id}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    # 流式写入，支持大文件
    async with aiofiles.open(filepath, "wb") as f:
        while chunk := await file.read(8 * 1024 * 1024):  # 8MB 块
            await f.write(chunk)

    return {
        "video_id": video_id,
        "filename": file.filename,
        "filepath": filepath,
        "size_mb": round(os.path.getsize(filepath) / 1024 / 1024, 2),
        "duration": await _probe_duration(filepath),   # 视频时长（秒），前端用于匹配旁白时长
    }


@router.get("/videos")
async def list_videos():
    """列出已上传的所有视频"""
    videos = []
    for f in os.listdir(UPLOAD_DIR):
        if f.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
            path = os.path.join(UPLOAD_DIR, f)
            videos.append({
                "video_id": os.path.splitext(f)[0],
                "filename": f,
                "size_mb": round(os.path.getsize(path) / 1024 / 1024, 2),
                "duration": await _probe_duration(path),
            })
    return {"videos": videos}
