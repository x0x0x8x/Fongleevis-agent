# app/ai_service/utils.py
import json
import re
import time
import requests
from flask import Response, stream_with_context
from .config import (
    MODEL_ROUTING_TABLE, 
    DEFAULT_MODEL, 
    NVIDIA_API_KEY, 
    DEBUG_PRINT_GATEWAY
)

# ==================== 全局累积变量（用于跨chunk） ====================
_accumulated_content = ""


# ==================== 模型选择 ====================
def select_backend_and_model(*, requested_model):
    model_name = requested_model or DEFAULT_MODEL
    if model_name not in MODEL_ROUTING_TABLE:
        raise ValueError(f"Model '{model_name}' not allowed")
    backend, real_model, task_type = MODEL_ROUTING_TABLE[model_name]
    return backend, real_model, task_type


# ==================== WSGI 头过滤 ====================
def filter_wsgi_headers(headers_dict):
    """过滤WSGI不允许的headers"""
    forbidden = {
        'transfer-encoding',
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailer',
        'upgrade'
    }
    filtered = {}
    for key, value in headers_dict.items():
        key_lower = key.lower()
        if key_lower not in forbidden:
            filtered[key] = value
    return filtered


# ==================== 格式转换函数 ====================
def fix_qwen_response(data):
    """修复Qwen模型的响应格式（OpenAI格式）"""
    if not isinstance(data, dict):
        return data

    if 'choices' in data and isinstance(data['choices'], list):
        for choice in data['choices']:
            if isinstance(choice, dict) and 'message' in choice:
                message = choice['message']
                if isinstance(message, dict):
                    if 'reasoning' in message and (not message.get('content') or message['content'] == ''):
                        message['content'] = message['reasoning']
    elif 'message' in data and isinstance(data['message'], dict):
        message = data['message']
        if 'reasoning' in message and (not message.get('content') or message['content'] == ''):
            message['content'] = message['reasoning']
    return data


def convert_nonstream_minimax_to_openai(response_json):
    """转换非流式响应中的工具调用格式"""
    if 'choices' in response_json and len(response_json['choices']) > 0:
        message = response_json['choices'][0].get('message', {})
        content = message.get('content', '')
        if content:
            tool_call_pattern = r'<minimax:tool_call>\s*<invoke name="([^"]+)">\s*<parameter name="([^"]+)">([^<]+)</parameter>\s*</invoke>\s*</minimax:tool_call>'
            match = re.search(tool_call_pattern, content, re.DOTALL)
            if match:
                tool_name = match.group(1)
                param_name = match.group(2)
                param_value = match.group(3).strip()
                message['content'] = None
                message['tool_calls'] = [{
                    'id': f'call_{hash(tool_name + param_value)}',
                    'type': 'function',
                    'function': {
                        'name': tool_name,
                        'arguments': json.dumps({param_name: param_value})
                    }
                }]
    return response_json


def convert_minimax_to_openai(sse_line: str) -> str:
    """将 MiniMax 的 XML 格式工具调用转换为 OpenAI 标准 JSON 格式"""
    global _accumulated_content

    if sse_line.startswith('data: '):
        data_content = sse_line[6:]
    else:
        data_content = sse_line

    if data_content == '[DONE]':
        _accumulated_content = ""
        return 'data: [DONE]\n\n'

    try:
        chunk = json.loads(data_content)
        if 'choices' in chunk and len(chunk['choices']) > 0:
            delta = chunk['choices'][0].get('delta', {})
            content = delta.get('content', '')
            if content:
                _accumulated_content += content
                tool_call_pattern = r'<minimax:tool_call>\s*<invoke name="([^"]+)">\s*<parameter name="([^"]+)">([^<]+)</parameter>\s*</invoke>\s*</minimax:tool_call>'
                match = re.search(tool_call_pattern, _accumulated_content, re.DOTALL)
                if match:
                    tool_name = match.group(1)
                    param_name = match.group(2)
                    param_value = match.group(3).strip()
                    delta['content'] = None
                    delta['tool_calls'] = [{
                        'id': f'call_{abs(hash(tool_name + param_value))}',
                        'type': 'function',
                        'function': {
                            'name': tool_name,
                            'arguments': json.dumps({param_name: param_value})
                        }
                    }]
                    _accumulated_content = ""
                    chunk['choices'][0]['delta'] = delta
                elif '<minimax:tool_call>' in _accumulated_content:
                    delta['content'] = None
                    chunk['choices'][0]['delta'] = delta
        return f"data: {json.dumps(chunk)}\n\n"
    except json.JSONDecodeError:
        return sse_line + "\n\n"
    except Exception as e:
        print(f"转换错误: {e}")
        return sse_line + "\n\n"


def send_sse_error(error_message: str, error_type: str = "api_error") -> Response:
    """发送标准SSE错误响应"""
    def generate():
        error_response = {
            "error": {
                "message": error_message,
                "type": error_type
            }
        }
        yield f"data: {json.dumps(error_response)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Content-Type': 'text/event-stream; charset=utf-8'
        }
    )


# ==================== NVIDIA 调用函数 ====================
def call_nvidia_completion(*, payload, model):
    """非流式调用 NVIDIA API"""
    import requests
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {NVIDIA_API_KEY}"
    }
    if DEBUG_PRINT_GATEWAY:
        print("AI msg: ", payload["messages"])
    body = {
        "model": model,
        "messages": payload["messages"],
        "temperature": payload.get("temperature", 0.7),
        "top_p": payload.get("top_p", 0.95),
        "max_tokens": payload.get("max_tokens", 512),
        "seed": payload.get("seed", 42),
        "stream": False
    }
    resp = requests.post(url, json=body, headers=headers, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"NVIDIA API Error {resp.status_code}: {resp.text}")
    result = resp.json()
    result = convert_nonstream_minimax_to_openai(result)
    return result


def stream_nvidia_completion(*, payload, model):
    """流式调用 NVIDIA API，并转换格式"""
    import requests
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {NVIDIA_API_KEY}"
    }
    body = {
        "model": model,
        "messages": payload["messages"],
        "temperature": payload.get("temperature", 0.7),
        "top_p": payload.get("top_p", 0.95),
        "max_tokens": payload.get("max_tokens", 2048),
        "seed": payload.get("seed", 42),
        "stream": True
    }

    try:
        resp = requests.post(url, json=body, headers=headers, stream=True, timeout=60)
        if resp.status_code != 200:
            error_text = resp.text[:500]
            return send_sse_error(f"NVIDIA API Error {resp.status_code}: {error_text}", "api_error")

        def generate():
            global _accumulated_content
            _accumulated_content = ""
            for line in resp.iter_lines():
                if line:
                    try:
                        line_str = line.decode('utf-8')
                        converted_line = convert_minimax_to_openai(line_str)
                        yield converted_line
                        if 'data: [DONE]' in line_str:
                            _accumulated_content = ""
                            break
                    except Exception as e:
                        print(f"\n[处理错误] {e}")
                        yield "data: [DONE]\n\n"
                        break

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Content-Type': 'text/event-stream; charset=utf-8'
            }
        )

    except requests.exceptions.Timeout:
        return send_sse_error("Request timeout: NVIDIA API did not respond", "timeout_error")
    except requests.exceptions.ConnectionError as e:
        return send_sse_error(f"Connection error: {str(e)}", "connection_error")
    except Exception as e:
        return send_sse_error(str(e), "request_error")