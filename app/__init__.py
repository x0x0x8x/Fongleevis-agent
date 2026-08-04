# app/__init__.py
"""
Flask 应用工厂

创建应用实例，注册所有蓝图和扩展
"""

from flask import Flask, send_from_directory
from .config import STATIC_FOLDER, BASE_DIR
from .extensions import socketio, init_extensions
from .ai_service import ai_bp
from .agent import init_agent, agent_bp
import os
import sys


def create_app():
    # ==================== 路径配置 ====================
    agent_static = os.path.join(STATIC_FOLDER, 'agent')

    app = Flask(__name__,
                static_folder=None,
                static_url_path=None)

    app.config['BASE_DIR'] = BASE_DIR
    app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
    app.config['AGENT_STATIC'] = agent_static

    # ==================== 检测调试模式 ====================
    is_debugging = 'pydevd' in sys.modules or 'PYCHARM_HOSTED' in os.environ

    # ==================== 初始化扩展 ====================
    init_extensions(app)

    # ==================== 初始化 Agent ====================
    default_model = os.environ.get('AGENT_DEFAULT_MODEL', 'deepseek-v4-flash')
    if is_debugging:
        try:
            init_agent(default_model=default_model)
        except Exception as e:
            print(f'[Debug Mode] Agent initialization skipped: {e}')
    else:
        init_agent(default_model=default_model)

    # ==================== 1. Agent API 蓝图 ====================
    app.register_blueprint(agent_bp)

    # ==================== 2. AI 蓝图 ====================
    app.register_blueprint(ai_bp)

    # ==================== 3. Agent 前端 ====================
    @app.route('/')
    def agent_index():
        """Agent 首页"""
        agent_dir = app.config['AGENT_STATIC']
        return send_from_directory(agent_dir, 'index.html')

    # ==================== 4. 静态资源路径映射 ====================
    # 直接映射 /agent/assets/ 到 static/agent/assets/
    @app.route('/agent/assets/<path:path>')
    def agent_assets(path):
        """Agent 静态资源（保持 /agent/assets/ 路径）"""
        agent_assets_dir = os.path.join(app.config['AGENT_STATIC'], 'assets')
        return send_from_directory(agent_assets_dir, path)

    # 处理其他静态文件（如 favicon.ico 等）
    @app.route('/agent/<path:path>')
    def agent_other_assets(path):
        """Agent 其他静态资源"""
        # 跳过 API 路径
        if path.startswith('api/') or path.startswith('v1/'):
            return "Not Found", 404

        agent_dir = app.config['AGENT_STATIC']
        file_path = os.path.join(agent_dir, path)

        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_from_directory(agent_dir, path)

        return "File not found", 404

    return app


__all__ = ['create_app', 'socketio']