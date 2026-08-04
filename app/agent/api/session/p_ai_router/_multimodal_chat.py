"""
_multimodal_chat.py
独立接口 /v1/multimodal/chat
VLM多模态对话：图片/视频/音频输入，文本输出
⚠️ 独立链路，不和 /v1/chat/completions 复用代码
"""
from typing import Dict, Any, Optional
import aiohttp

from ._config import resolve_model, increment_connections, decrement_connections, log
from ._limiter import wait_for_rate_limit, rate_limiter
from ._logger import request_logger
from ._media_schema import validate_media_content_unit


async def multimodal_chat_handler(raw_body: Dict[str, Any]):
    """
    多模态对话核心处理入口
    未来实现：独立aiohttp请求、独立重试、独立解析器
    """
    # 预留：校验消息content数组结构
    model_alias = raw_body.get("model")
    route = resolve_model(model_alias)
    rpm_limit = route.get("rpm_limit", 0)

    try:
        await wait_for_rate_limit(model_alias, rpm_limit)
        # 后续填充完整上游请求逻辑
        raise NotImplementedError("Multimodal chat not implemented")
    except Exception as e:
        log(f"Multimodal chat error: {e}", "ERROR")
        raise