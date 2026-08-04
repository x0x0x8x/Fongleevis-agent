"""
_multimodal.py
多媒体门面汇总模块
_router_api 仅引入本文件，不直接导入各个细分媒体模块
所有多媒体相关处理器统一在此导出，方便路由注册
"""
from ._multimodal_chat import multimodal_chat_handler
from ._media_generate import (
    image_generate_handler,
    tts_generate_handler,
    video_generate_handler
)
from ._media_audio_transcribe import audio_transcribe_handler

# 对外统一导出
__all__ = [
    "multimodal_chat_handler",
    "image_generate_handler",
    "tts_generate_handler",
    "video_generate_handler",
    "audio_transcribe_handler",
]