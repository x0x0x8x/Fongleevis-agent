# app/ai_service/__init__.py
"""
AI Service 独立模块

提供 OpenAI 兼容的聊天、Embedding、Rerank 接口
支持 NVIDIA API 和 Ollama 本地模型
"""

import requests
from flask import Blueprint

# ==================== 创建蓝图 ====================
# url_prefix='/v1' 使得路由路径为 /v1/chat/completions, /v1/embeddings 等
ai_bp = Blueprint('ai', __name__, url_prefix='/v1')

# ==================== 创建专用 requests session（用于代理） ====================
# 复用连接池，提高代理请求性能
ai_session = requests.Session()
ai_session.trust_env = False  # 忽略系统代理（防止代理干扰）
ai_session.timeout = 60       # 默认超时 60 秒

# ==================== 导入路由（必须在 ai_session 定义之后） ====================
# 因为 routes.py 中会引用 ai_bp 和 ai_session
from . import routes

# ==================== 导出模块 ====================
__all__ = [
    'ai_bp',
    'ai_session',
]