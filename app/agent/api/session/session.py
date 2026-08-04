#!/usr/bin/env python3
"""会话管理模块 - 基于摘要的上下文过滤会话"""
import queue
import uuid
import json
import subprocess
import time
import shutil
import threading
import secrets
from pathlib import Path
from typing import Dict, Optional, List, Any, Union, Generator, Tuple, Set, Callable
import logging
from datetime import datetime, timezone
from app.agent.api.memory_manager_src.memory_manager import get_memory_manager, MemorySpace
# ============================================================
# 🔥 所有配置通过函数动态获取，不缓存值
# ============================================================

def _get_agent_config():
    """延迟导入 agent_config，避免循环导入"""
    from app.agent.api import agent_config
    return agent_config


def get_default_model():
    """获取当前默认模型"""
    return _get_agent_config().DEFAULT_MODEL


def get_default_temperature():
    """获取当前默认温度"""
    return _get_agent_config().DEFAULT_TEMPERATURE


def get_default_top_p():
    """获取当前默认 top_p"""
    return _get_agent_config().DEFAULT_TOP_P


def get_default_max_tokens():
    """获取当前默认最大 token 数"""
    return _get_agent_config().DEFAULT_MAX_TOKENS


def get_default_thinking():
    """获取当前默认思考模式"""
    return _get_agent_config().DEFAULT_THINKING


def get_safe_verify_timeout():
    """获取当前安全验证超时时间"""
    return _get_agent_config().SAFE_VERIFY_TIMEOUT


def get_root_dir():
    """获取当前根目录"""
    return _get_agent_config().ROOT_DIR


def get_conversations_dir():
    """获取当前会话目录"""
    return _get_agent_config().CONVERSATIONS_DIR


def get_is_windows():
    """获取当前系统是否为 Windows"""
    return _get_agent_config().IS_WINDOWS


def get_is_linux():
    """获取当前系统是否为 Linux"""
    return _get_agent_config().IS_LINUX


def get_is_macos():
    """获取当前系统是否为 macOS"""
    return _get_agent_config().IS_MACOS


# ============================================================
# 🔥 延迟导入（在配置函数定义之后）
# ============================================================
def _get_goal_driven_executor():
    """延迟导入 GoalDrivenExecutor"""
    from app.agent.api.session.goal_orchestrator import GoalDrivenExecutor
    return GoalDrivenExecutor


def _get_memory_manager():
    """延迟导入 memory_manager"""
    from app.agent.api.memory_manager_src.memory_manager import get_memory_manager, MemorySpace
    return get_memory_manager, MemorySpace


# ============================================================
# 常量（只保留固定值）
# ============================================================
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.95
DEFAULT_MAX_TOKENS = 16384
DEFAULT_THINKING = True
MAX_TOOL_ITERATIONS = 99
SUMMARY_MAX_CHARS = 2000
MAX_BATCH_CHARS = 1500


# ============================================================
# 🔥 动态获取 SESSIONS_ROOT（每次调用时获取最新路径）
# ============================================================
def get_sessions_root() -> Path:
    """获取当前会话根目录（动态获取）"""
    return get_root_dir() / "sessions"


