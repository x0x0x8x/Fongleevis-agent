"""
_router_api.py
高层对外API实现
分层规范：
1. /v1/chat/completions     → 纯文本对话（复用现有链路）
2. /v1/embeddings           → 向量生成独立通路
3. /v1/rerank               → 重排序独立通路
4. /v1/multimodal/chat      → VLM图文问答【预留】
5. /v1/images/generations   → 文生图【预留】
⚠️ 链路互相隔离，不复用处理逻辑
⚠️ 客户端禁止在chat接口传入多媒体数组content
"""
import json
import time
import asyncio
import concurrent.futures
from typing import (
    Optional, Union, List, Dict, Any, Callable, Generator, AsyncGenerator
)
from ._config import log, GATEWAY_CONFIG, is_chat_model, is_embedding_model, is_rerank_model
from ._exceptions import InvalidParamError, ModelNotFoundError
from ._http_client import _stream_request, _fake_stream_response, _nonstream_request
from ._embedding_rerank import create_embedding, rerank
# 多媒体模块，仅预留，暂不启用
# from ._multimodal import (
#     multimodal_chat_handler,
#     image_generate_handler,
#     audio_transcribe_handler,
#     tts_generate_handler
# )
from ._logger import request_logger, extract_delta_text


# =========================================================
# 内部工具
# =========================================================
def _parse_sse_data(raw_line: str) -> Optional[str]:
    """内部工具：从SSE原始行中提取data字段内容"""
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("data: "):
        return line[6:].strip()
    return None


def _build_chat_request_body(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    tools: Optional[List[Dict[str, Any]]],
    tool_choice: Optional[Union[str, Dict[str, Any]]],
    temperature: float,
    top_p: float,
    stop: Optional[Union[str, List[str]]],
    presence_penalty: float,
    frequency_penalty: float,
    thinking_enabled: bool,
    reasoning_effort: Optional[str],
    extra_params: Optional[Dict[str, Any]],
    stream: bool
) -> Dict[str, Any]:
    """
    chat/completions 专用body构建
    【仅用于文本对话通路】
    """
    # ===================== 参数校验 =====================
    if reasoning_effort is not None and thinking_enabled is False:
        log("警告：传入了reasoning_effort，但thinking_enabled=False，该参数将被忽略", "WARNING")

    if not (-2.0 <= presence_penalty <= 2.0):
        raise InvalidParamError("presence_penalty 取值范围必须在 [-2.0, 2.0]")
    if not (-2.0 <= frequency_penalty <= 2.0):
        raise InvalidParamError("frequency_penalty 取值范围必须在 [-2.0, 2.0]")
    if not (0.0 <= temperature <= 2.0):
        raise InvalidParamError("temperature 取值范围必须在 [0.0, 2.0]")
    if not (0.0 <= top_p <= 1.0):
        raise InvalidParamError("top_p 取值范围必须在 [0.0, 1.0]")
    if max_tokens <= 0:
        raise InvalidParamError("max_tokens 必须大于0")

    # 架构约束：chat通路禁止多媒体数组content
    for msg in messages:
        cnt = msg.get("content")
        if isinstance(cnt, list):
            raise InvalidParamError(
                "当前接口仅支持纯文本对话，不支持多媒体数组content；"
                "图文/视频问答请调用 /v1/multimodal/chat"
            )

    # ===================== 组装请求体 =====================
    request_body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }

    if tools is not None:
        request_body["tools"] = tools
    if tool_choice is not None:
        request_body["tool_choice"] = tool_choice
    if stop is not None:
        request_body["stop"] = stop
    if abs(presence_penalty) > 1e-6:
        request_body["presence_penalty"] = presence_penalty
    if abs(frequency_penalty) > 1e-6:
        request_body["frequency_penalty"] = frequency_penalty

    # 思考参数处理
    if thinking_enabled:
        request_body["thinking_enabled"] = True
        request_body["reasoning_effort"] = reasoning_effort if reasoning_effort is not None else "medium"

    if extra_params and isinstance(extra_params, dict):
        request_body.update(extra_params)

    return request_body


