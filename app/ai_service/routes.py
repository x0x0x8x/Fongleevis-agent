# app/ai_service/routes.py
import json
import time
import requests
from flask import request, jsonify, Response, stream_with_context
from . import ai_bp, ai_session
from .config import OLLAMA_BASE, DEBUG_PRINT_GATEWAY
from .utils import (
    select_backend_and_model,
    call_nvidia_completion,
    stream_nvidia_completion,
    fix_qwen_response,
    send_sse_error,
    filter_wsgi_headers,
)
from app.agent.api.session.p_ai_router import chat_completions, chat_completions_stream


# ==================== 健康检查 ====================
@ai_bp.route('/health', methods=['GET'])
def health():
    """健康检查 - 检测 Ollama 连接状态"""
    try:
        test_start = time.time()
        response = ai_session.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        test_time = time.time() - test_start

        return jsonify({
            "status": "healthy",
            "ollama": {
                "connected": True,
                "status_code": response.status_code,
                "response_time": f"{test_time:.3f}s"
            },
            "proxy": {
                "fix_qwen_response": True,
                "filter_wsgi_headers": True,
                "trust_env": ai_session.trust_env
            },
            "timestamp": time.time()
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": time.time()
        }), 503


# ==================== Ollama 代理 ====================
@ai_bp.route('/ollama/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def ollama_proxy(path):
    """代理所有 Ollama API 请求"""
    start_time = time.time()
    target_url = f"{OLLAMA_BASE}/{path}"

    try:
        # 准备请求头
        headers = {}
        for key, value in request.headers:
            key_lower = key.lower()
            if key_lower not in ['host', 'content-length', 'connection']:
                headers[key] = value

        data = request.get_data()
        timeout = 300 if request.method == 'POST' else 30

        resp = ai_session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=data,
            params=request.args,
            timeout=timeout
        )

        process_time = time.time() - start_time

        # 获取原始 headers 并过滤
        raw_headers = dict(resp.headers)
        filtered_headers = filter_wsgi_headers(raw_headers)

        if 'content-length' not in filtered_headers and resp.content:
            filtered_headers['Content-Length'] = str(len(resp.content))

        content_type = raw_headers.get('content-type', '')

        # 如果是聊天响应，修复 Qwen 格式
        is_chat_endpoint = ('v1/chat/completions' in path or 'api/chat' in path)
        is_json_response = 'application/json' in content_type

        if is_chat_endpoint and resp.status_code == 200 and is_json_response:
            try:
                response_data = resp.json()
                fixed_data = fix_qwen_response(response_data)
                if response_data != fixed_data and DEBUG_PRINT_GATEWAY:
                    print(f"已修复 Qwen 响应格式")
                return Response(
                    json.dumps(fixed_data, ensure_ascii=False),
                    status=resp.status_code,
                    headers=filtered_headers,
                    content_type='application/json; charset=utf-8'
                )
            except json.JSONDecodeError:
                return Response(
                    resp.content,
                    status=resp.status_code,
                    headers=filtered_headers
                )
            except Exception as e:
                if DEBUG_PRINT_GATEWAY:
                    print(f"修复响应时出错: {e}")
                return Response(
                    resp.content,
                    status=resp.status_code,
                    headers=filtered_headers
                )
        else:
            return Response(
                resp.content,
                status=resp.status_code,
                headers=filtered_headers
            )

    except requests.exceptions.Timeout:
        error_time = time.time() - start_time
        return jsonify({
            "error": "Request timeout",
            "message": f"Request took too long ({error_time:.2f}s)",
            "target_url": target_url
        }), 504

    except requests.exceptions.ConnectionError:
        error_time = time.time() - start_time
        return jsonify({
            "error": "Connection failed",
            "message": f"Cannot connect to Ollama at {OLLAMA_BASE}",
            "suggestion": "Make sure Ollama is running: ollama serve",
            "target_url": target_url
        }), 503

    except Exception as e:
        error_time = time.time() - start_time
        if DEBUG_PRINT_GATEWAY:
            import traceback
            traceback.print_exc()
        return jsonify({
            "error": "Proxy error",
            "message": str(e),
            "type": type(e).__name__,
            "processing_time": f"{error_time:.2f}s",
            "target_url": target_url
        }), 500


