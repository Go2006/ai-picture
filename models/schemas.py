"""
数据模型 —— 定义 API 请求/响应的结构。
"""
from pydantic import BaseModel
from typing import Optional
from enum import Enum


# ============================================
# 旁白风格枚举
# ============================================
class NarrationStyle(str, Enum):
    documentary = "纪录片风"      # 冷静、客观
    storytelling = "故事风"       # 娓娓道来、有情绪
    suspense = "悬疑风"           # 紧张、快节奏
    casual = "轻松日常"           # 口语化、Vlog 感
    marketing = "营销带货"        # 激情、促单


# ============================================
# 请求模型
# ============================================
class GenerateNarrationRequest(BaseModel):
    """生成旁白文案的请求"""
    video_description: Optional[str] = None   # 手动描述视频内容（可选）
    style: NarrationStyle = NarrationStyle.storytelling
    duration_limit: int = 60                  # 旁白大概多少秒
    extra_requirements: str = ""              # 额外要求，比如"提到品牌名"


class SynthesizeSpeechRequest(BaseModel):
    """语音合成请求"""
    text: str                                 # 要合成的文本
    voice: str = "zh-CN-XiaoxiaoNeural"       # 微软 TTS 音色
    speed: float = 1.0                        # 语速
    emotion: str = "natural"                  # 情感: natural/gentle/newscast/cheerful/serious/lyrical/chat
    target_duration: Optional[float] = None   # 目标时长（秒，如视频时长），自动调速+句间停顿匹配


class EditVideoRequest(BaseModel):
    """剪辑请求"""
    video_id: str                             # 素材视频 ID
    narration_text: str                       # 旁白文案
    audio_path: str                           # 已合成的旁白音频
    segments: list[dict] = []                 # 旁白分段: [{"text":"...", "duration":3.5}, ...]


# ============================================
# 响应模型
# ============================================
class NarrationResult(BaseModel):
    """旁白文案结果"""
    title: str
    narration: str                            # 旁白文案
    segments: list[dict]                      # 分段: [{"text": "...", "duration": 3.5}, ...]
    style: str


class SpeechResult(BaseModel):
    """语音合成结果"""
    audio_path: str
    duration: float                           # 音频时长（秒）
    target_duration: Optional[float] = None   # 目标时长（秒）
    matched: Optional[bool] = None            # 是否已匹配到目标时长
    effective_speed: Optional[float] = None   # 匹配后实际使用的语速


class EditResult(BaseModel):
    """剪辑结果"""
    output_video_path: str
    duration: float
    audio_duration: Optional[float] = None     # 旁白音频时长（秒）
    video_duration: Optional[float] = None     # 成片视频时长（秒），应 == audio_duration


class TaskStatus(BaseModel):
    """异步任务状态"""
    task_id: str
    status: str                               # pending / running / done / failed
    result: Optional[dict] = None
