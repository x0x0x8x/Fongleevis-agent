"""
SQLite 长期记忆存储模块
======================
基于 SQLite 的 Agent 长期记忆持久化组件，多空间隔离 + 冷热分离架构，线程安全。

核心组件
--------
- MemoryManager  : 全局单例，负责库表初始化、空间句柄管理
- MemorySpace    : 单记忆空间实例，承载本空间全部 CRUD
- get_memory_manager() : 获取全局管理器入口

快速示例
--------
mgr = get_memory_manager()
space = mgr.get_space_handle("agent_001")

# 新增记忆
mid = space.append("user", "你好", "用户问候")

# 查询
space.get_infos([mid])          # 元数据（不计访问次数）
space.get_memory([mid])         # 完整正文（自动累计访问）
space.get_all_summaries()       # 全量 ID-摘要映射

# 更新/删除
space.update(mid, summary="用户打招呼")
space.delete(mid)

# 销毁整个空间
space.destroy()

主要接口
--------
MemorySpace:
  append(role, content, summary) -> str
  append_batch(memories) -> List[str]
  update(memory_id, content=None, summary=None) -> bool
  delete(memory_id) -> bool
  destroy() -> int
  get_infos(memory_ids) -> Dict
  get_all_infos(order_by=None, limit=None) -> Dict
  get_summaries(memory_ids) -> Dict
  get_all_summaries(order_by=None, limit=None) -> Dict
  get_memory(memory_ids) -> Dict

注意事项
--------
- role / content / summary 禁止空白字符串
- 排序仅支持白名单字段，默认 created_at ASC
- 空间 destroy 后实例失效，继续操作抛 RuntimeError
- 默认开启 WAL 模式，读写并发性能更优
"""

import sqlite3
import threading
import secrets
import string
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Dict, Optional, Union, Any, Tuple
from app.agent.api.agent_config import IS_WINDOWS, IS_LINUX, IS_MACOS, DEFAULT_ROOT_DIR

# ==================== 全局可配置变量 ====================
# 数据库文件路径
DEFAULT_DB_PATH = os.path.join(DEFAULT_ROOT_DIR, "memory.db")
# 列表查询兜底排序规则：默认创建时间正序
DEFAULT_ORDER_BY = "created_at ASC"
# 搜索接口参考阈值；接口不传limit默认查询全部
DEFAULT_SEARCH_LIMIT = 10
# 是否启用WAL预写日志模式(True=WAL，False=DELETE)，提升读写并发，推荐开启
ENABLE_WAL = True
# 事务落盘同步策略：FULL(最高安全)/NORMAL(均衡)/OFF(极速，断电易损坏数据库)
SYNCHRONOUS_MODE = "NORMAL"

# ID生成配置
ID_CHARSET = string.ascii_letters + string.digits
ID_LENGTH = 16

def _gen_memory_id() -> str:
    """生成16位随机唯一记忆ID"""
    return "".join(secrets.choice(ID_CHARSET) for _ in range(ID_LENGTH))

