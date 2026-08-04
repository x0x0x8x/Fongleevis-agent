"""
_media_audio_transcribe.py
/v1/audio/transcriptions
音频文件上传 → 文本（Whisper类转写）
表单请求，特殊处理，完全独立
"""
from typing import Dict
from ._config import log


async def audio_transcribe_handler(form_data):
    """接收multipart/form-data音频文件"""
    raise NotImplementedError("Audio transcription not implemented")