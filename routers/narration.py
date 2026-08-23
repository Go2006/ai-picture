"""
旁白路由 —— AI 生成旁白文案 + TTS 合成语音。
"""
import os
from fastapi import APIRouter
from models.schemas import (
    GenerateNarrationRequest, NarrationResult,
    SynthesizeSpeechRequest, SpeechResult,
)
from services.ai_service import generate_narration
from services.tts_service import synthesize

router = APIRouter(prefix="/api/narration", tags=["旁白"])


@router.post("/generate", response_model=NarrationResult)
async def generate(req: GenerateNarrationRequest):
    """
    生成旁白文案。
    传视频画面描述（或让 AI 自由发挥），选风格，得到分段文案。
    """
    result = await generate_narration(
        video_description=req.video_description or "",
        style=req.style.value,
        duration_limit=req.duration_limit,
        extra_requirements=req.extra_requirements,
    )

    # 拼接完整文案
    full_text = "".join(seg["text"] for seg in result["segments"])

    return NarrationResult(
        title=result["title"],
        narration=full_text,
        segments=result["segments"],
        style=req.style.value,
    )


@router.post("/synthesize", response_model=SpeechResult)
async def synthesize_speech(req: SynthesizeSpeechRequest):
    """
    把旁白文案合成语音。
    传 target_duration（如视频时长）时自动调速 + 句间停顿，把语音时长匹配到目标。
    """
    result = await synthesize(
        text=req.text,
        voice=req.voice,
        speed=req.speed,
        emotion=req.emotion,
        target_duration=req.target_duration,
    )
    return SpeechResult(**result)
