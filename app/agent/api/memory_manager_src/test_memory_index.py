import unittest
import os
import threading
import shutil
import sqlite3
import time
from app.agent.api.memory_manager_src.memory_indices import (
    init_index_tables,
    HotCacheIndex,
    LinkIndex,
    EdgeIndex,
    EdgeScanner,
    _get_conn,
)
import app.agent.api.memory_manager_src.memory_indices as memory_indices

class TestHotCacheIndex(unittest.TestCase):
    """热点缓存测试：小容量、高频率触发淘汰，验证最近/高频两段规则"""
    TEST_SPACE = "test_hot_space"
    TEST_DIR = "test_hot_cache"
    TINY_CAP = 264

    @classmethod
    def setUpClass(cls):
        os.makedirs(cls.TEST_DIR, exist_ok=True)
        memory_indices.HOT_CACHE_FILE_DIR = cls.TEST_DIR

    def setUp(self):
        self._clear_cache_files()
        self.cache = HotCacheIndex(self.TEST_SPACE, total_bytes=self.TINY_CAP)

    def tearDown(self):
        self.cache.close()
        # Windows 下等待文件句柄释放
        time.sleep(0.01)
        self._clear_cache_files()

    def _clear_cache_files(self):
        path = os.path.join(self.TEST_DIR, f"space_{self.TEST_SPACE}.dat")
        if os.path.exists(path):
            for _ in range(5):
                try:
                    os.remove(path)
                    break
                except Exception:
                    time.sleep(0.01)

    def test_01_recent_insert_order_and_eviction(self):
        """最近段：写入超量数据，验证先进先出淘汰规则"""
        for i in range(8):
            self.cache.update(f"mem_{i}", 100)
        recent = self.cache.get_recent_ids()
        self.assertEqual(len(recent), 4)
        # 最早4条被淘汰
        self.assertNotIn("mem_0", recent)
        self.assertNotIn("mem_1", recent)
        self.assertNotIn("mem_2", recent)
        self.assertNotIn("mem_3", recent)
        # 最新4条保留且顺序正确
        self.assertEqual(recent[0], "mem_7")
        self.assertEqual(recent[-1], "mem_4")

    def test_02_recent_high_frequency_moves_front(self):
        """最近段：高频重复访问，验证条目自动上浮到头部"""
        for i in range(4):
            self.cache.update(f"mem_{i}", 100)
        # 反复访问最早的 mem_0
        for _ in range(20):
            self.cache.update("mem_0", 100)
        recent = self.cache.get_recent_ids()
        self.assertEqual(recent[0], "mem_0")

    def test_03_frequent_count_sorting_stable(self):
        """高频段：高频率访问，验证计数排序稳定性"""
        for _ in range(10):
            self.cache.update("a", 100)
        for _ in range(5):
            self.cache.update("b", 100)
        for _ in range(3):
            self.cache.update("c", 100)
        self.cache.update("d", 100)

        frequent = self.cache.get_frequent_ids()
        self.assertEqual(len(frequent), 4)
        self.assertEqual(frequent[0], "a")
        self.assertEqual(frequent[1], "b")
        self.assertEqual(frequent[2], "c")
        self.assertEqual(frequent[3], "d")

    def test_04_frequent_evict_lowest_on_overflow(self):
        """高频段：超量写入，验证计数最低的条目被淘汰"""
        for _ in range(4):
            self.cache.update("top1", 100)
        for _ in range(3):
            self.cache.update("top2", 100)
        for _ in range(2):
            self.cache.update("top3", 100)
        self.cache.update("top4", 100)

        # 插入新条目，计数最低的 top4 被淘汰
        self.cache.update("newcomer", 100)
        frequent = self.cache.get_frequent_ids()
        self.assertEqual(len(frequent), 4)
        self.assertNotIn("top4", frequent)
        self.assertIn("newcomer", frequent)

    def test_05_massive_data_capacity_not_overflow(self):
        """大数据量：批量写入1000条，验证总容量始终不超限"""
        for i in range(1000):
            self.cache.update(f"mass_mem_{i}", 100)

        recent = self.cache.get_recent_ids()
        frequent = self.cache.get_frequent_ids()
        self.assertLessEqual(len(recent), self.cache.recent_cap)
        self.assertLessEqual(len(frequent), self.cache.frequent_cap)

    def test_06_contains_both_segments(self):
        """contains方法可同时检测两段缓存"""
        self.cache.update("recent_only", 100)
        for _ in range(10):
            self.cache.update("frequent_only", 100)

        self.assertTrue(self.cache.contains("recent_only"))
        self.assertTrue(self.cache.contains("frequent_only"))
        self.assertFalse(self.cache.contains("ghost_mem"))

    def test_07_limit_truncation_correct(self):
        """limit参数正确截断返回结果"""
        for i in range(4):
            self.cache.update(f"mem_{i}", 100)
        self.assertEqual(len(self.cache.get_recent_ids(limit=2)), 2)
        self.assertEqual(len(self.cache.get_frequent_ids(limit=1)), 1)
