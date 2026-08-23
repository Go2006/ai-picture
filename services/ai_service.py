"""
AI 服务 —— 调用 Claude / OpenAI 生成旁白文案、分析画面。
"""
import json
import httpx
from config import ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENAI_BASE_URL, AI_MODEL


# ============================================
# 旁白生成的 Prompt 模板
# ============================================
NARRATION_SYSTEM_PROMPT = """你是一个专业的视频旁白写手。用户会给你视频的画面描述，你需要写一段旁白。

要求：
1. 语言口语化、有画面感，像在跟朋友讲故事
2. 每句话控制在 5-15 个字，方便配音
3. 输出 JSON 格式：
{
  "title": "视频标题, 10字以内",
  "segments": [
    {"text": "旁白第1句", "duration": 估算的朗读秒数},
    {"text": "旁白第2句", "duration": 估算的朗读秒数}
  ]
}
4. duration 按中文朗读语速估算: 1个字约0.25秒
"""


async def generate_narration(
    video_description: str,
    style: str = "故事风",
    duration_limit: int = 60,
    extra_requirements: str = "",
) -> dict:
    """
    调用 Claude API 生成旁白文案。
    同时也支持 OpenAI 兼容接口（DeepSeek 等）。
    """

    user_prompt = f"""
视频画面描述：{video_description or '没有提供，请根据常见场景发挥'}

风格：{style}
总时长限制：约 {duration_limit} 秒
额外要求：{extra_requirements or '无'}

请生成旁白。
"""

    # --- 方式1: 用 Claude API ---
    if ANTHROPIC_API_KEY:
        return await _call_claude(user_prompt)

    # --- 方式2: 用 OpenAI 兼容接口 (DeepSeek / 千问) ---
    if OPENAI_API_KEY:
        return await _call_openai_compatible(user_prompt)

    # --- 都没有 → 返回演示数据 ---
    return _demo_narration(style, duration_limit)


# ============================================
# 底层 API 调用
# ============================================
async def _call_claude(user_prompt: str) -> dict:
    """调用 Claude API"""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    response = await client.messages.create(
        model=AI_MODEL,
        max_tokens=2000,
        system=NARRATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Claude 返回的文本里提取 JSON
    text = response.content[0].text
    return _parse_json_response(text)


async def _call_openai_compatible(user_prompt: str) -> dict:
    """调用 OpenAI 兼容接口（DeepSeek、千问、等）"""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{OPENAI_BASE_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": NARRATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 2000,
            },
        )
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return _parse_json_response(text)


def _parse_json_response(text: str) -> dict:
    """从 AI 返回中提取 JSON（AI 可能会多输出一些解释文字）"""
    # 找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return json.loads(text[start:end])
    # 解析失败，返回兜底
    return {
        "title": "未命名",
        "segments": [{"text": text[:100], "duration": 5}],
    }


def _demo_narration(style: str, duration: int) -> dict:
    """演示数据 —— 没有 API Key 时使用，方便测试流程"""
    return {
        "title": "清晨的街角",
        "segments": [
            {"text": "清晨六点，阳光刚刚穿过云层", "duration": 4},
            {"text": "街角的早餐摊，热气腾腾", "duration": 4},
            {"text": "老板娘熟练地翻着煎饼", "duration": 4},
            {"text": "这是城市苏醒的样子", "duration": 4},
            {"text": "平凡却充满力量", "duration": 3},
            {"text": "每一天，都从这里开始", "duration": 4},
        ],
    }


# ============================================
# 视频画面分析 (可选增强功能)
# ============================================
async def analyze_video_frames(frame_paths: list[str]) -> str:
    """
    分析视频抽帧图片，返回画面描述。
    需要支持图片理解的模型（Claude Vision / GPT-4o）。
    """
    if not ANTHROPIC_API_KEY and not OPENAI_API_KEY:
        return "视频画面（未配置 AI Key，无法自动分析）"

    # 简化版：如果有 Claude API Key，用 Vision 分析
    if ANTHROPIC_API_KEY:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

        # 只分析前 5 帧（省 token）
        descriptions = []
        for path in frame_paths[:5]:
            import base64
            with open(path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()

            resp = await client.messages.create(
                model=AI_MODEL,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        }},
                        {"type": "text", "text": "用一句话中文描述这个画面里有什么。"},
                    ],
                }],
            )
            descriptions.append(resp.content[0].text)

        return "；".join(descriptions)

    return "画面分析暂不可用"
