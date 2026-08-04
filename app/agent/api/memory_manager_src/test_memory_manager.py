import unittest
import os
import threading
from datetime import datetime, timezone
from app.agent.api.memory_manager_src.memory_manager import get_memory_manager
import app.agent.api.memory_manager_src.memory_manager as memory_manager

class TestMemorySystem(unittest.TestCase):
    DB_PATH = "test_memory.db"

    def setUp(self):
        self._remove_db()
        # 重置全局单例，使用测试数据库路径初始化
        memory_manager.MemoryManager._instance = None
        memory_manager.MemoryManager(self.DB_PATH)
        self.manager = get_memory_manager()

    def tearDown(self):
        self.manager.close()
        memory_manager.MemoryManager._instance = None
        self._remove_db()

    def _remove_db(self):
        for suffix in ["", "-wal", "-shm"]:
            path = self.DB_PATH + suffix
            try:
                if os.path.exists(path):
                    os.remove(path)
            except (OSError, FileNotFoundError):
                # Windows：文件占用、权限不足；Linux：权限、链接问题
                # FileNotFoundError：竞态删除（exists判断后别的线程抢先删掉）
                pass

    def test_001_init_db(self):
        """验证数据库表结构正常初始化"""
        with self.manager._new_conn() as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row["name"] for row in tables}
        self.assertIn("memories", table_names)
        self.assertIn("memory_info", table_names)
        self.assertTrue(os.path.exists(self.DB_PATH))

    def test_002_create_space_implicitly(self):
        """验证写入数据自动隐式创建记忆空间"""
        space = self.manager.get_space_handle("user001")
        space.append("user", "hello", "greeting")
        space.append("assistant", "hi back", "reply")
        infos = space.get_all_infos()
        self.assertEqual(len(infos), 2)

    def test_003_space_isolation(self):
        """验证不同空间数据完全隔离"""
        space_a = self.manager.get_space_handle("spaceA")
        space_b = self.manager.get_space_handle("spaceB")
        space_a.append("user", "memory A", summary="memory A")
        space_b.append("user", "memory B", summary="memory B")

        infos_a = space_a.get_all_infos()
        infos_b = space_b.get_all_infos()
        summaries_a = [i["summary"] for i in infos_a.values()]
        summaries_b = [i["summary"] for i in infos_b.values()]

        self.assertIn("memory A", summaries_a)
        self.assertNotIn("memory B", summaries_a)
        self.assertIn("memory B", summaries_b)
        self.assertNotIn("memory A", summaries_b)

    def test_004_append_single(self):
        """验证单条记忆写入，冷热表数据一致且字段正确"""
        space = self.manager.get_space_handle("user001")
        mid = space.append("user", "Hello World", "greeting")
        with self.manager._new_conn() as conn:
            mem_row = conn.execute("SELECT * FROM memories WHERE memory_id=?", (mid,)).fetchone()
            info_row = conn.execute("SELECT * FROM memory_info WHERE memory_id=?", (mid,)).fetchone()
        self.assertIsNotNone(mem_row)
        self.assertIsNotNone(info_row)
        self.assertEqual(mem_row["content"], "Hello World")
        self.assertEqual(info_row["summary"], "greeting")
        self.assertEqual(info_row["length"], len("Hello World"))
        self.assertEqual(mem_row["memory_id"], info_row["memory_id"])
        self.assertIn("created_at", info_row.keys())
        self.assertIn("updated_at", info_row.keys())

    def test_005_append_batch(self):
        """验证批量写入记忆功能正常"""
        count = 100
        memories = [{"role": "user", "content": f"memory_{i}", "summary": f"sum_{i}"} for i in range(count)]
        space = self.manager.get_space_handle("batch_space")
        ids = space.append_batch(memories)
        self.assertEqual(len(ids), count)
        with self.manager._new_conn() as conn:
            mem_count = conn.execute("SELECT COUNT(*) FROM memories WHERE space_id='batch_space'").fetchone()[0]
            info_count = conn.execute("SELECT COUNT(*) FROM memory_info WHERE space_id='batch_space'").fetchone()[0]
        self.assertEqual(mem_count, count)
        self.assertEqual(info_count, count)

    def test_006_blank_param_rejected(self):
        """验证空白角色/内容/摘要均被拦截"""
        space = self.manager.get_space_handle("space")
        # 空角色
        with self.assertRaises(ValueError):
            space.append("", "content", "summary")
        # 空内容
        with self.assertRaises(ValueError):
            space.append("user", "", "summary")
        # 空摘要
        with self.assertRaises(ValueError):
            space.append("user", "content", "")

        with self.manager._new_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertEqual(count, 0)

    def test_007_update_content(self):
        """验证更新记忆正文，同步刷新长度与更新时间"""
        space = self.manager.get_space_handle("space")
        mid = space.append("user", "original", "orig summary")
        result = space.update(mid, content="updated content")
        self.assertTrue(result)
        with self.manager._new_conn() as conn:
            mem = conn.execute("SELECT content FROM memories WHERE memory_id=?", (mid,)).fetchone()
            info = conn.execute("SELECT length, updated_at FROM memory_info WHERE memory_id=?", (mid,)).fetchone()
        self.assertEqual(mem["content"], "updated content")
        self.assertEqual(info["length"], len("updated content"))
        self.assertIsNotNone(info["updated_at"])

    def test_008_update_summary(self):
        """验证更新记忆摘要"""
        space = self.manager.get_space_handle("space")
        mid = space.append("user", "original", "orig summary")
        result = space.update(mid, summary="new summary")
        self.assertTrue(result)
        with self.manager._new_conn() as conn:
            info = conn.execute("SELECT summary FROM memory_info WHERE memory_id=?", (mid,)).fetchone()
        self.assertEqual(info["summary"], "new summary")

    def test_009_update_nonexistent(self):
        """验证更新不存在的记忆返回False"""
        space = self.manager.get_space_handle("space")
        result = space.update("nonexistent-id", content="new")
        self.assertFalse(result)

    def test_010_delete_memory(self):
        """验证单条记忆删除，冷热表同步清除"""
        space = self.manager.get_space_handle("space")
        mid = space.append("user", "to delete", "summary")
        result = space.delete(mid)
        self.assertTrue(result)
        with self.manager._new_conn() as conn:
            mem = conn.execute("SELECT * FROM memories WHERE memory_id=?", (mid,)).fetchone()
            info = conn.execute("SELECT * FROM memory_info WHERE memory_id=?", (mid,)).fetchone()
        self.assertIsNone(mem)
        self.assertIsNone(info)

    def test_011_delete_nonexistent(self):
        """验证删除不存在的记忆返回False"""
        space = self.manager.get_space_handle("space")
        result = space.delete("ghost-id")
        self.assertFalse(result)

    def test_012_transaction_rollback(self):
        """验证事务异常时自动回滚，数据不写入"""
        space = self.manager.get_space_handle("tx_space")
        try:
            with space._transaction() as conn:
                conn.execute("""
                    INSERT INTO memory_info
                    (memory_id, space_id, summary, length, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ("rollback_id", "tx_space", "test", 4,
                      datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
                raise RuntimeError("simulated error")
        except RuntimeError:
            pass
        with self.manager._new_conn() as conn:
            row = conn.execute("SELECT * FROM memory_info WHERE memory_id='rollback_id'").fetchone()
        self.assertIsNone(row)

    def test_013_committed_recovery(self):
        """验证数据持久化，重启管理器后数据仍存在"""
        space = self.manager.get_space_handle("space")
        space.append("user", "persistent", "summary")
        self.manager.close()
        memory_manager.MemoryManager._instance = None
        # 重新初始化测试库单例
        memory_manager.MemoryManager(self.DB_PATH)
        new_mgr = get_memory_manager()
        new_space = new_mgr.get_space_handle("space")
        infos = new_space.get_all_infos()
        self.assertEqual(len(infos), 1)
        new_mgr.close()

    def test_014_no_orphan_info(self):
        """验证无孤立的热表记录（外键约束生效）"""
        space = self.manager.get_space_handle("space")
        space.append("user", "good", "summary")
        with self.manager._new_conn() as conn:
            orphans = conn.execute(
                "SELECT memory_id FROM memory_info WHERE memory_id NOT IN (SELECT memory_id FROM memories)"
            ).fetchall()
        self.assertEqual(len(orphans), 0)

    def test_015_no_orphan_memories(self):
        """验证无孤立的冷表记录（外键约束生效）"""
        space = self.manager.get_space_handle("space")
        space.append("user", "good", "summary")
        with self.manager._new_conn() as conn:
            orphans = conn.execute(
                "SELECT memory_id FROM memories WHERE memory_id NOT IN (SELECT memory_id FROM memory_info)"
            ).fetchall()
        self.assertEqual(len(orphans), 0)

    def test_016_access_count_update_rule(self):
        """验证访问计数规则：get_memory更新，get_all_memory不更新"""
        space = self.manager.get_space_handle("space")
        mid = space.append("user", "test content", "test summary")
        infos = space.get_all_infos()
        self.assertEqual(infos[mid]["access_count"], 0)

        # get_memory 触发计数+1
        space.get_memory([mid])
        infos = space.get_all_infos()
        self.assertEqual(infos[mid]["access_count"], 1)
        self.assertIsNotNone(infos[mid]["last_access"])

        # get_all_memory 不触发计数
        space.get_all_memory()
        infos = space.get_all_infos()
        self.assertEqual(infos[mid]["access_count"], 1)

    def test_017_concurrent_same_space(self):
        """验证同一空间并发写入无异常、数据完整"""
        errors = []

        def writer(i):
            try:
                mgr = get_memory_manager()
                space = mgr.get_space_handle("concurrent_space")
                space.append("user", f"msg_{i}", f"sum_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        space = self.manager.get_space_handle("concurrent_space")
        self.assertEqual(len(space.get_all_infos()), 20)

    def test_018_concurrent_diff_space(self):
        """验证不同空间并发写入正常，空间间互不影响"""
        def writer(space_name, content):
            mgr = get_memory_manager()
            space = mgr.get_space_handle(space_name)
            space.append("user", content, "summary")

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=writer, args=(f"space_{i}", f"msg_{i}")))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i in range(10):
            space = self.manager.get_space_handle(f"space_{i}")
            infos = space.get_all_infos()
            self.assertEqual(len(infos), 1, f"space_{i} should have exactly one memory")
            full = space.get_memory(list(infos.keys()))
            self.assertIn(f"msg_{i}", list(full.values())[0]["content"])

    def test_019_special_characters(self):
        """验证特殊字符、emoji、SQL注入字符正常存储"""
        special = "中文 👋 emoji\n换行 '单引号' \"双引号\" ; DROP TABLE memories; -- comment"
        space = self.manager.get_space_handle("spec")
        mid = space.append("user", special, "summary")
        data = space.get_memory([mid])
        self.assertEqual(data[mid]["content"], special)

    def test_020_space_handle_singleton(self):
        """验证同一space_id全局返回同一个MemorySpace实例"""
        s1 = self.manager.get_space_handle("test_singleton")
        s2 = self.manager.get_space_handle("test_singleton")
        self.assertIs(s1, s2)

    def test_021_destroy_space(self):
        """验证空间销毁：数据清空、缓存移除、实例失效、重新获取生成新实例"""
        space = self.manager.get_space_handle("destroy_space")
        space.append("user", "content1", "sum1")
        space.append("user", "content2", "sum2")

        # 销毁返回删除条数
        deleted = space.destroy()
        self.assertEqual(deleted, 2)

        # 数据库无残留
        with self.manager._new_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_info WHERE space_id='destroy_space'").fetchone()[0]
        self.assertEqual(count, 0)

        # 已销毁实例操作抛异常
        with self.assertRaises(RuntimeError):
            space.append("user", "new", "sum")

        # 重新获取得到全新实例
        new_space = self.manager.get_space_handle("destroy_space")
        self.assertIsNot(space, new_space)
        new_space.append("user", "new content", "new summary")
        self.assertEqual(len(new_space.get_all_infos()), 1)

    def test_022_summaries_interface(self):
        """验证摘要类接口：仅返回摘要文本，结构为id->str"""
        space = self.manager.get_space_handle("sum_space")
        mid1 = space.append("user", "content1", "summary_1")
        mid2 = space.append("user", "content2", "summary_2")

        # 指定ID批量查摘要
        sums = space.get_summaries([mid1, mid2])
        self.assertIsInstance(sums, dict)
        self.assertEqual(sums[mid1], "summary_1")
        self.assertEqual(sums[mid2], "summary_2")

        # 全空间摘要
        all_sums = space.get_all_summaries()
        self.assertEqual(len(all_sums), 2)
        self.assertEqual(all_sums[mid1], "summary_1")

    def test_023_infos_interface(self):
        """验证元数据接口：不含content、role，仅返回元信息"""
        space = self.manager.get_space_handle("info_space")
        mid = space.append("user", "test content", "test sum")

        # 指定ID查元数据
        infos = space.get_infos([mid])
        self.assertIn("summary", infos[mid])
        self.assertIn("length", infos[mid])
        self.assertNotIn("content", infos[mid])
        self.assertNotIn("role", infos[mid])

        # 全空间元数据
        all_infos = space.get_all_infos()
        self.assertEqual(len(all_infos), 1)
        self.assertEqual(all_infos[mid]["summary"], "test sum")

    def test_024_get_all_memory_interface(self):
        """验证全量完整记忆接口：包含正文，不更新访问计数"""
        space = self.manager.get_space_handle("full_space")
        mid = space.append("user", "full content", "full sum")

        all_full = space.get_all_memory()
        self.assertEqual(len(all_full), 1)
        self.assertIn("content", all_full[mid])
        self.assertEqual(all_full[mid]["content"], "full content")
        self.assertEqual(all_full[mid]["role"], "user")
        # get_all_memory 仅返回role/content，需通过get_infos校验访问计数未增加
        infos = space.get_infos([mid])
        self.assertEqual(infos[mid]["access_count"], 0)

    def test_025_list_all_space_ids(self):
        """验证管理器枚举所有空间接口正常"""
        s1 = self.manager.get_space_handle("space_aaa")
        s1.append("user", "content1", "sum1")
        s2 = self.manager.get_space_handle("space_bbb")
        s2.append("user", "content2", "sum2")

        spaces = self.manager.list_all_space_ids()
        self.assertIn("space_aaa", spaces)
        self.assertIn("space_bbb", spaces)
        self.assertGreaterEqual(len(spaces), 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
