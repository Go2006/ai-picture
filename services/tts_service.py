"""
TTS 语音合成 —— 把旁白文字转成音频。
使用 Edge-TTS (免费、中文效果好)，也可以通过 API 调商业服务。

拟人化增强：自定义 SSML 直连 Edge WebSocket，加入自然停顿、逐句语调变化和情感风格；
SSML 路径失败时自动降级到普通 edge_tts.Communicate 路径，再失败降级静音占位。

时长匹配：synthesize(..., target_duration=视频时长) 自动调速逼近目标，
放到最慢仍不够时逐句合成 + 句间插入停顿精确补足；句级时间点写入 sidecar 供剪辑对齐。
"""
import asyncio
import hashlib
import os
import re
import subprocess
from config import OUTPUT_DIR

# 强制使用系统完整版 FFmpeg/FFprobe（Trae IDE 自带精简版缺少关键功能）
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"

# TTS 重试次数
MAX_RETRIES = 3

# ============================================
# 时长匹配参数（把语音时长对齐到视频时长）
# ============================================
MAX_RATE_ADJUST = 2          # 调速逼近目标时长的最大额外尝试次数
SPEED_MIN, SPEED_MAX = 0.5, 2.0   # Edge 语速可用范围（约 -50% ~ +100%）
DURATION_TOL = 0.5           # 匹配容差基础值（秒），实际取 max(0.3, 3% * 目标时长)

# ============================================
# SSML 拟人化参数
# ============================================
SSML_CACHE_VERSION = "ssml-v2"    # SSML 算法版本号；算法改动时 +1，旧缓存自动失效

# 确定性的韵律微调模式（不用 random，保证缓存可复现）
PITCH_PATTERN = [0, 6, -5, 8, -8, 10, -6, 5, -10, 8]   # Hz，句间音高浮动
RATE_PATTERN = [0, 3, -3, 4, -4, 5, -2, 2, -5, 4]      # 百分比，句间语速浮动

# 情感 → 基础音高/语速偏移 (Hz, %)。
# Edge 免费端点不支持 <break> 和 mstts:express-as（实测会被服务端拒绝），
# 所以用音高+语速塑造语气：温柔=低缓、活泼=高快、严肃=低慢、聊天=轻快。
EMOTION_PROFILE = {
    "natural": (0, 0),
    "gentle": (-8, -8),
    "newscast": (0, 5),
    "cheerful": (10, 10),
    "serious": (-12, -8),
    "lyrical": (-6, -12),
    "chat": (8, 8),
}


def _prosody_for(sentence: str, idx: int) -> tuple:
    """给一句话选确定性的音高/语速偏移。疑问句上扬稍快，感叹句更高更快。"""
    stripped = sentence.rstrip()
    if stripped.endswith(("？", "?")):
        return 12, 5       # 疑问：音高 +12Hz、语速 +5%
    if stripped.endswith(("！", "!")):
        return 6, 10       # 感叹：音高 +6Hz、语速 +10%
    h = int(hashlib.md5(sentence.encode("utf-8")).hexdigest()[:4], 16)
    return (PITCH_PATTERN[h % len(PITCH_PATTERN)],
            RATE_PATTERN[(h + idx) % len(RATE_PATTERN)])


def _build_prosody_body(text: str, base_pitch: int, base_rate: int) -> str:
    """按句拆分 → 每句包一层 <prosody>（基础语气 + 确定性音高/语速微调）。
    停顿靠 Edge 内置的标点自然停顿（该端点不支持 <break> 标签）。"""
    from xml.sax.saxutils import escape

    sentences = [s for s in re.split(r"(?<=[。！？!?…])", text) if s]
    parts = []
    for idx, sent in enumerate(sentences):
        pitch_delta, rate_delta = _prosody_for(sent, idx)
        pitch = max(-50, min(50, base_pitch + pitch_delta))
        rate = max(-50, min(50, base_rate + rate_delta))
        parts.append(f"<prosody pitch='{pitch:+d}Hz' rate='{rate:+d}%'>{escape(sent)}</prosody>")
    return "".join(parts)


