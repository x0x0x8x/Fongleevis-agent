# app/extensions.py

import sys
from pathlib import Path

from flask_socketio import SocketIO


BASE_DIR = Path(__file__).resolve().parent.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ====================
# Flask Extensions
# ====================

socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="threading"
)


mailer_client = None
token_mgr = None
session_mgr = None



def init_extensions(app):
    """
    初始化 Flask 扩展
    """

    socketio.init_app(app)

    print(
        "[Extensions] SocketIO initialized "
        "(threading mode)"
    )



__all__ = [
    "socketio",
    "mailer_client",
    "token_mgr",
    "session_mgr",
    "init_extensions"
]