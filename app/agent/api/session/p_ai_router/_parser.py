"""
_parser.py
响应解析工具、ResponseParser、内容提取函数
作用：抹平各家LLM服务商返回字段差异，统一归一化输出结构
⚠️ 重要警告：ResponseParser 实例持有流式工具缓冲区，【禁止多请求并发共用实例】
每条流式会话独立创建实例，或者处理完毕调用 .reset()
"""
import json
from typing import Dict, Any, List, Optional

# 常量定义，统一字段名称
FIELD_REASONING_1 = "reasoning_content"
FIELD_REASONING_2 = "reasoning"
FIELD_CONTENT = "content"
FIELD_TOOL_CALLS = "tool_calls"

def extract_llm_content(resp: Dict[str, Any]) -> str:
    """快速从完整响应提取assistant主文本内容"""
    try:
        choices = resp.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get(FIELD_CONTENT, "")
            return content.strip() if content else ""
    except Exception:
        pass
    return ""
def is_error_response(resp: Dict[str, Any]) -> bool:
    """判断响应是否属于错误响应"""
    if "error" in resp:
        return True
    content = extract_llm_content(resp)
    if content.startswith("服务暂时不可用"):
        return True
    return False
class ResponseParser:
    def __init__(self):
        # 流式工具调用分片缓冲区，有状态！多请求不可共用
        self._tool_buffer: Dict[int, Dict[str, str]] = {}

    def parse(self, data: Dict[str, Any], is_stream: bool = False) -> Dict[str, Any]:
        """
        统一入口：解析原始上游返回报文
        :param data: 上游原始json（完整response / SSE chunk）
        :param is_stream: 是否为流式增量分片
        :return: 标准化结构
        {
            "reasoning_text": str,
            "content_text": str,
            "tool": Optional[Dict],
            "finish_reason": Optional[str]
        }
        """
        try:
            choices = data.get("choices", [])
            if not choices:
                return self._empty_result()
            finish_reason = choices[0].get("finish_reason")
            if is_stream:
                return self._parse_stream_chunk(choices[0], finish_reason)
            return self._parse_nonstream_message(choices[0], finish_reason)
        except Exception as e:
            return {
                "reasoning_text": "",
                "content_text": f"[解析错误: {e}]",
                "tool": None,
                "finish_reason": None
            }

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {
            "reasoning_text": "",
            "content_text": "",
            "tool": None,
            "finish_reason": None
        }

    @staticmethod
    def _parse_nonstream_message(choice: Dict[str, Any], finish_reason: Optional[str]) -> Dict[str, Any]:
        message = choice.get("message", {})
        tool_calls = message.get(FIELD_TOOL_CALLS, [])
        if tool_calls:
            tc = tool_calls[0]
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {"_raw": args_str}
            return {
                "reasoning_text": "",
                "content_text": "",
                "tool": {
                    "name": name,
                    "args": args,
                    "id": tc.get("id", "")
                },
                "finish_reason": finish_reason
            }

        reasoning = message.get(FIELD_REASONING_1) or message.get(FIELD_REASONING_2) or ""
        content = message.get(FIELD_CONTENT, "")
        return {
            "reasoning_text": reasoning,
            "content_text": content,
            "tool": None,
            "finish_reason": finish_reason
        }

    def _parse_stream_chunk(self, choice: Dict[str, Any], finish_reason: Optional[str]) -> Dict[str, Any]:
        delta = choice.get("delta", {})
        tool_calls = delta.get(FIELD_TOOL_CALLS, [])
        if tool_calls:
            return self._handle_tool_stream(tool_calls, finish_reason)

        reasoning = delta.get(FIELD_REASONING_1) or delta.get(FIELD_REASONING_2)
        if reasoning:
            return {
                "reasoning_text": reasoning,
                "content_text": "",
                "tool": None,
                "finish_reason": finish_reason
            }

        content = delta.get(FIELD_CONTENT, "")
        if content:
            return {
                "reasoning_text": "",
                "content_text": content,
                "tool": None,
                "finish_reason": finish_reason
            }

        return self._empty_result()

    def _handle_tool_stream(self, tool_calls: List[Dict[str, Any]], finish_reason: Optional[str]) -> Dict[str, Any]:
        # 拼接流式分片传输的tool call
        for tc in tool_calls:
            idx = tc.get("index", 0)
            if idx not in self._tool_buffer:
                self._tool_buffer[idx] = {"id": "", "name": "", "args": ""}
            if "id" in tc:
                self._tool_buffer[idx]["id"] = tc["id"]
            func = tc.get("function", {})
            if "name" in func:
                self._tool_buffer[idx]["name"] = func["name"]
            if "arguments" in func:
                self._tool_buffer[idx]["args"] += func["arguments"]

        # 收到结束标志，组装完整工具调用
        if finish_reason == "tool_calls" and self._tool_buffer:
            data = list(self._tool_buffer.values())[0]
            try:
                args = json.loads(data["args"]) if data["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": data["args"]}
            self._tool_buffer.clear()
            return {
                "reasoning_text": "",
                "content_text": "",
                "tool": {"name": data["name"], "args": args, "id": data["id"]},
                "finish_reason": finish_reason
            }
        # 工具分片传输中，暂不对外输出完整tool
        return self._empty_result()

    def reset(self):
        """清空缓冲区，复用实例前必须调用"""
        self._tool_buffer.clear()