class TestLinkIndex(unittest.TestCase):
    TEST_DB = "test_link_index.db"
    TEST_SPACE = "test_link_space"

    @classmethod
    def setUpClass(cls):
        memory_indices.DEFAULT_DB_PATH = cls.TEST_DB
        init_index_tables(cls.TEST_DB)

    def setUp(self):
        self._clear_db()
        init_index_tables(self.TEST_DB)
        self.link = LinkIndex(self.TEST_SPACE, self.TEST_DB)

    def tearDown(self):
        self._clear_db()

    def _clear_db(self):
        for suffix in ["", "-wal", "-shm"]:
            path = self.TEST_DB + suffix
            if os.path.exists(path):
                for _ in range(5):
                    try:
                        os.remove(path)
                        break
                    except Exception:
                        time.sleep(0.01)

    def test_01_single_link_count_accumulate_high_freq(self):
        for _ in range(200):
            self.link.relate("root", "node_a")
        for _ in range(100):
            self.link.relate("root", "node_b")

        handle = self.link.act_single_link_mem_select("root")
        result = self.link.get_single_link_mem(handle)
        self.link.release_link_handle(handle)

        self.assertEqual(result[0], "node_a")
        self.assertEqual(result[1], "node_b")

        conn = sqlite3.connect(self.TEST_DB)
        row_a = conn.execute(
            "SELECT link_count FROM memory_links WHERE space_id=? AND from_id=? AND to_id=?",
            (self.TEST_SPACE, "root", "node_a")
        ).fetchone()
        row_b = conn.execute(
            "SELECT link_count FROM memory_links WHERE space_id=? AND from_id=? AND to_id=?",
            (self.TEST_SPACE, "root", "node_b")
        ).fetchone()
        conn.close()
        self.assertEqual(row_a[0], 200)
        self.assertEqual(row_b[0], 100)

    def test_02_single_link_batch_fetch_progressive(self):
        for i in range(100):
            self.link.relate("root", f"node_{i}")

        handle = self.link.act_single_link_mem_select("root")
        b1 = self.link.get_single_link_mem(handle, max_num=30)
        b2 = self.link.get_single_link_mem(handle, max_num=30)
        b3 = self.link.get_single_link_mem(handle)

        self.assertEqual(len(b1), 30)
        self.assertEqual(len(b2), 30)
        self.assertEqual(len(b3), 40)
        self.assertEqual(self.link.get_single_link_mem(handle), [])
        self.link.release_link_handle(handle)

    def test_03_tree_bfs_full_traversal_large(self):
        for i in range(5):
            self.link.relate("root", f"l1_{i}")
            for j in range(5):
                self.link.relate(f"l1_{i}", f"l2_{i}_{j}")

        handle = self.link.act_tree_link_mem_select("root")
        all_nodes = self.link.get_tree_link_mem(handle)
        self.link.release_link_handle(handle)

        self.assertEqual(len(all_nodes), 30)
        first_five = all_nodes[:5]
        for i in range(5):
            self.assertIn(f"l1_{i}", first_five)

    def test_04_tree_cycle_detection_robust(self):
        self.link.relate("a", "b")
        self.link.relate("b", "c")
        self.link.relate("c", "a")
        self.link.relate("b", "d")
        self.link.relate("d", "b")

        handle = self.link.act_tree_link_mem_select("a")
        result = self.link.get_tree_link_mem(handle)
        more = self.link.get_tree_link_mem(handle)
        self.link.release_link_handle(handle)

        self.assertEqual(len(result), 3)
        self.assertSetEqual(set(result), {"b", "c", "d"})
        self.assertEqual(more, [])

    def test_05_tree_batch_fetch_continues(self):
        self.link.relate("root", "a")
        self.link.relate("root", "b")
        self.link.relate("a", "c")
        self.link.relate("a", "d")
        self.link.relate("b", "e")

        handle = self.link.act_tree_link_mem_select("root")
        batch1 = self.link.get_tree_link_mem(handle, max_num=1)
        self.assertEqual(len(batch1), 1)

        batch2 = self.link.get_tree_link_mem(handle, max_num=10)
        self.assertEqual(len(batch2), 4)
        self.link.release_link_handle(handle)

    def test_06_clear_links_cleanup(self):
        for i in range(10):
            self.link.relate("target", f"node_{i}")
        self.link.clear_memory_links("target")

        handle = self.link.act_single_link_mem_select("target")
        self.assertEqual(self.link.get_single_link_mem(handle), [])
        self.link.release_link_handle(handle)

    def test_07_invalid_handle_rejected(self):
        # 单层关联游标：关闭后调用 fetch 抛出 ValueError
        single_cursor = self.link.act_single_link_mem_select("test_mem_id")
        single_cursor.close()
        with self.assertRaises(ValueError):
            single_cursor.fetch()
        tree_cursor = self.link.act_tree_link_mem_select("test_mem_id")
        tree_cursor.close()
        with self.assertRaises(ValueError):
            tree_cursor.fetch()

    def test_08_nonexist_mem_return_empty(self):
        single_cursor = self.link.act_single_link_mem_select("nonexist_id")
        self.assertEqual(single_cursor.fetch(), [])
        tree_cursor = self.link.act_tree_link_mem_select("nonexist_id")
        self.assertEqual(tree_cursor.fetch(), [])
