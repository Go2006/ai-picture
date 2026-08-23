"""
FFmpeg 视频处理服务 —— 抽帧、剪辑、拼接。
"""
import os
import subprocess
import asyncio
from config import OUTPUT_DIR, FRAME_EXTRACT_INTERVAL

# 强制使用系统完整版 FFmpeg（Trae IDE 自带精简版缺少 AAC/concat 等关键功能）
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"


async def extract_frames(video_path: str) -> list[str]:
    """
    从视频中每隔 N 秒抽一帧图片，用于 AI 画面分析。
    返回帧图片路径列表。
    """
    output_dir = os.path.join(OUTPUT_DIR, "frames", _video_id(video_path))
    os.makedirs(output_dir, exist_ok=True)

    # 先获取视频时长
    duration = await _get_video_duration(video_path)

    frame_paths = []
    for t in range(0, int(duration), FRAME_EXTRACT_INTERVAL):
        output_path = os.path.join(output_dir, f"frame_{t:04d}.jpg")
        if os.path.exists(output_path):
            frame_paths.append(output_path)
            continue

        cmd = [
            FFMPEG, "-y",
            "-ss", str(t),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_path,
        ]
        await _run_ffmpeg(cmd)
        if os.path.exists(output_path):
            frame_paths.append(output_path)

    return frame_paths


async def compose_video(
    video_path: str,
    narration_audio: str,
    segments: list[dict],
    total_duration: float | None = None,
) -> str:
    """
    根据旁白分段，从素材视频中截取对应时长的画面，拼接成片。
    这是最核心的剪辑逻辑。

    segments: [{"text": "...", "duration": 3.5, "start_time": 0}, ...]
      start_time: 素材视频中的起始时间（秒），不填则自动截取

    total_duration: 目标成片时长（秒）。传入时：
      1. 画面总长不够 → 最后一段自动延长（素材自动循环补足）；
      2. 最终用 -t 精确截到该时长，保证成片视频与旁白语音完全对齐。
    """
    output_path = os.path.join(OUTPUT_DIR, f"final_{_video_id(video_path)}.mp4")
    concat_file = os.path.join(OUTPUT_DIR, "concat_list.txt")

    clip_paths = []
    total_clip_dur = 0.0
    for i, seg in enumerate(segments):
        dur = seg.get("duration", 3)
        start = seg.get("start_time", i * dur)  # 默认按顺序往后取
        total_clip_dur += dur
        clip_paths.append((await _cut_clip(video_path, start, dur, i), dur))

    # 画面总长不够目标时长 → 最后一段延长补足（-stream_loop 会自动循环素材）
    if total_duration and total_clip_dur < total_duration and clip_paths:
        last_path, last_dur = clip_paths[-1]
        if last_path:
            os.remove(last_path)  # 重新截取最后一段
        extend_dur = last_dur + (total_duration - total_clip_dur)
        i = len(clip_paths) - 1
        last_seg = segments[-1]
        new_path = await _cut_clip(
            video_path, last_seg.get("start_time", 0), extend_dur, i)
        clip_paths[-1] = (new_path, extend_dur)
        print(f"[FFmpeg] 最后一段延长至 {extend_dur:.1f}s 以补足画面时长")

    valid = [cp for cp, _ in clip_paths if cp]
    if not valid:
        return ""

    # 拼接所有片段（用绝对路径，避免 FFmpeg concat 路径解析问题）
    with open(concat_file, "w") as f:
        for cp in valid:
            f.write(f"file '{os.path.abspath(cp)}'\n")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-i", narration_audio,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "30",
        "-c:a", "aac",
        "-b:a", "48k",          # 旁白语音低码率即可
        "-movflags", "+faststart",  # 浏览器快速播放
    ]
    if total_duration:
        # 精确截到旁白时长，保证视频与语音完全对齐
        cmd += ["-t", f"{total_duration:.3f}"]
    else:
        cmd += ["-shortest"]
    cmd.append(output_path)
    await _run_ffmpeg(cmd)

    # 清理临时片段
    for cp, _ in clip_paths:
        os.remove(cp) if cp and os.path.exists(cp) else None

    if not os.path.exists(output_path):
        return ""

    if total_duration:
        actual = await _get_video_duration(output_path)
        if abs(actual - total_duration) > 0.5:
            print(f"[FFmpeg] 提示: 成片时长 {actual:.2f}s 与目标 {total_duration:.2f}s 偏差较大")

    return output_path


async def _cut_clip(video_path: str, start: float, dur: float, index: int) -> str:
    """从素材视频截取一段画面（素材不够时自动循环），返回片段文件路径。"""
    clip_path = os.path.join(OUTPUT_DIR, f"clip_{index:03d}.mp4")
    cmd = [
        FFMPEG, "-y",
        "-stream_loop", "-1",       # 素材不够长时自动循环
        "-ss", str(start),
        "-i", video_path,
        "-t", f"{dur:.3f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "33",
        "-vf", "scale=-2:480",       # 限制高度 480p，宽度按比例
        "-an",  # 去掉原视频声音
        clip_path,
    ]
    await _run_ffmpeg(cmd)
    return clip_path if os.path.exists(clip_path) else ""


# ============================================
# 工具函数
# ============================================
async def _run_ffmpeg(cmd: list[str], timeout: int = 120):
    """异步运行 FFmpeg 命令"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            err_msg = stderr.decode()[-500:] if stderr else "no stderr"
            print(f"[FFmpeg ERROR] returncode={proc.returncode}")
            print(f"[FFmpeg CMD]  {' '.join(cmd)}")
            print(f"[FFmpeg STDERR] {err_msg}")
    except asyncio.TimeoutError:
        proc.kill()
        raise


async def _get_video_duration(filepath: str) -> float:
    """获取视频时长"""
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
         filepath],
        capture_output=True, text=True, timeout=10,
    )
    return float(result.stdout.strip()) if result.stdout.strip() else 0


def _video_id(path: str) -> str:
    """从文件路径提取简短 ID"""
    return os.path.splitext(os.path.basename(path))[0]
