"""
agent_config.py
Agent模块配置加载 - 从 config.json 读取配置
"""
import json
import os
import sys
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Tuple

# =========================================================
# 配置加载
# =========================================================
def load_config(config_path: str = "config.json") -> dict:
    paths_to_try = [
        config_path,
        os.path.join(os.path.dirname(__file__), config_path),
        os.path.join(os.getcwd(), config_path),
        os.path.join(os.path.dirname(__file__), "../..", config_path),
        os.path.join(os.path.dirname(__file__), "../../..", config_path),
    ]

    found_path = None
    for path in paths_to_try:
        if os.path.exists(path):
            found_path = path
            break

    if found_path is None:
        print(f"[Agent] ❌ 配置文件不存在: {config_path}")
        print(f"[Agent] ❌ 已尝试路径: {paths_to_try}")
        # 使用默认值继续运行
        return {}

    try:
        with open(found_path, 'r', encoding='utf-8') as f:
            full_config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[Agent] ❌ 配置文件格式错误: {found_path}")
        print(f"[Agent] ❌ {e}")
        return {}

    agent_config = full_config.get("agent")
    if agent_config is None:
        print(f"[Agent] ⚠️ 配置文件中缺少 'agent' 模块，使用默认配置")
        return {}

    print(f"[Agent] ✅ 加载配置文件: {found_path}")
    return agent_config

_DEFAULT_CONFIG = '''{
  "ai_router": {
    "gateway": {
      "timeout": 600,
      "connect_timeout": 30,
      "read_timeout": 300,
      "max_retries": 99,
      "retry_delays": [1, 2, 4],
      "max_wait_seconds": 30,
      "fake_streaming": false,
      "log_requests": true,
      "log_responses": true,
      "log_system_prompt": false,
      "log_level": "none",
      "persist_chunks": false,
      "debug": false
    },
    "rate_limiter": {
      "default_max_requests_per_minute": 38,
      "window_size": 60
    },
    "defaults": {
      "thinking_level": "medium",
      "temperature": 0.7,
      "top_p": 0.95,
      "max_tokens": 32768
    },
    "auth": {
      "bearer_nvidia": "YOUR_NVIDIA_API_KEY",
      "bearer_SenseNova": "YOUR_SENSENOVA_API_KEY",
      "bearer_minimax": "YOUR_MINIMAX_API_KEY",
      "bearer_deepseek": "YOUR_DEEPSEEK_API_KEY",
      "bearer_anthropic": "YOUR_ANTHROPIC_API_KEY"
    },
    "models": {
      "deepseek-v4-flash": {
        "provider": "deepseek",
        "api_type": "chat_completions",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-flash",
        "auth": "bearer_deepseek",
        "supports_thinking": true,
        "supports_tools": true,
        "supports_streaming": true,
        "thinking_config": {
          "type": "boolean",
          "param_path": "enable_thinking",
          "supports_levels": false,
          "enabled_value": true
        },
        "multimodal": {
          "image": false,
          "audio": false,
          "video": false,
          "document": false,
          "output_audio": false
        },
        "extra_body": {}
      },
      "local-model": {
        "provider": "llama",
        "api_type": "chat_completions",
        "base_url": "http://localhost:8080/v1/chat/completions",
        "model": "local-model-name",
        "auth": null,
        "supports_thinking": true,
        "supports_tools": true,
        "supports_streaming": true,
        "thinking_config": {
          "type": "boolean",
          "param_path": "enable_thinking",
          "supports_levels": false,
          "enabled_value": true
        },
        "multimodal": {
          "image": false,
          "audio": false,
          "video": false,
          "document": false,
          "output_audio": false
        },
        "extra_body": {}
      }
    },
    "logging": {
      "persist_to_disk": true,
      "persist_path": "./logs"
    }
  },
  "agent": {
    "root": {
      "default_root_dir": "./agentspace"
    },
    "executor": {
      "max_retries": 10,
      "max_verify_retries": 3,
      "max_tool_iterations": 50,
      "persist_size_threshold": 16384
    },
    "planner": {
      "max_depth": 5,
      "max_replan_limit": 10
    },
    "defaults": {
      "model": "deepseek-v4-flash",
      "thinking": true,
      "temperature": 0.7,
      "max_tokens": 4096,
      "top_p": 0.95
    },
    "safety": {
      "verify_timeout": 300
    },
    "logging": {
      "level": "INFO",
      "enable_thinking_log": true
    }
  },
  "other_module": {
    "some_config": "value"
  }
}'''