# ==================== 全局单例管理器 ====================
class MemoryManager:
    """全局记忆管理器（线程安全单例）
    职责：数据库资源、表结构初始化、空间目录管理、空间句柄缓存
    不提供任何单空间内的数据增删改查接口
    """
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, db_path: str = None):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self, db_path: str = None):
        if self._initialized:
            return
        self.db_path = db_path or DEFAULT_DB_PATH

        # 空间句柄缓存：保证每个 space_id 全局唯一实例
        self._space_handles: Dict[str, "MemorySpace"] = {}
        self._space_handle_lock = threading.Lock()

        # 合法排序表达式白名单（全局通用规则）
        self._allowed_order_expr = {
            "created_at DESC",
            "created_at ASC",
            "updated_at DESC",
            "updated_at ASC",
            "length DESC",
            "length ASC",
            "access_count DESC",
            "access_count ASC",
            "last_access DESC",
            "last_access ASC",
        }

        self._init_db()
        self._initialized = True

    @contextmanager
    def _new_conn(self):
        """数据库连接工厂，供所有空间复用"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if ENABLE_WAL:
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(f"PRAGMA synchronous={SYNCHRONOUS_MODE};")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """初始化全局数据库表结构与索引，并自动迁移新增字段"""
        with self._new_conn() as conn:
            conn.executescript("""
                -- 热表：检索元数据、摘要、时间、统计（高频批量读取）
                CREATE TABLE IF NOT EXISTS memory_info (
                    memory_id TEXT PRIMARY KEY,
                    space_id TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    length INTEGER DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_access DATETIME
                );

                -- 冷表：原始对话内容（低频读取，大容量）
                -- 注意：已包含 tool_calls 和 tool_call_id 字段（新版本）
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    space_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls TEXT,          -- JSON 数组
                    tool_call_id TEXT,        -- 关联工具调用 ID
                    FOREIGN KEY(memory_id) REFERENCES memory_info(memory_id)
                );

                -- 空间元数据表
                CREATE TABLE IF NOT EXISTS space_metadata (
                    space_id TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    tags TEXT DEFAULT '[]',   -- JSON 数组
                    extra TEXT DEFAULT '{}'    -- JSON 对象，扩展字段
                );

                CREATE INDEX IF NOT EXISTS idx_info_space_ctime ON memory_info(space_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_info_space ON memory_info(space_id);
                CREATE INDEX IF NOT EXISTS idx_memories_space ON memories(space_id);
                CREATE INDEX IF NOT EXISTS idx_metadata_name ON space_metadata(name);
            """)

            # ---------- 自动迁移：为旧表添加新增列 ----------
            cursor = conn.execute("PRAGMA table_info(memories)")
            existing_columns = {row[1] for row in cursor.fetchall()}  # 集合便于查找

            if "tool_calls" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN tool_calls TEXT")
            if "tool_call_id" not in existing_columns:
                conn.execute("ALTER TABLE memories ADD COLUMN tool_call_id TEXT")

    def create_space(
        self,
        space_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        **extra
    ) -> "MemorySpace":
        """
        显式创建新的记忆空间（原子操作）

        Args:
            space_id: 空间ID（必填，不能为空）
            name: 空间名称（可选，默认使用 "会话_{space_id[:8]}"）
            description: 空间描述（可选）
            tags: 标签列表（可选）
            **extra: 其他扩展元数据

        Returns:
            MemorySpace 实例

        Raises:
            ValueError: 如果 space_id 为空或空间已存在
            RuntimeError: 如果创建失败
        """
        if not space_id or not space_id.strip():
            raise ValueError("space_id must not be empty or blank")

        # 使用事务：原子化创建 space_metadata 记录 + 缓存句柄
        now = datetime.now(timezone.utc).isoformat()
        default_name = name or f"会话_{space_id[:8]}"
        default_description = description or ""
        default_tags = json.dumps(tags or [])
        default_extra = json.dumps(extra or {})

        with self._new_conn() as conn:
            try:
                # 原子插入：如果 space_id 已存在，UNIQUE 约束会抛出 IntegrityError
                conn.execute(
                    """
                    INSERT INTO space_metadata 
                    (space_id, name, description, created_at, updated_at, tags, extra)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        space_id,
                        default_name,
                        default_description,
                        now,
                        now,
                        default_tags,
                        default_extra
                    )
                )
                conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"Space '{space_id}' already exists")
            except Exception as e:
                raise RuntimeError(f"Failed to create space '{space_id}': {e}")

        # 创建 MemorySpace 实例并缓存
        # 注意：此时 database 中已有 space_metadata 记录，get_space_handle 不会再隐式创建
        with self._space_handle_lock:
            if space_id in self._space_handles:
                # 理论上不应发生，但以防万一
                self._space_handles[space_id]._metadata_cache = None
                return self._space_handles[space_id]

            space = MemorySpace(space_id, self)
            # 预填充缓存，避免后续查询数据库
            space._metadata_cache = {
                "space_id": space_id,
                "name": default_name,
                "description": default_description,
                "created_at": now,
                "updated_at": now,
                "tags": tags or [],
                "extra": extra or {}
            }
            self._space_handles[space_id] = space
            return space

    def space_exists(self, space_id: str) -> bool:
        """
        检查空间是否存在

        Args:
            space_id: 空间ID

        Returns:
            bool: 空间是否存在
        """
        if not space_id or not space_id.strip():
            return False

        with self._new_conn() as conn:
            row = conn.execute(
                "SELECT space_id FROM space_metadata WHERE space_id = ?",
                (space_id,)
            ).fetchone()
            return row is not None

    def get_or_create_space(
        self,
        space_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        **extra
    ) -> "MemorySpace":
        """
        获取已有空间，如果不存在则创建

        Args:
            space_id: 空间ID
            name: 空间名称（仅在创建时生效）
            description: 空间描述（仅在创建时生效）
            tags: 标签列表（仅在创建时生效）
            **extra: 其他扩展元数据（仅在创建时生效）

        Returns:
            MemorySpace 实例
        """
        try:
            return self.get_space_handle(space_id)
        except ValueError:
            return self.create_space(space_id, name, description, tags, **extra)

    # 修改 list_all_space_ids 方法，处理不存在的空间
    def list_all_space_ids(self, include_empty: bool = True) -> List[str]:
        """
        枚举所有存在数据的记忆空间ID（去重）
        Args:
            include_empty: 是否包含仅有元数据但无记忆的空空间
        """
        with self._new_conn() as conn:
            if include_empty:
                # 从 metadata 表查询
                rows = conn.execute("SELECT DISTINCT space_id FROM space_metadata").fetchall()
            else:
                rows = conn.execute("SELECT DISTINCT space_id FROM memory_info").fetchall()
        return [r["space_id"] for r in rows]

    def get_space_handle(self, space_id: str) -> "MemorySpace":
        """
        获取指定记忆空间句柄（空间级单例）
        仅返回已存在的空间，不会自动创建
        如果空间不存在，抛出 ValueError
        """
        if not space_id.strip():
            raise ValueError("space_id must not be empty or blank")

        with self._space_handle_lock:
            # 如果已缓存，直接返回
            if space_id in self._space_handles:
                return self._space_handles[space_id]

            # 检查空间是否真实存在
            with self._new_conn() as conn:
                row = conn.execute(
                    "SELECT space_id FROM space_metadata WHERE space_id = ?",
                    (space_id,)
                ).fetchone()
                if not row:
                    raise ValueError(f"Space '{space_id}' does not exist")

            # 创建并缓存
            space = MemorySpace(space_id, self)
            self._space_handles[space_id] = space
            return space


    def close(self):
        """兼容关闭接口"""
        pass

    def _remove_space_cache(self, space_id: str):
        """内部：从缓存移除空间句柄，仅由MemorySpace.destroy调用"""
        with self._space_handle_lock:
            if space_id in self._space_handles:
                del self._space_handles[space_id]
