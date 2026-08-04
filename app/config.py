# app/config.py
import os
from pathlib import Path

# ==================== 项目根目录 ====================
# app/config.py 在 app/ 目录下，所以 .parent 就是项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# ==================== 文件上传配置 ====================
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# 上传目录（基于项目根目录）
STATIC_URL_PATH = '/static/agent'

# ==================== 开发配置 ====================
BYPASS_PAYMENT = False          # 是否绕过支付（开发用）
MIN_DELIVERY_AMOUNT = 50        # 最低配送金额（单位：元，注意原代码是分？原代码写的是50，实际是元？）

# ==================== 日志配置 ====================
LOG_DIR = os.path.join(BASE_DIR, 'log')
LOG_FILE = os.path.join(LOG_DIR, 'wxpay.log')

# ==================== 静态文件目录 ====================
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')