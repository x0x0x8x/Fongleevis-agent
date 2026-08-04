"""
_config.py
配置加载、全局常量、模型路由工具、连接计数器、thinking参数处理
⚠️ 规范：所有外部代码禁止直接读取 MODEL_REGISTRY，统一使用 resolve_model()
"""
import json
import os
import sys
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

# =========================================================
# 🔥 延迟导入 agent_config，避免循环导入
# =========================================================
def _get_full_config():
    """延迟获取 _FULL_CONFIG，确保模块已完全加载"""
    from app.agent.api.agent_config import _FULL_CONFIG
    return _FULL_CONFIG


# =========================================================
# 🔥 所有配置通过函数获取，确保实时性
# =========================================================

def _get_ai_router_config() -> dict:
    """获取 ai_router 配置"""
    full_config = _get_full_config()
    if full_config is None:
        return {}
    return full_config.get("ai_router", {})


# =========================================================
# 🔥 使用函数而不是直接暴露变量（确保实时获取最新值）
# =========================================================

def get_gateway_config() -> Dict[str, Any]:
    """获取网关配置"""
    return _get_ai_router_config().get("gateway", {})


def get_debug() -> bool:
    """获取调试模式"""
    return _get_ai_router_config().get("gateway", {}).get("debug", False)


def get_window_size() -> int:
    """获取限流窗口大小"""
    return _get_ai_router_config().get("rate_limiter", {}).get("window_size", 60)


def get_skip_rate_limit_wait() -> bool:
    """获取是否跳过限流等待"""
    return _get_ai_router_config().get("rate_limiter", {}).get("skip_rate_limit_wait", False)


def get_default_thinking_level() -> str:
    """获取默认思考级别"""
    return _get_ai_router_config().get("defaults", {}).get("thinking_level", "medium")


def get_default_temperature() -> float:
    """获取默认温度"""
    return _get_ai_router_config().get("defaults", {}).get("temperature", 0.7)


def get_default_top_p() -> float:
    """获取默认 top_p"""
    return _get_ai_router_config().get("defaults", {}).get("top_p", 0.95)


def get_default_max_tokens() -> int:
    """获取默认最大 token 数"""
    return _get_ai_router_config().get("defaults", {}).get("max_tokens", 32768)


def get_auth() -> Dict[str, Any]:
    """获取认证配置"""
    return _get_ai_router_config().get("auth", {})


def get_model_registry() -> Dict[str, Any]:
    """获取模型注册表"""
    return _get_ai_router_config().get("models", {})


def get_logging_config() -> Dict[str, Any]:
    """获取日志配置"""
    return _get_ai_router_config().get("logging", {})


# =========================================================
# 向后兼容：暴露变量（通过属性延迟访问）
# =========================================================
class _LazyConfig:
    """延迟加载配置的代理类"""

    @property
    def GATEWAY_CONFIG(self):
        return get_gateway_config()

    @property
    def DEBUG(self):
        return get_debug()

    @property
    def WINDOW_SIZE(self):
        return get_window_size()

    @property
    def SKIP_RATE_LIMIT_WAIT(self):
        return get_skip_rate_limit_wait()

    @property
    def DEFAULT_THINKING_LEVEL(self):
        return get_default_thinking_level()

    @property
    def DEFAULT_TEMPERATURE(self):
        return get_default_temperature()

    @property
    def DEFAULT_TOP_P(self):
        return get_default_top_p()

    @property
    def DEFAULT_MAX_TOKENS(self):
        return get_default_max_tokens()

    @property
    def AUTH(self):
        return get_auth()

    @property
    def MODEL_REGISTRY(self):
        return get_model_registry()

    @property
    def LOGGING_CONFIG(self):
        return get_logging_config()


# 创建代理实例
_lazy = _LazyConfig()

