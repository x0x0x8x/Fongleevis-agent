"""
记忆存储扩展索引集（纯增量，零侵入）
本质是三套独立的自定义索引工具，仅负责「记忆ID检索」，不碰主数据读写
"""

'''
"""
记忆存储扩展索引集（纯增量，零侵入）
三套独立自定义索引工具，仅负责「记忆ID检索」，不触碰主记忆数据读写
模块组成：
    HotCacheIndex    热点记忆索引(mmap本地二进制缓存)
    LinkIndex         连锁关联索引(SQLite记忆关联图谱)
    EdgeIndex         边缘孤立记忆索引
    EdgeScanner       边缘记忆后台定时扫描器
初始化前置：程序启动执行 init_index_tables() 创建数据表
"""

def init_index_tables(db_path: str = None):
    """
    初始化索引数据表 memory_links / memory_edge
    使用 CREATE IF NOT EXISTS，重复调用安全，不会清空已有持久化数据
    :param db_path: 数据库路径，缺省使用 DEFAULT_DB_PATH
    """

# ===================== HotCacheIndex =====================
class HotCacheIndex:
    """
    mmap 内存映射热点缓存，本地文件持久化
    分区：Recent(最近访问LRU队列) + Frequent(高频访问按计数排序队列)
    限制：memory_id 最大16字节ASCII编码
    """
    def __init__(self, space_id: str, total_bytes: int = HOT_CACHE_TOTAL_BYTES):
        """
        :param space_id: 空间标识，每个space独立缓存文件
        :param total_bytes: 缓存文件总字节大小
        """

    def update(self, memory_id: str, data_len: int):
        """
        访问记忆时更新热点索引
        1. 写入Recent最近队列置顶
        2. 更新Frequent高频队列访问计数并重排
        :param memory_id: 记忆ID
        :param data_len: 记忆数据长度(仅存储记录，不参与淘汰逻辑)
        """

    def get_recent_ids(self, limit: int = None) -> List[str]:
        """
        获取最近访问记忆ID列表
        :param limit: 返回条数；None=返回当前全部存量(不超过容量上限)
        :return: List[memory_id]
        """

    def get_frequent_ids(self, limit: int = None) -> List[str]:
        """
        获取高频访问记忆ID列表，按访问次数降序
        :param limit: 返回条数；None=返回当前全部存量(不超过容量上限)
        :return: List[memory_id]
        """

    def contains(self, memory_id: str) -> bool:
        """判断记忆是否存在热点缓存中"""

    def rebuild(self):
        """清空当前space热点缓存，重置头部计数"""

    def close(self):
        """
        释放mmap映射与文件句柄
        close后实例不可继续调用接口；如需使用必须重新实例化
        不会删除磁盘缓存文件
        """

# ===================== LinkIndex =====================
class LinkIndex:
    """
    记忆关联链路索引，维护 from_id → to_id 单向关联关系
    handle遍历状态保存在实例内部，不主动释放会引发内存泄漏
    """
    def __init__(self, space_id: str, db_path: str = None):
        """
        :param space_id: 记忆空间ID
        :param db_path: sqlite库路径，不传使用默认 DEFAULT_DB_PATH
        """

    def relate(self, from_id: str, to_id: str):
        """
        建立单向关联 from_id → to_id
        规则：
            链路不存在 → 新增记录，link_count=1
            链路已存在 → link_count += 1
            relate(A,B)与relate(B,A)相互独立，属于两条单向链路
        查询关联按link_count DESC排序，数值越高关联权重越大
        :param from_id: 源记忆ID
        :param to_id: 目标记忆ID
        """

    def clear_memory_links(self, memory_id: str):
        """删除指定记忆全部入向、出向关联链路"""

    def act_single_link_mem_select(self, memory_id: str) -> str:
        """创建单层一级关联遍历句柄（仅直接下游，不递归）"""

    def get_single_link_mem(self, handle_id: str, max_num: int = None) -> List[str]:
        """
        分批读取单层关联结果
        :param max_num: None读取剩余全部
        :return List[str]: memory_id列表，无数据返回空列表
        :raises ValueError: 句柄无效或类型不匹配
        """

    def act_tree_link_mem_select(self, memory_id: str) -> str:
        """创建BFS树状多层联想遍历句柄，内置去重防止循环引用"""

    def get_tree_link_mem(self, handle_id: str, max_num: int = None) -> List[str]:
        """
        分批读取BFS多层联想结果
        :param max_num: None读取剩余全部
        :return List[str]: memory_id列表，无数据返回空列表
        :raises ValueError: 句柄无效或类型不匹配
        """

    def release_link_handle(self, handle_id: str):
        """
        【新业务推荐】统一释放遍历句柄
        兼容single单层、tree树状两类handle
        约束：必须由创建该handle的同一个LinkIndex实例调用
        """

    def finish_single_deep_mem_select(self, handle_id: str):
        """【遗留兼容接口，新项目禁止使用】功能同release_link_handle，命名存在歧义"""

# ===================== EdgeIndex =====================
class EdgeIndex:
    """
    边缘记忆索引：不存在任何关联链路、且不在热点缓存内的孤立记忆
    """
    def __init__(self, space_id: str, db_path: str = None):
        """
        :param space_id: 记忆空间ID
        :param db_path: sqlite库路径，不传使用默认 DEFAULT_DB_PATH
        """

    def get_edge_ids(self, limit: int = None) -> List[str]:
        """
        获取边缘记忆列表，按最早检测时间升序
        :param limit: None 查询表内全部边缘记忆（数据量大慎用）
        :return List[str]: memory_id列表
        """

    def remove_from_edge(self, memory_id: str):
        """将记忆从边缘索引中移除（建立关联/被访问后调用）"""

# ===================== EdgeScanner =====================
class EdgeScanner:
    """
    边缘记忆后台扫描守护线程
    周期遍历所有空间，自动识别孤立冷记忆写入 memory_edge
    配置 EDGE_AUTO_DELETE = True 时扫描后自动删除边缘记忆本体
    """
    def __init__(self, db_path: str = None):
        """
        :param db_path: sqlite库路径，不传使用默认 DEFAULT_DB_PATH
        """

    def start(self):
        """启动后台扫描线程，重复调用安全"""

    def stop(self):
        """停止扫描线程，等待线程正常退出"""

# ===================== 标准使用模板 =====================
"""
# 1. 程序启动一次性执行
init_index_tables()
scanner = EdgeScanner()
scanner.start()

# HotCacheIndex 使用
hot = HotCacheIndex("space_001")
hot.update("mem_001", data_len=1024)
recent_list = hot.get_recent_ids(limit=20)
hot.close()

# LinkIndex 单层遍历模板
link = LinkIndex("space_001")
handle = link.act_single_link_mem_select("mem_001")
try:
    while batch := link.get_single_link_mem(handle, max_num=5):
        pass
finally:
    link.release_link_handle(handle)

# LinkIndex BFS多层遍历模板
handle = link.act_tree_link_mem_select("mem_001")
try:
    while chunk := link.get_tree_link_mem(handle, max_num=5):
        pass
finally:
    link.release_link_handle(handle)

# EdgeIndex 使用
edge = EdgeIndex("space_001")
cold_list = edge.get_edge_ids(limit=30)
edge.remove_from_edge("mem_cold_01")

# 程序退出
scanner.stop()
"""

"""
【全局强制约束】
1. HotCacheIndex 基于mmap，适合单进程架构；多进程存在文件竞争风险
2. LinkIndex handle仅当前实例有效，禁止跨实例release；常驻服务必须try-finally释放防止内存泄漏
3. init_index_tables 仅建表，不会删除业务数据，可安全反复启动调用
4. EdgeScanner全库扫描量大时会瞬时增加SQL负载，可调整EDGE_SCAN_INTERVAL_SEC拉长周期
"""


'''