def _build_ssml(text: str, voice: str, speed: float, emotion: str = "natural") -> str:
    """把纯文本构造成带韵律/语气变化的完整 SSML（确定性，可缓存复现）。"""
    from edge_tts.communicate import remove_incompatible_characters

    cleaned = remove_incompatible_characters(text or "")
    cleaned = re.sub(r"…{2,}", "…", cleaned)      # 连续省略号合并
    base_rate = int((speed - 1) * 100)            # 与现有速率映射一致: 1.0→0

    base_pitch, emo_rate = EMOTION_PROFILE.get(emotion, EMOTION_PROFILE["natural"])
    total_base_rate = max(-50, min(50, base_rate + emo_rate))

    body = _build_prosody_body(cleaned, base_pitch, total_base_rate)

    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>"
        f"<voice name='{voice}'>{body}</voice></speak>"
    )


async def _synth_ssml(ssml: str, output_path: str) -> None:
    """直接连 Edge WebSocket 发送自定义 SSML（Communicate 会转义 SSML 标签，不能复用），
    把收到的 MP3 分片按序写入 output_path。失败抛异常，由上层降级。"""
    import aiohttp
    from edge_tts.communicate import (
        _SSL_CTX, connect_id, date_to_string, get_headers_and_data,
        ssml_headers_plus_data,
    )
    from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL
    from edge_tts.drm import DRM

    url = (f"{WSS_URL}&ConnectionId={connect_id()}"
           f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
           f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}")
    timeout = aiohttp.ClientTimeout(total=120, sock_connect=15)

    audio_chunks = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(
            url,
            compress=15,
            headers=DRM.headers_with_muid(WSS_HEADERS),
            ssl=_SSL_CTX,
        ) as ws:
            # 1) speech.config 帧（与 edge_tts 一致: 24kHz 48kbps 单声道 MP3，关掉边界事件）
            await ws.send_str(
                f"X-Timestamp:{date_to_string()}\r\n"
                "Content-Type:application/json; charset=utf-8\r\n"
                "Path:speech.config\r\n\r\n"
                '{"context":{"synthesis":{"audio":{"metadataoptions":{'
                '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"},'
                '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n'
            )
            # 2) SSML 帧
            await ws.send_str(ssml_headers_plus_data(connect_id(), date_to_string(), ssml))

            # 3) 接收帧：BINARY=音频分片，TEXT=turn.end 表示结束
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    if len(msg.data) < 2:
                        raise RuntimeError("二进制帧太短，缺少头部长度")
                    header_len = int.from_bytes(msg.data[:2], "big")  # 2 字节大端帧头长度
                    if header_len > len(msg.data):
                        raise RuntimeError("帧头长度非法")
                    params, data = get_headers_and_data(msg.data, header_len)
                    if params.get(b"Path") != b"audio":
                        raise RuntimeError("收到非音频二进制帧")
                    if data:                      # 末尾空帧（无 Content-Type）直接跳过
                        audio_chunks.append(data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    encoded = msg.data.encode("utf-8")
                    pos = encoded.find(b"\r\n\r\n")
                    if pos < 0:
                        raise RuntimeError("文本帧缺少头部")
                    params, _ = get_headers_and_data(encoded, pos)
                    if params.get(b"Path") == b"turn.end":
                        break
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    raise RuntimeError(f"WebSocket 连接异常: {msg.data}")

    if not audio_chunks:
        raise RuntimeError("未收到任何音频数据")

    with open(output_path, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)


async def _synth_plain(text: str, voice: str, speed: float, output_path: str) -> None:
    """普通合成路径：edge_tts.Communicate 原样文本（无 SSML 增强），作为降级方案。"""
    import edge_tts

    rate_str = f"{int((speed - 1) * 100):+d}%"
    communicate = edge_tts.Communicate(text, voice, rate=rate_str)
    await communicate.save(output_path)


async def _synth_with_fallbacks(text: str, voice: str, speed: float, emotion: str,
                                 output_path: str) -> float | None:
    """
    合成一次（SSML → 普通路径 → 静音占位 降级），成功返回音频时长，全部失败返回 None。
    """
    use_ssml = True
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if use_ssml:
                ssml = _build_ssml(text, voice, speed, emotion)
                await _synth_ssml(ssml, output_path)
            else:
                await _synth_plain(text, voice, speed, output_path)

            # 验证生成的文件
            if os.path.getsize(output_path) == 0:
                raise RuntimeError("生成的音频文件为空 (0 字节)")

            if not await _has_audio_content(output_path):
                raise RuntimeError("生成的音频文件是静音的")

            duration = await _get_audio_duration(output_path)
            print(f"[TTS] 合成成功 (尝试 {attempt}/{MAX_RETRIES}, SSML={use_ssml}): "
                  f"{output_path} ({duration:.1f}s)")
            return duration

        except ImportError:
            if use_ssml:
                # edge-tts 缺失或内部接口变化 → 本次起改用普通路径
                print("[TTS] SSML 路径不可用（edge-tts 缺失或内部接口变化），改用普通路径")
                use_ssml = False
                continue
            print("[TTS] edge-tts 未安装")
            return None
        except Exception as e:
            last_error = e
            print(f"[TTS] 尝试 {attempt}/{MAX_RETRIES} 失败 (SSML={use_ssml}): {e}")
            if use_ssml:
                # SSML 被服务端拒绝（不支持的 style 等）→ 下次尝试走普通路径
                use_ssml = False
                continue
            if attempt < MAX_RETRIES:
                await asyncio.sleep(1)  # 重试前等 1 秒

    # 全部重试失败
    print(f"[TTS] 全部 {MAX_RETRIES} 次尝试失败，最后错误: {last_error}")
    return None


async def synthesize(text: str, voice: str = "zh-CN-XiaoxiaoNeural", speed: float = 1.0,
                     emotion: str = "natural", target_duration: float | None = None) -> dict:
    """
    合成语音，返回音频文件路径和时长。

    音色推荐 (中文):
      zh-CN-XiaoxiaoNeural  - 女声，活泼 (默认)
      zh-CN-YunxiNeural     - 男声，沉稳
      zh-CN-XiaoyiNeural    - 女声，温柔

    情感 (emotion): natural/gentle/newscast/cheerful/serious/lyrical/chat，
    通过音高+语速塑造语气（Edge 免费端点不支持 mstts 情感标签），对全部音色生效。

    target_duration: 目标时长（秒，如视频时长）。给定后自动匹配：
      1. 按请求语速合成 → 测时长；
      2. 偏差超容差时按 实际/目标 比例调速重合成（最多 2 轮），保留最接近的一次；
      3. 放到最慢仍不够长时，改为逐句合成 + 句间插入停顿，精确补到目标时长。
    匹配结果写 sidecar JSON（audio_path + ".json"），供剪辑时按句精确对齐。
    """

    output_dir = os.path.join(OUTPUT_DIR, "audio")
    os.makedirs(output_dir, exist_ok=True)

    # 使用稳定的 MD5 哈希做缓存 key；版本号前缀 + 情感 + 目标时长，参数一变旧缓存自动失效
    cache_key = f"{SSML_CACHE_VERSION}|{text}|{voice}|{speed}|{emotion}|{target_duration}"
    filename = f"narration_{_stable_hash(cache_key)}.mp3"
    output_path = os.path.join(output_dir, filename)
    sidecar_path = output_path + ".json"

    # 如果已经合成过（且音频非空、有声音），直接返回缓存
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        if await _has_audio_content(output_path):
            duration = await _get_audio_duration(output_path)
            meta = _read_sidecar(sidecar_path) or {}
            return {
                "audio_path": output_path,
                "duration": duration,
                "target_duration": meta.get("target_duration", target_duration),
                "matched": meta.get("matched"),
                "effective_speed": meta.get("effective_speed", speed),
            }
        else:
            # 缓存是静音的，删掉重新合成
            print(f"[TTS] 缓存文件是静音的，删除并重新合成: {output_path}")
            os.remove(output_path)

    # --- 第 1 轮：按请求语速合成 ---
    best_dur = await _synth_with_fallbacks(text, voice, speed, emotion, output_path)
    if best_dur is None:
        return await _create_silent_audio(text, output_path, target_duration)

    best_speed = speed
    matched = None
    timings = None
    tol = DURATION_TOL

    # --- 第 2 轮：调速逼近目标时长 ---
    if target_duration and target_duration > 0:
        tol = max(0.3, 0.03 * target_duration)
        attempts = [(best_dur, speed, output_path)]  # (时长, 语速, 文件)
        cur_speed = speed

        for _ in range(MAX_RATE_ADJUST):
            cur_dur = attempts[-1][0]
            if abs(cur_dur - target_duration) <= tol:
                break
            # 时长 ≈ 1/语速，按比例反推新语速
            new_speed = max(SPEED_MIN, min(SPEED_MAX, cur_speed * cur_dur / target_duration))
            if abs(new_speed - cur_speed) < 0.02:
                break  # 已到语速边界，再试无意义
            cur_speed = new_speed

            tmp_path = f"{output_path}.adjust"
            dur = await _synth_with_fallbacks(text, voice, cur_speed, emotion, tmp_path)
            if dur is None:
                break
            attempts.append((dur, cur_speed, tmp_path))

        best = min(attempts, key=lambda a: abs(a[0] - target_duration))
        best_dur, best_speed, best_path = best
        if best_path != output_path:
            os.replace(best_path, output_path)

        # --- 第 3 轮：仍太短 → 逐句合成 + 句间停顿补足 ---
        if target_duration - best_dur > tol:
            print(f"[TTS] 语速已最慢仍短于目标 ({best_dur:.1f}s < {target_duration:.1f}s)，"
                  f"逐句合成并插入句间停顿补足")
            ok, timings = await _gap_fill_to_target(
                text, voice, best_speed, emotion, target_duration, output_path)
            if ok:
                best_dur = await _get_audio_duration(output_path)

        matched = abs(best_dur - target_duration) <= tol
        if not matched:
            print(f"[TTS] 时长匹配提示: 目标 {target_duration:.1f}s, 实际 {best_dur:.1f}s"
                  f"（{'已插入句间停顿仍不足' if best_dur < target_duration else '语速最快仍超时'}）")

    # 写 sidecar：剪辑时按句精确对齐用
    _write_sidecar(sidecar_path, {
        "text": text,
        "voice": voice,
        "speed": speed,
        "emotion": emotion,
        "target_duration": target_duration,
        "duration": best_dur,
        "matched": matched,
        "effective_speed": round(best_speed, 3),
        "sentence_timings": timings,   # 仅句间停顿路径有值，其余为 None
    })

    print(f"[TTS] 合成完成: {output_path} ({best_dur:.1f}s)"
          + (f", 目标 {target_duration:.1f}s, matched={matched}" if target_duration else ""))
    return {
        "audio_path": output_path,
        "duration": best_dur,
        "target_duration": target_duration,
        "matched": matched,
        "effective_speed": round(best_speed, 3),
    }


async def _synth_sentence_cached(sentence: str, voice: str, speed: float,
                                 emotion: str) -> str | None:
    """逐句合成（独立缓存），用于句间停顿补足。成功返回文件路径，失败返回 None。"""
    output_dir = os.path.join(OUTPUT_DIR, "audio")
    os.makedirs(output_dir, exist_ok=True)

    cache_key = f"{SSML_CACHE_VERSION}|sent|{sentence}|{voice}|{speed}|{emotion}"
    path = os.path.join(output_dir, f"sent_{_stable_hash(cache_key)}.mp3")

    if os.path.exists(path) and os.path.getsize(path) > 0 \
            and await _has_audio_content(path):
        return path

    try:
        await _synth_ssml(_build_ssml(sentence, voice, speed, emotion), path)
        if os.path.getsize(path) == 0 or not await _has_audio_content(path):
            raise RuntimeError("句子音频为空或静音")
        return path
    except Exception as e:
        print(f"[TTS] 逐句合成失败: {sentence[:20]}... ({e})")
        if os.path.exists(path):
            os.remove(path)
        return None


async def _gap_fill_to_target(text: str, voice: str, speed: float, emotion: str,
                              target: float, output_path: str):
    """逐句合成，句间插入静音停顿把总时长精确补到 target。
    返回 (成功?, [{"text","start","end"}, ...])。"""
    sentences = [s for s in re.split(r"(?<=[。！？!?…])", text) if s]
    if not sentences:
        return False, None

    # 逐句合成并测时长
    parts = []  # (路径, 时长, 文本)
    for sent in sentences:
        path = await _synth_sentence_cached(sent, voice, speed, emotion)
        if path is None:
            return False, None
        parts.append((path, await _get_audio_duration(path), sent))

    total = sum(d for _, d, _ in parts)
    gap_total = target - total
    if gap_total <= 0.05:
        return False, None  # 无需停顿（或停顿没有意义）

    # 停顿按相邻句平均长度加权分配，长句之后停得更久
    weights = [(len(parts[i][2]) + len(parts[i + 1][2])) / 2
               for i in range(len(parts) - 1)]
    gaps = [gap_total * w / sum(weights) for w in weights]
    gaps[-1] += gap_total - sum(gaps)  # 修正舍入误差，保证总和精确

    # 生成静音片段并拼接
    files, timings = [], []
    t = 0.0
    for i, (path, dur, sent) in enumerate(parts):
        files.append(path)
        timings.append({"text": sent, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
        if i < len(parts) - 1 and gaps[i] > 0.05:
            sil = await _make_silence(gaps[i], f"{output_path}.sil{i}.mp3")
            if sil:
                files.append(sil)
            t += gaps[i]

    ok = await _concat_mp3(files, output_path)
    if ok:
        # 补 0.05s 的 MP3 编码损耗，end 按最终实际时长修正
        final_dur = await _get_audio_duration(output_path)
        if timings and final_dur > 0:
            timings[-1]["end"] = round(final_dur, 3)
    return ok, (timings if ok else None)


async def _make_silence(duration: float, path: str) -> str | None:
    """生成一段静音 MP3（与 Edge 输出同参数: 24kHz 单声道 48kbps），成功返回路径。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{duration:.3f}",
            "-codec:a", "libmp3lame", "-b:a", "48k",
            path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        return path if os.path.exists(path) and os.path.getsize(path) > 0 else None
    except Exception as e:
        print(f"[TTS] 生成静音失败: {e}")
        return None


async def _concat_mp3(paths: list[str], output_path: str) -> bool:
    """用 concat demuxer 按顺序拼接 MP3 文件，返回是否成功。"""
    if not paths:
        return False
    list_file = f"{output_path}.concat.txt"
    with open(list_file, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-codec:a", "libmp3lame", "-b:a", "48k",
            output_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            print(f"[TTS] 音频拼接失败: {(stderr or b'').decode()[-300:]}")
    except Exception as e:
        print(f"[TTS] 音频拼接异常: {e}")
    finally:
        for p in [list_file] + [x for x in paths if ".sil" in x]:
            os.remove(p) if os.path.exists(p) else None

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


# ============================================
# sidecar 元数据（剪辑时按句精确对齐用）
# ============================================
def _write_sidecar(sidecar_path: str, meta: dict) -> None:
    try:
        import json
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[TTS] 写 sidecar 失败: {e}")


def _read_sidecar(sidecar_path: str) -> dict | None:
    try:
        import json
        with open(sidecar_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_sentence_timings(audio_path: str) -> list[dict] | None:
    """读取 sidecar 里的句级时间点 [{"text","start","end"}]。
    仅"句间停顿补足"路径有值；普通合成返回 None（剪辑时走比例分配兜底）。"""
    meta = _read_sidecar(audio_path + ".json")
    timings = meta.get("sentence_timings") if meta else None
    return timings or None


async def _get_audio_duration(filepath: str) -> float:
    """获取音频时长（秒）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            FFPROBE, "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if stdout.strip():
            return float(stdout.decode().strip())
        return 3.0
    except Exception:
        # 粗略估算：中文 1 字 ≈ 0.25 秒
        return 3.0


async def _has_audio_content(filepath: str) -> bool:
    """
    检查音频文件是否包含实际声音（非静音）。
    解码前 2 秒音频，检查是否有非零采样点。
    """
    try:
        # 用 FFmpeg 解码前 2 秒为原始 PCM（强制单声道，简化检测）
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y",
            "-i", filepath,
            "-t", "2",
            "-ac", "1",          # 强制单声道
            "-f", "s16le",
            "pipe:1",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

        if len(stdout) < 2:
            return False

        # 检查是否有非零采样点（每 100 个采样点检查一次以提高效率）
        import struct
        sample_count = len(stdout) // 2
        for i in range(0, sample_count, 100):
            val = struct.unpack_from('<h', stdout, i * 2)[0]
            if val != 0:
                return True
        return False
    except Exception:
        # 无法检测时默认认为有声音（避免误删）
        return True


async def _create_silent_audio(text: str, output_path: str,
                               target_duration: float | None = None) -> dict:
    """用 FFmpeg 创建静音占位音频 (TTS 不可用时的降级方案)"""
    # 有目标时长就直接用目标时长，否则按字数估算
    duration = target_duration if target_duration and target_duration > 0 \
        else max(len(text) * 0.25, 2)

    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"anullsrc=r=24000",
        "-t", str(duration),
        output_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=10)

    _write_sidecar(output_path + ".json", {
        "text": text,
        "target_duration": target_duration,
        "duration": duration,
        "matched": bool(target_duration),
        "effective_speed": None,
        "sentence_timings": None,
    })

    return {
        "audio_path": output_path,
        "duration": duration,
        "target_duration": target_duration,
        "matched": bool(target_duration),
        "effective_speed": None,
    }


def _stable_hash(text: str) -> str:
    """稳定的 MD5 哈希，用于文件名去重（跨进程一致）"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
