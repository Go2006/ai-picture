"""
剪辑路由 —— 旁白 + 画面 合成最终视频。
"""
import os
import re
from fastapi import APIRouter
from fastapi.responses import FileResponse
from models.schemas import EditVideoRequest, EditResult
from services.ffmpeg_service import compose_video, extract_frames, _get_video_duration
from services.ai_service import analyze_video_frames
from config import UPLOAD_DIR

router = APIRouter(prefix="/api/edit", tags=["剪辑"])


@router.post("/compose", response_model=EditResult)
async def compose(req: EditVideoRequest):
    """
    把素材视频 + 旁白音频 + 分段信息 合成最终视频。
    自动剪辑与语音对齐：
      1. 每个分段的画面时长按旁白实际节奏分配（优先用 TTS sidecar 的句级时间点，
         没有则按 AI 预估时长比例缩放，使分段总长 == 音频实际时长）；
      2. 成片用 -t 精确截到旁白音频时长，保证视频与语音完全对齐；
      3. 素材不够长时自动循环使用。
    """
    # 找到素材文件
    video_path = _find_video(req.video_id)

    # 获取素材视频总时长
    video_duration = await _get_video_duration(video_path)

    # 使用前端传来的分段，没有则退化为一段
    segments = req.segments if req.segments else [{"text": req.narration_text, "duration": 10}]

    # 旁白音频实际时长
    from services.tts_service import _get_audio_duration, get_sentence_timings
    audio_duration = await _get_audio_duration(req.audio_path)

    # 每个分段的画面时长：优先按句级时间点（TTS sidecar），否则按预估比例缩放
    seg_durs = _segment_durations(segments, get_sentence_timings(req.audio_path),
                                  audio_duration)

    # 为每个分段分配画面起始时间（素材不够就循环）
    current_time = 0.0
    timed_segments = []
    for seg, dur in zip(segments, seg_durs):
        timed_segments.append({
            "text": seg.get("text", ""),
            "duration": round(dur, 3),
            "start_time": current_time % video_duration if video_duration > 0 else 0,
        })
        current_time += dur

    result_path = await compose_video(
        video_path=video_path,
        narration_audio=req.audio_path,
        segments=timed_segments,
        total_duration=audio_duration,   # 成片精确截到旁白时长
    )

    if not result_path:
        return EditResult(output_video_path="", duration=0)

    final_duration = await _get_video_duration(result_path)
    return EditResult(
        output_video_path=result_path,
        duration=final_duration,
        audio_duration=round(audio_duration, 2),
        video_duration=round(final_duration, 2),
    )


def _segment_durations(segments: list[dict], timings: list[dict] | None,
                       audio_duration: float) -> list[float]:
    """
    为每个分段计算画面时长，使分段总长 == 音频实际时长。
    timings: TTS sidecar 的句级时间点 [{"text","start","end"}]，可用时按句精确对齐：
      每段画面 = 本段首句起点 → 下一段首句起点（句间停顿归属前一段画面），
      切点与旁白停顿完全同步。
    """
    # 方式1: 句级时间点精确对齐
    if timings:
        timing_map = {t["text"]: t for t in timings}
        starts = []
        for seg in segments:
            sentences = [s for s in re.split(r"(?<=[。！？!?…])", seg.get("text", "")) if s]
            if not sentences or sentences[0] not in timing_map:
                starts = []   # 文本对不上 → 放弃该方式
                break
            starts.append(timing_map[sentences[0]]["start"])
        if len(starts) == len(segments):
            durs = [(starts[i + 1] if i + 1 < len(starts) else audio_duration) - s
                    for i, s in enumerate(starts)]
            durs[-1] += audio_duration - sum(durs)  # 修正舍入误差
            return [max(0.1, d) for d in durs]

    # 方式2: 按 AI 预估时长比例缩放，总长 == 音频实际时长
    ests = [seg.get("duration", 3) for seg in segments]
    total_est = sum(ests)
    if total_est <= 0:
        return [audio_duration / len(segments)] * len(segments)
    return [audio_duration * e / total_est for e in ests]


@router.get("/download/{file_path:path}")
async def download_file(file_path: str):
    """下载/播放 生成的音视频文件（支持子目录）"""
    from config import OUTPUT_DIR
    path = os.path.join(OUTPUT_DIR, file_path)
    if os.path.exists(path):
        # 根据后缀判断类型
        media_type = "audio/mpeg" if path.endswith(".mp3") else "video/mp4"
        return FileResponse(path, media_type=media_type)
    return {"error": "文件不存在", "path": path}


@router.get("/videos")
async def list_generated_videos():
    """
    列出所有已生成的视频文件，供前端展示和播放。
    """
    from config import OUTPUT_DIR
    import os
    import time

    videos = []
    for f in sorted(os.listdir(OUTPUT_DIR), reverse=True):
        if f.startswith("final_") and f.endswith(".mp4"):
            path = os.path.join(OUTPUT_DIR, f)
            stat = os.stat(path)
            # 获取视频时长
            try:
                from services.tts_service import _get_audio_duration
                duration = await _get_audio_duration(path)
            except Exception:
                duration = 0
            videos.append({
                "filename": f,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "duration": round(duration, 1),
                "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            })

    return {"videos": videos}


@router.get("/analyze/{video_id}")
async def analyze_video(video_id: str):
    """
    分析视频画面内容 —— 抽帧 → AI 识别 → 返回描述。
    这个描述可以喂给旁白生成接口。
    """
    video_path = _find_video(video_id)
    frames = await extract_frames(video_path)
    description = await analyze_video_frames(frames)

    return {
        "video_id": video_id,
        "frames_extracted": len(frames),
        "description": description,
    }


def _find_video(video_id: str) -> str:
    """根据 video_id 找素材文件"""
    for f in os.listdir(UPLOAD_DIR):
        if f.startswith(video_id):
            return os.path.join(UPLOAD_DIR, f)
    raise FileNotFoundError(f"视频不存在: {video_id}")