import sqlite3
import threading
import mmap
import os
import struct
import time
import traceback
from datetime import datetime, timezone
from typing import List, Optional, Set, Union

from app.agent.api.memory_manager_src.memory_manager import DEFAULT_DB_PATH, ENABLE_WAL, SYNCHRONOUS_MODE


# ==================== 扩展配置常量 ====================
HOT_CACHE_TOTAL_BYTES = 1024 * 1024
HOT_CACHE_RECENT_RATIO = 0.5
HOT_CACHE_ENTRY_FMT = "<16sIdI"
HOT_CACHE_HEADER_FMT = "<II"
HOT_CACHE_FILE_DIR = "hot_cache"

EDGE_SCAN_INTERVAL_SEC = 3600 * 24
EDGE_AUTO_DELETE = False

# ==================== 内部通用工具 ====================
def _get_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if ENABLE_WAL:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA synchronous={SYNCHRONOUS_MODE};")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
def init_index_tables(db_path: str = None):
    db_path = db_path or DEFAULT_DB_PATH
    conn = _get_conn(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_links (
                space_id TEXT NOT NULL,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                link_count INTEGER DEFAULT 1,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (space_id, from_id, to_id)
            );
            CREATE INDEX IF NOT EXISTS idx_link_from ON memory_links(space_id, from_id, link_count DESC);
            CREATE INDEX IF NOT EXISTS idx_link_to ON memory_links(space_id, to_id);

            CREATE TABLE IF NOT EXISTS memory_edge (
                space_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                detected_at DATETIME NOT NULL,
                PRIMARY KEY (space_id, memory_id)
            );
        """)
    finally:
        conn.close()
# ==================================================
# ================= 索引1：热点记忆 ==================
# ==================================================
class HotCacheIndex:
    _HEADER_SIZE = struct.calcsize(HOT_CACHE_HEADER_FMT)
    _ENTRY_SIZE = struct.calcsize(HOT_CACHE_ENTRY_FMT)
    def __init__(self, space_id: str, total_bytes: int = HOT_CACHE_TOTAL_BYTES):
        self.space_id = space_id
        self.total_bytes = total_bytes
        self.recent_cap = int(total_bytes * HOT_CACHE_RECENT_RATIO) // self._ENTRY_SIZE
        self.frequent_cap = (total_bytes - self._HEADER_SIZE - self.recent_cap * self._ENTRY_SIZE) // self._ENTRY_SIZE

        os.makedirs(HOT_CACHE_FILE_DIR, exist_ok=True)
        self.file_path = os.path.join(HOT_CACHE_FILE_DIR, f"space_{space_id}.dat")

        self._lock = threading.Lock()
        self._mmap_obj: Optional[mmap.mmap] = None
        self._file_handle = None
        self._init_file()
    def _init_file(self):
        need_rebuild = False
        if not os.path.exists(self.file_path):
            need_rebuild = True
        elif os.path.getsize(self.file_path) != self.total_bytes:
            need_rebuild = True

        mode = "r+b" if os.path.exists(self.file_path) else "w+b"
        self._file_handle = open(self.file_path, mode)
        if need_rebuild:
            self._file_handle.truncate(self.total_bytes)
            self._file_handle.flush()
        self._mmap_obj = mmap.mmap(self._file_handle.fileno(), self.total_bytes, access=mmap.ACCESS_WRITE)

        if need_rebuild:
            self._write_header(0, 0)
    def _read_header(self) -> tuple[int, int]:
        if self._mmap_obj is None:
            raise RuntimeError("mmap not initialized")
        self._mmap_obj.seek(0)
        data = self._mmap_obj.read(self._HEADER_SIZE)
        return struct.unpack(HOT_CACHE_HEADER_FMT, data)
    def _write_header(self, recent_cnt: int, frequent_cnt: int):
        if self._mmap_obj is None:
            raise RuntimeError("mmap not initialized")
        self._mmap_obj.seek(0)
        self._mmap_obj.write(struct.pack(HOT_CACHE_HEADER_FMT, recent_cnt, frequent_cnt))
    def _read_entry(self, index: int, is_recent: bool) -> tuple[str, int, float, int]:
        offset = self._HEADER_SIZE
        if not is_recent:
            offset += self.recent_cap * self._ENTRY_SIZE
        offset += index * self._ENTRY_SIZE
        if self._mmap_obj is None:
            raise RuntimeError("mmap not initialized")
        self._mmap_obj.seek(offset)
        raw = self._mmap_obj.read(self._ENTRY_SIZE)
        mid_bytes, cnt, ts, dlen = struct.unpack(HOT_CACHE_ENTRY_FMT, raw)
        mid = mid_bytes.rstrip(b"\x00").decode("ascii")
        return mid, cnt, ts, dlen
    def _write_entry(self, index: int, is_recent: bool, memory_id: str, access_count: int, last_access: float, data_len: int):
        offset = self._HEADER_SIZE
        if not is_recent:
            offset += self.recent_cap * self._ENTRY_SIZE
        offset += index * self._ENTRY_SIZE
        mid_bytes = memory_id.encode("ascii").ljust(16, b"\x00")
        raw = struct.pack(HOT_CACHE_ENTRY_FMT, mid_bytes, access_count, last_access, data_len)
        if self._mmap_obj is None:
            raise RuntimeError("mmap not initialized")
        self._mmap_obj.seek(offset)
        self._mmap_obj.write(raw)
    def update(self, memory_id: str, data_len: int):
        now = time.time()
        with self._lock:
            recent_cnt, frequent_cnt = self._read_header()

            found_idx = -1
            for i in range(recent_cnt):
                mid, _, _, _ = self._read_entry(i, True)
                if mid == memory_id:
                    found_idx = i
                    break

            if found_idx >= 0:
                entry = self._read_entry(found_idx, True)
                for i in range(found_idx, 0, -1):
                    prev = self._read_entry(i - 1, True)
                    self._write_entry(i, True, *prev)
                self._write_entry(0, True, memory_id, entry[1] + 1, now, data_len)
            else:
                if recent_cnt >= self.recent_cap:
                    recent_cnt = self.recent_cap - 1
                for i in range(recent_cnt, 0, -1):
                    prev = self._read_entry(i - 1, True)
                    self._write_entry(i, True, *prev)
                self._write_entry(0, True, memory_id, 1, now, data_len)
                recent_cnt += 1

            freq_found = False
            freq_idx = -1
            for i in range(frequent_cnt):
                mid, cnt, _, _ = self._read_entry(i, False)
                if mid == memory_id:
                    freq_found = True
                    freq_idx = i
                    break

            if freq_found:
                _, old_cnt, _, _ = self._read_entry(freq_idx, False)
                new_cnt = old_cnt + 1
                cur = freq_idx
                while cur > 0:
                    prev_mid, prev_cnt, prev_ts, prev_len = self._read_entry(cur - 1, False)
                    if prev_cnt >= new_cnt:
                        break
                    self._write_entry(cur, False, prev_mid, prev_cnt, prev_ts, prev_len)
                    cur -= 1
                self._write_entry(cur, False, memory_id, new_cnt, now, data_len)
            else:
                if frequent_cnt >= self.frequent_cap:
                    frequent_cnt = self.frequent_cap - 1
                insert_pos = frequent_cnt
                self._write_entry(insert_pos, False, memory_id, 1, now, data_len)
                frequent_cnt += 1
                while insert_pos > 0:
                    prev_mid, prev_cnt, prev_ts, prev_len = self._read_entry(insert_pos - 1, False)
                    if prev_cnt >= 1:
                        break
                    self._write_entry(insert_pos, False, prev_mid, prev_cnt, prev_ts, prev_len)
                    insert_pos -= 1
                self._write_entry(insert_pos, False, memory_id, 1, now, data_len)

            self._write_header(recent_cnt, frequent_cnt)
    def get_recent_ids(self, limit: int = None) -> List[str]:
        with self._lock:
            recent_cnt, _ = self._read_header()
            count = min(recent_cnt, limit) if limit else recent_cnt
            return [self._read_entry(i, True)[0] for i in range(count)]
    def get_frequent_ids(self, limit: int = None) -> List[str]:
        with self._lock:
            _, frequent_cnt = self._read_header()
            count = min(frequent_cnt, limit) if limit else frequent_cnt
            return [self._read_entry(i, False)[0] for i in range(count)]
    def contains(self, memory_id: str) -> bool:
        with self._lock:
            recent_cnt, frequent_cnt = self._read_header()
            for i in range(recent_cnt):
                if self._read_entry(i, True)[0] == memory_id:
                    return True
            for i in range(frequent_cnt):
                if self._read_entry(i, False)[0] == memory_id:
                    return True
            return False
    def rebuild(self):
        with self._lock:
            if self._mmap_obj is None:
                raise RuntimeError("mmap not initialized")
            self._mmap_obj.seek(0)
            self._mmap_obj.write(b"\x00" * self.total_bytes)
            self._write_header(0, 0)
    def close(self):
        if self._mmap_obj:
            self._mmap_obj.close()
            self._mmap_obj = None
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
# ==================================================
# ================= 索引2：连锁记忆 ==================
# ==================================================
# ==================== 游标迭代器类 ====================
class SingleLinkCursor:
    """单层关联迭代器，一次性预加载全部结果，分批返回"""
    def __init__(self, root_id: str, remaining: List[str]):
        self.root_id = root_id
        self._remaining: List[str] = remaining
        self._fetched: int = 0
        self._closed: bool = False

    def fetch(self, max_num: int = None) -> List[str]:
        """分批读取关联ID，无剩余返回空列表"""
        if self._closed:
            raise ValueError("Cursor has been closed")

        if not self._remaining:
            return []

        if max_num is None or max_num >= len(self._remaining):
            batch = self._remaining
            self._remaining = []
        else:
            batch = self._remaining[:max_num]
            self._remaining = self._remaining[max_num:]

        self._fetched += len(batch)
        return batch

    def close(self) -> None:
        """主动释放游标状态，提前回收内存"""
        self._remaining.clear()
        self._closed = True

    @property
    def fetched(self) -> int:
        """已读取总数"""
        return self._fetched
class TreeLinkCursor:
    """BFS树状联想迭代器，逐层展开数据库关联，自动去重防循环"""
    def __init__(self, root_id: str, space_id: str, db_path: str):
        self.root_id = root_id
        self.space_id = space_id
        self._db_path = db_path
        self._queue: List[str] = [root_id]
        self._visited: Set[str] = {root_id}
        self._pending: List[str] = []
        self._fetched: int = 0
        self._closed: bool = False

    def fetch(self, max_num: int = None) -> List[str]:
        """分批读取联想结果，自动展开下一层节点"""
        if self._closed:
            raise ValueError("Cursor has been closed")

        result = []
        queue = self._queue
        visited = self._visited
        pending = self._pending

        while True:
            # 优先消耗已缓冲节点
            while pending and (max_num is None or len(result) < max_num):
                result.append(pending.pop(0))

            if max_num is not None and len(result) >= max_num:
                break

            # 缓冲耗尽，展开下一层
            if not queue:
                break

            current = queue.pop(0)
            conn = _get_conn(self._db_path)
            try:
                rows = conn.execute("""
                    SELECT to_id FROM memory_links
                    WHERE space_id = ? AND from_id = ?
                    ORDER BY link_count DESC
                """, (self.space_id, current)).fetchall()
            finally:
                conn.close()

            for row in rows:
                to_id = row["to_id"]
                if to_id not in visited:
                    visited.add(to_id)
                    pending.append(to_id)
                    queue.append(to_id)

        self._fetched += len(result)
        return result

    def close(self) -> None:
        """主动释放游标状态，清空队列与已访问集合"""
        self._queue.clear()
        self._visited.clear()
        self._pending.clear()
        self._closed = True

    @property
    def fetched(self) -> int:
        """已读取总数"""
        return self._fetched
class LinkIndex:
    def __init__(self, space_id: str, db_path: str = None):
        self.space_id = space_id
        self.db_path = db_path or DEFAULT_DB_PATH
        self._lock = threading.RLock()
        # 移除 _handles / _handle_lock / _handle_seq，不再集中管理游标

    def relate(self, from_id: str, to_id: str):
        # 原逻辑完全不变
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = _get_conn(self.db_path)
            try:
                cur = conn.execute("""
                    UPDATE memory_links
                    SET link_count = link_count + 1, updated_at = ?
                    WHERE space_id = ? AND from_id = ? AND to_id = ?
                """, (now, self.space_id, from_id, to_id))
                if cur.rowcount == 0:
                    conn.execute("""
                        INSERT INTO memory_links (space_id, from_id, to_id, link_count, created_at, updated_at)
                        VALUES (?, ?, ?, 1, ?, ?)
                    """, (self.space_id, from_id, to_id, now, now))
                conn.commit()
            finally:
                conn.close()

    # ---------- 单层关联迭代 ----------
    def act_single_link_mem_select(self, memory_id: str) -> SingleLinkCursor:
        """创建单层关联迭代器，返回独立游标对象"""
        conn = _get_conn(self.db_path)
        try:
            rows = conn.execute("""
                SELECT to_id FROM memory_links
                WHERE space_id = ? AND from_id = ?
                ORDER BY link_count DESC
            """, (self.space_id, memory_id)).fetchall()
        finally:
            conn.close()

        remaining = [r["to_id"] for r in rows]
        return SingleLinkCursor(root_id=memory_id, remaining=remaining)

    # ---------- 树状BFS迭代 ----------
    def act_tree_link_mem_select(self, memory_id: str) -> TreeLinkCursor:
        """创建树状联想迭代器，返回独立游标对象"""
        return TreeLinkCursor(
            root_id=memory_id,
            space_id=self.space_id,
            db_path=self.db_path
        )

    def clear_memory_links(self, memory_id: str):
        # 原逻辑完全不变
        with self._lock:
            conn = _get_conn(self.db_path)
            try:
                conn.execute(
                    "DELETE FROM memory_links WHERE space_id = ? AND (from_id = ? OR to_id = ?)",
                    (self.space_id, memory_id, memory_id)
                )
                conn.commit()
            finally:
                conn.close()

    # ========== 兼容过渡方法（可选，存量代码可直接用，新代码不推荐） ==========
    @staticmethod
    def get_single_link_mem(cursor: SingleLinkCursor, max_num: int = None) -> List[str]:
        return cursor.fetch(max_num)

    @staticmethod
    def get_tree_link_mem(cursor: TreeLinkCursor, max_num: int = None) -> List[str]:
        return cursor.fetch(max_num)

    @staticmethod
    def release_link_handle(cursor: SingleLinkCursor | TreeLinkCursor) -> None:
        cursor.close()

    @staticmethod
    def finish_single_deep_mem_select(cursor: SingleLinkCursor | TreeLinkCursor) -> None:
        cursor.close()
# ==================================================
# ================= 索引3：边缘记忆 ==================
# ==================================================
class EdgeIndex:
    def __init__(self, space_id: str, db_path: str = None):
        self.space_id = space_id
        self.db_path = db_path or DEFAULT_DB_PATH
    def get_edge_ids(self, limit: int = None) -> List[str]:
        query = "SELECT memory_id FROM memory_edge WHERE space_id = ? ORDER BY detected_at ASC"
        params: List[Union[str, int]] = [self.space_id]
        if limit and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        conn = _get_conn(self.db_path)
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
        return [r["memory_id"] for r in rows]
    def remove_from_edge(self, memory_id: str):
        conn = _get_conn(self.db_path)
        try:
            conn.execute(
                "DELETE FROM memory_edge WHERE space_id = ? AND memory_id = ?",
                (self.space_id, memory_id)
            )
            conn.commit()
        finally:
            conn.close()
class EdgeScanner:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while not self._stop_event.is_set():
                try:
                    self._scan_once()
                except sqlite3.Error as e:
                    print(f"scan_once 数据库异常: {e}")
                    traceback.print_exc()
                except Exception:
                    print("scan_once 未知异常")
                    traceback.print_exc()
                self._stop_event.wait(EDGE_SCAN_INTERVAL_SEC)

        self._thread = threading.Thread(target=loop, daemon=True)
        if self._thread is not None:
            self._thread.start()
        else:
            raise RuntimeError("Thread instance has not been initialized")
    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
    def _scan_once(self):
        conn = _get_conn(self.db_path)
        try:
            space_rows = conn.execute("SELECT DISTINCT space_id FROM memory_info").fetchall()
            now = datetime.now(timezone.utc).isoformat()

            for row in space_rows:
                sid = row["space_id"]
                sql = """
                    SELECT mi.memory_id FROM memory_info mi
                    WHERE mi.space_id = ?
                    AND mi.memory_id NOT IN (SELECT from_id FROM memory_links WHERE space_id = ?)
                    AND mi.memory_id NOT IN (SELECT to_id FROM memory_links WHERE space_id = ?)
                """
                mem_rows = conn.execute(sql, (sid, sid, sid)).fetchall()
                if not mem_rows:
                    continue

                hot = HotCacheIndex(sid)
                edge_ids = []
                for m in mem_rows:
                    mid = m["memory_id"]
                    if not hot.contains(mid):
                        edge_ids.append(mid)
                hot.close()

                if edge_ids:
                    conn.executemany(
                        "INSERT OR IGNORE INTO memory_edge (space_id, memory_id, detected_at) VALUES (?, ?, ?)",
                        [(sid, mid, now) for mid in edge_ids]
                    )
                    conn.commit()

                if EDGE_AUTO_DELETE and edge_ids:
                    for mid in edge_ids:
                        conn.execute("DELETE FROM memory_info WHERE space_id = ? AND memory_id = ?", (sid, mid))
                        # 兼容表不存在的场景（测试/轻量化部署）
                        try:
                            conn.execute("DELETE FROM memories WHERE space_id = ? AND memory_id = ?", (sid, mid))
                        except sqlite3.OperationalError:
                            pass
                    conn.execute("DELETE FROM memory_edge WHERE space_id = ?", (sid,))
                    conn.commit()
        finally:
            conn.close()

