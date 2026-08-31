# -*- coding: utf-8 -*-
'''安全默认配置：DEBUG 关闭（生产强制）、密钥外置、请求限长、CORS 白名单、SECURE cookie、子站点注册机制。'''
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class SecureDefaultConfig:
    APP_NAME = 'SecureFastAPI'
    VERSION = '1.0.0'
    DEBUG = False  # 生产强制关闭，validate() 校验

    SECRET_KEY = os.environ.get('SFA_SECRET_KEY', '')
    ADMIN_TOKEN = os.environ.get('SFA_ADMIN_TOKEN', '')

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB 请求限长

    CORS_ALLOWED_ORIGINS = ['https://www.sweetmido.asia', 'https://shop.sweetmido.asia', 'http://localhost:5000', 'http://127.0.0.1:5000', 'http://localhost:5173']
    CORS_ALLOW_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS']
    CORS_ALLOW_HEADERS = ['Authorization', 'Content-Type', 'X-Request-Id']

    COOKIE_SECURE = True  # SECURE cookie
    COOKIE_HTTPONLY = True
    COOKIE_SAMESITE = 'lax'

    ALLOWED_HOSTS = ['www.sweetmido.asia', 'sweetmido.asia', 'localhost', '127.0.0.1']
    SENSITIVE_PATH_PREFIXES = ['/.well-known', '/wx_v3', '/log', '/debug_logs', '/executor_logs', '/TMP', '/static/secret', '/.git', '/.env']
    SENSITIVE_FILE_SUFFIXES = ['.pem', '.p12', '.key', '.log', '.sqlite3', '.db', '.env']

    STATIC_ALLOW_PREFIXES = ['/static/public']
    STATIC_ROOT = BASE_DIR / 'app' / 'static'

    # 子站点注册机制
    REGISTRY_PATH = BASE_DIR / 'registry.json'
    SECURE_ROOT = BASE_DIR / 'secure_root'
    REGISTRY_HOT_MOUNT = False  # 生产默认关闭动态挂载

    @classmethod
    def load_env_file(cls) -> None:
        env_file = BASE_DIR / '.env'
        if env_file.exists():
            for line in env_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip().strip(chr(34) + chr(39)))
        cls._apply_env()

    @classmethod
    def _apply_env(cls) -> None:
        if os.environ.get('SFA_DEBUG') == '1':
            cls.DEBUG = True
        secret = os.environ.get('SFA_SECRET_KEY')
        if secret:
            cls.SECRET_KEY = secret
        admin = os.environ.get('SFA_ADMIN_TOKEN')
        if admin:
            cls.ADMIN_TOKEN = admin
        hosts = os.environ.get('SFA_ALLOWED_HOSTS')
        if hosts:
            cls.ALLOWED_HOSTS = [h.strip() for h in hosts.split(',')]
        if os.environ.get('SFA_REGISTRY_HOT_MOUNT') == '1':
            cls.REGISTRY_HOT_MOUNT = True

    @classmethod
    def validate(cls) -> None:
        if cls.DEBUG:
            raise RuntimeError('SECURITY: DEBUG=True 禁止生产启动')
        if not cls.SECRET_KEY:
            raise RuntimeError('SECURITY: SECRET_KEY 缺失，请通过 .env 或环境变量 SFA_SECRET_KEY 提供')