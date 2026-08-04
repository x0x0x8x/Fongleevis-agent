"""
_media_generate.py
文本生成多媒体：文生图 / 文生视频 / TTS语音生成
接口: /v1/images/generations, /v1/audio/speech
独立通路
"""
from typing import Dict, Any
from ._config import resolve_model, log
from ._limiter import wait_for_rate_limit, rate_limiter


async def image_generate_handler(body: Dict[str, Any]):
    """文生图处理入口"""
    model_alias = body.get("model")
    route = resolve_model(model_alias)
    rpm_limit = route.get("rpm_limit", 0)
    await wait_for_rate_limit(model_alias, rpm_limit)
    raise NotImplementedError("Image generation not implemented")


async def tts_generate_handler(body: Dict[str, Any]):
    """文字转语音"""
    raise NotImplementedError("TTS not implemented")


async def video_generate_handler(body: Dict[str, Any]):
    """文生视频"""
    raise NotImplementedError("Video generate not implemented")