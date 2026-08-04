"""
LLM Gateway 主类

对外统一入口，整合 Session 模块
"""

import uuid
import asyncio
from typing import Optional, List, Dict, Any, Union, AsyncGenerator
from datetime import datetime

from app.agent.api.session.session import Session, SessionManager
from app.agent.api.agent_config import (
    ROOT_DIR,
)


from typing import Optional, List, Dict, Any
from datetime import datetime

class LLMGateway:
    """
    LLM Gateway 主类
    提供同步和流式两种独立的对话接口。
    """

    def __init__(self,):
        # 会话根目录：使用全局配置的 ROOT_DIR
        sessions_root = ROOT_DIR
        self._session_manager = SessionManager(sessions_root=sessions_root)
        self._stats = {"total_requests": 0, "created_at": datetime.now()}

    # ========== 非流式对话 ==========
    def chat_sync(
            self,
            message: str,
            session_id: Optional[str] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
    ) -> dict:
        """
        非流式对话，返回完整结果字典。

        Returns:
            dict: {"reply": str, "stats": dict}
        """
        self._stats["total_requests"] += 1

        session = self._get_or_create_session(session_id, model, temperature)
        reply = session.chat_sync(message)

        # 获取统计信息
        stats = session.get_last_stats()

        return {"reply": reply, "stats": stats}

    # ========== 流式对话 ==========
    def chat_stream(
            self,
            message: str,
            safe_verify_callback,
            session_id: Optional[str] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,

    ):
        """
        流式对话，返回生成器，输出 OpenAI SSE 格式。

        Yields:
            str: SSE 格式的数据块
        """
        self._stats["total_requests"] += 1

        session = self._get_or_create_session(session_id, model, temperature)
        return session.chat_stream(message,safe_verify_callback)

    # ========== 内部辅助 ==========
    def _get_or_create_session(
            self,
            session_id: Optional[str] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            name: Optional[str] = None,
            description: Optional[str] = None,
            tags: Optional[List[str]] = None,
    ) -> Session:
        """获取或创建会话，并设置 LLM 参数"""
        if session_id:
            session = self._session_manager.get(session_id)
            if session:
                # 如果传入名称等信息，更新元数据
                if name is not None or description is not None or tags is not None:
                    self._session_manager._update_session_metadata(
                        session_id,
                        name=name,
                        description=description,
                        tags=tags
                    )
                return session

            # 创建新会话
            session = self._session_manager.create(
                session_id=session_id,
                name=name,
                description=description,
                tags=tags,
            )
        else:
            # 无 session_id，创建新会话（元数据不保存）
            session = self._session_manager.create()
        return session

    # ========== 会话管理 ==========
    def create_session(
            self,
            session_id: Optional[str] = None,
            name: Optional[str] = None,
            description: Optional[str] = None,
            tags: Optional[List[str]] = None,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
    ) -> Session:
        """
        创建新会话

        Args:
            session_id: 会话 ID，不提供则自动生成
            name: 会话名称
            description: 会话描述
            tags: 标签列表
            model: LLM 模型
            temperature: 温度参数
        """
        return self._session_manager.create(
            session_id=session_id,
            name=name,
            description=description,
            tags=tags,
        )

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._session_manager.get(session_id)

    def get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话元数据

        Returns:
            dict: {
                "space_id": str,
                "name": str,
                "description": str,
                "created_at": str,
                "updated_at": str,
                "tags": List[str],
                "extra": Dict,
                "memory_count": int
            }
        """
        return self._session_manager.get_session_metadata(session_id)

    def get_session_by_name(self, name: str) -> Optional[Session]:
        """根据名称查找会话"""
        return self._session_manager.get_session_by_name(name)

    def update_session_name(self, session_id: str, name: str) -> bool:
        """更新会话名称"""
        return self._session_manager.update_session_name(session_id, name)

    def update_session_description(self, session_id: str, description: str) -> bool:
        """更新会话描述"""
        return self._session_manager.update_session_description(session_id, description)

    def update_session_tags(self, session_id: str, tags: List[str]) -> bool:
        """更新会话标签"""
        return self._session_manager.update_session_tags(session_id, tags)

    def get_or_create_session(
            self,
            session_id: str,
            name: Optional[str] = None,
            description: Optional[str] = None,
            tags: Optional[List[str]] = None,
    ) -> Session:
        """获取已有会话，若不存在则创建"""
        return self._session_manager.get_or_create(
            session_id,
            name=name,
            description=description,
            tags=tags,
        )

    def delete_session(self, session_id: str) -> bool:
        return self._session_manager.delete(session_id)

    def list_sessions(self, include_metadata: bool = True) -> List[Dict]:
        """
        列出所有会话

        Args:
            include_metadata: 是否包含元数据
        """
        return self._session_manager.list_sessions(include_metadata=include_metadata)

    def get_session_name(self, session_id: str) -> Optional[str]:
        """获取会话名称（便捷方法）"""
        return self._session_manager.get_session_name(session_id)

    # ========== 统计 ==========
    def get_stats(self) -> dict:
        return self._stats

    # ========== 生命周期 ==========
    def shutdown(self):
        self._session_manager.shutdown()


# ========== LLM Gateway 全场景单元测试 ==========


# ========== 简单交互测试 ==========

async def interactive_chat():
    """简单的持续交互测试"""
    print("=" * 50)
    print("LLM Gateway 交互对话 (输入 quit 退出)")
    print("=" * 50)

    gateway = LLMGateway()
    session_list = gateway.list_sessions()
    if not session_list:
        session = gateway.create_session(name="test_gatway_1")
    else:
        session_id = session_list[0]["session_id"]
        session = gateway.get_session(session_id)


    #print(f"会话ID: {session.id}\n")

    while True:
        user_input = input("你: ")
        resp = session.chat_stream(user_input)
        if resp:
            print("AI: ", resp)
if __name__ == "__main__":

    asyncio.run(interactive_chat())