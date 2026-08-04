"""
_media_schema.py
多媒体通用常量、类型定义、校验工具
所有多媒体模块共享依赖
"""
from typing import Dict, Any, List, Optional, Literal

MediaType = Literal["text", "image_url", "video_url", "audio_url"]
BASE64_PREFIX = "data:"
HTTP_PREFIXES = ("http://", "https://")


def is_base64_media_url(url: str) -> bool:
    return url.startswith(BASE64_PREFIX)


def is_remote_http_url(url: str) -> bool:
    return url.startswith(HTTP_PREFIXES)


def validate_media_content_unit(unit: Dict[str, Any]) -> Optional[str]:
    if not isinstance(unit, dict):
        return "content unit must be dict"
    utype = unit.get("type")
    if utype == "text":
        if "text" not in unit:
            return "text unit missing text field"
        return None
    if utype in ("image_url", "video_url", "audio_url"):
        key_name = f"{utype}"
        info = unit.get(key_name)
        if not isinstance(info, dict) or "url" not in info:
            return f"{utype} missing url"
        return None
    return f"unsupported media type: {utype}"