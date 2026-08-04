"""
_embedding_rerank.py
Embedding向量生成、Rerank重排序处理器
独立RAG链路，不和对话、多媒体模块合并
接口对应：/v1/embeddings、/v1/rerank
【重要】网关整体为异步架构，移除所有同步包装函数，仅保留async接口
"""
import asyncio
from typing import Union, List, Dict, Any

import aiohttp

from ._config import (
    resolve_model,
    increment_connections,
    decrement_connections,
    GATEWAY_CONFIG,
    log,
    AUTH
)
from ._limiter import wait_for_rate_limit, rate_limiter
from ._exceptions import InvalidParamError, UpstreamRequestError

# 重试常量
BASE_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 60.0
RETRY_MULTIPLIER = 2.0


async def rerank(
    query: str,
    passages: List[Union[str, Dict[str, Any]]],
    model_alias: str = "nv-rerank-qa",
    return_input: bool = False
) -> Dict[str, Any]:
    """
    Rerank重排序接口
    :param query: 查询文本
    :param passages: 候选文档列表
    :param model_alias: 路由模型别名
    :param return_input: 是否在结果附带原始输入文档
    """
    route_info = resolve_model(model_alias)
    if route_info.get("api_type") != "rerank":
        raise InvalidParamError(f"模型 {model_alias} 类型不是rerank")

    rpm_limit = route_info.get("rpm_limit", 0)
    original_passages = passages.copy() if return_input else None
    processed_passages: List[Dict[str, Any]] = []

    for passage in passages:
        if isinstance(passage, str):
            processed_passages.append({"text": passage})
        elif isinstance(passage, dict):
            item = {"text": passage.get("text", "")}
            processed_passages.append(item)
        else:
            processed_passages.append({"text": str(passage)})

    url = route_info["base_url"]
    payload = {
        "model": route_info["model"],
        "query": {"text": query},
        "passages": processed_passages
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    auth_key = route_info.get("auth")
    if auth_key:
        headers["Authorization"] = f"Bearer {AUTH[auth_key]}"

    await wait_for_rate_limit(model_alias, rpm_limit)
    rate_limiter.mark_request(model_alias)
    increment_connections()

    max_retries = GATEWAY_CONFIG["max_retries"]
    timeout = aiohttp.ClientTimeout(total=GATEWAY_CONFIG["timeout"])

    try:
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload, headers=headers) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise UpstreamRequestError(f"Rerank HTTP {response.status}: {error_text[:500]}")
                        result = await response.json()
                        if return_input and original_passages is not None:
                            result["original_passages"] = original_passages
                        return result
            except (aiohttp.ClientError, UpstreamRequestError) as e:
                if attempt >= max_retries:
                    raise UpstreamRequestError(f"Rerank最终请求失败: {str(e)}")
                delay = min(BASE_RETRY_DELAY * (RETRY_MULTIPLIER ** attempt), MAX_RETRY_DELAY)
                log(f"Rerank请求失败，准备重试 {attempt+1}/{max_retries} delay={delay:.1f}s: {e}", "WARN")
                await asyncio.sleep(delay)
    finally:
        decrement_connections()


async def create_embedding(
    text: Union[str, List[str]],
    model_alias: str = "bge-m3",
    input_type: str = "passage",
    truncate: str = "NONE"
) -> List[float] | List[List[float]]:
    """
    获取向量Embedding
    :param text: 单个文本 / 文本列表
    :param model_alias: 模型别名
    :param input_type: query / passage
    :param truncate: NONE / START / END
    :return: 单条向量｜多条向量列表
    """
    input_texts = [text] if isinstance(text, str) else text
    route_info = resolve_model(model_alias)
    if route_info.get("api_type") != "embeddings":
        raise InvalidParamError(f"模型 {model_alias} 类型不是embeddings")

    rpm_limit = route_info.get("rpm_limit", 0)
    base_url = route_info["base_url"].rstrip("/")
    if base_url.endswith("/embeddings"):
        url = base_url
    else:
        url = f"{base_url}/embeddings"
    real_model = route_info["model"]

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    auth_key = route_info.get("auth")
    if auth_key:
        headers["Authorization"] = f"Bearer {AUTH[auth_key]}"

    body = {
        "input": input_texts,
        "model": real_model,
        "encoding_format": "float",
        "input_type": input_type,
        "truncate": truncate
    }

    await wait_for_rate_limit(model_alias, rpm_limit)
    rate_limiter.mark_request(model_alias)
    increment_connections()

    max_retries = GATEWAY_CONFIG["max_retries"]
    timeout = aiohttp.ClientTimeout(total=GATEWAY_CONFIG["timeout"])

    try:
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=body, headers=headers) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise UpstreamRequestError(f"Embedding HTTP {response.status}: {error_text[:200]}")
                        result = await response.json()
                        embeddings = [item["embedding"] for item in result["data"]]
                        if isinstance(text, str):
                            return embeddings[0]
                        return embeddings
            except aiohttp.ClientError as e:
                if attempt >= max_retries:
                    raise UpstreamRequestError(f"Embedding最终请求失败: {str(e)}")
                delay = min(BASE_RETRY_DELAY * (RETRY_MULTIPLIER ** attempt), MAX_RETRY_DELAY)
                log(f"Embedding请求失败，准备重试 {attempt+1}/{max_retries} delay={delay:.1f}s: {e}", "WARN")
                await asyncio.sleep(delay)
    finally:
        decrement_connections()