# =========================================================
# 【私有底层】对话原始转发（不对外暴露）
# =========================================================
def _chat_completions_stream_raw(
    request_body: Dict[str, Any],
    on_progress: Optional[Callable[[Any, Any], None]] = None
) -> Generator[str, None, None]:
    """【内部私有底层】流式链路，强制stream=True
    所有上游源（真实流 / fake_stream）统一输出标准SSE字符串，不再兼容裸字典
    产出：str
    """
    loop = None
    async_gen = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if GATEWAY_CONFIG.get("fake_streaming", False):
            source_agen = _fake_stream_response(request_body, on_progress)
        else:
            source_agen = _stream_request(request_body, on_progress)

        req_id = request_logger.log_request(request_body)
        chunk_num = 0
        total_content_parts = []
        total_tool_calls = []
        finish_reason = None
        done_received = False
        start_time = time.time()

        async def wrapped_source() -> AsyncGenerator[str, None]:
            nonlocal chunk_num, done_received, finish_reason
            try:
                async for raw_chunk in source_agen:
                    # 架构约束：上游一定输出str，直接跳过dict分支
                    if not isinstance(raw_chunk, str):
                        log(f"非法分片类型，预期字符串，得到{type(raw_chunk)}，丢弃", "WARN")
                        continue

                    sse_data = _parse_sse_data(raw_chunk)
                    chunk_dict: Optional[Dict[str, Any]] = None
                    if sse_data == "[DONE]":
                        done_received = True
                    elif sse_data:
                        try:
                            chunk_dict = json.loads(sse_data)
                        except Exception:
                            pass

                    chunk_num += 1
                    if chunk_dict is not None:
                        request_logger.log_stream_chunk(req_id, chunk_num, chunk_dict)
                        choices = chunk_dict.get("choices")
                        # 两层判断：非空 + 确认是列表
                        if choices is not None and isinstance(choices, list):
                            choice = choices[0]
                            # choice 依然可能是Any，继续窄化
                            if isinstance(choice, dict):
                                delta = choice.get("delta", {})
                                content = extract_delta_text(delta)
                                if content:
                                    total_content_parts.append(content)
                                tool_calls = delta.get("tool_calls", [])
                                if isinstance(tool_calls, list):
                                    for tc in tool_calls:
                                        total_tool_calls.append(tc)
                                if choice.get("finish_reason"):
                                    finish_reason = choice["finish_reason"]
                        if chunk_dict.get("done"):
                            done_received = True
                    yield raw_chunk
            finally:
                try:
                    request_logger.log_stream_complete(
                        req_id=req_id,
                        chunk_count=chunk_num,
                        total_content=''.join(total_content_parts),
                        tool_calls=total_tool_calls if total_tool_calls else None,
                        finish_reason=finish_reason,
                        duration=time.time() - start_time,
                        done_received=done_received,
                    )
                except Exception:
                    pass

        async_gen = wrapped_source()
        while True:
            try:
                chunk: str = loop.run_until_complete(async_gen.__anext__())
                yield chunk
            except StopAsyncIteration:
                break
            except GeneratorExit:
                log("客户端主动断开流式连接", "INFO")
                break
            except Exception as e:
                log(f"流式生成器异常: {e}", "ERROR")
                # 🔥 修复：发送错误事件给客户端
                error_event = f"data: {json.dumps({'error': str(e)})}\n\n"
                yield error_event
                break

    except Exception as e:
        # 🔥 修复：外层异常捕获，发送错误事件
        log(f"流式外层异常: {e}", "ERROR")
        error_event = f"data: {json.dumps({'error': str(e)})}\n\n"
        yield error_event

    finally:
        if async_gen is not None and loop is not None and not loop.is_closed():
            try:
                loop.run_until_complete(async_gen.aclose())
            except Exception as e:
                log(f"执行async_gen.aclose异常: {e}", "ERROR")
        if loop is not None and not loop.is_closed():
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)


def _chat_completions_nonstream_raw(
    request_body: Dict[str, Any]
) -> Dict[str, Any]:
    """【内部私有底层】非流式链路，强制stream=False"""
    req_id = request_logger.log_request(request_body)
    start_time = time.time()

    def _run_request():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_nonstream_request(request_body, False))
                request_logger.log_response(
                    req_id=req_id,
                    response_data=result,
                    error=None,
                    duration=time.time() - start_time
                )
                return result
            finally:
                if not loop.is_closed():
                    loop.close()
        except Exception as e:
            request_logger.log_response(
                req_id=req_id,
                response_data=None,
                error=e,
                duration=time.time() - start_time
            )
            raise

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        return _run_request()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_request)
            return future.result()