# ==================== 单空间实例 ====================

class MemorySpace:
    """单个记忆空间实例（空间级单例）
    承载本空间内所有数据增删改查操作
    """

    def __init__(self, space_id: str, manager: MemoryManager):
        self.space_id = space_id
        self._manager = manager
        self._lock = threading.RLock()
        self._destroyed = False

        # 缓存元数据（延迟加载）
        self._metadata_cache: Optional[Dict] = None
        self._metadata_lock = threading.RLock()

    def destroy(self) -> int:
        """
        【销毁整个记忆空间】原子操作
        1. 删除数据库内该space所有记忆记录
        2. 删除空间元数据
        3. 通知管理器移除全局句柄缓存
        4. 将当前实例标记为失效
        :return: 总共删除条目数量(memory_info记录数)
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] already destroyed")

        with self._transaction() as conn:
            # 先删子表memories，再删主表memory_info（外键约束顺序）
            conn.execute("DELETE FROM memories WHERE space_id = ?", (self.space_id,))
            cur = conn.execute("DELETE FROM memory_info WHERE space_id = ?", (self.space_id,))
            deleted_count = cur.rowcount
            # 删除元数据
            conn.execute("DELETE FROM space_metadata WHERE space_id = ?", (self.space_id,))

        # 清除缓存
        with self._metadata_lock:
            self._metadata_cache = None

        # 通知管理器清除全局缓存
        self._manager._remove_space_cache(self.space_id)
        # 标记实例失效
        self._destroyed = True
        return deleted_count

    @contextmanager
    def _transaction(self):
        """本空间专属事务上下文
        同一空间串行执行，自动管理 commit/rollback
        """
        with self._lock:
            with self._manager._new_conn() as conn:
                with conn:
                    yield conn

    @staticmethod
    def get_current_iso_time() -> str:
        """返回当前UTC时间的ISO格式字符串，匹配 created_at 参数要求"""
        return datetime.now(timezone.utc).isoformat()
    # ---------- 写入接口 ----------
    def append(
            self,
            role: str,
            content: Optional[str] = None,
            summary: str = "",
            tool_calls: Optional[List[Dict]] = None,
            tool_call_id: Optional[str] = None,
            created_at: Optional[str] = None
    ) -> str:
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        role = role.strip()
        if not role:
            raise ValueError("role must not be empty")

        # ----- 校验 tool_calls -----
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise ValueError("tool_calls must be a list")
            for item in tool_calls:
                if not isinstance(item, dict):
                    raise ValueError("Each tool_call item must be a dict")

        # ----- 角色及内容校验 -----
        if role == "tool":
            if not tool_call_id:
                raise ValueError("tool role requires tool_call_id")
            if content is not None and content.strip() == "":
                content = None
        else:
            # 对于 assistant 且有 tool_calls，允许 content 为空或 None
            if role == "assistant" and tool_calls:
                if content is not None and not content.strip():
                    content = None
            else:
                # 其他情况：content 必须非空
                if not content or not content.strip():
                    raise ValueError(f"content must not be empty for role {role}")

        summary = summary.strip() if summary else ""
        length = len(content) if content else 0

        # 生成创建时间
        if created_at is None:
            created_at = self.get_current_iso_time()
        # 若需校验格式，可在此处添加（略）

        tool_calls_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None

        # ----- 带重试的插入 -----
        max_retries = 5
        for attempt in range(max_retries):
            memory_id = _gen_memory_id()
            try:
                with self._transaction() as conn:
                    conn.execute(
                        """
                        INSERT INTO memory_info
                        (memory_id, space_id, summary, length, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (memory_id, self.space_id, summary, length, created_at, created_at)
                    )
                    conn.execute(
                        """
                        INSERT INTO memories
                        (memory_id, space_id, role, content, tool_calls, tool_call_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (memory_id, self.space_id, role, content, tool_calls_json, tool_call_id)
                    )
                return memory_id  # 成功，返回
            except sqlite3.IntegrityError as e:
                # 如果冲突原因是主键或唯一约束，则重试
                if "PRIMARY KEY" in str(e) or "UNIQUE" in str(e):
                    if attempt == max_retries - 1:
                        raise RuntimeError("Failed to generate unique memory ID after retries")
                    continue
                raise  # 其他 IntegrityError 直接抛出
        # 理论上不会到这里
        raise RuntimeError("Unexpected error in append")

    def append_batch(self, memories: List[Dict[str, Any]]) -> List[str]:
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        # ----- 预处理与校验 -----
        prepared = []
        for idx, mem in enumerate(memories):
            role = mem.get("role", "").strip()
            if not role:
                raise ValueError(f"memories[{idx}]: role must not be empty")

            content = mem.get("content")
            summary = mem.get("summary", "").strip()
            tool_calls = mem.get("tool_calls")
            tool_call_id = mem.get("tool_call_id")
            created_at = mem.get("created_at")

            # 校验 tool_calls
            if tool_calls is not None:
                if not isinstance(tool_calls, list):
                    raise ValueError(f"memories[{idx}]: tool_calls must be a list")
                for item in tool_calls:
                    if not isinstance(item, dict):
                        raise ValueError(f"memories[{idx}]: each tool_call item must be a dict")

            # 角色及内容校验（逻辑同 append）
            if role == "tool":
                if not tool_call_id:
                    raise ValueError(f"memories[{idx}]: tool role requires tool_call_id")
                if content is not None and content.strip() == "":
                    content = None
            else:
                if role == "assistant" and tool_calls:
                    if content is not None and not content.strip():
                        content = None
                else:
                    if not content or not content.strip():
                        raise ValueError(f"memories[{idx}]: content required for role {role}")

            # 处理默认时间
            if created_at is None:
                created_at = self.get_current_iso_time()
            # 可选：校验格式

            length = len(content) if content else 0
            tool_calls_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None

            prepared.append({
                "role": role,
                "content": content,
                "summary": summary,
                "tool_calls_json": tool_calls_json,
                "tool_call_id": tool_call_id,
                "created_at": created_at,
                "length": length
            })

        # ----- 批量插入（事务内）-----
        memory_ids = []
        with self._transaction() as conn:
            for data in prepared:
                memory_id = _gen_memory_id()
                # 插入 memory_info
                conn.execute(
                    """
                    INSERT INTO memory_info
                    (memory_id, space_id, summary, length, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (memory_id, self.space_id, data["summary"], data["length"],
                     data["created_at"], data["created_at"])
                )
                # 插入 memories
                conn.execute(
                    """
                    INSERT INTO memories
                    (memory_id, space_id, role, content, tool_calls, tool_call_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (memory_id, self.space_id, data["role"], data["content"],
                     data["tool_calls_json"], data["tool_call_id"])
                )
                memory_ids.append(memory_id)
        return memory_ids

    # ---------- 更新接口 ----------
    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        tool_calls: Optional[List[Dict]] = None,
        tool_call_id: Optional[str] = None
    ) -> bool:
        """
        更新记忆内容或摘要或工具相关字段。
        若某字段为 None 则不更新，若为 None 但想置空，请传空字符串或空列表。
        注意：调用者需保证更新后的数据符合角色规则。
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        # 检查是否有任何更新
        if all(v is None for v in (content, summary, tool_calls, tool_call_id)):
            return True  # 无更新

        # 若更新 content 且内容为空字符串，我们将其存为 None（但若原角色是 tool 或 assistant 有 tool_calls 则可接受）
        if content is not None and isinstance(content, str) and not content.strip():
            content = None

        with self._transaction() as conn:
            updates_mem = []
            params_mem = []

            if content is not None:
                updates_mem.append("content = ?")
                params_mem.append(content)
                # 同时更新 length
                updates_mem.append("length = ?")
                params_mem.append(len(content) if content else 0)

            if summary is not None:
                summary = summary.strip() if summary else ""
                updates_mem.append("summary = ?")
                params_mem.append(summary)

            if tool_calls is not None:
                tool_calls_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
                updates_mem.append("tool_calls = ?")
                params_mem.append(tool_calls_json)

            if tool_call_id is not None:
                updates_mem.append("tool_call_id = ?")
                params_mem.append(tool_call_id)

            if updates_mem:
                # 更新 memory_info 的 updated_at
                now = datetime.now(timezone.utc).isoformat()
                updates_mem.append("updated_at = ?")
                params_mem.append(now)
                params_mem.append(memory_id)
                params_mem.append(self.space_id)

                sql = f"""
                    UPDATE memories SET {', '.join(updates_mem)}
                    WHERE memory_id = ? AND space_id = ?
                """
                conn.execute(sql, params_mem)
                # 同步更新 memory_info.updated_at
                conn.execute("""
                    UPDATE memory_info SET updated_at = ?
                    WHERE memory_id = ? AND space_id = ?
                """, (now, memory_id, self.space_id))
                return conn.total_changes > 0
            else:
                # 没有有效更新（理论上不会发生）
                return True

    # ---------- 删除接口 ----------
    def delete(self, memory_id: str) -> bool:
        """删除单条记忆"""
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")
        with self._transaction() as conn:
            conn.execute("DELETE FROM memories WHERE memory_id = ? AND space_id = ?", (memory_id, self.space_id))
            conn.execute("DELETE FROM memory_info WHERE memory_id = ? AND space_id = ?", (memory_id, self.space_id))
            return conn.total_changes > 0

    # ---------- 查询接口 ----------
    def get_infos(self, memory_ids: List[str]) -> Dict[str, Dict]:
        """
        根据一组memory_id批量查询记忆元数据（仅memory_info，不含content、role）
        不会触发访问计数更新
        :param memory_ids: 记忆ID列表
        :return: key = memory_id, value = 元数据字典
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        if not memory_ids:
            return {}

        placeholders = ",".join(["?"] * len(memory_ids))
        query = f"""
            SELECT memory_id, space_id, summary, length, created_at, updated_at, access_count, last_access
            FROM memory_info
            WHERE space_id = ? AND memory_id IN ({placeholders})
        """
        params = [self.space_id] + memory_ids

        with self._manager._new_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        result = {}
        for row in rows:
            item = dict(row)
            result[item["memory_id"]] = item
        return result

    def get_all_infos(self, order_by: str = None, limit: Optional[int] = None) -> Dict[str, Dict]:
        """
        获取本空间全部记忆【元数据摘要】
        返回字典 key = memory_id，value = 元数据（不含role、content）
        不会更新访问计数
        :param order_by: 排序规则
        :param limit: 最大条数，None 查询全部
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        query = """
            SELECT memory_id, space_id, summary, length, created_at, updated_at, access_count, last_access
            FROM memory_info
            WHERE space_id = ?
        """
        params: List[Union[str, int]] = [self.space_id]

        effective_order = order_by if order_by is not None else DEFAULT_ORDER_BY
        if effective_order is not None:
            expr = effective_order.strip()
            if expr in self._manager._allowed_order_expr:
                query += f" ORDER BY {expr}"

        if limit is not None and isinstance(limit, int) and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        with self._manager._new_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        result = {}
        for row in rows:
            item = dict(row)
            result[item["memory_id"]] = item
        return result

    def get_all_summaries(self, order_by: str = None, limit: Optional[int] = None) -> Dict[str, str]:
        """
        获取本空间全部记忆的【ID-摘要】映射
        返回字典 key = memory_id，value = summary 摘要文本
        仅查询两列，性能最轻量；不更新访问计数
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        query = "SELECT memory_id, summary FROM memory_info WHERE space_id = ?"
        params: List[Union[str, int]] = [self.space_id]

        effective_order = order_by if order_by is not None else DEFAULT_ORDER_BY
        if effective_order is not None:
            expr = effective_order.strip()
            if expr in self._manager._allowed_order_expr:
                query += f" ORDER BY {expr}"

        if limit is not None and isinstance(limit, int) and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        with self._manager._new_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return {row["memory_id"]: row["summary"] for row in rows}

    def get_summaries(self, memory_ids: List[str]) -> Dict[str, str]:
        """
        根据一批 memory_id 批量查询【摘要文本】
        返回字典 key = memory_id，value = summary
        不更新访问计数
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        if not memory_ids:
            return {}

        placeholders = ",".join(["?"] * len(memory_ids))
        query = f"""
            SELECT memory_id, summary
            FROM memory_info
            WHERE space_id = ? AND memory_id IN ({placeholders})
        """
        params = [self.space_id] + memory_ids

        with self._manager._new_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return {row["memory_id"]: row["summary"] for row in rows}

    def get_all_memory(self, order_by: str = None, limit: Optional[int] = None) -> Dict[str, Dict]:
        """
        获取本空间全部【完整记忆正文】（包含 memory_id, role, content, tool_calls, tool_call_id）
        返回字典 key = memory_id，value = 记忆正文字典
        不更新访问计数
        WARNING：数据量巨大时占用大量内存，仅用于导出、备份、全量同步
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        query = """
            SELECT m.memory_id, m.role, m.content, m.tool_calls, m.tool_call_id
            FROM memories m
            JOIN memory_info i ON m.memory_id = i.memory_id
            WHERE m.space_id = ?
        """
        params: List[Union[str, int]] = [self.space_id]

        effective_order = order_by if order_by is not None else DEFAULT_ORDER_BY
        if effective_order is not None:
            expr = effective_order.strip()
            if expr in self._manager._allowed_order_expr:
                query += f" ORDER BY {expr}"

        if limit is not None and isinstance(limit, int) and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        with self._manager._new_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        result = {}
        for row in rows:
            item = dict(row)
            if item.get("tool_calls"):
                try:
                    item["tool_calls"] = json.loads(item["tool_calls"])
                except json.JSONDecodeError:
                    item["tool_calls"] = None
            result[item["memory_id"]] = item
        return result

    def get_memory(self, memory_ids: List[str]) -> Dict[str, Dict]:
        """
        批量获取完整记忆正文（包含 memory_id, role, content, tool_calls, tool_call_id）
        触发访问计数更新
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")
        if not memory_ids:
            return {}

        placeholders = ",".join(["?"] * len(memory_ids))
        query = f"""
            SELECT memory_id, role, content, tool_calls, tool_call_id
            FROM memories
            WHERE memory_id IN ({placeholders}) AND space_id = ?
        """
        params = list(memory_ids) + [self.space_id]

        with self._manager._new_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        result = {}
        hit_ids = []
        for row in rows:
            item = dict(row)
            if item.get("tool_calls"):
                try:
                    item["tool_calls"] = json.loads(item["tool_calls"])
                except json.JSONDecodeError:
                    item["tool_calls"] = None
            result[row["memory_id"]] = item
            hit_ids.append(row["memory_id"])

        if hit_ids:
            self._update_access_stats(hit_ids)
        return result

    def get_messages_with_metadata(self, order_by: str = "created_at ASC") -> List[Dict]:
        """
        获取所有消息及其完整元数据（用于 API 展示）
        返回按时间排序的消息列表
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed")

        # 获取所有元数据
        infos = self.get_all_infos(order_by=order_by)
        if not infos:
            return []

        # 批量获取完整内容
        memory_ids = list(infos.keys())
        full_memories = self.get_memory(memory_ids) if memory_ids else {}

        messages = []
        for mid, info in infos.items():
            mem = full_memories.get(mid, {})
            messages.append({
                "id": mid,
                "role": mem.get("role", "unknown"),
                "content": mem.get("content", ""),
                "summary": info.get("summary", ""),
                "created_at": info.get("created_at"),
                "updated_at": info.get("updated_at"),
                "access_count": info.get("access_count", 0),
                "last_access": info.get("last_access"),
                "length": info.get("length", 0)
            })
        return messages

    # ---------- 元数据管理 ----------
    def _init_metadata(self) -> Dict:
        """初始化默认元数据"""
        now = datetime.now(timezone.utc).isoformat()
        default_data = {
            "space_id": self.space_id,
            "name": f"会话_{self.space_id[:8]}",
            "description": "",
            "created_at": now,
            "updated_at": now,
            "tags": [],
            "extra": {}
        }

        with self._manager._new_conn() as conn:
            conn.execute(
                """
                INSERT INTO space_metadata 
                (space_id, name, description, created_at, updated_at, tags, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.space_id,
                    default_data["name"],
                    default_data["description"],
                    now,
                    now,
                    json.dumps([]),
                    json.dumps({})
                )
            )

        self._metadata_cache = default_data
        return default_data.copy()

    def update_metadata(
            self,
            name: Optional[str] = None,
            description: Optional[str] = None,
            tags: Optional[List[str]] = None,
            **extra
    ) -> bool:
        """
        更新空间元数据
        Args:
            name: 空间名称
            description: 空间描述
            tags: 标签列表
            **extra: 其他扩展字段，会合并到 extra JSON 中
        Returns:
            bool: 是否更新成功
        """
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")

        # 先获取当前数据
        current = self.get_metadata()
        now = datetime.now(timezone.utc).isoformat()

        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name.strip() if name.strip() else "")

        if description is not None:
            updates.append("description = ?")
            params.append(description.strip() if description.strip() else "")

        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))

        if extra:
            # 合并额外字段
            new_extra = current.get("extra", {})
            new_extra.update(extra)
            updates.append("extra = ?")
            params.append(json.dumps(new_extra, ensure_ascii=False))

        if not updates:
            return True

        updates.append("updated_at = ?")
        params.append(now)
        params.append(self.space_id)

        sql = f"""
            UPDATE space_metadata 
            SET {', '.join(updates)}
            WHERE space_id = ?
        """

        with self._manager._new_conn() as conn:
            conn.execute(sql, params)
            success = conn.total_changes > 0

        # 清除缓存
        if success:
            with self._metadata_lock:
                self._metadata_cache = None

        return success

    def get_name(self) -> str:
        """便捷方法：获取空间名称"""
        return self.get_metadata().get("name", "")

    def set_name(self, name: str) -> bool:
        """便捷方法：设置空间名称"""
        return self.update_metadata(name=name)

    def get_metadata(self) -> Dict[str, Any]:
        if self._destroyed:
            raise RuntimeError(f"Space[{self.space_id}] has been destroyed, cannot operate")
        #print(f"self.space_id={self.space_id}")
        with self._metadata_lock:
            if self._metadata_cache is not None:
                return self._metadata_cache.copy()

            with self._manager._new_conn() as conn:
                row = conn.execute(
                    "SELECT space_id, name, description, created_at, updated_at, tags, extra "
                    "FROM space_metadata WHERE space_id = ?",
                    (self.space_id,)
                ).fetchone()

            if row:
                result = {
                    "space_id": row["space_id"],
                    "name": row["name"] or "",
                    "description": row["description"] or "",
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "tags": json.loads(row["tags"]) if row["tags"] else [],
                    "extra": json.loads(row["extra"]) if row["extra"] else {}
                }
            else:
                # 如果不存在，创建一个默认的
                result = self._init_metadata()

            self._metadata_cache = result
            return result.copy()

    def get_all_spaces_metadata(self) -> Dict[str, Dict]:
        """
        获取所有空间的基本元数据（用于列表展示）
        Returns:
            {space_id: {name, description, created_at, updated_at, tags, memory_count}}
        """
        with self._manager._new_conn() as conn:
            rows = conn.execute("""
                SELECT 
                    sm.space_id,
                    sm.name,
                    sm.description,
                    sm.created_at,
                    sm.updated_at,
                    sm.tags,
                    COUNT(mi.memory_id) as memory_count
                FROM space_metadata sm
                LEFT JOIN memory_info mi ON sm.space_id = mi.space_id
                GROUP BY sm.space_id
            """).fetchall()

        result = {}
        for row in rows:
            result[row["space_id"]] = {
                "space_id": row["space_id"],
                "name": row["name"] or "",
                "description": row["description"] or "",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "tags": json.loads(row["tags"]) if row["tags"] else [],
                "memory_count": row["memory_count"] or 0
            }
        return result

    # ---------- 内部工具 ----------
    def _update_access_stats(self, memory_ids: List[str]):
        """更新本空间内指定记忆的访问统计"""
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction() as conn:
            conn.executemany(
                """
                UPDATE memory_info
                SET access_count = access_count + 1, last_access = ?
                WHERE memory_id = ? AND space_id = ?
                """,
                [(now, mid, self.space_id) for mid in memory_ids]
            )

    def __repr__(self):
        return f"<MemorySpace id={self.space_id}>"