# ============================================================
# 🔥 Session 类 - 所有配置动态获取，不存储任何配置值
# ============================================================
class Session:
    def __init__(
        self,
        session_id: str,
        work_dir: Path,
        memory_space: Optional['MemorySpace'] = None,
        # 🔥 不再接收任何配置参数，所有配置从 agent_config 动态读取
    ):
        self.session_id = session_id
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # 记忆空间：如果未传入，则从全局管理器获取
        if memory_space is None:
            get_memory_manager, _ = _get_memory_manager()
            mgr = get_memory_manager()
            memory_space = mgr.get_space_handle(session_id)
        self._memory_space = memory_space

        # 日志
        self.logger = logging.getLogger(f"Session.{session_id}")
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        # 统计（仅用于记录）
        self._last_stats = {}

    # ============================================================
    # 🔥 所有配置都是 property，每次从 agent_config 读取最新值
    # ============================================================
    @property
    def model(self) -> str:
        """动态获取当前默认模型"""
        return get_default_model()

    @property
    def temperature(self) -> float:
        """动态获取当前默认温度"""
        return get_default_temperature()

    @property
    def top_p(self) -> float:
        """动态获取当前默认 top_p"""
        return get_default_top_p()

    @property
    def max_tokens(self) -> int:
        """动态获取当前默认最大 token 数"""
        return get_default_max_tokens()

    @property
    def thinking(self) -> bool:
        """动态获取当前默认思考模式"""
        return get_default_thinking()

    # ============================================================
    # 方法
    # ============================================================
    def chat_sync(self, message: str) -> str:
        """同步对话"""
        def dummy_callback(typ: str, data: str, percent: Optional[float]) -> bool:
            return False

        # 🔥 每次调用时从 agent_config 获取最新配置
        GoalDrivenExecutor = _get_goal_driven_executor()
        executor = GoalDrivenExecutor(
            session_id=self.session_id,
            model=self.model,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            thinking=self.thinking,
            stream=False,
            log_file=str(self.work_dir / "executor.log"),
            enable_thinking_log=False,
        )

        user_message_time = datetime.now(timezone.utc).isoformat()

        success, history_item = executor.process_request(
            user_message=message,
            memory_space=self._memory_space,
            stream_callback=dummy_callback,
            stream=False,
            stop_signal={}
        )

        self._last_stats = {
            "llm_call_count": executor.llm_call_count,
            "llm_total_tokens": executor.llm_total_tokens,
            "llm_total_time": executor.llm_total_time,
        }

        if success:
            reply = history_item.get("content", "")
            summary = history_item.get("summary", "")

            user_record = {
                "role": "user",
                "content": message,
                "summary": message,
                "created_at": user_message_time
            }
            assistant_record = {
                "role": "assistant",
                "content": reply,
                "summary": summary or reply,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            self._memory_space.append_batch([user_record, assistant_record])
            return reply
        else:
            return history_item.get("content", "任务执行失败")

    def chat_stream(self, message: str, safe_verify_callback=None):
        """流式对话"""
        user_message_time = datetime.now(timezone.utc).isoformat()

        # 🔥 每次调用时从 agent_config 获取最新配置
        GoalDrivenExecutor = _get_goal_driven_executor()
        executor = GoalDrivenExecutor(
            session_id=self.session_id,
            model=self.model,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            thinking=self.thinking,
            stream=True,
            log_file=str(self.work_dir / "executor.log"),
            enable_thinking_log=False,
        )

        event_queue = queue.Queue()
        full_reply = ""
        full_thinking = ""

        def stream_callback(typ: str, data: str, percent: Optional[float]) -> bool:
            nonlocal full_reply, full_thinking
            if typ == "thinking":
                full_thinking += data
            elif typ == "content":
                full_reply += data
            elif typ == "final":
                full_reply = data
            elif typ == "usage":
                pass
            elif typ == "safe_verify":
                verify_id = secrets.token_urlsafe(32)
                safe_data = {
                    "verify_id": verify_id,
                    "reason": data,
                    "timeout": get_safe_verify_timeout(),
                }
                event_queue.put(("event", typ, json.dumps(safe_data, ensure_ascii=False), None))

                if safe_verify_callback is not None:
                    is_safe, suggestion = safe_verify_callback(verify_id, get_safe_verify_timeout())
                else:
                    print("safe_verify_callback is none")
                    is_safe = False
                return is_safe
            else:
                return False

            event_queue.put(("event", typ, data, percent))
            return False

        def run_executor():
            try:
                success, history_item = executor.process_request(
                    user_message=message,
                    memory_space=self._memory_space,
                    stream_callback=stream_callback,
                    stream=True,
                    stop_signal={}
                )
                event_queue.put(("done", success, history_item))
            except Exception as e:
                event_queue.put(("error", str(e), None))

        thread = threading.Thread(target=run_executor, daemon=True)
        thread.start()

        success = False
        history_item = None
        reply = ""

        while True:
            try:
                item = event_queue.get()
            except KeyboardInterrupt:
                yield f"data: {json.dumps({'type': 'error', 'error': 'Interrupted'})}\n\n"
                break

            typ = item[0]

            if typ == "event":
                _, typ_data, data, percent = item
                event = {"type": typ_data, "data": data, "percent": percent}
                if typ_data == "safe_verify":
                    print(f"send safe verify to client:{data}")
                    pass
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            elif typ == "done":
                _, success, history_item = item
                self._last_stats = {
                    "llm_call_count": executor.llm_call_count,
                    "llm_total_tokens": executor.llm_total_tokens,
                    "llm_total_time": executor.llm_total_time,
                }

                print("编排退出: ", success)
                if history_item:
                    reply = history_item.get("content", "") or full_reply
                    summary = history_item.get("summary", "") or full_thinking or (reply[:50] if reply else "")
                    print("content: ", reply)
                else:
                    reply = full_reply
                    summary = full_thinking or reply[:50] if reply else ""

                if not reply:
                    if full_thinking:
                        reply = f"[思考过程]\n{full_thinking}"
                        summary = summary or "思考过程"
                    else:
                        reply = "(无回复内容)"
                        summary = summary or "无回复"

                if reply and reply != "(无回复内容)":
                    user_record = {
                        "role": "user",
                        "content": message,
                        "summary": message,
                        "created_at": user_message_time
                    }
                    assistant_record = {
                        "role": "assistant",
                        "content": reply,
                        "summary": summary or reply,
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                    try:
                        self._memory_space.append_batch([user_record, assistant_record])
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'error': f'Memory save failed: {e}'}, ensure_ascii=False)}\n\n"

                print(f"最终回复: {reply}")
                yield f"data: {json.dumps({'type': 'done', 'success': success, 'reply': reply}, ensure_ascii=False)}\n\n"
                break
            elif typ == "error":
                _, error_msg, _ = item
                yield f"data: {json.dumps({'type': 'error', 'error': error_msg}, ensure_ascii=False)}\n\n"
                break

        thread.join(timeout=1)

    # ============================================================
    # 其他方法
    # ============================================================
    def _make_sse_chunk(self, delta: dict) -> str:
        """构造 OpenAI SSE 格式数据块"""
        chunk = {
            "id": "chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": None
            }]
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    def _save_to_memory(self, reply: str, summary: str):
        if not reply:
            return
        if not summary:
            summary = reply[:50] + ("..." if len(reply) > 50 else "")
        self._memory_space.append(
            role="assistant",
            content=reply,
            summary=summary
        )
        self.logger.info(f"记忆已保存 (ID: {self.session_id})")

    def delete_memory(self, memory_id: str) -> bool:
        if not self._memory_space:
            self.logger.warning(f"会话 {self.session_id} 没有记忆空间")
            return False
        try:
            result = self._memory_space.delete(memory_id)
            if result:
                self.logger.info(f"记忆已删除: {memory_id}")
            else:
                self.logger.warning(f"记忆不存在: {memory_id}")
            return result
        except Exception as e:
            self.logger.error(f"删除记忆失败 {memory_id}: {e}")
            return False

    def delete_memories(self, memory_ids: List[str]) -> int:
        if not self._memory_space:
            self.logger.warning(f"会话 {self.session_id} 没有记忆空间")
            return 0
        if not memory_ids:
            return 0
        deleted_count = 0
        for mid in memory_ids:
            try:
                if self._memory_space.delete(mid):
                    deleted_count += 1
            except Exception as e:
                self.logger.error(f"删除记忆失败 {mid}: {e}")
        self.logger.info(f"批量删除完成: {deleted_count}/{len(memory_ids)}")
        return deleted_count

    def clear_all_memories(self) -> int:
        if not self._memory_space:
            self.logger.warning(f"会话 {self.session_id} 没有记忆空间")
            return 0
        try:
            summaries = self._memory_space.get_all_summaries()
            memory_ids = list(summaries.keys())
            if not memory_ids:
                return 0
            deleted_count = 0
            for mid in memory_ids:
                if self._memory_space.delete(mid):
                    deleted_count += 1
            self.logger.info(f"清空所有记忆: {deleted_count} 条")
            return deleted_count
        except Exception as e:
            self.logger.error(f"清空记忆失败: {e}")
            return 0

    def get_all_memory_ids(self) -> List[str]:
        if not self._memory_space:
            return []
        try:
            summaries = self._memory_space.get_all_summaries()
            return list(summaries.keys())
        except Exception as e:
            self.logger.error(f"获取记忆ID列表失败: {e}")
            return []

    def get_memory_count(self) -> int:
        if not self._memory_space:
            return 0
        try:
            summaries = self._memory_space.get_all_summaries()
            return len(summaries)
        except Exception as e:
            self.logger.error(f"获取记忆数量失败: {e}")
            return 0

    def get_last_stats(self) -> dict:
        return self._last_stats