# 暴露变量（实际是属性访问）
GATEWAY_CONFIG = _lazy.GATEWAY_CONFIG
DEBUG = _lazy.DEBUG
WINDOW_SIZE = _lazy.WINDOW_SIZE
SKIP_RATE_LIMIT_WAIT = _lazy.SKIP_RATE_LIMIT_WAIT
DEFAULT_THINKING_LEVEL = _lazy.DEFAULT_THINKING_LEVEL
DEFAULT_TEMPERATURE = _lazy.DEFAULT_TEMPERATURE
DEFAULT_TOP_P = _lazy.DEFAULT_TOP_P
DEFAULT_MAX_TOKENS = _lazy.DEFAULT_MAX_TOKENS
AUTH = _lazy.AUTH
MODEL_REGISTRY = _lazy.MODEL_REGISTRY
LOGGING_CONFIG = _lazy.LOGGING_CONFIG


# =========================================================
# 日志级别控制
# =========================================================
def set_log_level(level: str):
    if level not in ["full", "compact", "none"]:
        raise ValueError(f"Invalid log level: {level}. Must be one of: full, compact, none")
    ai_router = _get_ai_router_config()
    if "gateway" not in ai_router:
        ai_router["gateway"] = {}
    ai_router["gateway"]["log_level"] = level
    print(f"[LLM-Gateway] 日志级别已设置为: {level}")


def get_log_level() -> str:
    return _get_ai_router_config().get("gateway", {}).get("log_level", "full")


# =========================================================
# 配置重载
# =========================================================
def reload_config(config_path: str = None):
    """
    重新加载配置 - agent_config 的 reload_config 已经更新了 _FULL_CONFIG
    这里只需要重新读取标量值即可
    """
    print(f"[AI Router] 🔄 配置已重新加载")


# =========================================================
# 连接统计全局变量
# =========================================================
_active_connections = 0
_connection_lock = threading.Lock()
_total_connections_created = 0
_total_connections_closed = 0


def increment_connections():
    global _active_connections, _total_connections_created
    with _connection_lock:
        _active_connections += 1
        _total_connections_created += 1


def decrement_connections():
    global _active_connections, _total_connections_closed
    with _connection_lock:
        _active_connections -= 1
        _total_connections_closed += 1


def get_connection_stats():
    with _connection_lock:
        return {
            "active": _active_connections,
            "total_created": _total_connections_created,
            "total_closed": _total_connections_closed
        }


def print_connection_stats():
    from ._limiter import rate_limiter
    log_level = _get_ai_router_config().get("gateway", {}).get("log_level", "full")
    if log_level == "none":
        return
    stats = get_connection_stats()
    if log_level == "full":
        print(f"\n{'=' * 60}")
        print(f"连接统计:")
        print(f"  活跃连接数: {stats['active']}")
        print(f"  总创建连接数: {stats['total_created']}")
        print(f"  总关闭连接数: {stats['total_closed']}")
        rate_stats = rate_limiter.get_all_model_stats({})
        if rate_stats:
            print(f"\n频率限制统计:")
            for model, stats_item in rate_stats.items():
                print(f"  {model}: {stats_item['current']}/{stats_item['max_rpm']} 次/分钟")
        print(f"{'=' * 60}")
    elif log_level == "compact":
        print(f"📊 连接: 活跃={stats['active']} 总={stats['total_created']} | 频率: {len(rate_limiter._buckets)}个模型")


# =========================================================
# 模型工具函数
# =========================================================
def is_chat_model(model_alias: str) -> bool:
    config = _get_ai_router_config().get("models", {}).get(model_alias, {})
    return config.get("api_type") == "chat_completions"


def is_embedding_model(model_alias: str) -> bool:
    config = _get_ai_router_config().get("models", {}).get(model_alias, {})
    return config.get("api_type") == "embeddings"


def is_rerank_model(model_alias: str) -> bool:
    config = _get_ai_router_config().get("models", {}).get(model_alias, {})
    return config.get("api_type") == "rerank"


def is_audio_model(model_alias: str) -> bool:
    config = _get_ai_router_config().get("models", {}).get(model_alias, {})
    return config.get("api_type") in ["audio_transcriptions", "audio_speech"]