# ==================== 模块级便捷接口（自启动） ====================
_global_manager: Optional[MemoryManager] = None

def get_memory_manager() -> MemoryManager:
    """获取全局单例记忆管理器"""
    global _global_manager
    if _global_manager is None:
        _global_manager = MemoryManager()
    assert _global_manager is not None
    return _global_manager

# ==================== 调试辅助接口 ====================
def debug_dump_all_memories() -> Dict[str, Dict[str, Dict]]:
    """
    调试函数：返回所有空间下的全部记忆完整内容（role + content）。
    结构：{space_id: {memory_id: {'role': ..., 'content': ...}}}
    注意：数据量大时可能占用较多内存，仅限开发/测试环境使用。
    """
    mgr = get_memory_manager()
    result = {}
    for space_id in mgr.list_all_space_ids():
        space = mgr.get_space_handle(space_id)
        # 若空间已被销毁则跳过（正常情况下list_all_space_ids不会包含已销毁空间）
        if space._destroyed:
            continue
        result[space_id] = space.get_all_memory()
    return result


def debug_get_all_memories_flat() -> List[Dict]:
    """
    调试函数：返回扁平列表，每个元素包含空间ID、记忆ID、角色、内容。
    便于快速遍历所有记忆条目。
    """
    mgr = get_memory_manager()
    flat = []
    for space_id in mgr.list_all_space_ids():
        space = mgr.get_space_handle(space_id)
        if space._destroyed:
            continue
        memories = space.get_all_memory()
        for mid, data in memories.items():
            flat.append({
                'space_id': space_id,
                'memory_id': mid,
                'role': data['role'],
                'content': data['content']
            })
    return flat


