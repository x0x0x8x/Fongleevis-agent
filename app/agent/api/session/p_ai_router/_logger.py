"""
请求日志类、消息提取、持久化日志相关
【模块文件名：_logger.py】
控制台输出轻量化，磁盘jsonl保留完整信息
支持 log_level="none" 彻底关闭日志，最低运行开销
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import re

from ._config import GATEWAY_CONFIG, log

def extract_message_content(message: Dict[str, Any], max_len: int = 300) -> str:
    if max_len == 0:
        max_len = 99999
    content = message.get("content", "")

    tool_calls = message.get("tool_calls", [])
    if tool_calls:
        tools = []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "unknown")
            args = func.get("arguments", "{}")
            try:
                args_dict = json.loads(args)
                if "path" in args_dict:
                    path = args_dict["path"]
                    filename = path.split("/")[-1] if "/" in path else path
                    tools.append(f"{name}({filename})")
                elif "command" in args_dict:
                    cmd = args_dict["command"][:40]
                    tools.append(f"{name}({cmd}...)")
                else:
                    tools.append(name)
            except Exception:
                tools.append(name)
        return f"🔧 {', '.join(tools)}"

    if content is None or content == "" or content == "None":
        return "(无文本)"

    if isinstance(content, str):
        if len(content) > max_len:
            return content[:max_len] + "..."
        return content

    if isinstance(content, list):
        result_parts = []
        for item in content:
            if not isinstance(item, dict):
                result_parts.append(f"[未知类型: {item}]")
                continue
            item_type = item.get("type", "unknown")
            if item_type == "text":
                text = item.get("text", "")
                if text:
                    if "```json" in text:
                        match = re.search(r'```\s*\n\n(?:\[[^\]]+\]\s*)?(.+?)$', text, re.DOTALL)  # noqa: regexp
                        if match:
                            actual_msg = match.group(1).strip()
                            if actual_msg:
                                text = actual_msg
                        else:
                            match = re.search(r']\s*(.+?)$', text)
                            if match:
                                actual_msg = match.group(1).strip()
                                if actual_msg:
                                    text = actual_msg
                    if len(text) > max_len:
                        text = text[:max_len] + "..."
                    result_parts.append(text)
            elif item_type == "image_url":
                result_parts.append("[📷 图片]")
            elif item_type == "file":
                filename = item.get("file_name", item.get("name", "未知文件"))
                result_parts.append(f"[📎 文件: {filename}]")
            elif item_type == "tool_result":
                result_content = item.get("content", "")[:100]
                result_parts.append(f"[🔧 工具结果: {result_content}...]")
            else:
                result_parts.append(f"[{item_type}]")
        if result_parts:
            return " ".join(result_parts)
        else:
            return f"[多模态内容 {len(content)}项]"

    return str(content)[:max_len]
def extract_delta_text(delta: Dict[str, Any]) -> str:
    if not delta:
        return ""
    content = delta.get("content", "")
    if content:
        return content
    reasoning_content = delta.get("reasoning_content", "")
    if reasoning_content:
        return reasoning_content
    reasoning = delta.get("reasoning", "")
    if reasoning:
        return reasoning
    return ""

class DebugRequestLogger:
    def __init__(self, persist_to_disk: bool, persist_path: str):
        self._request_counter = 0
        self._lock = threading.Lock()
        self._internal_conversion = False
        self._stream_states: Dict[int, Dict[str, Any]] = {}

        self._persist_to_disk = persist_to_disk
        self._persist_path = Path(persist_path)
        self._current_log_file: Optional[Path] = None
        self._file_lock = threading.Lock()

        if self._persist_to_disk:
            self._setup_persistence()

    def _setup_persistence(self):
        try:
            self._persist_path.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            session_id = datetime.now().strftime("%H%M%S_%f")[:-3]
            self._current_log_file = self._persist_path / f"debug_log_{date_str}_{session_id}.jsonl"
            self._write_to_file({
                "type": "session_start",
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id
            })
            log(f"[DEBUG] 日志持久化已启用，保存路径: {self._current_log_file}", "INFO")
        except Exception as e:
            log(f"[WARNING] 无法创建日志目录: {e}", "WARNING")
            self._persist_to_disk = False

    def _write_to_file(self, data: Dict[str, Any]):
        if not self._persist_to_disk or not self._current_log_file:
            return
        with self._file_lock:
            try:
                with open(self._current_log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(data, ensure_ascii=False) + '\n')
            except Exception as e:
                log(f"[WARNING] 写入日志文件失败: {e}", "WARNING")

    def _should_log(self) -> bool:
        log_level = GATEWAY_CONFIG.get("log_level", "full")
        # 最高优先级：none 直接关闭所有日志
        if log_level == "none":
            return False
        return not self._internal_conversion

    def set_internal_conversion(self, is_conversion: bool):
        self._internal_conversion = is_conversion

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def log_request(self, request_body: Dict[str, Any]) -> Optional[int]:
        if not self._should_log():
            return None
        with self._lock:
            self._request_counter += 1
            req_id = self._request_counter
        log_level = GATEWAY_CONFIG.get("log_level", "full")
        timestamp = self._timestamp()
        model = request_body.get("model", "unknown")
        messages = request_body.get("messages", [])
        stream = request_body.get("stream", False)
        thinking_enabled = request_body.get("thinking_enabled")
        tools = request_body.get("tools")  # 获取工具列表

        self._stream_states[req_id] = {
            "start_time": self._now(),
            "first_chunk_time": None,
            "chunk_count": 0,
            "raw_bytes": 0,
            "done_received": False,
            "user_output_started": False,
            "finish_reason": None,
            "accumulated_content": "",  # 新增：累积内容
            "accumulated_tool_calls": [],  # 新增：累积工具调用
        }

        # 持久化数据
        persist_data: Dict[str, Any] = {
            "type": "request",
            "req_id": req_id,
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "stream": stream,
            "thinking_enabled": thinking_enabled,
            "messages": messages,  # 完整消息
            "tools": tools,  # 完整工具列表
        }

        # 控制台输出：显示工具列表
        thinking_str = f" think:{thinking_enabled}" if thinking_enabled is not None else ""
        user_preview = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_preview = extract_message_content(msg, 120)
                break

        # 🔥 新增：打印工具列表
        tools_str = ""
        if tools:
            tool_names = [t.get("function", {}).get("name", "unknown") for t in tools]
            tools_str = f" tools:[{', '.join(tool_names)}]"

        if log_level == "compact":
            print(f"[{timestamp}] ▶ #{req_id} {model} stream={stream}{thinking_str}{tools_str} | Q:{user_preview}")
        else:
            print(f"[{timestamp}] ▶ #{req_id} model={model} stream={stream}{thinking_str}{tools_str}")
            if user_preview:
                print(f"  Query: {user_preview}")

        persist_data["user_message_preview"] = user_preview
        self._write_to_file(persist_data)
        return req_id

    def log_stream_open(self, req_id: int):
        if not self._should_log():
            return
        timestamp = self._timestamp()
        print(f"[{timestamp}] ⇄ STREAM OPEN #{req_id}")
        persist_data: Dict[str, Any] = {
            "type": "stream_open",
            "req_id": req_id,
            "timestamp": datetime.now().isoformat()
        }
        self._write_to_file(persist_data)

    def log_first_token(self, req_id: int):
        if not self._should_log():
            return
        state = self._stream_states.get(req_id)
        if not state or state["first_chunk_time"] is not None:
            return
        now = self._now()
        state["first_chunk_time"] = now
        ttfb = now - state["start_time"]
        timestamp = self._timestamp()
        print(f"[{timestamp}] ⇄ #{req_id} FIRST TOKEN ttfb={ttfb:.2f}s")
        persist_data: Dict[str, Any] = {
            "type": "first_token",
            "req_id": req_id,
            "ttfb": ttfb,
            "timestamp": datetime.now().isoformat()
        }
        self._write_to_file(persist_data)

    def log_user_stream_commit(self, req_id: int):
        if not self._should_log():
            return
        state = self._stream_states.get(req_id)
        if not state or state["user_output_started"]:
            return
        state["user_output_started"] = True
        timestamp = self._timestamp()
        print(f"[{timestamp}] ⇄ #{req_id} USER OUTPUT START")
        persist_data: Dict[str, Any] = {
            "type": "user_commit",
            "req_id": req_id,
            "timestamp": datetime.now().isoformat()
        }
        self._write_to_file(persist_data)

    def log_raw_stream_line(self, req_id: int, line: str):
        if not self._should_log():
            return
        log_level = GATEWAY_CONFIG.get("log_level", "full")
        state = self._stream_states.get(req_id)
        if state:
            state["raw_bytes"] += len(line.encode("utf-8"))
        if log_level != "full":
            return
        print(f"  [RAW] {line[:200]}")

    def log_stream_chunk(self, req_id: int, chunk_num: int, chunk_data: Dict[str, Any]):
        if not self._should_log():
            return
        try:
            # 🔥 累积内容（仅用于持久化，不打印每个chunk）
            state = self._stream_states.get(req_id)
            if state:
                delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    state["accumulated_content"] += content

                # 累积 tool_calls
                tool_calls_delta = delta.get("tool_calls", [])
                if tool_calls_delta:
                    # 按 index 合并
                    for tc in tool_calls_delta:
                        idx = tc.get("index", 0)
                        # 确保列表足够长
                        while len(state["accumulated_tool_calls"]) <= idx:
                            state["accumulated_tool_calls"].append({
                                "id": "",
                                "function": {"name": "", "arguments": ""}
                            })
                        existing = state["accumulated_tool_calls"][idx]
                        if tc.get("id"):
                            existing["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            existing["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            existing["function"]["arguments"] += func["arguments"]

            # 🔥 不再打印每个 chunk 内容，仅打印状态
            log_level = GATEWAY_CONFIG.get("log_level", "full")
            if log_level == "full":
                choices = chunk_data.get("choices", [])
                finish = choices[0].get("finish_reason") if choices else None
                if finish:
                    print(f"  [CHUNK #{chunk_num}] finish={finish}")
                # 其他情况不打印，减少日志量

            # 持久化可选（默认关闭）
            if GATEWAY_CONFIG.get("persist_chunks", False):
                persist_data: Dict[str, Any] = {
                    "type": "chunk",
                    "req_id": req_id,
                    "chunk_num": chunk_num,
                    "data": chunk_data,
                    "timestamp": datetime.now().isoformat()
                }
                self._write_to_file(persist_data)
        except Exception as e:
            log(f"[LOGGER ERROR] log_stream_chunk: {e}", "ERROR")

    def log_stream_complete(self, req_id: int, chunk_count: int, total_content: str = "",
                            tool_calls: Optional[List[Dict[str, Any]]] = None,
                            finish_reason: Optional[str] = None, duration: Optional[float] = None,
                            done_received: bool = False):
        if not self._should_log():
            return
        try:
            log_level = GATEWAY_CONFIG.get("log_level", "full")
            duration_str = f"{duration:.2f}s" if duration else "?"

            # 🔥 使用 state 中累积的内容（优先使用传入的，fallback 到 state）
            state = self._stream_states.get(req_id)
            if state:
                final_content = total_content or state.get("accumulated_content", "")
                final_tool_calls = tool_calls or state.get("accumulated_tool_calls", [])
            else:
                final_content = total_content
                final_tool_calls = tool_calls or []

            tool_count = len(final_tool_calls) if final_tool_calls else 0

            # 控制台输出（只显示摘要）
            content_preview = final_content[:150] + ("..." if len(final_content) > 150 else "")
            print(
                f"[{self._timestamp()}] ✔ STREAM END #{req_id} chunks={chunk_count} reason={finish_reason} dur={duration_str} tools={tool_count}")
            if log_level == "full":
                if content_preview:
                    print(f"  Preview: {content_preview}")
                # 🔥 打印工具调用摘要
                if final_tool_calls:
                    tool_summary = []
                    for tc in final_tool_calls:
                        name = tc.get("function", {}).get("name", "unknown")
                        tool_summary.append(name)
                    print(f"  Tools: {', '.join(tool_summary)}")

            # 🔥 持久化：完整内容
            persist_data: Dict[str, Any] = {
                "type": "stream_complete",
                "req_id": req_id,
                "chunk_count": chunk_count,
                "finish_reason": finish_reason,
                "duration": duration,
                "done_received": done_received,
                "total_content_length": len(final_content),
                "tool_calls_count": tool_count,
                "full_content": final_content,  # 完整文本（无截断）
                "full_tool_calls": final_tool_calls,  # 完整工具调用列表
                "timestamp": datetime.now().isoformat()
            }
            self._write_to_file(persist_data)
            self._stream_states.pop(req_id, None)
        except Exception as e:
            log(f"[LOGGER ERROR] log_stream_complete: {e}", "ERROR")

    def log_stream_abort(self, req_id: int, error: Exception):
        if not self._should_log():
            return
        state = self._stream_states.get(req_id)
        if not state:
            return
        duration = self._now() - state["start_time"]
        ts = self._timestamp()
        print(f"[{ts}] ✖ STREAM ABORT #{req_id} err={type(error).__name__} dur={duration:.2f}s")
        persist_data: Dict[str, Any] = {
            "type": "stream_abort",
            "req_id": req_id,
            "error_type": type(error).__name__,
            "error_detail": str(error),
            "duration": duration,
            "chunks": state['chunk_count'],
            "timestamp": datetime.now().isoformat()
        }
        self._write_to_file(persist_data)
        self._stream_states.pop(req_id, None)

    def log_response(self, req_id: int, response_data: Any, error: Optional[Exception] = None,
                     duration: Optional[float] = None):
        if not self._should_log():
            return
        timestamp = self._timestamp()
        duration_str = f"{duration:.2f}s" if duration else "?"
        persist_data: Dict[str, Any] = {
            "type": "response",
            "req_id": req_id,
            "timestamp": datetime.now().isoformat(),
            "duration": duration
        }
        if error:
            print(f"[{timestamp}] ◀ #{req_id} ❌ {type(error).__name__}: {str(error)[:120]} | dur={duration_str}")
            persist_data["error"] = {
                "type": type(error).__name__,
                "message": str(error)
            }
            self._write_to_file(persist_data)
            return

        if not isinstance(response_data, dict):
            return

        # ✅ 持久化完整响应体（包含完整 message 和 tool_calls）
        persist_data["response"] = response_data  # 原样存储全部内容

        # 控制台摘要（截断）
        choices = response_data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            finish_reason = choices[0].get("finish_reason", "unknown")
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            usage = response_data.get("usage", {})
            total_tok = usage.get("total_tokens", 0)

            preview = ""
            if content:
                preview = content[:100].replace("\n", " ")
            elif tool_calls:
                tool_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                preview = f"tools:{','.join(tool_names)}"

            print(f"[{timestamp}] ◀ #{req_id} finish={finish_reason} tok={total_tok} dur={duration_str} | {preview}")

            # 可额外保存一些摘要字段（但 response 已包含所有）
            persist_data["finish_reason"] = finish_reason
            persist_data["usage"] = usage

        self._write_to_file(persist_data)

    def close(self):
        if self._persist_to_disk and self._current_log_file:
            persist_data: Dict[str, Any] = {
                "type": "session_end",
                "timestamp": datetime.now().isoformat(),
                "total_requests": self._request_counter
            }
            self._write_to_file(persist_data)
            log(f"[DEBUG] 日志持久化完成，保存至: {self._current_log_file}", "INFO")

PERSIST_LOG = GATEWAY_CONFIG.get("persist_log", True)
LOG_PATH = "./debug_logs"
request_logger = DebugRequestLogger(persist_to_disk=PERSIST_LOG, persist_path=LOG_PATH)

def log_request_to_file(
    url: str,
    headers: Dict[str, Any],
    body: Dict[str, Any],
    log_file: str = "request_history.log"
) -> None:
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"\n{'=' * 60}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"URL: {url}\n")
        f.write(f"Headers:\n{json.dumps(headers, indent=2, ensure_ascii=False)}\n")
        f.write(f"Body:\n{json.dumps(body, indent=2, ensure_ascii=False)}\n")
        f.write(f"{'=' * 60}\n")