def list_models_by_type(api_type: str = None) -> List[str]:
    models = _get_ai_router_config().get("models", {})
    if api_type:
        return [name for name, config in models.items() if config.get("api_type") == api_type]
    return list(models.keys())


def resolve_model(model_alias: str) -> Dict[str, Any]:
    """统一路由查询入口，禁止外部直接读取MODEL_REGISTRY"""
    from ._exceptions import ModelNotFoundError
    models = _get_ai_router_config().get("models", {})
    route_cfg = models.get(model_alias)
    if route_cfg is None:
        raise ModelNotFoundError(f"Unknown model alias: {model_alias}")
    return route_cfg


# =========================================================
# thinking 参数处理
# =========================================================
def _set_nested_param(data: Dict, path: str, value):
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        elif not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def apply_thinking_param(request_body: Dict, model_alias: str) -> Dict:
    route = resolve_model(model_alias)
    result = request_body.copy()

    if "enable_thinking" in result:
        result.pop("thinking", None)
        return result

    thinking_val = result.pop("thinking", None)

    if not route.get("supports_thinking"):
        if thinking_val is not None:
            log(f"模型 {model_alias} 不支持思考参数，已忽略", "WARN")
        return result

    if thinking_val is None:
        thinking_val = _get_ai_router_config().get("defaults", {}).get("thinking_level", "medium")

    if isinstance(thinking_val, str) and thinking_val.lower() == "off":
        thinking_val = False

    thinking_config = route.get("thinking_config", {})
    if not thinking_config:
        log(f"模型 {model_alias} 标记支持思考但缺少配置", "ERROR")
        return result

    config_type = thinking_config.get("type", "boolean")
    param_path = thinking_config.get("param_path")
    supports_levels = thinking_config.get("supports_levels", False)

    if not param_path:
        log(f"模型 {model_alias} 的 thinking_config 缺少 param_path", "ERROR")
        return result

    if isinstance(thinking_val, bool):
        if config_type == "boolean":
            _set_nested_param(result, param_path, thinking_val)
            return result
        else:
            if thinking_val is False:
                from ._exceptions import InvalidParamError
                raise InvalidParamError(
                    f"模型 {model_alias} 不支持禁用思考（思考配置类型为 '{config_type}'），"
                    "请使用 thinking='low' 降低思考强度，或使用原始请求体直接控制参数。"
                )
            else:
                thinking_val = _get_ai_router_config().get("defaults", {}).get("thinking_level", "medium")

    if isinstance(thinking_val, str):
        valid_levels = ["low", "medium", "high"]
        if thinking_val not in valid_levels:
            from ._exceptions import InvalidParamError
            raise InvalidParamError(f"thinking 必须是 'low', 'medium', 'high' 或 'off'，收到: {thinking_val}")

    if config_type == "boolean":
        enabled_value = thinking_config.get("enabled_value", True)
        _set_nested_param(result, param_path, enabled_value)
    elif config_type == "level":
        if supports_levels:
            level_mapping = thinking_config.get("level_mapping", {})
            mapped_value = level_mapping.get(thinking_val, thinking_val)
            _set_nested_param(result, param_path, mapped_value)
        else:
            enabled_value = thinking_config.get("enabled_value", True)
            _set_nested_param(result, param_path, enabled_value)
    elif config_type == "budget":
        if supports_levels:
            level_mapping = thinking_config.get("level_mapping", {})
            budget_config = level_mapping.get(thinking_val)
            if budget_config:
                _set_nested_param(result, param_path, budget_config)
        else:
            enabled_value = thinking_config.get("enabled_value", {"type": "enabled", "budget_tokens": 8000})
            _set_nested_param(result, param_path, enabled_value)

    extra_body = route.get("extra_body", {})
    if extra_body:
        result.update(extra_body)
    return result


# =========================================================
# 内部日志工具
# =========================================================
def log(msg: str, level: str = "INFO"):
    debug = _get_ai_router_config().get("gateway", {}).get("debug", False)
    if debug or level in ["ERROR", "WARN"]:
        print(f"[LLM-Gateway][{level}] {msg}")