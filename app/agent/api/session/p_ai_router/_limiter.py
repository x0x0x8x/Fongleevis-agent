"""
模型独立RPM滑动窗口限流器
【模块文件名：_limiter.py】
重度重构版本｜不兼容旧接口
定位：前置RPM请求频次限流，规避上游厂商429
规则：阈值由上层路由配置传入，不再使用全局统一限额
仅限制 请求数(RPM)，Token限流(TPM)另行实现
"""
import time
import threading
import asyncio
from collections import deque
from typing import Dict, Any

from ._config import WINDOW_SIZE, SKIP_RATE_LIMIT_WAIT, GATEWAY_CONFIG
from ._config import log

# 常量定义
WAIT_MIN_SLEEP = 0.5
WAIT_MAX_STEP_SLEEP = 1.0
WAIT_TIME_BUFFER = 0.1
IDLE_CLEAN_SECONDS = 300.0  # 空闲5分钟回收限流对象

class _ModelRateBucket:
    """单个模型的滑动窗口桶，纯数据容器"""
    def __init__(self, window_seconds: int):
        self.window = window_seconds
        self.requests: deque[float] = deque()
        self.last_active: float = time.time()
        self._lock = threading.Lock()

    def _prune(self, now: float):
        cutoff = now - self.window
        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()

    def is_idle(self, now: float) -> bool:
        """长时间无请求且窗口为空，允许回收"""
        with self._lock:
            self._prune(now)
            return (now - self.last_active) > IDLE_CLEAN_SECONDS and len(self.requests) == 0

    def can_consume(self, now: float, max_rpm: int) -> bool:
        """判断是否可以发起新请求"""
        with self._lock:
            self._prune(now)
            self.last_active = now
            return len(self.requests) < max_rpm

    def get_required_wait(self, now: float, max_rpm: int) -> float:
        """获取需要等待的时长，0=无需等待"""
        with self._lock:
            self._prune(now)
            self.last_active = now
            if len(self.requests) < max_rpm:
                return 0.0
            oldest_ts = self.requests[0]
            wait_sec = (oldest_ts + self.window) - now
            return max(0.0, wait_sec + WAIT_TIME_BUFFER)

    def consume(self, now: float):
        """占用一个请求额度"""
        with self._lock:
            self._prune(now)
            self.requests.append(now)
            self.last_active = now

    def get_stat(self, now: float, max_rpm: int) -> Dict[str, Any]:
        with self._lock:
            self._prune(now)
            self.last_active = now
            count = len(self.requests)
            return {
                "current": count,
                "max_rpm": max_rpm,
                "remaining": max(0, max_rpm - count),
                "window_seconds": self.window
            }
class PerModelRateLimiter:
    def __init__(self, window_seconds: int):
        self._window = window_seconds
        self._buckets: Dict[str, _ModelRateBucket] = {}
        self._global_lock = threading.Lock()

    def _get_bucket(self, model_alias: str) -> _ModelRateBucket:
        with self._global_lock:
            # 定时清理长期空闲bucket
            self._clean_idle_buckets()
            if model_alias not in self._buckets:
                self._buckets[model_alias] = _ModelRateBucket(self._window)
            return self._buckets[model_alias]

    def _clean_idle_buckets(self):
        now = time.time()
        remove_list = []
        for alias, bucket in self._buckets.items():
            if bucket.is_idle(now):
                remove_list.append(alias)
        for alias in remove_list:
            del self._buckets[alias]
            log(f"[LIMITER] 回收空闲模型限流桶: {alias}", "DEBUG")

    def can_request(self, model_alias: str, max_rpm: int) -> bool:
        now = time.time()
        bucket = self._get_bucket(model_alias)
        return bucket.can_consume(now, max_rpm)

    def get_wait_seconds(self, model_alias: str, max_rpm: int) -> float:
        now = time.time()
        bucket = self._get_bucket(model_alias)
        return bucket.get_required_wait(now, max_rpm)

    def mark_request(self, model_alias: str):
        """标记一次请求占用额度（请求成功发起后调用）"""
        now = time.time()
        bucket = self._get_bucket(model_alias)
        bucket.consume(now)

    def get_model_stat(self, model_alias: str, max_rpm: int) -> Dict[str, Any]:
        now = time.time()
        bucket = self._get_bucket(model_alias)
        return bucket.get_stat(now, max_rpm)

    def get_all_model_stats(self, rpm_mapping: Dict[str, int]) -> Dict[str, Dict[str, Any]]:
        """批量获取状态，需要传入{model_alias: max_rpm}映射"""
        out = {}
        for alias, rpm in rpm_mapping.items():
            out[alias] = self.get_model_stat(alias, rpm)
        return out

# 全局单例，窗口尺寸由全局配置统一控制
rate_limiter = PerModelRateLimiter(window_seconds=WINDOW_SIZE)

async def wait_for_rate_limit(model_alias: str, max_rpm: int):
    """
    阻塞等待获取请求额度
    :param model_alias: 模型别名
    :param max_rpm: 当前路由对应的厂商每分钟最大请求数，来自路由配置
    """
    if max_rpm <= 0:
        # 配置0代表不启用限流，直接放行
        return

    if SKIP_RATE_LIMIT_WAIT:
        stat = rate_limiter.get_model_stat(model_alias, max_rpm)
        if stat["remaining"] <= 0:
            raise RuntimeError(
                f"限流触发[{model_alias}] "
                f"配额{stat['current']}/{stat['max_rpm']} RPM，快速失败模式禁止排队"
            )
        return

    max_wait = GATEWAY_CONFIG.get("max_wait_seconds", 30)
    start_ts = time.time()
    logged_wait_notice = False

    while not rate_limiter.can_request(model_alias, max_rpm):
        wait_sec = rate_limiter.get_wait_seconds(model_alias, max_rpm)
        wait_sec = max(wait_sec, WAIT_MIN_SLEEP)

        if (time.time() - start_ts) >= max_wait:
            log(
                f"[LIMITER] [{model_alias}] 限流等待超时{max_wait}s，强制放行",
                "WARNING"
            )
            break

        if not logged_wait_notice:
            log(f"[LIMITER] [{model_alias}] 请求达到RPM上限，开始排队等待", "INFO")
            logged_wait_notice = True

        # 单次sleep设置上限，保证任务可以被asyncio正常取消
        await asyncio.sleep(min(wait_sec, WAIT_MAX_STEP_SLEEP))