# =========================================================
# 全局配置变量 & 锁
# =========================================================
CONFIG_FILE = "config.json"
_CONFIG_LOCK = threading.Lock()
_CONFIG = load_config(CONFIG_FILE)
_FULL_CONFIG = None  # 完整配置缓存
_CONFIG_FILE_PATH = None  # 配置文件路径


def _get_full_config() -> dict:
    """获取完整配置（包含所有模块）"""
    global _FULL_CONFIG, _CONFIG_FILE_PATH

    if _FULL_CONFIG is not None:
        return _FULL_CONFIG

    paths_to_try = [
        CONFIG_FILE,
        os.path.join(os.path.dirname(__file__), CONFIG_FILE),
        os.path.join(os.getcwd(), CONFIG_FILE),
        os.path.join(os.path.dirname(__file__), "../..", CONFIG_FILE),
        os.path.join(os.path.dirname(__file__), "../../..", CONFIG_FILE),
    ]

    for path in paths_to_try:
        if os.path.exists(path):
            _CONFIG_FILE_PATH = path
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    _FULL_CONFIG = json.load(f)
                return _FULL_CONFIG
            except:
                continue

    _FULL_CONFIG = {}
    return _FULL_CONFIG


def _save_full_config():
    """保存完整配置到文件"""
    global _FULL_CONFIG, _CONFIG_FILE_PATH

    if _FULL_CONFIG is None:
        _get_full_config()

    if _CONFIG_FILE_PATH is None:
        # 尝试找到配置文件路径
        paths_to_try = [
            CONFIG_FILE,
            os.path.join(os.path.dirname(__file__), CONFIG_FILE),
            os.getcwd(),
        ]
        for path in paths_to_try:
            if os.path.exists(path):
                _CONFIG_FILE_PATH = path
                break
        else:
            # 如果不存在，使用当前目录
            _CONFIG_FILE_PATH = CONFIG_FILE

    # 保存配置文件
    with open(_CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(_FULL_CONFIG, f, ensure_ascii=False, indent=2)


def _get_nested_value(data: dict, path: str) -> Any:
    """
    根据路径获取嵌套值
    path: "executor.max_retries" 或 "defaults.model"
    """
    keys = path.split('.')
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def _set_nested_value(data: dict, path: str, value: Any) -> Tuple[bool, str]:
    """
    根据路径设置嵌套值
    返回: (是否成功, 错误信息)
    """
    keys = path.split('.')
    current = data
    for i, key in enumerate(keys[:-1]):
        if isinstance(current, dict):
            if key not in current:
                current[key] = {}
            current = current[key]
        else:
            return False, f"路径 '{'.'.join(keys[:i + 1])}' 不是字典类型"

    last_key = keys[-1]
    if isinstance(current, dict):
        current[last_key] = value
        return True, ""
    else:
        return False, f"无法设置 '{last_key}'，父路径不是字典类型"

def _delete_nested_value(data: dict, path: str) -> Tuple[bool, str]:
    """
    根据路径删除嵌套值
    返回: (是否成功, 错误信息)
    """
    keys = path.split('.')
    current = data
    for i, key in enumerate(keys[:-1]):
        if isinstance(current, dict):
            if key not in current:
                return False, f"路径 '{'.'.join(keys[:i + 1])}' 不存在"
            current = current[key]
        else:
            return False, f"路径 '{'.'.join(keys[:i + 1])}' 不是字典类型"

    last_key = keys[-1]
    if isinstance(current, dict):
        if last_key in current:
            del current[last_key]
            return True, ""
        else:
            return False, f"键 '{last_key}' 不存在"
    else:
        return False, f"无法删除 '{last_key}'，父路径不是字典类型"

def _flatten_dict(data: dict, prefix: str = '') -> dict:
    """
    将嵌套字典扁平化
    例如: {"executor": {"max_retries": 10}} -> {"executor.max_retries": 10}
    """
    result = {}
    for key, value in data.items():
        new_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and not isinstance(value, list):
            result.update(_flatten_dict(value, new_key))
        else:
            result[new_key] = value
    return result


def _unflatten_dict(data: dict) -> dict:
    """
    将扁平字典还原为嵌套字典
    例如: {"executor.max_retries": 10} -> {"executor": {"max_retries": 10}}
    """
    result = {}
    for path, value in data.items():
        keys = path.split('.')
        current = result
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value
    return result


def update_config(updates: Dict[str, Any], save_to_file: bool = True) -> Dict[str, Any]:
    """
    更新配置（支持任意层级）

    Args:
        updates: 更新字段字典，支持点号路径
                例如: {"executor.max_retries": 20, "defaults.model": "gpt-4"}
                或者嵌套格式: {"executor": {"max_retries": 20}}
                值为 None 时表示删除该字段
        save_to_file: 是否同时保存到配置文件

    Returns:
        包含成功/失败信息的字典
    """
    global _CONFIG, _FULL_CONFIG

    with _CONFIG_LOCK:
        # 获取完整配置
        if _FULL_CONFIG is None:
            _get_full_config()

        # 扁平化更新字段
        flattened_updates = {}
        for key, value in updates.items():
            if isinstance(value, dict) and not isinstance(value, list):
                # 嵌套字典，扁平化处理
                flattened_updates.update(_flatten_dict(value, key))
            else:
                flattened_updates[key] = value

        print(f"[Agent Config] 收到更新请求，共 {len(flattened_updates)} 个字段")

        # 应用更新
        updated_fields = []
        deleted_fields = []
        failed_fields = []

        for path, value in flattened_updates.items():
            # 判断是删除还是更新
            is_delete = value is None

            # 根据路径前缀决定更新哪个模块
            if path.startswith("agent."):
                agent_path = path[6:]
                if "agent" not in _FULL_CONFIG:
                    _FULL_CONFIG["agent"] = {}
                target = _FULL_CONFIG["agent"]

                if is_delete:
                    success, error = _delete_nested_value(target, agent_path)
                    if success:
                        deleted_fields.append(path)
                        print(f"[Agent Config] 🗑️ 删除 agent.{agent_path}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 删除 agent.{agent_path} 失败: {error}")
                else:
                    success, error = _set_nested_value(target, agent_path, value)
                    if success:
                        updated_fields.append(path)
                        print(f"[Agent Config] ✅ 更新 agent.{agent_path} = {value}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 更新 agent.{agent_path} 失败: {error}")

            elif path.startswith("ai_router."):
                ai_path = path[10:]
                if "ai_router" not in _FULL_CONFIG:
                    _FULL_CONFIG["ai_router"] = {}
                target = _FULL_CONFIG["ai_router"]

                if is_delete:
                    success, error = _delete_nested_value(target, ai_path)
                    if success:
                        deleted_fields.append(path)
                        print(f"[Agent Config] 🗑️ 删除 ai_router.{ai_path}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 删除 ai_router.{ai_path} 失败: {error}")
                else:
                    success, error = _set_nested_value(target, ai_path, value)
                    if success:
                        updated_fields.append(path)
                        print(f"[Agent Config] ✅ 更新 ai_router.{ai_path} = {value}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 更新 ai_router.{ai_path} 失败: {error}")

            elif path.startswith("other_module."):
                other_path = path[13:]
                if "other_module" not in _FULL_CONFIG:
                    _FULL_CONFIG["other_module"] = {}
                target = _FULL_CONFIG["other_module"]

                if is_delete:
                    success, error = _delete_nested_value(target, other_path)
                    if success:
                        deleted_fields.append(path)
                        print(f"[Agent Config] 🗑️ 删除 other_module.{other_path}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 删除 other_module.{other_path} 失败: {error}")
                else:
                    success, error = _set_nested_value(target, other_path, value)
                    if success:
                        updated_fields.append(path)
                        print(f"[Agent Config] ✅ 更新 other_module.{other_path} = {value}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 更新 other_module.{other_path} 失败: {error}")
            else:
                # 默认：直接更新到根级
                if is_delete:
                    success, error = _delete_nested_value(_FULL_CONFIG, path)
                    if success:
                        deleted_fields.append(path)
                        print(f"[Agent Config] 🗑️ 删除 {path}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 删除 {path} 失败: {error}")
                else:
                    success, error = _set_nested_value(_FULL_CONFIG, path, value)
                    if success:
                        updated_fields.append(path)
                        print(f"[Agent Config] ✅ 更新 {path} = {value}")
                    else:
                        failed_fields.append({"path": path, "error": error})
                        print(f"[Agent Config] ❌ 更新 {path} 失败: {error}")

        # 重新加载内存中的配置变量（只针对 agent 模块）
        if "agent" in _FULL_CONFIG:
            _reload_memory_config(_FULL_CONFIG["agent"])

        # 保存到文件
        if save_to_file and (updated_fields or deleted_fields):
            _save_full_config()
            print(f"[Agent Config] ✅ 配置已保存到文件")

        return {
            "success": len(updated_fields) > 0 or len(deleted_fields) > 0,
            "updated_fields": updated_fields,
            "deleted_fields": deleted_fields,
            "failed_fields": failed_fields,
            "message": f"成功更新 {len(updated_fields)} 个字段，删除 {len(deleted_fields)} 个字段" + (
                f"，{len(failed_fields)} 个失败" if failed_fields else "")
        }


def _reload_memory_config(agent_config: dict):
    """从 agent_config 重新加载内存中的配置变量"""
    global EXECUTOR_RETRY_CNT_MAX, VERIFY_CNT_MAX, MAX_TOOL_ITERATIONS
    global MAX_DEPTH, MAX_REPLAN_LIMIT, PERSIST_SIZE_THRESHOLD
    global DEFAULT_MODEL, DEFAULT_THINKING, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, DEFAULT_TOP_P
    global SAFE_VERIFY_TIMEOUT, LOG_LEVEL, ENABLE_THINKING_LOG
    global ROOT_DIR, TOOLS_DIR, CONVERSATIONS_DIR, LOGS_DIR

    # 更新根目录
    ROOT_CONFIG = agent_config.get("root", {})
    DEFAULT_ROOT_DIR = ROOT_CONFIG.get(
        "default_root_dir",
        "D:\\agentspace" if IS_WINDOWS else "/mnt/win_h/MoveDisk/tmp2"
    )
    ROOT_DIR = Path(os.environ.get("MYAGENT_DATA_DIR", DEFAULT_ROOT_DIR))
    TOOLS_DIR = ROOT_DIR / "tools"
    CONVERSATIONS_DIR = ROOT_DIR / "conversations"
    LOGS_DIR = ROOT_DIR / "logs"
    _ensure_dirs()

    # 更新执行器配置
    EXECUTOR_CONFIG = agent_config.get("executor", {})
    EXECUTOR_RETRY_CNT_MAX = EXECUTOR_CONFIG.get("max_retries", 10)
    VERIFY_CNT_MAX = EXECUTOR_CONFIG.get("max_verify_retries", 3)
    MAX_TOOL_ITERATIONS = EXECUTOR_CONFIG.get("max_tool_iterations", 50)
    PERSIST_SIZE_THRESHOLD = EXECUTOR_CONFIG.get("persist_size_threshold", 16 * 1024)

    # 更新规划器配置
    PLANNER_CONFIG = agent_config.get("planner", {})
    MAX_DEPTH = PLANNER_CONFIG.get("max_depth", 5)
    MAX_REPLAN_LIMIT = PLANNER_CONFIG.get("max_replan_limit", 10)

    # 更新默认值
    DEFAULTS_CONFIG = agent_config.get("defaults", {})
    DEFAULT_MODEL = DEFAULTS_CONFIG.get("model", "deepseek-v4-flash")
    DEFAULT_THINKING = DEFAULTS_CONFIG.get("thinking", True)
    DEFAULT_TEMPERATURE = DEFAULTS_CONFIG.get("temperature", 0.7)
    DEFAULT_MAX_TOKENS = DEFAULTS_CONFIG.get("max_tokens", 4096)
    DEFAULT_TOP_P = DEFAULTS_CONFIG.get("top_p", 0.95)

    # 更新安全配置
    SAFETY_CONFIG = agent_config.get("safety", {})
    SAFE_VERIFY_TIMEOUT = SAFETY_CONFIG.get("verify_timeout", 300)

    # 更新日志配置
    LOG_CONFIG = agent_config.get("logging", {})
    LOG_LEVEL = LOG_CONFIG.get("level", "INFO")
    ENABLE_THINKING_LOG = LOG_CONFIG.get("enable_thinking_log", True)


def get_config(path: Optional[str] = None, flatten: bool = False) -> Any:
    """
    获取配置

    Args:
        path: 配置路径，支持点号分隔
             例如: "executor.max_retries" 或 "defaults"
             如果不指定，返回整个 agent 配置
        flatten: 是否扁平化返回

    Returns:
        配置值
    """
    with _CONFIG_LOCK:
        if _FULL_CONFIG is None:
            _get_full_config()

        agent_config = _FULL_CONFIG.get("agent", {})

        if path is None:
            return agent_config if not flatten else _flatten_dict(agent_config)

        if path.startswith("agent."):
            path = path[6:]

        value = _get_nested_value(agent_config, path)

        if isinstance(value, dict) and flatten:
            return _flatten_dict(value)

        return value


def reload_config(config_path: str = None) -> Dict[str, Any]:
    """重新加载配置文件"""
    global _CONFIG, _FULL_CONFIG, _CONFIG_FILE_PATH

    with _CONFIG_LOCK:
        try:
            path = config_path if config_path else CONFIG_FILE

            # 读取新配置
            new_full_config = _load_full_config_from_file(path)

            if not new_full_config:
                print(f"[Agent Config] ⚠️ 配置文件不存在或为空，使用默认配置")
                reset_result = reset_config_to_default(keep_other_modules=True)
                if not reset_result.get("success"):
                    return {"success": False, "message": "创建默认配置失败"}
                # reset 已经更新了 _FULL_CONFIG
            else:
                # 🔥 关键：原地更新，保持引用不变
                if _FULL_CONFIG is None:
                    _FULL_CONFIG = {}
                _FULL_CONFIG.clear()
                _FULL_CONFIG.update(new_full_config)

            _CONFIG_FILE_PATH = path

            agent_config = _FULL_CONFIG.get("agent", {})
            _CONFIG = agent_config
            if agent_config:
                _reload_memory_config(agent_config)

            print(f"[Agent] 🔄 配置已重新加载")
            return {"success": True, "message": "配置已重新加载"}

        except json.JSONDecodeError as e:
            print(f"[Agent] ❌ 配置文件格式错误: {e}")
            return {"success": False, "message": f"配置文件格式错误: {e}"}
        except Exception as e:
            print(f"[Agent] ❌ 重新加载配置失败: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"重新加载配置失败: {e}"}

def _load_full_config_from_file(config_path: str) -> dict:
    """从文件加载完整配置"""
    paths_to_try = [
        config_path,
        os.path.join(os.path.dirname(__file__), config_path),
        os.path.join(os.getcwd(), config_path),
        os.path.join(os.path.dirname(__file__), "../..", config_path),
        os.path.join(os.path.dirname(__file__), "../../..", config_path),
    ]
    for path in paths_to_try:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                continue
    return {}

def get_all_config() -> dict:
    """获取完整配置（包含所有模块）"""
    with _CONFIG_LOCK:
        if _FULL_CONFIG is None:
            _get_full_config()
        return _FULL_CONFIG.copy()


def reset_config_to_default(keep_other_modules: bool = True) -> Dict[str, Any]:
    """重置配置为默认值"""
    global _FULL_CONFIG, _CONFIG_FILE_PATH

    with _CONFIG_LOCK:
        try:
            default_full_config = json.loads(_DEFAULT_CONFIG)
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"默认配置格式错误: {e}"}

        if _FULL_CONFIG is None:
            _FULL_CONFIG = {}

        # 🔥 原地更新，保持引用不变
        _FULL_CONFIG.clear()
        _FULL_CONFIG.update(default_full_config)

        if "agent" in _FULL_CONFIG:
            _reload_memory_config(_FULL_CONFIG["agent"])

        try:
            _save_full_config()
        except Exception as e:
            print(f"[Agent Config] ⚠️ 保存配置文件失败: {e}")

        return {
            "success": True,
            "message": "配置已重置为默认值",
            "data": _FULL_CONFIG.copy()
        }


# =========================================================
# 原配置加载代码（保持不变）
# =========================================================

# 系统类型检测
SYSTEM_TYPE = os.name
IS_WINDOWS = SYSTEM_TYPE == "nt"
IS_LINUX = SYSTEM_TYPE == "posix" and sys.platform != "darwin"
IS_MACOS = sys.platform == "darwin"

# 根目录配置
ROOT_CONFIG = _CONFIG.get("root", {})
DEFAULT_ROOT_DIR = ROOT_CONFIG.get(
    "default_root_dir",
    "D:\\agentspace" if IS_WINDOWS else "/mnt/win_h/MoveDisk/tmp2"
)
ROOT_DIR = Path(os.environ.get("MYAGENT_DATA_DIR", DEFAULT_ROOT_DIR))

# 子目录
TOOLS_DIR = ROOT_DIR / "tools"
CONVERSATIONS_DIR = ROOT_DIR / "conversations"
LOGS_DIR = ROOT_DIR / "logs"


def _ensure_dirs():
    for d in [TOOLS_DIR, CONVERSATIONS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


_ensure_dirs()

# 命令行工具配置
if IS_WINDOWS:
    SHELL_CMD = "cmd.exe"
    SHELL_ARGS = ["/c"]
    GIT_CHECK_CMD = ["git", "--version"]
    MKDIR_CMD = ["mkdir"]
else:
    SHELL_CMD = "/bin/bash"
    SHELL_ARGS = ["-c"]
    GIT_CHECK_CMD = ["git", "--version"]
    MKDIR_CMD = ["mkdir", "-p"]

# Agent 配置项
AGENT_CONFIG = _CONFIG.get("agent", {})

# 执行器配置
EXECUTOR_CONFIG = AGENT_CONFIG.get("executor", {})
EXECUTOR_RETRY_CNT_MAX = EXECUTOR_CONFIG.get("max_retries", 10)
VERIFY_CNT_MAX = EXECUTOR_CONFIG.get("max_verify_retries", 3)
MAX_TOOL_ITERATIONS = EXECUTOR_CONFIG.get("max_tool_iterations", 50)
PERSIST_SIZE_THRESHOLD = EXECUTOR_CONFIG.get("persist_size_threshold", 16 * 1024)

# 规划器配置
PLANNER_CONFIG = AGENT_CONFIG.get("planner", {})
MAX_DEPTH = PLANNER_CONFIG.get("max_depth", 5)
MAX_REPLAN_LIMIT = PLANNER_CONFIG.get("max_replan_limit", 10)

# 默认模型参数
DEFAULTS_CONFIG = AGENT_CONFIG.get("defaults", {})
DEFAULT_MODEL = DEFAULTS_CONFIG.get("model", "deepseek-v4-flash")
DEFAULT_THINKING = DEFAULTS_CONFIG.get("thinking", True)
DEFAULT_TEMPERATURE = DEFAULTS_CONFIG.get("temperature", 0.7)
DEFAULT_MAX_TOKENS = DEFAULTS_CONFIG.get("max_tokens", 4096)
DEFAULT_TOP_P = DEFAULTS_CONFIG.get("top_p", 0.95)

# 安全配置
SAFETY_CONFIG = AGENT_CONFIG.get("safety", {})
SAFE_VERIFY_TIMEOUT = SAFETY_CONFIG.get("verify_timeout", 300)

# 日志配置
LOG_CONFIG = AGENT_CONFIG.get("logging", {})
LOG_LEVEL = LOG_CONFIG.get("level", "INFO")
ENABLE_THINKING_LOG = LOG_CONFIG.get("enable_thinking_log", True)

# ========== 辅助函数 ==========
def get_conversation_path(session_id: str) -> str:
    """获取会话文件路径"""
    return str(CONVERSATIONS_DIR / f"{session_id}.json")


def get_tools_path(filename: str = "") -> str:
    """获取工具文件路径"""
    if filename:
        return str(TOOLS_DIR / filename)
    return str(TOOLS_DIR)


def reset_root_dir(new_root: str):
    """重置根目录（用于测试）"""
    global ROOT_DIR, TOOLS_DIR, CONVERSATIONS_DIR, LOGS_DIR
    ROOT_DIR = Path(new_root)
    TOOLS_DIR = ROOT_DIR / "tools"
    CONVERSATIONS_DIR = ROOT_DIR / "conversations"
    LOGS_DIR = ROOT_DIR / "logs"
    _ensure_dirs()


# 旧的 reload_config 保留但标记为 deprecated
def reload_config_deprecated(config_path: str = None):
    """已弃用，请使用 reload_config"""
    reload_config(config_path)

_get_full_config()