class TestEdgeIndexAndScanner(unittest.TestCase):
    TEST_DB = "test_edge_scan.db"
    TEST_SPACE = "test_edge_space"
    TEST_CACHE_DIR = "test_edge_cache"

    @classmethod
    def setUpClass(cls):
        os.makedirs(cls.TEST_CACHE_DIR, exist_ok=True)
        memory_indices.HOT_CACHE_FILE_DIR = cls.TEST_CACHE_DIR
        memory_indices.DEFAULT_DB_PATH = cls.TEST_DB
        memory_indices.EDGE_AUTO_DELETE = False
        init_index_tables(cls.TEST_DB)

    def setUp(self):
        self._clean_all()
        init_index_tables(self.TEST_DB)

        # 建主表
        conn = _get_conn(self.TEST_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_info (
                memory_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                summary TEXT DEFAULT '',
                length INTEGER DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_access DATETIME
            )
        """)
        conn.commit()
        conn.close()

        # 插10条测试数据
        now = "2026-07-14T00:00:00+00:00"
        conn = _get_conn(self.TEST_DB)
        for i in range(10):
            conn.execute(
                "INSERT INTO memory_info (memory_id, space_id, summary, length, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (f"mem_{i}", self.TEST_SPACE, f"sum_{i}", 10, now, now)
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        self._clean_all()

    def _clean_all(self):
        for suffix in ["", "-wal", "-shm"]:
            path = self.TEST_DB + suffix
            if os.path.exists(path):
                for _ in range(5):
                    try:
                        os.remove(path)
                        break
                    except Exception:
                        time.sleep(0.01)
        if os.path.exists(self.TEST_CACHE_DIR):
            for _ in range(5):
                try:
                    shutil.rmtree(self.TEST_CACHE_DIR)
                    break
                except Exception:
                    time.sleep(0.01)
        os.makedirs(self.TEST_CACHE_DIR, exist_ok=True)

    def test_01_scan_marks_all_orphan_as_edge(self):
        scanner = EdgeScanner(self.TEST_DB)
        scanner._scan_once()

        edge = EdgeIndex(self.TEST_SPACE, self.TEST_DB)
        edge_ids = edge.get_edge_ids()
        self.assertEqual(len(edge_ids), 10)

    def test_02_hot_memory_excluded_from_edge(self):
        # 使用全局默认容量，和扫描器保持一致，避免大小不匹配重建
        hot = HotCacheIndex(self.TEST_SPACE)
        for i in range(3):
            hot.update(f"mem_{i}", 10)
        hot.close()
        # 确保文件写入完成
        time.sleep(0.01)

        scanner = EdgeScanner(self.TEST_DB)
        scanner._scan_once()

        edge = EdgeIndex(self.TEST_SPACE, self.TEST_DB)
        edge_ids = edge.get_edge_ids()
        self.assertEqual(len(edge_ids), 7)
        for i in range(3):
            self.assertNotIn(f"mem_{i}", edge_ids)

    def test_03_linked_memory_excluded_from_edge(self):
        link = LinkIndex(self.TEST_SPACE, self.TEST_DB)
        link.relate("mem_0", "mem_1")

        scanner = EdgeScanner(self.TEST_DB)
        scanner._scan_once()

        edge = EdgeIndex(self.TEST_SPACE, self.TEST_DB)
        edge_ids = edge.get_edge_ids()
        self.assertNotIn("mem_0", edge_ids)
        self.assertNotIn("mem_1", edge_ids)

    def test_04_remove_from_edge_works(self):
        scanner = EdgeScanner(self.TEST_DB)
        scanner._scan_once()

        edge = EdgeIndex(self.TEST_SPACE, self.TEST_DB)
        edge.remove_from_edge("mem_5")
        edge_ids = edge.get_edge_ids()
        self.assertNotIn("mem_5", edge_ids)
        self.assertEqual(len(edge_ids), 9)

    def test_05_auto_delete_mode_purges_edge(self):
        memory_indices.EDGE_AUTO_DELETE = True
        try:
            scanner = EdgeScanner(self.TEST_DB)
            scanner._scan_once()

            conn = sqlite3.connect(self.TEST_DB)
            cnt = conn.execute(
                "SELECT COUNT(*) FROM memory_info WHERE space_id=?",
                (self.TEST_SPACE,)
            ).fetchone()[0]
            conn.close()
            self.assertEqual(cnt, 0)
        finally:
            memory_indices.EDGE_AUTO_DELETE = False

    def test_06_edge_limit_truncation(self):
        scanner = EdgeScanner(self.TEST_DB)
        scanner._scan_once()

        edge = EdgeIndex(self.TEST_SPACE, self.TEST_DB)
        partial = edge.get_edge_ids(limit=3)
        self.assertEqual(len(partial), 3)
class TestCrashRecovery(unittest.TestCase):
    TEST_DB = "test_crash.db"
    TEST_SPACE = "crash_space"
    TEST_CACHE_DIR = "test_crash_cache"

    @classmethod
    def setUpClass(cls):
        os.makedirs(cls.TEST_CACHE_DIR, exist_ok=True)
        memory_indices.HOT_CACHE_FILE_DIR = cls.TEST_CACHE_DIR
        memory_indices.DEFAULT_DB_PATH = cls.TEST_DB
        init_index_tables(cls.TEST_DB)

    def setUp(self):
        self._clean_all()
        init_index_tables(self.TEST_DB)

    def tearDown(self):
        self._clean_all()

    def _clean_all(self):
        for suffix in ["", "-wal", "-shm"]:
            path = self.TEST_DB + suffix
            if os.path.exists(path):
                for _ in range(5):
                    try:
                        os.remove(path)
                        break
                    except Exception:
                        time.sleep(0.01)
        if os.path.exists(self.TEST_CACHE_DIR):
            for _ in range(5):
                try:
                    shutil.rmtree(self.TEST_CACHE_DIR)
                    break
                except Exception:
                    time.sleep(0.01)
        os.makedirs(self.TEST_CACHE_DIR, exist_ok=True)

    def test_01_hot_cache_file_deleted_rebuilds(self):
        cache = HotCacheIndex(self.TEST_SPACE, total_bytes=512)
        cache.update("mem_a", 100)
        cache.close()
        time.sleep(0.01)

        cache_path = os.path.join(self.TEST_CACHE_DIR, f"space_{self.TEST_SPACE}.dat")
        os.remove(cache_path)

        cache2 = HotCacheIndex(self.TEST_SPACE, total_bytes=512)
        self.assertEqual(len(cache2.get_recent_ids()), 0)
        cache2.update("mem_new", 100)
        self.assertEqual(len(cache2.get_recent_ids()), 1)
        cache2.close()

    def test_02_hot_cache_file_corrupted_rebuilds(self):
        cache = HotCacheIndex(self.TEST_SPACE, total_bytes=512)
        cache.update("mem_a", 100)
        cache.close()
        time.sleep(0.01)

        cache_path = os.path.join(self.TEST_CACHE_DIR, f"space_{self.TEST_SPACE}.dat")
        with open(cache_path, "r+b") as f:
            f.truncate(64)

        cache2 = HotCacheIndex(self.TEST_SPACE, total_bytes=512)
        self.assertEqual(len(cache2.get_recent_ids()), 0)
        cache2.update("mem_ok", 100)
        self.assertTrue(cache2.contains("mem_ok"))
        cache2.close()

    def test_03_hot_rebuild_method_explicit(self):
        cache = HotCacheIndex(self.TEST_SPACE, total_bytes=512)
        for i in range(10):
            cache.update(f"mem_{i}", 100)
        self.assertGreater(len(cache.get_recent_ids()), 0)

        cache.rebuild()
        self.assertEqual(len(cache.get_recent_ids()), 0)
        self.assertEqual(len(cache.get_frequent_ids()), 0)
        cache.close()

    def test_04_link_data_persistent_after_restart(self):
        link1 = LinkIndex(self.TEST_SPACE, self.TEST_DB)
        for i in range(20):
            link1.relate("root", f"node_{i}")

        link2 = LinkIndex(self.TEST_SPACE, self.TEST_DB)
        handle = link2.act_single_link_mem_select("root")
        result = link2.get_single_link_mem(handle)
        link2.release_link_handle(handle)
        self.assertEqual(len(result), 20)

    def test_05_edge_data_persistent(self):
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO memory_edge VALUES (?, ?, ?)",
            (self.TEST_SPACE, "orphan_mem", "2026-01-01T00:00:00+00:00")
        )
        conn.commit()
        conn.close()

        edge2 = EdgeIndex(self.TEST_SPACE, self.TEST_DB)
        self.assertIn("orphan_mem", edge2.get_edge_ids())
class TestConcurrency(unittest.TestCase):
    TEST_DB = "test_concurrent.db"
    TEST_SPACE = "concurrent_space"
    TEST_CACHE_DIR = "test_concurrent_cache"

    @classmethod
    def setUpClass(cls):
        os.makedirs(cls.TEST_CACHE_DIR, exist_ok=True)
        memory_indices.HOT_CACHE_FILE_DIR = cls.TEST_CACHE_DIR
        memory_indices.DEFAULT_DB_PATH = cls.TEST_DB
        init_index_tables(cls.TEST_DB)

        conn = _get_conn(cls.TEST_DB)
        # 同时创建冷热两张表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_info (
                memory_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                summary TEXT DEFAULT '',
                length INTEGER DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_access DATETIME
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def setUp(self):
        self._clean_all()
        init_index_tables(self.TEST_DB)

        conn = _get_conn(self.TEST_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_info (
                memory_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                summary TEXT DEFAULT '',
                length INTEGER DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_access DATETIME
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self._clean_all()

    def _clean_all(self):
        for suffix in ["", "-wal", "-shm"]:
            path = self.TEST_DB + suffix
            if os.path.exists(path):
                for _ in range(5):
                    try:
                        os.remove(path)
                        break
                    except Exception:
                        time.sleep(0.01)
        if os.path.exists(self.TEST_CACHE_DIR):
            for _ in range(5):
                try:
                    shutil.rmtree(self.TEST_CACHE_DIR)
                    break
                except Exception:
                    time.sleep(0.01)
        os.makedirs(self.TEST_CACHE_DIR, exist_ok=True)

    def test_01_hot_cache_high_concurrency_update(self):
        cache = HotCacheIndex(self.TEST_SPACE, total_bytes=1024)
        errors = []

        def worker(tid):
            try:
                for i in range(100):
                    cache.update(f"t{tid}_m{i}", 10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertLessEqual(len(cache.get_recent_ids()), cache.recent_cap)
        self.assertLessEqual(len(cache.get_frequent_ids()), cache.frequent_cap)
        cache.close()

    def test_02_link_high_concurrency_relate(self):
        link = LinkIndex(self.TEST_SPACE, self.TEST_DB)
        errors = []

        def worker():
            try:
                for _ in range(50):
                    link.relate("root", "target")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        conn = sqlite3.connect(self.TEST_DB)
        cnt = conn.execute(
            "SELECT link_count FROM memory_links WHERE space_id=? AND from_id=? AND to_id=?",
            (self.TEST_SPACE, "root", "target")
        ).fetchone()[0]
        conn.close()
        self.assertEqual(cnt, 1000)

    def test_03_link_concurrent_multi_handle_traversal(self):
        link = LinkIndex(self.TEST_SPACE, self.TEST_DB)
        for i in range(50):
            link.relate("root", f"node_{i}")

        results = []
        errors = []

        def worker():
            try:
                handle = link.act_single_link_mem_select("root")
                batch = link.get_single_link_mem(handle, max_num=10)
                results.append(batch)
                link.release_link_handle(handle)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 20)
        for r in results:
            self.assertEqual(len(r), 10)

    def test_04_edge_scan_concurrent_with_write(self):
        errors = []

        def writer():
            try:
                link = LinkIndex(self.TEST_SPACE, self.TEST_DB)
                for i in range(100):
                    link.relate(f"from_{i}", f"to_{i}")
            except Exception as e:
                errors.append(e)

        def scanner_worker():
            try:
                scanner = EdgeScanner(self.TEST_DB)
                for _ in range(5):
                    scanner._scan_once()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=scanner_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