# ==================== Chat Completions Gateway ====================
@ai_bp.route('/chat/completions', methods=['POST'])
def chat_completions_gateway():
    """
    OpenAI-compatible Chat Completions Gateway
    支持流式和非流式
    """
    if DEBUG_PRINT_GATEWAY:
        print("AI request received")

    raw_data = request.get_data(as_text=True)
    if DEBUG_PRINT_GATEWAY:
        print("raw_data:", raw_data)

    try:
        payload = request.get_json(force=True)
        if not payload:
            return jsonify({"error": {"message": "Empty request body"}}), 400

        # ==================== 打印调试信息 ====================
        if DEBUG_PRINT_GATEWAY:
            print("\n" + "=" * 80)
            print("[收到请求]")
            print("=" * 80)

            messages = payload.get("messages", [])
            print(f"[消息数量]: {len(messages)}")
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    print(f"[消息{i+1}] role={role}, tool_calls={json.dumps(tool_calls, ensure_ascii=False)[:500]}")
                elif content:
                    content_preview = content[:500] + "..." if len(content) > 500 else content
                    print(f"[消息{i+1}] role={role}, content={content_preview}")
                else:
                    print(f"[消息{i+1}] role={role}")

            tools = payload.get("tools", [])
            if tools:
                tool_names = [t.get("function", {}).get("name", "unknown") for t in tools]
                print(f"[工具定义] 数量={len(tools)}, 工具列表={tool_names}")
            else:
                print("[工具定义] 无")

            print(f"[stream]={payload.get('stream', False)}")
            print(f"[model]={payload.get('model', 'unknown')}")

        # ==================== 调用处理 ====================
        stream = payload.get("stream", False)

        model = payload.get("model")
        messages = payload.get("messages", [])
        max_tokens = payload.get("max_tokens", 4096)
        tools = payload.get("tools")
        tool_choice = payload.get("tool_choice", "auto")
        temperature = payload.get("temperature", 0.7)
        top_p = payload.get("top_p", 0.95)
        stop = payload.get("stop")
        presence_penalty = payload.get("presence_penalty", 0.0)
        frequency_penalty = payload.get("frequency_penalty", 0.0)
        thinking_enabled = payload.get("thinking_enabled", False)
        reasoning_effort = payload.get("reasoning_effort")
        extra_params = payload.get("extra_params")

        if stream:
            # ====================== 流式返回 ======================
            def generate_stream():
                try:
                    for chunk in chat_completions_stream(
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
                        extra_params=extra_params
                    ):
                        try:
                            yield chunk
                            if chunk == "data: [DONE]\n\n":
                                return
                        except (GeneratorExit, BrokenPipeError, ConnectionAbortedError):
                            return
                except (GeneratorExit, BrokenPipeError, ConnectionAbortedError):
                    return
                except Exception as e:
                    error_msg = str(e)
                    if "10053" not in error_msg and "ConnectionAborted" not in error_msg:
                        print(f"[Gateway] 错误: {error_msg[:100]}")
                    try:
                        yield "data: [DONE]\n\n"
                    except:
                        pass

            return Response(generate_stream(), mimetype='text/event-stream')

        else:
            # ====================== 非流式返回 ======================
            if DEBUG_PRINT_GATEWAY:
                print("\n[非流式请求]")

            resp = chat_completions(
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
                        extra_params=extra_params
                    )

            if DEBUG_PRINT_GATEWAY:
                print(f"[非流式响应]")
                choices = resp.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    tool_calls = message.get("tool_calls", [])
                    if content:
                        content_preview = content[:500] + "..." if len(content) > 500 else content
                        print(f"[模型回复内容]: {content_preview}")
                    if tool_calls:
                        print(f"[模型工具调用]: {json.dumps(tool_calls, ensure_ascii=False)[:500]}")
                print("=" * 80 + "\n")

            return jsonify(resp)

    except Exception as e:
        if DEBUG_PRINT_GATEWAY:
            import traceback
            traceback.print_exc()
            print(f"[错误] {type(e).__name__}: {e}")
        return jsonify({
            "error": {
                "message": str(e),
                "type": type(e).__name__
            }
        }), 500

# ==================== OpenClaw 代理 ====================
@ai_bp.route('/openclaw/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def proxy_openclaw(path):
    """OpenClaw API 代理 - 支持流式响应和 CORS"""
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response

    target_url = f'http://127.0.0.1:18790/{path}'

    headers = {}
    for key, value in request.headers:
        key_lower = key.lower()
        if key_lower not in ['host', 'content-length']:
            headers[key] = value

    if 'Authorization' not in headers and request.headers.get('Authorization'):
        headers['Authorization'] = request.headers.get('Authorization')

    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            stream=True,
            timeout=60
        )

        response = Response(
            stream_with_context(resp.iter_content(chunk_size=1024)),
            status=resp.status_code,
            content_type=resp.headers.get('Content-Type', 'application/json')
        )
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except requests.exceptions.ConnectionError:
        return {"error": "无法连接到 OpenClaw 服务"}, 503
    except Exception as e:
        print(f"OpenClaw 代理错误: {e}")
        return {"error": str(e)}, 500