def debug_list_all_memory_ids() -> Dict[str, List[str]]:
    """
    调试函数：列出所有空间及其对应的记忆ID列表。
    返回 {space_id: [memory_id, ...]}，不加载内容，速度较快。
    """
    mgr = get_memory_manager()
    result = {}
    for space_id in mgr.list_all_space_ids():
        space = mgr.get_space_handle(space_id)
        if space._destroyed:
            continue
        # 使用 get_all_infos 仅获取元数据，但我们需要ID，直接取 keys
        infos = space.get_all_infos(limit=None)  # 全部，不排序
        result[space_id] = list(infos.keys())
    return result

def debug_view():
    # 查看所有空间及其记忆
    all_data = debug_dump_all_memories()
    for space, mems in all_data.items():
        print(f"空间 {space}:")
        for mid, info in mems.items():
            print(f"  {mid}: {info['role']} -> {info['content'][:50]}...")

    # 扁平列表，便于迭代
    flat = debug_get_all_memories_flat()
    for item in flat:
        print(f"[{item['space_id']}] {item['memory_id']}: {item['role']} - {item['content']}")

    # 仅查看所有记忆ID
    id_map = debug_list_all_memory_ids()
    print(id_map)

def test():
    mgr = get_memory_manager()
    print("")
    print("所有空间：", mgr.list_all_space_ids())

    spclist = mgr.list_all_space_ids()

    for spcid in spclist:
        spc = mgr.get_space_handle(spcid)
        spc.destroy()

    print("所有空间：", mgr.list_all_space_ids())

    spc = mgr.get_space_handle(space_id="1")

    spc.get_infos([""])

    mid = spc.append("user", "测试内容", "测试摘要")
    mid = spc.append("ai", "测试内容2", "测试摘要2")
    mid = spc.append("123", "测试内容3", "测试摘要3")
    mid = spc.append("#$%", "测试内容4", "测试摘要4")

    print("记忆：")
    print(spc.get_all_memory())
    print("")
    print("摘要：")
    print(spc.get_all_summaries())
    print("")
    print("infos：")
    print(spc.get_all_infos())

    spc.destroy()

    print("")
    print("所有空间：", mgr.list_all_space_ids())

    pass
if __name__ == "__main__":
    debug_view()