# =========================================================
# 【对外公开接口】对话模型（/v1/chat/completions）
# =========================================================
def chat_completions(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Union[str, Dict[str, Any]]] = "auto",
    temperature: float = 0.7,
    top_p: float = 0.95,
    stop: Optional[Union[str, List[str]]] = None,
    presence_penalty: float = 0.0,
    frequency_penalty: float = 0.0,
    thinking_enabled: bool = False,
    reasoning_effort: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    LLM对话【非流式】 /v1/chat/completions
    仅支持纯文本消息，多媒体消息禁止调用此接口
    """
    # 路由校验
    if not is_chat_model(model):
        raise ModelNotFoundError(f"模型 {model} 不属于文本对话模型，禁止使用chat/completions接口")

    body = _build_chat_request_body(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        tools=tools,
        tool_choice=tool_choice,
        temperature=temperature,
        top_p=top_p,
        stop=stop,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        extra_params=extra_params,
        stream=False
    )
    return _chat_completions_nonstream_raw(body)


def chat_completions_stream(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Union[str, Dict[str, Any]]] = "auto",
    temperature: float = 0.7,
    top_p: float = 0.95,
    stop: Optional[Union[str, List[str]]] = None,
    presence_penalty: float = 0.0,
    frequency_penalty: float = 0.0,
    thinking_enabled: bool = False,
    reasoning_effort: Optional[str] = None,
    on_progress: Optional[Callable[[Any, Any], None]] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Generator[str, None, None]:
    """
    LLM对话【流式】 /v1/chat/completions
    仅支持纯文本消息，多媒体消息禁止调用此接口
    """
    start_ts = time.time()
    log(f"[STREAM START] model={model}", "INFO")
    try:
        if not is_chat_model(model):
            raise ModelNotFoundError(f"模型 {model} 不属于文本对话模型，禁止使用chat/completions接口")

        body = _build_chat_request_body(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            extra_params=extra_params,
            stream=True
        )
        yield from _chat_completions_stream_raw(body, on_progress=on_progress)
    finally:
        cost = time.time() - start_ts
        log(f"[STREAM FINISH] model={model}, total_cost={cost:.3f}s", "INFO")


# =========================================================
# 【对外公开接口】向量 & Rerank 通路（/v1/embeddings / /v1/rerank）
# =========================================================
async def embedding_request(
    model: str,
    text_input: Union[str, List[str]],
    input_type: str = "passage",
    truncate: str = "NONE"
) -> Union[List[float], List[List[float]]]:
    """
    /v1/embeddings 向量生成接口
    独立通路，不和chat链路复用
    """
    if not is_embedding_model(model):
        raise InvalidParamError(f"{model} 不是embedding向量模型")
    return await create_embedding(
        text=text_input,
        model_alias=model,
        input_type=input_type,
        truncate=truncate
    )


async def rerank_request(
    query: str,
    passages: List[Union[str, Dict[str, Any]]],
    model: str,
    return_input: bool = False
) -> Dict[str, Any]:
    """
    /v1/rerank 重排序接口
    独立通路，不和chat链路复用
    """
    if not is_rerank_model(model):
        raise InvalidParamError(f"{model} 不是rerank重排序模型")
    return await rerank(
        query=query,
        passages=passages,
        model_alias=model,
        return_input=return_input
    )


# =========================================================
# 【预留占位】多媒体通路（文本→媒体 / 媒体→文本，暂不启用）
# =========================================================
# async def handle_multimodal_chat(raw_body: Dict[str, Any]):
#     """
#     /v1/multimodal/chat
#     多媒体输入→文本输出（VLM图文/视频问答）
#     """
#     return await multimodal_chat_handler(raw_body)
#
# async def handle_image_generate(raw_body: Dict[str, Any]):
#     """/v1/images/generations 文生图"""
#     return await image_generate_handler(raw_body)
#
# async def handle_tts(raw_body: Dict[str, Any]):
#     """/v1/audio/speech 文字转语音"""
#     return await tts_generate_handler(raw_body)
#
# async def handle_audio_transcribe(form_data):
#     """/v1/audio/transcriptions 语音转文本"""
#     return await audio_transcribe_handler(form_data)