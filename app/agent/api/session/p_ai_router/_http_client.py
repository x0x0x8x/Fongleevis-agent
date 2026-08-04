"""
底层aiohttp网络请求、流式/非流式原始请求、fake_stream模拟流式
【内部私有模块，禁止外部直接导入】
"""
import json
import asyncio
import time
import traceback
from typing import Dict, Any, Optional, Callable

import aiohttp

from ._config import (
    resolve_model,
    apply_thinking_param,
    GATEWAY_CONFIG,
    increment_connections,
    decrement_connections,
    AUTH,
    log
)
from ._limiter import wait_for_rate_limit, rate_limiter
from ._logger import request_logger, extract_delta_text, log_request_to_file

# 常量
BASE_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 60.0
RETRY_MULTIPLIER = 2.0


async def _stream_request(request_body: Dict[str, Any], on_progress: Optional[Callable] = None):
    model_alias = request_body.get("model")
    if not model_alias:
        raise ValueError("request_body must contain 'model' field")
    start_time = time.time()
    req_id = request_logger.log_request(request_body)
    route = resolve_model(model_alias)
    url = route["base_url"]
    real_model = route["model"]
    rpm_limit = route.get("rpm_limit", 0)
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if route.get("auth"):
        headers["Authorization"] = f"Bearer {AUTH[route['auth']]}"

    body = request_body.copy()
    body["model"] = real_model
    body["stream"] = True
    body = apply_thinking_param(body, model_alias)
    if body.get("tools") and body.get("tool_choice") is None:
        body["tool_choice"] = "auto"

    total_chunk_num = 0
    total_content_parts = []
    finish_reason = None
    received_done = False
    has_sent_to_client = False
    usage_info = {}

    for attempt in range(GATEWAY_CONFIG["max_retries"] + 1):
        try:
            await wait_for_rate_limit(model_alias, rpm_limit)
            rate_limiter.mark_request(model_alias)
            increment_connections()

            timeout = aiohttp.ClientTimeout(
                total=GATEWAY_CONFIG["timeout"],
                sock_read=30
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                log_request_to_file(url, headers, body)
                async with session.post(url, json=body, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(f"HTTP {response.status}: {error_text[:400]}")

                    try:
                        async for line in response.content:
                            line = line.decode('utf-8').strip()
                            if not line:
                                continue

                            if line.startswith('data:'):
                                data = line[5:].strip()

                                if data == '[DONE]':
                                    received_done = True
                                    complete_response = {
                                        "choices": [{
                                            "index": 0,
                                            "finish_reason": finish_reason or "stop",
                                            "message": {
                                                "role": "assistant",
                                                "content": ''.join(total_content_parts),
                                                "tool_calls": None
                                            }
                                        }],
                                        "usage": usage_info
                                    }
                                    request_logger.log_stream_complete(
                                        req_id=req_id,
                                        chunk_count=total_chunk_num,
                                        total_content=''.join(total_content_parts),
                                        tool_calls=None,
                                        finish_reason=finish_reason,
                                        duration=time.time() - start_time,
                                        done_received=received_done,
                                    )
                                    request_logger.log_response(
                                        req_id,
                                        complete_response,
                                        duration=time.time() - start_time
                                    )
                                    has_sent_to_client = True
                                    yield "data: [DONE]\n\n"
                                    return

                                try:
                                    chunk = json.loads(data)
                                except json.JSONDecodeError as e:
                                    log(f"SSE JSON解析失败: {e}", "WARN")
                                    continue

                                choices = chunk.get("choices", [])
                                if not choices:
                                    if "usage" in chunk:
                                        usage_info.update(chunk["usage"])
                                    continue

                                total_chunk_num += 1
                                choice = choices[0]
                                if choice.get("finish_reason") is not None:
                                    finish_reason = choice.get("finish_reason")
                                delta = choice.get("delta", {})
                                content = extract_delta_text(delta)
                                if content:
                                    total_content_parts.append(content)

                                if "usage" in chunk:
                                    usage_info.update(chunk["usage"])

                                request_logger.log_stream_chunk(req_id, total_chunk_num, chunk)

                                if on_progress:
                                    try:
                                        on_progress(None, chunk)
                                    except Exception:
                                        pass

                                out = f"data: {data}\n\n"
                                has_sent_to_client = True
                                yield out
                    except GeneratorExit:
                        # 上层客户端主动断开生成器，立刻关闭HTTP连接，终止上游推理
                        log("流式生成器收到关闭信号，主动断开上游TCP连接", "INFO")
                        response.close()
                        raise

            if not received_done:
                raise RuntimeError("upstream closed before [DONE]")

        except aiohttp.ClientError as e:
            duration = time.time() - start_time
            if has_sent_to_client:
                log(f"流式中途断流(不重试): {e}", "ERROR")
                traceback.print_exc()
                request_logger.log_response(req_id, None, error=e, duration=duration)
                return
            if attempt < GATEWAY_CONFIG["max_retries"]:
                delay = min(BASE_RETRY_DELAY * (RETRY_MULTIPLIER ** attempt), MAX_RETRY_DELAY)
                log(
                    f"流式请求失败，准备重试 ({attempt + 1}/{GATEWAY_CONFIG['max_retries']}): {e}, 延迟 {delay:.1f}s",
                    "WARN"
                )
                await asyncio.sleep(delay)
                continue
            log(f"流式请求最终失败: {e}", "ERROR")
            traceback.print_exc()
            request_logger.log_response(req_id, None, error=e, duration=duration)
            error_response = {"error": {"message": str(e), "type": "gateway_error"}}
            yield f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n"
            return

        except Exception as e:
            duration = time.time() - start_time
            if has_sent_to_client:
                log(f"流式中途断流(不重试): {e}", "ERROR")
                traceback.print_exc()
                request_logger.log_response(req_id, None, error=e, duration=duration)
                return
            if attempt < GATEWAY_CONFIG["max_retries"]:
                delay = min(BASE_RETRY_DELAY * (RETRY_MULTIPLIER ** attempt), MAX_RETRY_DELAY)
                log(
                    f"流式请求失败，准备重试 ({attempt + 1}/{GATEWAY_CONFIG['max_retries']}): {e}, 延迟 {delay:.1f}s",
                    "WARN"
                )
                await asyncio.sleep(delay)
                continue
            log(f"流式请求最终失败: {e}", "ERROR")
            traceback.print_exc()
            request_logger.log_response(req_id, None, error=e, duration=duration)
            error_response = {"error": {"message": str(e), "type": "gateway_error"}}
            has_sent_to_client = True
            yield f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n"
            return
        finally:
            decrement_connections()
async def _nonstream_request(request_body: Dict[str, Any], stream: bool):
    model_alias = request_body.get("model")
    if not model_alias:
        raise ValueError("request_body must contain 'model' field")

    start_time = time.time()
    req_id = request_logger.log_request(request_body)
    route = resolve_model(model_alias)
    url = route["base_url"]
    real_model = route["model"]
    rpm_limit = route.get("rpm_limit", 0)

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if route.get("auth"):
        headers["Authorization"] = f"Bearer {AUTH[route['auth']]}"

    body = request_body.copy()
    body["model"] = real_model
    body["stream"] = stream
    body = apply_thinking_param(body, model_alias)
    if body.get("tools") and body.get("tool_choice") is None:
        body["tool_choice"] = "auto"

    for attempt in range(GATEWAY_CONFIG["max_retries"] + 1):
        try:
            await wait_for_rate_limit(model_alias, rpm_limit)
            rate_limiter.mark_request(model_alias)
            increment_connections()

            timeout = aiohttp.ClientTimeout(total=GATEWAY_CONFIG["timeout"])
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise RuntimeError(f"HTTP {response.status}: {error_text[:200]}")
                    result = await response.json()

            duration = time.time() - start_time
            request_logger.log_response(req_id, result, duration=duration)
            return result

        except asyncio.CancelledError:
            raise

        except aiohttp.ClientError as e:
            if attempt < GATEWAY_CONFIG["max_retries"]:
                delay = min(BASE_RETRY_DELAY * (RETRY_MULTIPLIER ** attempt), MAX_RETRY_DELAY)
                log(
                    f"请求失败: {e}，{delay:.1f}s后重试 (尝试 {attempt + 2}/{GATEWAY_CONFIG['max_retries'] + 1})",
                    "WARN"
                )
                await asyncio.sleep(delay)
                continue
            else:
                duration = time.time() - start_time
                request_logger.log_response(req_id, None, error=e, duration=duration)
                return {
                    "choices": [{
                        "message": {"content": f"服务暂时不可用: {str(e)[:100]}", "tool_calls": []},
                        "finish_reason": "stop"
                    }]
                }

        except Exception as e:
            if attempt < GATEWAY_CONFIG["max_retries"]:
                delay = min(BASE_RETRY_DELAY * (RETRY_MULTIPLIER ** attempt), MAX_RETRY_DELAY)
                log(
                    f"请求失败: {e}，{delay:.1f}s后重试 (尝试 {attempt + 2}/{GATEWAY_CONFIG['max_retries'] + 1})",
                    "WARN"
                )
                await asyncio.sleep(delay)
                continue
            else:
                duration = time.time() - start_time
                request_logger.log_response(req_id, None, error=e, duration=duration)
                return {
                    "choices": [{
                        "message": {"content": f"服务暂时不可用: {str(e)[:100]}", "tool_calls": []},
                        "finish_reason": "stop"
                    }]
                }
        finally:
            decrement_connections()
async def _fake_stream_response(request_body: Dict[str, Any], on_progress: Optional[Callable] = None, req_id: int = None):
    """将非流式结果模拟为SSE流式输出（内部转换）"""
    model_alias = request_body.get("model")
    if not model_alias:
        raise ValueError("request_body must contain 'model' field")

    # 外层已经执行log_request，内部不再重复打请求日志
    request_logger.set_internal_conversion(True)
    try:
        request_body["stream"] = False
        result = await _nonstream_request(request_body, stream=False)
    finally:
        request_logger.set_internal_conversion(False)

    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    tool_calls = result.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])

    CHUNK_STEP = 20
    SLEEP_INTERVAL = 0.05

    if content:
        for i in range(0, len(content), CHUNK_STEP):
            chunk_text = content[i:i + CHUNK_STEP]
            chunk = {"choices": [{"index": 0, "delta": {"content": chunk_text}}]}
            chunk_data = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield chunk_data
            if on_progress:
                try:
                    on_progress(i // CHUNK_STEP + 1, chunk)
                except Exception:
                    pass
            await asyncio.sleep(SLEEP_INTERVAL)
    if tool_calls:
        tool_call_chunk = {"choices": [{"index": 0, "delta": {"tool_calls": tool_calls}}]}
        tool_call_data = f"data: {json.dumps(tool_call_chunk, ensure_ascii=False)}\n\n"
        yield tool_call_data
    end_marker = "data: [DONE]\n\n"
    yield end_marker