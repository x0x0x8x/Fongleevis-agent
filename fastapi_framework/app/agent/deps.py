# app/api/agent/deps.py
"""
Agent 依赖管理：单例模式，供路由使用。
"""
import threading
from .api.core import LLMGateway


_agent = None
_agent_lock = threading.Lock()


def get_agent() -> LLMGateway:
    """获取 Agent 单例实例"""
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = LLMGateway()
    return _agent


def init_agent(default_model: str = None):
    """
    初始化 Agent（在服务启动时调用）

    Args:
        default_model: 默认模型名称，不传则使用 DEFAULT_MODEL
    """
    global _agent
    if _agent is not None:
        print("[Agent] Agent 已存在，跳过初始化")
        return _agent

    with _agent_lock:
        if _agent is None:
            _agent = LLMGateway()
            print("[Agent] 初始化完成")
    return _agent


def shutdown_agent():
    """关闭 Agent（在服务关闭时调用）"""
    global _agent
    if _agent is not None:
        print("[Agent] 正在关闭...")
        _agent.shutdown()
        _agent = None
        print("[Agent] 已关闭")