# ============================================================
# 🔥 SessionManager - 使用动态路径
# ============================================================
class SessionManager:
    def __init__(self, sessions_root: Optional[Path] = None):
        """
        Args:
            sessions_root: 会话工作目录根路径，如果不传则使用全局配置
        """
        if sessions_root is None:
            sessions_root = get_sessions_root()
        self._sessions_root = sessions_root
        self._sessions_root.mkdir(parents=True, exist_ok=True)

        self._sessions: Dict[str, Session] = {}
        self._session_ids: Set[str] = set()
        self._index_file = self._sessions_root / "sessions_index.json"
        self._load_index()

    # -------------------- 索引持久化 --------------------
    def _load_index(self) -> None:
        """加载会话 ID 列表"""
        if self._index_file.exists():
            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._session_ids = set(data.get("session_ids", []))
            except Exception as e:
                print(f"⚠️ 加载会话索引失败: {e}")
                self._session_ids = set()

    def _save_index(self) -> None:
        """保存会话 ID 列表"""
        try:
            with open(self._index_file, "w", encoding="utf-8") as f:
                json.dump({
                    "session_ids": list(self._session_ids)
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ 保存会话索引失败: {e}")

    # -------------------- 会话工厂 --------------------
    def create(
            self,
            session_id: Optional[str] = None,
            name: Optional[str] = None,
            description: Optional[str] = None,
            tags: Optional[List[str]] = None,
            # 🔥 移除所有配置参数，Session 自己从 agent_config 动态读取
    ) -> Session:
        """创建新会话"""
        if session_id is None:
            session_id = str(uuid.uuid4())

        if session_id in self._session_ids:
            raise ValueError(f"会话 {session_id} 已存在")

        work_dir = self._sessions_root / session_id

        # 1. 先在记忆模块中创建空间
        try:
            get_memory_manager, _ = _get_memory_manager()
            mgr = get_memory_manager()
            space = mgr.create_space(
                space_id=session_id,
                name=name or f"会话_{session_id[:8]}",
                description=description or "",
                tags=tags or []
            )
            print(f"✅ 记忆空间已创建: {session_id}")
        except ValueError as e:
            print(f"⚠️ 记忆空间已存在: {e}")
            get_memory_manager, _ = _get_memory_manager()
            mgr = get_memory_manager()
            space = mgr.get_space_handle(session_id)
            if name or description or tags:
                space.update_metadata(
                    name=name,
                    description=description,
                    tags=tags
                )
        except Exception as e:
            print(f"❌ 创建记忆空间失败: {e}")
            raise RuntimeError(f"创建会话失败: {e}")

        # 2. 创建 Session 对象（不传入任何配置参数）
        session = Session(
            session_id=session_id,
            work_dir=work_dir,
            # 🔥 不传入 model, temperature, top_p, max_tokens, thinking
        )

        # 3. 注册
        self._session_ids.add(session_id)
        self._sessions[session_id] = session
        self._save_index()

        return session

    # -------------------- 获取会话 --------------------
    def get(self, session_id: str) -> Optional[Session]:
        """获取已存在的会话"""
        if session_id in self._sessions:
            return self._sessions[session_id]

        if session_id not in self._session_ids:
            return None

        try:
            work_dir = self._sessions_root / session_id
            if not work_dir.exists():
                self._session_ids.discard(session_id)
                self._save_index()
                return None

            get_memory_manager, _ = _get_memory_manager()
            mgr = get_memory_manager()
            if not mgr.space_exists(session_id):
                mgr.create_space(
                    space_id=session_id,
                    name=f"会话_{session_id[:8]}"
                )
                print(f"✅ 为已存在会话创建记忆空间: {session_id}")

            # 🔥 不传入任何配置参数
            session = Session(
                session_id=session_id,
                work_dir=work_dir,
            )
            self._sessions[session_id] = session
            return session
        except Exception as e:
            print(f"❌ 加载会话 {session_id} 失败: {e}")
            return None

    def get_or_create(
        self,
        session_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        # 🔥 移除所有配置参数
    ) -> Session:
        """获取已有会话，若不存在则创建"""
        session = self.get(session_id)
        if session is None:
            session = self.create(
                session_id=session_id,
                name=name,
                description=description,
                tags=tags,
            )
        return session

    # -------------------- 元数据管理 --------------------
    def _get_memory_space(self, session_id: str, create_if_missing: bool = True) -> Optional['MemorySpace']:
        """获取会话对应的记忆空间"""
        try:
            get_memory_manager, MemorySpace = _get_memory_manager()
            mgr = get_memory_manager()
            if create_if_missing:
                return mgr.get_or_create_space(session_id)
            else:
                if mgr.space_exists(session_id):
                    return mgr.get_space_handle(session_id)
                return None
        except Exception as e:
            print(f"⚠️ 获取记忆空间失败 ({session_id}): {e}")
            return None

    def _update_session_metadata(
        self,
        session_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        **extra
    ) -> bool:
        """更新会话元数据"""
        try:
            get_memory_manager, _ = _get_memory_manager()
            mgr = get_memory_manager()
            space = mgr.get_or_create_space(session_id)

            update_kwargs = {}
            if name is not None:
                update_kwargs['name'] = name
            if description is not None:
                update_kwargs['description'] = description
            if tags is not None:
                update_kwargs['tags'] = tags
            if extra:
                update_kwargs.update(extra)

            if update_kwargs:
                return space.update_metadata(**update_kwargs)
            return True
        except Exception as e:
            print(f"⚠️ 更新元数据失败 ({session_id}): {e}")
            return False

    def get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话元数据"""
        space = self._get_memory_space(session_id)
        if not space:
            return None

        try:
            metadata = space.get_metadata()
            all_infos = space.get_all_infos(limit=None)
            metadata['memory_count'] = len(all_infos)
            return metadata
        except Exception as e:
            print(f"⚠️ 获取元数据失败 ({session_id}): {e}")
            return None

    def update_session_name(self, session_id: str, name: str) -> bool:
        """更新会话名称"""
        if not name or not name.strip():
            return False
        return self._update_session_metadata(session_id, name=name.strip())

    def get_session_name(self, session_id: str) -> Optional[str]:
        """获取会话名称"""
        metadata = self.get_session_metadata(session_id)
        if metadata:
            return metadata.get('name', '')
        return None

    def update_session_description(self, session_id: str, description: str) -> bool:
        """更新会话描述"""
        return self._update_session_metadata(session_id, description=description)

    def update_session_tags(self, session_id: str, tags: List[str]) -> bool:
        """更新会话标签"""
        return self._update_session_metadata(session_id, tags=tags)

    # -------------------- 删除会话 --------------------
    def delete(self, session_id: str) -> bool:
        """删除会话"""
        if session_id not in self._session_ids:
            return False

        self._sessions.pop(session_id, None)

        work_dir = self._sessions_root / session_id
        if work_dir.exists():
            self._delete_directory(work_dir)

        self._session_ids.discard(session_id)
        self._save_index()
        return True

    def _delete_directory(self, path: Path) -> None:
        """跨平台强力删除目录"""
        if not path.exists():
            return

        print(f"🗑️ 删除会话目录: {path}")

        import platform
        if platform.system() == "Windows":
            try:
                subprocess.run(
                    f'attrib -r -s -h /s /d "{path}"',
                    shell=True,
                    capture_output=True,
                    timeout=5
                )
                time.sleep(0.2)
                result = subprocess.run(
                    f'rmdir /s /q "{path}"',
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    print(f"✅ rmdir 删除成功: {path}")
                    return
            except Exception as e:
                print(f"⚠️ rmdir 异常: {e}")

        try:
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                print(f"✅ shutil 删除成功: {path}")
                return
        except Exception as e:
            print(f"⚠️ shutil 删除失败: {e}")

        if platform.system() == "Windows":
            try:
                ps_cmd = f'Remove-Item -Path "{path}" -Force -Recurse -ErrorAction SilentlyContinue'
                subprocess.run(
                    ["powershell", "-Command", ps_cmd],
                    capture_output=True,
                    timeout=10
                )
                if not path.exists():
                    print(f"✅ PowerShell 删除成功: {path}")
                    return
            except Exception as e:
                print(f"⚠️ PowerShell 删除异常: {e}")

        try:
            temp_name = f"__deleting_{uuid.uuid4().hex[:8]}"
            temp_path = path.parent / temp_name
            path.rename(temp_path)
            print(f"↪️ 重命名: {path} -> {temp_path}")
            time.sleep(0.5)
            shutil.rmtree(temp_path, ignore_errors=True)
        except Exception as e:
            print(f"⚠️ 重命名删除失败: {e}")

    # -------------------- 列出会话 --------------------
    def list_sessions(self, include_metadata: bool = True) -> List[Dict]:
        """列出所有会话"""
        result = []
        for sid in self._session_ids:
            work_dir = self._sessions_root / sid
            exists = work_dir.exists()

            item = {
                "session_id": sid,
                "work_dir": str(work_dir),
                "exists": exists,
                "in_memory": sid in self._sessions,
            }

            if include_metadata:
                metadata = self.get_session_metadata(sid)
                if metadata:
                    item.update({
                        "name": metadata.get("name", ""),
                        "description": metadata.get("description", ""),
                        "tags": metadata.get("tags", []),
                        "created_at": metadata.get("created_at"),
                        "updated_at": metadata.get("updated_at"),
                        "memory_count": metadata.get("memory_count", 0),
                    })
                else:
                    item.update({
                        "name": f"会话_{sid[:8]}",
                        "description": "",
                        "tags": [],
                        "created_at": None,
                        "updated_at": None,
                        "memory_count": 0,
                    })

            result.append(item)
        return result

    def get_session_by_name(self, name: str) -> Optional[Session]:
        """根据名称查找会话"""
        for session in self._sessions.values():
            metadata = self.get_session_metadata(session.session_id)
            if metadata and metadata.get("name") == name:
                return session

        for sid in self._session_ids:
            if sid in self._sessions:
                continue
            metadata = self.get_session_metadata(sid)
            if metadata and metadata.get("name") == name:
                return self.get(sid)

        return None

    def get_active_sessions(self) -> List[Session]:
        """获取当前内存中加载的会话对象"""
        return list(self._sessions.values())

    def shutdown(self) -> None:
        """关闭所有会话"""
        for session in self._sessions.values():
            pass
        self._sessions.clear()


# ========== 辅助清理 ==========
def _clean_index():
    """删除索引文件"""
    index_file = get_sessions_root() / "sessions_index.json"
    if index_file.exists():
        index_file.unlink()


# ==================== 测试函数 ====================
def test_session_creation():
    """测试会话创建"""
    print("\n=== 测试1: 会话创建 ===")
    _clean_index()
    mgr = SessionManager(sessions_root=get_sessions_root())
    session = mgr.create()
    assert session.session_id is not None
    assert session.work_dir.exists()
    assert session.work_dir.parent == get_sessions_root()
    assert session._memory_space is not None
    print(f"✅ 会话创建测试通过 (工作目录: {session.work_dir})")


def test_session_manager_basic():
    """测试会话管理器基本功能"""
    print("\n=== 测试2: 会话管理器 ===")
    _clean_index()
    mgr = SessionManager(sessions_root=get_sessions_root())

    s1 = mgr.create()
    s2 = mgr.create()
    assert len(mgr._sessions) == 2
    assert s1.work_dir.parent == get_sessions_root()
    assert s2.work_dir.parent == get_sessions_root()

    assert mgr.get(s1.session_id).session_id == s1.session_id

    s3 = mgr.get_or_create("fixed-id")
    assert s3.session_id == "fixed-id"
    s3a = mgr.get_or_create("fixed-id")
    assert s3a.session_id == s3.session_id
    assert len(mgr._sessions) == 3

    sessions_list = mgr.list_sessions()
    assert len(sessions_list) == 3

    assert mgr.delete(s1.session_id) is True
    assert len(mgr._sessions) == 2

    mgr.delete(s2.session_id)
    mgr.delete(s3.session_id)
    print("✅ 会话管理器测试通过")


def test_memory_space_integration():
    """测试 memory_space 集成"""
    print("\n=== 测试3: memory_space 集成测试 ===")
    _clean_index()
    mgr = SessionManager(sessions_root=get_sessions_root())
    session = mgr.create()

    test_id = session._memory_space.append(
        role="user",
        content="你好，这是测试消息",
        summary="用户打招呼"
    )
    assert test_id is not None

    summaries = session._memory_space.get_all_summaries()
    assert len(summaries) == 1
    assert "用户打招呼" in list(summaries.values())[0]

    memory = session._memory_space.get_memory([test_id])
    assert test_id in memory
    assert memory[test_id]["content"] == "你好，这是测试消息"
    assert memory[test_id]["role"] == "user"

    session._memory_space.delete(test_id)
    print("✅ memory_space 集成测试通过")


def test_chat_sync_basic():
    """测试 chat_sync 基本功能"""
    print("\n=== 测试4: chat_sync 基本功能 ===")
    _clean_index()
    mgr = SessionManager(sessions_root=get_sessions_root())
    session = mgr.create()

    summaries = session._memory_space.get_all_summaries()
    assert len(summaries) == 0

    print("⚠️ chat_sync 测试跳过（需要真实 LLM 调用）")
    print("✅ chat_sync 基本功能测试通过（跳过 LLM 调用）")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("会话模块单元测试（基于 memory 模块）")
    print("=" * 60)
    print(f"会话根目录: {get_sessions_root()}")

    test_session_creation()
    test_session_manager_basic()
    test_memory_space_integration()
    test_chat_sync_basic()

    print("\n" + "=" * 60)
    print("🎉 所有测试通过")
    print("=" * 60)


# ==================== 交互式会话 ====================
def run_interactive_session(
    session: Session,
    initial_stream_mode: bool = False,
    show_thinking: bool = False,
    manager: Optional[SessionManager] = None
):
    """交互式运行会话"""
    stream_mode = initial_stream_mode

    print(f"会话 ID: {session.session_id}")
    print(f"当前输出模式: {'流式' if stream_mode else '非流式'}")
    print("输入消息开始对话，输入 ':q' 退出，':stream' / ':nostream' 切换模式")
    if manager:
        print("  :list   - 显示所有会话")
    print("  :history - 显示当前会话的历史记忆")

    while True:
        try:
            text = input(">: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if not text:
            continue

        if text == "/q":
            break
        elif text == "/stream":
            stream_mode = True
            print("已切换为流式输出")
            continue
        elif text == "/nostream":
            stream_mode = False
            print("已切换为非流式输出")
            continue

        if text == "/list" and manager:
            sessions_info = manager.list_sessions()
            if not sessions_info:
                print("暂无会话。")
            else:
                print("所有会话：")
                for info in sessions_info:
                    sid = info.get("session_id", "")
                    work_dir = info.get("work_dir", "")
                    in_memory = "活跃" if info.get("in_memory") else "已卸载"
                    print(f"  - {sid}  (目录: {work_dir}, 状态: {in_memory})")
            continue

        if text == "/history":
            if not session._memory_space:
                print("当前会话没有记忆空间。")
                continue

            summaries = session._memory_space.get_all_summaries()
            if not summaries:
                print("暂无历史记忆。")
            else:
                print(f"历史记忆（共 {len(summaries)} 条）：")
                for mid, summary in summaries.items():
                    mem = session._memory_space.get_memory([mid])
                    if mid in mem:
                        content = mem[mid].get("content", "")
                        role = mem[mid].get("role", "unknown")
                        print(f"  [{role}] {mid}: {summary}")
                        if content:
                            if len(content) > 200:
                                content = content[:200] + "..."
                            print(f"      内容: {content}")
                    else:
                        print(f"  {mid}: {summary}")
            continue

        if stream_mode:
            gen = session.chat_stream(text, None)
            print("🤖 [流式] ", end="", flush=True)
            full_reply = ""
            for chunk in gen:
                if chunk.startswith("data: "):
                    if chunk == "data: [DONE]\n\n":
                        break
                    try:
                        data = json.loads(chunk[6:].strip())
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta:
                            print(delta["content"], end="", flush=True)
                            full_reply += delta["content"]
                        elif show_thinking and "reasoning_content" in delta:
                            print(f"\n[思考] {delta['reasoning_content']}")
                    except Exception:
                        pass
            print("\n")
        else:
            result = session.chat_sync(text)
            print(f"🤖 [非流式] {result}")


# ==================== 主入口 ====================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_all_tests()
    else:
        mgr = SessionManager(sessions_root=get_sessions_root())
        sessions = mgr.list_sessions()
        if sessions:
            session_id = sessions[0]["session_id"]
            session = mgr.get(session_id)
            if session is None:
                session = mgr.create()
        else:
            session = mgr.create()

        run_interactive_session(session, initial_stream_mode=True, show_thinking=True, manager=mgr)