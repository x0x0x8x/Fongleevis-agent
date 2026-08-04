# app/agent/__init__.py
"""
Agent 模块初始化

在 Flask 启动时创建 Agent 实例，并管理其生命周期
"""

import asyncio
import threading
from .api.core import LLMGateway


# ==================== 全局 Agent 实例 ====================
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
    初始化 Agent（在 Flask 启动时调用）

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
    """关闭 Agent（在 Flask 关闭时调用）"""
    global _agent
    if _agent is not None:
        print("[Agent] 正在关闭...")
        _agent.shutdown()
        _agent = None
        print("[Agent] 已关闭")


# ==================== 导出 ====================
from .routes import agent_bp

__all__ = ['agent_bp', 'get_agent', 'init_agent', 'shutdown_agent']