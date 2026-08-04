"""
AI Router - 统一 LLM 网关 【对外门面】
零改动对外接口，业务导入保持不变
"""
from ._router_api import chat_completions, chat_completions_stream
from ._parser import ResponseParser, extract_llm_content, is_error_response
from ._config import (
    reload_config,
    set_log_level,
    get_log_level,
    print_connection_stats,
    get_connection_stats
)
from ._embedding_rerank import create_embedding, rerank
from ._exceptions import RouterBaseError, ModelNotFoundError, UpstreamRequestError, InvalidParamError

__all__ = [
    # LLM 对话主接口
    "chat_completions",
    "chat_completions_stream",
    # 响应解析工具
    "ResponseParser",
    "extract_llm_content",
    "is_error_response",
    # 配置管理
    "reload_config",
    "set_log_level",
    "get_log_level",
    # 连接统计
    "print_connection_stats",
    "get_connection_stats",
    # Embedding & Rerank
    "create_embedding",
    "rerank",
    # 自定义异常
    "RouterBaseError",
    "ModelNotFoundError",
    "UpstreamRequestError",
    "InvalidParamError",
]