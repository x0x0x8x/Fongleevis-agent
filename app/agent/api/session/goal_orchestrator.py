"""
目标驱动型LLM递归自治任务执行框架 (人工精校v1.0)
"""
import json
import logging
import re
import time
import os
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any, TypedDict, Literal, Generator, cast
#from app.agent.api.tool.tool import get_tools_for_llm, execute_tool_sync
from app.agent.api.skill_manager import skill_manager
from app.agent.api.memory_manager_src.memory_manager import _gen_memory_id as gen_memory_id
from  .p_ai_router import chat_completions, chat_completions_stream
from app.agent.api.agent_config import ROOT_DIR, CONVERSATIONS_DIR, IS_WINDOWS, IS_LINUX, IS_MACOS,VERIFY_CNT_MAX, EXECUTOR_RETRY_CNT_MAX
import copy
#from app.agent.api.p_skill_system import SkillEntry

# Master 入口仅可用：闲聊回复 + 任务规划
MASTER_TOOLS = {
    "start_executor_simple",
    "plan_task",
}

# Worker 编排仅可用：规划/跳过/拆解/完成/启动执行器
WORKER_TOOLS = {
    "replan_task",
    "work_fail",
    "need_break_down",
    "start_executor",
}

# Executor 执行层仅可用：结果提交/失败提交/纯文本提交/读历史
EXECUTOR_TOOLS = {
    "execution_submit",
    "execution_error_submit",
}

# ── 提示词 ──
PROMPTS_USER = {
    "master_entry": (
'''
user msg是{main_task}。

根据以下执行逻辑判断操作选择对应的tool调用:
if(user msg简单，且输出内容不多, 单步即可完成全部的输出){{
调用 start_executor_simple(main_task, indicators)
}} 
else{{
调用tool plan_task(main_task, indicators, task_plan, relevant_history_ids, relevant_tools).
}}
'''
    ),
    "worker_loop": (
'''
当前主任务:{main_task}
当前任务计划:{task_plan}
当前所在子任务是:{cur_task}.

根据以下执行逻辑判断操作选择对应的tool调用:
if(出现原计划没有想到的状况导致现有规划需要调整){{
    调用 replan_task 进行重规划.
}}
else if(当前任务因某种原因已无法完成或违反安全规定){{
    调用 work_fail
}}
else if(当前有任务等待执行){{
    调用 start_executor 开启任务执行流程。
}}
else{{
    调用 need_break_down.
}}
'''
    ),
    "worker_verify": (
'''
你是任务验收和汇总员.
你的主要任务有2个:
1. 判断任务是否完成. 
2. 如果完成，将完整成果用合适的方式拼接到一起，可选择额外添加title或总结描述性文本是完整输出更圆滑。

当前主任务:{main_task}
验收指标:{indicators}

请调用 work_verify_submit(success:bool, reason:str, finalResp:str) 交付。
'''
#成果列表描述的内容可能仅为摘要，但默认其主体一定存在。只需要判断是否严重缺失必要成果或违反指标。
    ),
    "executor_loop": (
'''
当前任务: {cur_task}
验收指标: {indicators}

根据以下执行逻辑判断操作选择对应的tool调用:
if(当前任务遇阻,无法完成){{
调用 execution_error_submit.
}}
else if(任务已完成 or 可单步纯文本输出完成){{
调用tool execution_submit.
}}
else(需要调用功能性工具(如shell,browser等)){{
调用相应业务工具.
}}

'''
#else if(根据当前任务描述判断其一定与某历史记忆相关且该记忆在历史摘要中完全不存在){{}}  todo:深度记忆支持后开放
    ),
    "plan_loop": (
'''
你是任务规划器,你需要为实现主任务目标规划若干子任务和提供最终验收指标.
主任务是:{main_task}
- 约束：
a.{con_b}所有子任务应能直接产生主任务交付成果。
b.不许规划[优化,回顾,审查,返回确认...等性质]子任务。
c.除非主任务指定了特定标准，否则不要自行加码过多额外指标，指标需简单且具象。
d.子任务数量不宜过多。
e.不得中途提问，一切自行处理，自由发挥。
'''
    ),
    "task_plan_check": (
'''
你是任务流合理性审查员.请检查当前任务规划和验收指标是否对于主任务来讲合适，且不违反相关约束。
主任务是:{main_task}
子任务流:{task_plan}
验收指标:{indicators}
- 约束：
a.{con_b}所有子任务应能直接产生主任务交付成果。
b.不许规划[优化,回顾,审查,返回确认...等类型]子任务。
c.除非主任务指定了特定标准，否则不要自行设定过多额外指标，简化指标设定。
d.不得中途提问，一切自行处理，自由发挥。

请调用 plan_review(success:bool, reason:str) 提交审查结果。
'''
    ),
    "tool_safety_verify":(
    '''
    审查目标工具调用:{toolcallfunc}
    参数:{args}
    
    你必须调用 tool_safety_verify 提交安全审查结果。
    请严格遵循相关规则判断当前操作是否为敏感操作,以决定是否直接拒绝或等待确认。
    '''
    ),
}
PROMPTS_SYSTEM = {
    "master_entry": (
'''
{base_system_prompts}

你现在是agent master(对内身份).
你需要根据用户消息，设定一个主要任务供后续操作使用。

你必须遵守以下准则：
- 你必须调用流程分支tool，不得直接返回文本.
- 任务流规划约束:
a.规划能直接产生主任务交付成果的子任务,且单个任务在单笔输出中完成更多工作为佳。
b.每个子任务都应该相对前一个子任务能生产更多主任务交付产物,严格避免[优化,回顾,审查,返回确认...等类型]子任务。
c.除非主任务指定了特定标准，否则不要自行设定过多额外指标，简化指标设定。
d.不得中途提问，一切自行处理，自由发挥。
e.剔除历史记忆条目和功能性tool的时候，需要谨慎，只剔除绝对无关的条目。
- 摘要生成准则：
a.摘要的用途是在不查看原文基础上也能有足够的信息作为后续任务的直接依据。
b.关注核心信息的收集，而不是缩句。尤其是事件,数值,状态等。
c.摘要的目的是减少字数，但是信息丰富度更重要。如必须取舍，优先保留信息丰富度。
-toolcall执行约束：
a.你只能选择用于程序逻辑分支选取的tool执行。
b.你不能直接执行功能性tool。
c.如为了完成主任务需要借助功能性tool，请选择进入任务规划分支。
-如遇到安全审查问题，停止执行当前任务，将任务改为回复相关情况和拒绝理由。
'''
    ),
    "worker_loop": (
'''
{base_system_prompts}

你现在是agent worker(对内身份).
**你需要根据当前的任务完成情况与流程阶段，根据指定的程序逻辑对下一步操作做出合理判断**

你必须遵守以下准则：
- 你必须调用流程分支tool，不得直接返回文本.
- 任务拆解准则:
a.对子任务进一步拆解的目的是由于当前子任务无法通过单笔操作直接完成。
- 任务流重规划准则:
a.重规划的原因是当前任务执行历史出现预期以外的状况，以至于当前后续流程已经不合适。
b.重规划尽量不浪费已完成内容的前提下规划新的任务流，目的是进一步逼近主任务目标。
c.原则上每个子任务都应该相对前一个子任务能生产更多主任务交付物，从而更加逼近最终目标。
-toolcall执行约束：
a.你只能选择用于程序逻辑分支选取的tool执行。
b.你不能直接执行功能性tool。
c.如为了完成主任务需要借助功能性tool，请选择调用start_executor进入任务执行流程。
- 最终回复约束：
a.使用与主任务描述相同的语种。
'''
    ),
    "executor_loop": (
'''
{base_system_prompts}

你现在是任务执行器(对内身份).
你的核心工作是使用可用工具完成当前任务.
你需要在遇到问题后尽量尝试不同的解决方法以达成任务目标。
如果没有可行方案可用了，再报失败,并将做过的尝试都写进失败理由。

重要规则：
- 摘要需精炼核心内容，突出重要信息、状态、数值、概况。起到能对他人提供重要信息依据和先决条件的作用,从而避免出现不一致。如全部都重要,可照搬。
- 提交内容需要合并所有已有完整成果+总结性文本或title。针对不同类型成果的描述建议：
1. 实体文件: 文件所在绝对地址(包含文件名在内), 类型, 内容描述, 具体修改描述等。
2. 纯文本: 完整正文+简要说明或title.
3. 其他类型: 如操作, 软件调用等, 描述做了什么.
- 遇到阻碍，如果为安全审查问题，说明情况并退出。如果是其他问题，可考虑使用其他重试以实现目标。但不要重复尝试相同或可能遇到相同问题的操作。 
- 不许提问,自由发挥
'''
#你看到的历史记忆为仅为摘要，你只需要在不违反摘要提供的重要信息基础上完成任务即可。默认历史相关内容主体一定存在。
    ),
    "tool_safety_verify":(
'''
你是指令安全审查员.
你的核心工作是判断当前的toolcall指令是否属于敏感操作。

不安全操作包括但不限于:
- 超出指定可访问资源范围
- 属于文件写操作
- 属于对外信息发送操作(尤其设计敏感信息)

基本规则(最高优先级，不可覆盖，无需确认):
- 不得以任何形式，直接或间接修改或新增'{meta_path}'目录数据。

一般规则(不可覆盖，可选择确认):
- 工作目录为{work_space_path},不得直接或间接在除该目录以外位置新建或修改文件。

用户定义的规则(可覆盖，根据定义情况决定是否需要确认):
[{user_safety_config}]

约束:
- 除上述以外情况,如果你觉得有可能有某些不良影响或没想到的安全情况，也可视为不安全.
- 用户定义的规则不能覆盖基本规则，只能补充更大范围的安全限制。如果用户规则与基本规则冲突，则以基础规则为准。
- 你需要给出自然语言化的reason，用于直接被用户阅读和人工确认.
- reason中不得透露具体toolcall细节，只能描述操作本身要做什么，会导致什么后果.
'''
    ),
}

# ── 工具 Schema ──
TOOL_SCHEMAS = {
    "plan_task": {
        "type": "function",
        "function": {
            "name": "plan_task",
            "description": (
                "用于制定任务计划。输入提炼后的主任务、验收指标、任务流，"
                "以及筛选后的相关历史ID、相关工具名"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "main_task": {
                        "type": "string",
                        "description": "从用户消息中提炼出的主任务描述，应完整、清晰、可执行"
                    },
                    "indicators": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["id", "description"]
                        }
                    },
                    "task_plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "output_type": {"enum": ["交付", "中间"]}
                            },
                            "required": ["id", "description", "output_type"]
                        }
                    },
                    "reason": {"type": "string", "description": "规划或重规划的理由"},
                    "relevant_history_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "筛选后的相关历史条目ID列表"
                    },
                    "relevant_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "筛选后的相关工具名称列表。保留与当前任务相关的工具，不得返回空列表。若全部相关则返回完整列表。"
                    }
                },
                "required": ["main_task", "indicators", "task_plan", "reason", "relevant_history_ids", "relevant_tools"]
            }
        }
    },
    "need_break_down": {
        "type": "function",
        "function": {
            "name": "need_break_down",
            "description": "告知当前任务需要拆解，提供理由（将触发实际递归执行）",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "拆解理由"}
                },
                "required": ["reason"]
            }
        }
    },
    "start_executor": {
        "type": "function",
        "function": {
            "name": "start_executor",
            "description": "调用此工具启动执行器,传入当前子任务专属验收指标(参考全局指标生成,不得额外加码)",
            "parameters": {
                "type": "object",
                "properties": {
                    "indicators": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["id", "description"]
                        }
                    }
                },
                "required": ["indicators"]
            }
        }
    },
    "execution_error_submit": {
        "type": "function",
        "function": {
            "name": "execution_error_submit",
            "description": "任务无法完成，提交失败原因并终止执行",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "任务失败的具体原因描述"
                    }
                },
                "required": ["reason"]
            }
        }
    },
    "execution_submit": {
        "type": "function",
        "function": {
            "name": "execution_submit",
            "description": "执行器提交任务执行结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "完整的任务执行结果内容的自然语言提交"
                    },
                    "summary": {
                        "type": "string",
                        "description": "摘要"
                    }
                },
                "required": ["result", "summary"]
            }
        }
    },
    "start_executor_simple": {
    "type": "function",
    "function": {
        "name": "start_executor_simple",
        "description": "简单单步任务直接启动执行器，不经过worker任务流编排",
        "parameters": {
            "type": "object",
            "properties": {
                "main_task": {
                    "type": "string",
                    "description": "从用户消息中提炼出的主任务描述，应完整、清晰、可执行"
                },
                "indicators": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"}
                        },
                        "required": ["id", "description"]
                    }
                },
            },
            "required": ["main_task", "indicators"]
        }
    }
},
    "replan_task": {
        "type": "function",
        "function": {
            "name": "replan_task",
            "description": "Worker层级重规划任务流，现有计划不适配现状时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "indicators": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["id", "description"]
                        }
                    },
                    "task_plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "output_type": {"enum": ["交付", "中间"]}
                            },
                            "required": ["id", "description", "output_type"]
                        }
                    },
                    "reason": {"type": "string", "description": "重规划理由"}
                },
                "required": ["indicators", "task_plan", "reason"]
            }
        }
    },
    "work_fail": {
        "type": "function",
        "function": {
            "name": "work_fail",
            "description": "当前层级任务无法继续推进，宣告本级任务失败",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "失败原因"}
                },
                "required": ["reason"]
            }
        }
    },
    "work_verify_submit": {
        "type": "function",
        "function": {
            "name": "work_verify_submit",
            "description": "验收和汇总提交，标记任务是否通过、给出理由与最终交付文本",
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "是否通过验收"},
                    "reason": {"type": "string", "description": "验收通过或不通过的理由"},
                    "finalResp": {"type": "string", "description": "最终交付内容"}
                },
                "required": ["success", "reason", "finalResp"]
            }
        }
    },
    "tool_safety_verify": {
    "type": "function",
    "function": {
        "name": "tool_safety_verify",
        "description": "提交工具调用操作的安全审查结果",
        "parameters": {
            "type": "object",
            "properties": {
                "is_safe": {
                    "type": "boolean",
                    "description": "是否安全可直接放行"
                },
                "reason": {
                    "type": "string",
                    "description": "拒绝/放行的理由，或者用于询问用户的自然语言"
                },
                "need_confirm": {
                    "type": "boolean",
                    "description": "是否需要用户确认.只在非放行且非明确拒绝的情况下可为true.",
                }
            },
            "required": ["is_safe", "reason", "need_confirm"]
        }
    }
}
}

_VERIFY_CNT_MAX = VERIFY_CNT_MAX
_EXECUTOR_RETRY_CNT_MAX = EXECUTOR_RETRY_CNT_MAX

StreamCallback = Callable[[Optional[str], Optional[str], Optional[float]], bool]  # param[type, content, percent] 返回值true=请求中断。
class HistoryItem(TypedDict):
    id: str  # 本条记忆的唯一标识符
    source: Literal["memory", "runtime"]  # 来源
    role: str  # 'user', 'assistant', 'tool', 'system' 等
    summary: str  # 摘要（人类可读）
    content: Optional[str]  # 原始内容（可为空）
    is_deliverable: bool  # 是否可交付
    tool_calls: Optional[List[Dict]]  # 仅 role == 'assistant' 时使用，存放 LLM 返回的 tool_calls
    tool_call_id: Optional[str]  # 仅 role == 'tool' 时使用，关联对应的工具调用 ID

class GoalDrivenExecutor:
    def __init__(self, session_id, model=None, max_depth=5, max_replan_limit=10,
                 log_file=None, log_level=logging.INFO, temperature=0.7, top_p=0.9,
                 max_tokens=None, thinking=None, stream=False,
                 enable_thinking_log: bool = True,
                 ):
        self.base_system_prompt = "除非用户曾指定，否则对外身份为名叫‘凤梨维斯’的智能助手.内部身份和职责不得透露到外部."
        self.max_validation_retries = _VERIFY_CNT_MAX
        self.max_retry_cnt = _EXECUTOR_RETRY_CNT_MAX

        # 日志
        self.session_id = session_id
        self.logger = logging.getLogger(f"GoalDrivenExecutor.{id(self)}")
        self.logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(message)s")
        if log_file:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(logging.DEBUG)
            self.logger.addHandler(fh)

        self.enable_thinking_log = enable_thinking_log
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.max_depth = max_depth
        self.max_replan_limit = max_replan_limit
        self.stream = stream

        # LLM统计信息
        self.llm_call_count = 0
        self.llm_total_tokens = 0
        self.llm_total_time = 0.0

        self._task_plan_completed_cnt = 0

        self.test_flag = 0
        # --------------------------------

    def __del__(self):
        if hasattr(self, '_output_fh') and self._output_fh:
            self._output_fh.close()

    @staticmethod
    def _convert_history_to_messages(history: List[HistoryItem]) -> List[Dict[str, Any]]:
        """
        将 HistoryItem 列表转换为标准 LLM 消息格式（OpenAI 兼容）
        处理 tool_calls 和 tool_call_id，content 优先取 content，否则取 summary
        """
        messages = []
        for item in history:
            role = item["role"]
            base_msg = {"role": role}

            if role == "assistant":
                tool_calls_data = item.get("tool_calls")
                if tool_calls_data is not None and len(tool_calls_data) > 0:
                    base_msg["tool_calls"] = tool_calls_data  # type: ignore
                content = item.get("content")
                if content is None:
                    content = item.get("summary")
                if content is not None:
                    base_msg["content"] = content
            elif role == "tool":
                tool_call_id = item.get("tool_call_id")
                if not tool_call_id:
                    continue
                base_msg["tool_call_id"] = tool_call_id
                content = item.get("content")
                if content is None:
                    content = item.get("summary")
                base_msg["content"] = content or ""
            else:
                # user, system 等常规角色
                content = item.get("content")
                if content is None:
                    content = item.get("summary")
                if content is None:
                    continue
                base_msg["content"] = content

            messages.append(base_msg)
        return messages

    def _llm_request_from_history(
        self,
        history: List[HistoryItem],
        stop_signal: dict,
        tools: Optional[List] = None,
        tool_choice: Optional[str | Dict] = "auto",
        stream: Optional[bool] = True,
        stream_callback: Optional[StreamCallback] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        基于历史记录的统一 LLM 请求入口
        返回生成器，格式固定：{"type": str, "data": Any}
        stream=None 时自动读取 self.stream
        """
        # 转换历史为标准 messages
        messages = self._convert_history_to_messages(history)

        use_stream = self.stream if stream is None else stream

        base_args = {
            "stream": use_stream,
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "tool_choice": "auto"
        }
        if tools:
            base_args["tools"] = tools
        if tool_choice:
            base_args["tool_choice"] = tool_choice

        if not use_stream:
            yield from self._llm_request_nonstream(base_args)
        else:
            yield from self._llm_request_stream(
                base_args=base_args,
                stream_callback=stream_callback,
                stop_signal=stop_signal
            )

    # ======= LLM request =====
    def _llm_request(
            self,
            messages: List[Dict],
            stop_signal: dict,
            tools: Optional[List] = None,  # json tool list
            tool_choice: Optional[str | Dict] = "auto",  # 当前一定为None
            stream: Optional[bool] = True,
            stream_callback: Optional[StreamCallback] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        统一LLM请求入口
        返回生成器，格式固定：{"type": str, "data": Any}
        stream=None 自动读取 self.stream
        """
        use_stream = self.stream if stream is None else stream
        # 确保 tool_choice 有默认值
        final_tool_choice = tool_choice if tool_choice is not None else "auto"

        if not use_stream:
            yield from self._llm_request_nonstream(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                thinking=self.thinking,
                tools=tools,
                tool_choice=final_tool_choice,
            )
        else:
            yield from self._llm_request_stream(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
                thinking=self.thinking,
                tools=tools,
                tool_choice=final_tool_choice,
                stream_callback=stream_callback,
                stop_signal=stop_signal,
            )

    # ====================== 非流式子逻辑 ======================
    def _llm_request_nonstream(
            self,
            *,
            model: str,
            messages: List[Dict],
            temperature: float,
            top_p: float,
            max_tokens: int,
            thinking: bool,
            tools: Optional[List] = None,
            tool_choice: str | Dict = "auto",
    ) -> Generator[Dict[str, Any], None, None]:
        """
        非流式LLM请求
        """
        t_start = time.time()

        # 显式传递所有参数
        resp = chat_completions(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            tools=tools,
            tool_choice=tool_choice,
            thinking_enabled=thinking,  # 参数名映射
            stream=False,  # 强制非流式
        )

        t_end = time.time()
        self._parse_resp_reasoning(resp)

        # 提取思考内容并用 info 级别打印
        reasoning = resp.get('choices', [{}])[0].get('message', {}).get('reasoning_content')
        if reasoning:
            # self.logger.info(f"非流式思考内容: {reasoning}")
            pass

        yield from self._parse_nonstream_message(resp)

        # 统计埋点
        usage = resp.get("usage", {}) if isinstance(resp, dict) else getattr(resp, 'usage', {})
        self.llm_call_count += 1
        self.llm_total_tokens += usage.get("total_tokens", 0)
        self.llm_total_time += t_end - t_start

    @staticmethod
    def _parse_nonstream_message(resp: Dict) -> Generator[Dict[str, Any], None, None]:
        """解析非流式完整返回，转换成统一chunk格式"""
        try:
            msg = resp["choices"][0]["message"]
            reasoning = msg.get("reasoning_content")
            if reasoning:
                yield {"type": "thinking", "data": reasoning}

            content = msg.get("content", "")
            if content:
                yield {"type": "content", "data": content}

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                yield {"type": "tool_calls", "data": tool_calls}
        except Exception:
            return

    # ====================== 流式子逻辑 ======================
    def _llm_request_stream(
            self,
            *,
            model: str,
            messages: List[Dict],
            temperature: float,
            top_p: float,
            max_tokens: int,
            thinking: bool,
            tools: Optional[List] = None,
            tool_choice: str | Dict = "auto",
            stream_callback: Optional[StreamCallback],
            stop_signal: dict,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        流式LLM请求
        """
        assert stop_signal is not None, "_llm_request_stream: stop_signal 为必填参数，禁止传入None"
        t_start = time.time()
        self.llm_call_count += 1

        response = chat_completions_stream(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            tools=tools,
            tool_choice=tool_choice,
            thinking_enabled=thinking,
        )

        state = {
            "total_chars": 0,
            "usage_info": None,
            "accumulated_tool_calls": {},
            "reasoning_buffer": "",
            "full_reasoning": ""
        }

        try:
            for chunk in response:
                items = self._collect_stream_chunk(chunk, state)
                for item in items:
                    yield item
                if stream_callback is not None:
                    if stream_callback("", "", None):
                        stop_signal["aborted"] = True
                        return

            # 🔥 累加统计
            if state["usage_info"]:
                total_tokens = state["usage_info"].get("total_tokens", 0)
                self.llm_total_tokens += total_tokens
                self.llm_total_time += time.time() - t_start

                # 🔥 发送 usage 事件给前端
                yield {
                    "type": "usage",
                    "data": state["usage_info"]
                }

            if state["accumulated_tool_calls"]:
                accumulated = cast(dict, state["accumulated_tool_calls"])
                tc_list = [v for _, v in sorted(accumulated.items())]
                yield {"type": "tool_calls", "data": tc_list}

        finally:
            if state["full_reasoning"]:
                pass

    def _collect_stream_chunk(self, chunk: str, state: Dict) -> List[Dict]:
        """单条网络分片 -> 拆分多条SSE data帧，输出标准化事件列表"""
        events = []
        # 一条网络块内可能存在多条完整SSE消息，按分隔符切割
        frame_blocks = chunk.split("\n\n")
        for block in frame_blocks:
            block = block.strip()
            if not block.startswith("data:"):
                continue
            data_raw = block[5:].strip()
            if not data_raw or data_raw == "[DONE]":
                continue

            try:
                cd = json.loads(data_raw)
                # 捕获usage信息
                if "usage" in cd:
                    state["usage_info"] = cd["usage"]
                elif "choices" in cd and cd["choices"] and "usage" in cd["choices"][0]:
                    state["usage_info"] = cd["choices"][0]["usage"]

                delta = cd.get("choices", [{}])[0].get("delta", {})
                if not delta:
                    delta = cd.get("delta", {})

                # 思考片段
                reasoning = delta.get("reasoning_content") or delta.get("thinking")
                if reasoning:
                    reasoning_str = str(reasoning)
                    state["reasoning_buffer"] += reasoning_str
                    state["full_reasoning"] += reasoning_str
                    events.append({"type": "thinking", "data": reasoning_str})
                    # 移除了这里的 return events！允许继续解析content / tool_calls

                # 正文文本
                text = delta.get("content") or delta.get("text", "")
                if text:
                    if state["reasoning_buffer"] and self.enable_thinking_log:
                        state["reasoning_buffer"] = ""
                    state["total_chars"] += len(text)
                    events.append({"type": "content", "data": text})

                # 增量tool_call拼接
                for tc_d in delta.get("tool_calls", []):
                    idx = tc_d.get("index", 0)
                    if idx not in state["accumulated_tool_calls"]:
                        state["accumulated_tool_calls"][idx] = {
                            "id": "",
                            "function": {"name": "", "arguments": ""}
                        }
                    cell = state["accumulated_tool_calls"][idx]
                    if tc_d.get("id"):
                        cell["id"] = tc_d["id"]
                    func = tc_d.get("function", {})
                    if func.get("name"):
                        cell["function"]["name"] = func["name"]
                    if func.get("arguments"):
                        cell["function"]["arguments"] += func["arguments"]

            except json.JSONDecodeError:
                continue
        return events

    def _fill_stream_usage(self, base_args: Dict, state: Dict, cost: float):
        """流式收尾：耗时统计 + 缺失usage兜底补发请求"""
        self.llm_call_count += 1
        self.llm_total_time += cost
        if state["usage_info"] and "total_tokens" in state["usage_info"]:
            token_count = state["usage_info"]["total_tokens"]
            self.llm_total_tokens += token_count
            return

        # 兜底补发非流式拿usage
        try:
            fill_arg = base_args.copy()
            fill_arg["stream"] = False
            resp = chat_completions_stream(**fill_arg)
            usage = resp.get("usage", {})
            token_count = usage.get("total_tokens", 0)
            self.llm_total_tokens += token_count
        except Exception as e:
            self.llm_total_tokens += state["total_chars"]

    def _parse_resp_reasoning(self, resp: Dict):
        """统一提取思考内容并打印日志（非流式专用）"""
        try:
            reasoning = resp.get('choices', [{}])[0].get('message', {}).get('reasoning_content')
            if reasoning and self.enable_thinking_log:
                self.logger.info("LLM 思考: %s", str(reasoning))
        except Exception:
            self.logger.info("LLM 思考解析失败")
            pass

    #=======================================================

    # ── 历史管理 ──
    @staticmethod
    def _format_history(entries: list[HistoryItem]) -> str:
        if not entries:
            return ""
        lines = []
        for item in entries:
            summary = item['summary']
            lines.append(f"ID: {item['id']} | 摘要: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _format_history_full(entries: list[HistoryItem] | None = None) -> str:
        if not entries:
            return ""
        lines = []
        for item in entries:
            content = item.get("content") or item.get("summary")
            lines.append(f"{item['id']}:\n{content}")
        return "\n\n".join(lines)

    @staticmethod
    def _format_history_structured(entries: List[HistoryItem]) -> str:
        """
        简洁结构化格式：显示每条记录的 ID、角色和摘要。
        若存在工具调用，则简要标记。
        """
        if not entries:
            return ""
        lines = []
        for item in entries:
            role = item.get('role', 'unknown')
            summary = item.get('summary', '')
            tool_calls = item.get('tool_calls')
            tool_call_id = item.get('tool_call_id')

            # 基础行
            base = f"ID: {item['id']} | Role: {role} | Summary: {summary}"
            # 附加工具信息标记
            if tool_calls:
                base += f" | ToolCalls: {len(tool_calls)}"
            if tool_call_id:
                base += f" | ToolCallID: {tool_call_id}"
            lines.append(base)
        return "\n".join(lines)

    @staticmethod
    def _format_history_full_structured(entries: List[HistoryItem]) -> str:
        """
        详细结构化格式：包含完整内容、角色、工具调用参数及结果。
        每个条目用分隔线隔开，清晰展示对话历史。
        """
        if not entries:
            return ""
        blocks = []
        for idx, item in enumerate(entries, 1):
            role = item.get('role', 'unknown')
            # 优先使用 content，若为空则使用 summary
            content = item.get('content')
            if content is None:
                content = item.get('summary', '')

            lines = []
            lines.append(f"--- Entry {idx} ---")
            lines.append(f"Role: {role}")
            lines.append(f"Content: {content}")

            # 处理 assistant 角色的 tool_calls
            tool_calls = item.get('tool_calls')
            if tool_calls:
                lines.append("Tool Calls:")
                for tc in tool_calls:
                    # 提取关键字段，适应不同模型返回的结构
                    tc_id = tc.get('id', '')
                    func = tc.get('function', {})
                    func_name = func.get('name', '')
                    func_args = func.get('arguments', '')
                    lines.append(f" - ID: {tc_id}")
                    lines.append(f" Function: {func_name}")
                    lines.append(f" Arguments: {func_args}")

            # 处理 tool 角色的 tool_call_id
            tool_call_id = item.get('tool_call_id')
            if tool_call_id:
                lines.append(f"Tool Call ID: {tool_call_id}")

            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


    @staticmethod
    def merge_deliverable_history_items(item_list: List[HistoryItem]) -> HistoryItem:
        """
        批量合并多条交付物历史条目，仅筛选 is_deliverable=True 的项
        :param item_list: 原始HistoryItem列表
        :return: 合并后的单条HistoryItem（source=runtime，is_deliverable=True）
        """
        # 仅保留交付物条目
        deliverable_items = [it for it in item_list if it.get("is_deliverable", False)]
        if not deliverable_items:
            return HistoryItem(
                id=gen_memory_id(),
                source="runtime",
                summary="无可用交付物历史",
                content=None,
                is_deliverable=True,
                role="assistant",
                tool_call_id=None,
                tool_calls=None
            )

        summary_parts = []
        content_parts = []
        for item in deliverable_items:
            s = item["summary"].strip()
            c = (item.get("content") or "").strip()
            summary_parts.append(s)
            if c:
                content_parts.append(c)

        merged_summary = "\n".join(summary_parts)
        merged_content = "\n".join(content_parts) if content_parts else None

        return HistoryItem(
            id=gen_memory_id(),
            source="runtime",
            summary=merged_summary,
            content=merged_content,
            is_deliverable=True,
            role="assistant",
            tool_call_id=None,
            tool_calls=None
        )


    @staticmethod
    def _filter_history_by_ids(id_list: list[str], source_entries: list[HistoryItem]) -> list[HistoryItem]:
        id_set = set(id_list)
        return [e for e in source_entries if e["id"] in id_set]


    @staticmethod
    def _normalize_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        对工具参数进行硬编码式的修复性微调
        确保必需字段存在，设置合理的默认值

        Args:
            tool_name: 工具名称
            args: 原始参数字典

        Returns:
            修复后的参数字典（会修改原字典）

        TODO: 完善补全
        """
        if tool_name == "plan_task":
            args.setdefault("main_task", "")
            # 修复 task_plan
            task_plan = args.get("task_plan", [])
            for t in task_plan:
                t.setdefault("description", t.get("id", "无描述"))
                t.setdefault("output_type", "中间")

            # 修复 indicators
            indicators = args.get("indicators", [])
            for ind in indicators:
                ind.setdefault("description", ind.get("id", "无描述"))

        elif tool_name == "chat_reply":
            # chat_reply 不需要修复，但确保字段存在
            args.setdefault("reply", "")
            args.setdefault("summary", "")

        elif tool_name == "execution_submit":
            args.setdefault("result", "")
            args.setdefault("summary", "")

        elif tool_name == "text_result":
            args.setdefault("result", "")
            args.setdefault("summary", "")

        elif tool_name == "start_executor":
            args.setdefault("indicators", [])

        elif tool_name == "start_executor_simple":

            args.setdefault("main_task", "")

            args.setdefault("indicators", [])

        elif tool_name == "task_done":
            args.setdefault("conclusion", "")

        elif tool_name == "skip_current_task":
            args.setdefault("reason", "")

        elif tool_name == "need_break_down":
            args.setdefault("reason", "")

        elif tool_name == "execution_error_submit":
            args.setdefault("reason", "")

        elif tool_name == "read_history_by_id":
            args.setdefault("ids", [])
        elif tool_name == "replan_task":
            args.setdefault("indicators", [])
            args.setdefault("task_plan", [])
            args.setdefault("reason", "")
        elif tool_name == "work_fail":
            args.setdefault("reason", "")
        elif tool_name == "verify_main_task":
            args.setdefault("conclusion", "")
        elif tool_name == "work_verify_submit":
            args.setdefault("success", False)
            args.setdefault("reason", "")
            args.setdefault("finalResp", "")
        return args

    def _request_first_tool_call(
            self,
            messages: list[dict],
            stream: bool,
            tools: list[dict],
            stop_signal: dict,
            stream_callback: StreamCallback,
            depth: int = 0,
    ) -> tuple[str | None, dict | None, str | None]:
        """
        通用工具调用请求：发起LLM请求，读取并解析首个tool_calls
        返回: (tool_name, tool_args, error_msg)
        成功时 error_msg 为 None；失败/中断时 name/args 为 None
        """
        # 收集完整响应的变量
        full_response = ""
        all_chunks = []  # 可选：收集所有chunk用于调试
        tool_calls_result = None
        print("request_first_tool_call")
        for chunk in self._llm_request(
                messages=messages,
                stream=stream,
                tools=tools,
                stop_signal=stop_signal,
                stream_callback=stream_callback
        ):
            if stop_signal.get("aborted"):
                self.logger.warning("[深度%d] LLM请求被中止", depth)
                return None, None, "aborted"

            # 收集响应内容（根据你的chunk结构调整）
            if chunk.get("content"):
                full_response += chunk["content"]

            if chunk["type"] == "tool_calls" and chunk["data"]:
                tool_calls_result = chunk["data"]
                break

            if stream_callback:
                if stream_callback("", "", None):
                    return None, None, "主动终止"
            else:
                print("stream_callback is none")

        if not tool_calls_result:
            # ========== 打印完整的请求和响应（仅失败时） ==========
            self.logger.error("[深度%d] 未返回有效tool_calls，开始打印调试信息", depth)
            self.logger.info("=" * 80)
            self.logger.info("【请求信息】")
            self.logger.info("  - stream: %s", stream)
            self.logger.info("  - tools 数量: %d", len(tools) if tools else 0)
            self.logger.info("  - messages 数量: %d", len(messages))

            self.logger.info("\n完整的 messages:")
            for idx, msg in enumerate(messages):
                self.logger.info("  [%d] %s", idx, json.dumps(msg, ensure_ascii=False, indent=2))

            if tools:
                self.logger.info("\n完整的 tools:")
                self.logger.info(json.dumps(tools, ensure_ascii=False, indent=2))

            self.logger.info("\n【响应信息】")
            self.logger.info("完整响应内容:")
            self.logger.info(full_response if full_response else "(无响应内容)")

            # 如果有tool_calls_result但为空，也打印出来
            if tool_calls_result is not None:
                self.logger.info("tool_calls_result 内容:")
                self.logger.info(json.dumps(tool_calls_result, ensure_ascii=False, indent=2))
            else:
                self.logger.info("tool_calls_result: None")

            self.logger.info("=" * 80)
            # ========== 打印结束 ==========

            self.logger.error("[深度%d] 未返回有效tool_calls", depth)
            return None, None, "LLM未输出工具调用指令，决策失败"

        tc = cast(list, tool_calls_result)[0]

        name = tc.get("function", {}).get("name", "")
        try:
            args = json.loads(
                tc.get("function", {}).get("arguments", "{}")
            )
        except json.JSONDecodeError as e:
            self.logger.error(
                "[深度%d] 工具参数解析失败: %s",
                depth,
                e
            )
            return None, None, f"工具参数JSON解析异常: {str(e)}"

        return name, args, None


    def _filter_available_function_tools(self, relevant_tools: list[str] | None) -> tuple[list[str], str | None]:
        """
        处理plan_task.relevant_tools业务工具白名单
        返回 (可用业务工具名称列表, 错误信息)
        err_msg不为None代表校验失败，应当终止流程返回失败
        """
        all_schemas = skill_manager.get_all_tool_descriptions()
        # 提取所有业务工具的名称
        all_func_names = [tool["function"]["name"] for tool in all_schemas]
        allowed_control_names = {"chat_reply", "plan_task"}

        if not relevant_tools:
            return all_func_names.copy(), None  # 返回名称列表

        valid_names: list[str] = []
        for name in relevant_tools:
            if name in all_func_names:
                valid_names.append(name)
            elif name in allowed_control_names:
                continue  # 流程控制工具静默丢弃
            else:
                return [], f"relevant_tools包含未定义工具名称: {name}"
        return valid_names, None

    def _verify_tool_call_safety(
            self,
            tool_name: str,
            tool_args: dict,
            stop_signal: dict,
            stream_callback,
    ) -> bool:
        """
        校验工具调用是否安全合规

        Args:
            tool_name: 要调用的工具名称
            tool_args: 工具参数
            stream_callback: 流回调函数
            stop_signal: 停止信号

        Returns:
            (is_safe, reason):
                - is_safe: True表示安全，False表示不安全
                - reason: 校验结果说明
        """
        try:
            print("verify_tool_call_safety")
            tool_schema = None

            # 1. 先从 TOOL_SCHEMAS 中查找
            if tool_name in TOOL_SCHEMAS:
                tool_schema = TOOL_SCHEMAS[tool_name]
            else:
                # 2. 从 tool_schemas 中查找
                tool_schemas = skill_manager.get_all_tool_descriptions()

                for schema in tool_schemas:
                    if schema.get("function", {}).get("name") == tool_name:
                        tool_schema = schema
                        break

            assert tool_schema, f"未找到工具 {tool_name} 的定义"

            # ========== 提取工具信息（只保留name, description, parameters） ==========
            tool_info = {
                "name": tool_schema["function"]["name"],
                "description": tool_schema["function"]["description"],
                "parameters": tool_schema["function"]["parameters"]
            }

            # ========== 构建系统提示词 ==========
            system_prompt = PROMPTS_SYSTEM["tool_safety_verify"].format(
                base_system_prompts=self.base_system_prompt,
                meta_path="H:\\MoveDisk\\WorkSpace\\myagent",
                work_space_path=ROOT_DIR,
                user_safety_config="暂无"
            )
            # ========== 构建用户提示词 ==========
            user_prompt = PROMPTS_USER["tool_safety_verify"].format(
                toolcallfunc=json.dumps(tool_info, ensure_ascii=False, indent=2),
                args=json.dumps(tool_args, ensure_ascii=False, indent=2)
            )

            msgs = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            #print(msgs)
            # ========== 调用LLM进行校验 ==========
            # 直接从TOOL_SCHEMAS获取安全校验工具
            safety_tool = TOOL_SCHEMAS.get("tool_safety_verify")
            self.test_flag = True
            name, args, err_msg = self._request_first_tool_call(
                messages=msgs,
                tools=[safety_tool],
                depth=0,
                stream=True,
                stop_signal=stop_signal,
                stream_callback=stream_callback
            )
            if err_msg:
                raise ValueError(f"安全校验失败: {err_msg}")

            if name != "tool_safety_verify":
                raise ValueError(f"安全校验返回了非预期的工具: {name}")

            # ========== 严格校验必要字段 ==========
            is_safe = args.get("is_safe")
            reason = args.get("reason")
            need_confirm = args.get("need_confirm")
            if is_safe is None or reason is None or need_confirm is None:
                raise ValueError("安全校验结果缺少参数字段")

            print(f"安全审查{is_safe} : {reason}")
            if not is_safe and need_confirm:
                if stream_callback("safe_verify", f"{reason}\n", None):
                    #允许继续操作
                    is_safe = True
                    pass
                else:
                    #不允许操作
                    is_safe = False
                    pass

            return is_safe

        except Exception as e:
            error_msg = f"安全校验过程异常: {str(e)}"
            print(error_msg)
            stream_callback("thinking", f"安全校验异常\n", None)
            return False

    # ── 公共入口 ──
    def process_request(
            self,
            user_message: str,
            memory_space=None,
            stream: bool = True,
            stream_callback=None,
            stop_signal: dict | None = None
    ) -> tuple[bool, HistoryItem]:
        print("=======================================")
        print("[USER MSG]:", user_message)
        print("=======================================")
        assert (not stream) or (stop_signal is not None), "流式传输时stop_signal不能为None"
        self._task_plan_completed_cnt = 0
        self.llm_call_count = self.llm_total_tokens = self.llm_total_time = 0
        self.stream = stream
        top_history = self._load_memory_history(memory_space)

        if stop_signal is None:
            stop_signal = {}

        # depth=0 表示入口层级，会执行完整的 Master 决策 + 校验 + 工具筛选
        success, result = self.execute_subtask(
            task=user_message,
            parent_uplevel=top_history,
            depth=0,
            stop_signal=stop_signal,
            uplevel_task_plan_num=0,
            stream_callback=stream_callback,
            memory_space=memory_space
        )

        # 统计信息回调
        stream_callback("usage", json.dumps({
            "total_tokens": self.llm_total_tokens,
            "total_time": self.llm_total_time,
            "call_count": self.llm_call_count
        }), None)

        if success:
            stream_callback("thinking", "任务全部完成\n", 100.0)
        else:
            stream_callback("thinking", "任务失败\n", None)

        result["source"] = cast(Literal["memory", "runtime"], "memory")
        return success, result

    # ── Executor Loop ──
    def _executor_loop(
            self,
            cur_task: str,
            indicators: List[Dict],
            depth: int,
            func_tools: list[str],
            full_history: list[HistoryItem],
            memory_space,
            stop_signal: dict,
            stream_callback=None,
    ) -> tuple[bool, HistoryItem]:
        # ========== 初始化日志文件 ==========
        log_dir = "executor_logs"
        os.makedirs(log_dir, exist_ok=True)

        working_history = copy.deepcopy(full_history)
        iteration = 0
        exec_cur_history: list[HistoryItem] = []

        # ========== 1. 构造执行器工具集 ==========
        exec_flow_schemas = [TOOL_SCHEMAS[name] for name in EXECUTOR_TOOLS if name in TOOL_SCHEMAS]
        # 如果是 Schema 列表，先提取名称
        if func_tools and isinstance(func_tools[0], dict) and "function" in func_tools[0]:
            # func_tools 是 Schema 列表，提取名称
            tool_names = {tool["function"]["name"] for tool in func_tools}
        else:
            # func_tools 已经是名称列表
            tool_names = set(func_tools) if func_tools else set()

        all_business_schemas = skill_manager.get_all_tool_descriptions()
        filtered_business_schemas = [
            sch for sch in all_business_schemas
            if sch["function"]["name"] in tool_names
        ]
        tool_schemas = exec_flow_schemas + all_business_schemas#filtered_business_schemas

        # 检查流回调
        if stream_callback("thinking", f"开始执行...\n", None):
            history_item: HistoryItem = {
                "id": gen_memory_id(),
                "source": "runtime",
                "role": "assistant",
                "summary": f"已终止",
                "content": f"已终止",
                "is_deliverable": False,
                "tool_calls": None,
                "tool_call_id": None
            }
            return False, history_item

        while iteration < self.max_retry_cnt:
            iteration += 1
            # ========== 2. 构造完整的消息列表 ==========
            temp_history = []
            for item in working_history + exec_cur_history:
                new_item = item.copy()
                # 不再添加ID前缀
                temp_history.append(new_item)

            historical_messages = self._convert_history_to_messages(temp_history)

            system_prompt = PROMPTS_SYSTEM["executor_loop"].format(
                base_system_prompts=self.base_system_prompt
            )
            user_prompt = PROMPTS_USER["executor_loop"].format(
                cur_task=cur_task,
                indicators=json.dumps(indicators, ensure_ascii=False, indent=2)
            )
            msgs = [{"role": "system", "content": system_prompt}] + historical_messages + [
                {"role": "user", "content": user_prompt}]

            # ========== LLM 工具调用 ==========
            start_step = time.time()

            name, args, err_msg = self._request_first_tool_call(
                messages=msgs,
                tools=tool_schemas,
                depth=depth,
                stream=self.stream,
                stop_signal=stop_signal,
                stream_callback=stream_callback
            )

            if err_msg:
                history_item: HistoryItem = {
                    "id": gen_memory_id(),
                    "source": "runtime",
                    "role": "assistant",
                    "summary": f"任务失败: {err_msg}",
                    "content": f"任务失败: {err_msg}",
                    "is_deliverable": False,
                    "tool_calls": None,
                    "tool_call_id": None
                }
                return False, history_item

            assert name is not None, "tool name is None"
            assert args is not None, "args is None"
            args = self._normalize_tool_args(tool_name=name, args=args)

            # ---------------------- 分支1：执行失败提交 ----------------------
            if name == "execution_error_submit":
                reason = args.get("reason", "未知失败原因")
                history_item: HistoryItem = {
                    "id": gen_memory_id(),
                    "source": "runtime",
                    "role": "assistant",
                    "summary": f"执行失败: {reason}",
                    "content": f"执行失败: {reason}",
                    "is_deliverable": False,
                    "tool_calls": None,
                    "tool_call_id": None
                }

                if stream_callback("thinking", f"执行失败!\n", None):
                    history_item: HistoryItem = {
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"已终止",
                        "content": f"已终止",
                        "is_deliverable": True,
                        "tool_calls": None,
                        "tool_call_id": None
                    }
                    return False, history_item
                return False, history_item

            # ---------------------- 分支2：成果提交 ----------------------
            elif name == "execution_submit":
                result = args.get("result", "")
                summary = args.get("summary", "")
                wc = self._check_word_count(result) if isinstance(result, str) else 0
                if wc:
                    summary = f"{summary} [字数:{wc}]"

                history_item: HistoryItem = {
                    "id": gen_memory_id(),
                    "source": "runtime",
                    "role": "assistant",
                    "summary": result,
                    "content": result,
                    "is_deliverable": True,
                    "tool_calls": None,
                    "tool_call_id": None
                }

                if stream_callback("thinking", f"执行完成\n", None):
                    history_item: HistoryItem = {
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"已终止",
                        "content": f"已终止",
                        "is_deliverable": False,
                        "tool_calls": None,
                        "tool_call_id": None
                    }
                    return False, history_item
                return True, history_item

            # ---------------------- 分支3：业务功能性工具调用 ----------------------
            else:
                args_tmp = args.copy()
                args["session_id"] = self.session_id
                current_tc_id = gen_memory_id()
                tool_call_data = [
                    {
                        "id": current_tc_id,
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args_tmp, ensure_ascii=False)
                        },
                        "type": "function"
                    }
                ]

                #安全校验
                is_safe = self._verify_tool_call_safety(tool_name=name, tool_args=args,stop_signal=stop_signal, stream_callback=stream_callback)

                if is_safe:
                    # 执行工具
                    start_tool = time.time()
                    print(f"toolcall: {name}({args})")

                    skill_resp = skill_manager.execute(skill_name=name, **args)
                    # result_text, err  SkillResult
                    if skill_resp["success"]:
                        print(f"toolcall success: resp: {skill_resp["data"]}")
                    else:
                        print(f"toolcall done: err={skill_resp["error_message"]}")
                    # assistant 消息：发起工具调用
                    exec_cur_history.append({
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"发起工具调用：{name}，参数：{args_tmp}",
                        "content": f"发起工具调用：{name}，参数：{args_tmp}",
                        "is_deliverable": False,
                        "tool_calls": tool_call_data,
                        "tool_call_id": None
                    })
                    # tool 消息：工具返回结果
                    exec_cur_history.append({
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "tool",
                        "summary": f"已完成: {skill_resp["data"]}",
                        "content": f"已完成: {skill_resp["data"]}",
                        "is_deliverable": False,
                        "tool_calls": None,
                        "tool_call_id": current_tc_id
                    })
                    if stream_callback("thinking", f"工具调用结果:{skill_resp["data"]}\n", None):
                        history_item: HistoryItem = {
                            "id": gen_memory_id(),
                            "source": "runtime",
                            "role": "assistant",
                            "summary": f"已终止",
                            "content": f"已终止",
                            "is_deliverable": False,
                            "tool_calls": None,
                            "tool_call_id": None
                        }
                        return False, history_item
                    continue
                else:
                    history_item: HistoryItem = {
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"非法操作!高安全风险!立刻停止任务!",
                        "content": f"非法操作!高安全风险!立刻停止任务!",
                        "is_deliverable": True,
                        "tool_calls": None,
                        "tool_call_id": None
                    }
                    return False, history_item

        # 迭代超限兜底
        history_item: HistoryItem = {
            "id": gen_memory_id(),
            "source": "runtime",
            "role": "assistant",
            "summary": f"执行超时，超过最大迭代次数{self.max_retry_cnt}",
            "content": f"执行超时，超过最大迭代次数{self.max_retry_cnt}",
            "is_deliverable": False,
            "tool_calls": None,
            "tool_call_id": None
        }
        return False, history_item

    # ── Worker 循环 ──
    def _worker_loop(
            self,
            main_task: str,
            indicators: List[Dict],
            task_plan: List[Dict],
            history: List[HistoryItem],
            stop_signal: Optional[Dict],
            uplevel_task_plan_num: int,
            memory_space,
            depth: int,
            func_tools: Optional[List[str]] = None,
            stream_callback=None
    ) -> tuple[bool, HistoryItem]:
        """
        执行任务流，所有子任务完成后进行整体校验，校验失败时重试整个任务流。
        内部任务执行逻辑与原版完全一致。
        """
        assert stop_signal is not None, "stop_signal 不能为None"
        print("worker_loop")
        # ========== 调试打印 ==========    print(f"\n{'='*60}")
        print(f"[_worker_loop] 深度: {depth}")
        print(f"[_worker_loop] 主任务: {main_task}")
        print(f"[_worker_loop] 任务计划数量: {len(task_plan)}")
        for idx, t in enumerate(task_plan):
            print(f"  - 任务{idx + 1}: {t.get('description', t.get('id', '无描述'))}")
        print(f"[_worker_loop] 传入的 func_tools: {func_tools}")
        print(f"{'=' * 60}\n")

        failure_reasons = []  # 记录每次尝试的失败原因，用于下次尝试的上下文

        for attempt in range(self.max_validation_retries):
            # ----- 重置所有状态 -----
            task_ptr = 0
            curlevel_entries: List[HistoryItem] = []
            replan_count = 0
            executor_fail_count = 0
            max_fail_per_task = 3
            tmp_task_plan_completed_cnt = 0
            # 总任务数（用于进度显示）
            tmp_task_plan_total = uplevel_task_plan_num + len(task_plan) - (1 if uplevel_task_plan_num > 0 else 0)

            # 若之前有失败原因，将其注入上下文
            if failure_reasons:
                for reason in failure_reasons:
                    curlevel_entries.append({
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"上一轮校验失败原因: {reason}",
                        "content": f"上一轮校验失败原因: {reason}",
                        "is_deliverable": False,
                        "tool_calls": None,
                        "tool_call_id": None
                    })

            # ----- 主执行循环 -----
            while True:
                if task_ptr < len(task_plan):
                    cur_task_desc = task_plan[task_ptr].get("description", task_plan[task_ptr].get("id", "无任务描述"))
                else:
                    # 所有子任务执行完毕，跳出循环进行校验
                    break

                available_tools = list(WORKER_TOOLS)

                # 构造消息
                full_history = history + curlevel_entries
                temp_history = []
                for item in full_history:
                    new_item = item.copy()
                    content = new_item.get("content")
                    if content is None:
                        content = new_item.get("summary", "")
                    new_item["content"] = f"[ID: {new_item['id']}] {content}"
                    temp_history.append(new_item)
                historical_messages = self._convert_history_to_messages(temp_history)

                system_prompt = PROMPTS_SYSTEM["worker_loop"].format(base_system_prompts=self.base_system_prompt)
                user_prompt = PROMPTS_USER["worker_loop"].format(
                    main_task=main_task,
                    task_plan=task_plan,
                    cur_task=cur_task_desc,
                )
                msgs = [{"role": "system", "content": system_prompt}] + historical_messages + [
                    {"role": "user", "content": user_prompt}]

                tools = [TOOL_SCHEMAS[t] for t in available_tools]

                # 进度回调
                if stream_callback:
                    progress = tmp_task_plan_completed_cnt / tmp_task_plan_total if tmp_task_plan_total > 0 else 0
                    if stream_callback("thinking", f"当前任务:{cur_task_desc}...\n", progress):
                        abort_item: HistoryItem = {
                            "id": gen_memory_id(),
                            "source": "runtime",
                            "role": "assistant",
                            "summary": "已终止",
                            "content": "已终止",
                            "is_deliverable": False,
                            "tool_calls": None,
                            "tool_call_id": None
                        }
                        return False, abort_item

                # 决策调用
                name, args, err_msg = self._request_first_tool_call(
                    messages=msgs,
                    tools=tools,
                    depth=depth,
                    stream=self.stream,
                    stop_signal=stop_signal,
                    stream_callback=stream_callback
                )

                # 失败场景3: Worker LLM 决策失败 - 不新增历史，重试该任务
                if err_msg:
                    self.logger.warning("[深度%d] Worker LLM决策失败 (重试): %s", depth, err_msg)
                    # 继续循环，重试当前任务
                    continue

                assert name is not None and args is not None, "name and args cannot be None"
                args = self._normalize_tool_args(tool_name=name, args=args)

                # ---------- 分支处理 ----------
                if name == "replan_task":
                    replan_count += 1
                    if replan_count > self.max_replan_limit:
                        fail_item: HistoryItem = {
                            "id": gen_memory_id(),
                            "source": "runtime",
                            "role": "assistant",
                            "summary": f"重规划次数超限(上限{self.max_replan_limit})，任务终止",
                            "content": f"重规划次数超限(上限{self.max_replan_limit})，任务终止",
                            "is_deliverable": False,
                            "tool_calls": None,
                            "tool_call_id": None
                        }
                        return False, fail_item

                    new_indicators = args.get("indicators", [])
                    task_plan_new = args.get("task_plan", [])
                    curlevel_entries.append({
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "user",
                        "summary": f"重规划：指标更新为{new_indicators}，新任务流{task_plan_new}",
                        "content": f"重规划：指标更新为{new_indicators}，新任务流{task_plan_new}",
                        "is_deliverable": False,
                        "tool_calls": None,
                        "tool_call_id": None
                    })
                    indicators = new_indicators
                    task_plan = task_plan_new
                    task_ptr = 0
                    executor_fail_count = 0
                    tmp_task_plan_total = uplevel_task_plan_num + len(task_plan) - (
                        1 if uplevel_task_plan_num > 0 else 0)
                    tmp_task_plan_completed_cnt = 0
                    continue

                elif name == "work_fail":
                    fail_reason = args.get("reason", "未知原因，本级任务无法继续执行")
                    fail_item: HistoryItem = {
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"Worker宣告任务失败：{fail_reason}",
                        "content": f"Worker宣告任务失败：{fail_reason}",
                        "is_deliverable": False,
                        "tool_calls": None,
                        "tool_call_id": None
                    }
                    return False, fail_item

                elif name == "need_break_down":
                    sub_history = copy.deepcopy(history + curlevel_entries)
                    success, new_history = self.execute_subtask(
                        task=cur_task_desc,
                        parent_uplevel=sub_history,
                        depth=depth + 1,
                        stop_signal=stop_signal,
                        uplevel_task_plan_num=uplevel_task_plan_num + len(task_plan),
                        stream_callback=stream_callback,
                        memory_space=memory_space,
                    )
                    # 失败场景6: execute_subtask 返回 False - 将返回的history加入临时历史，重试
                    if not success:
                        curlevel_entries.append(new_history)
                        # 记录失败原因到临时历史
                        curlevel_entries.append({
                            "id": gen_memory_id(),
                            "source": "runtime",
                            "role": "assistant",
                            "summary": f"子任务拆解失败: {new_history.get('summary', '')}",
                            "content": f"子任务拆解失败: {new_history.get('content', new_history.get('summary', ''))}",
                            "is_deliverable": False,
                            "tool_calls": None,
                            "tool_call_id": None
                        })
                        # 继续循环，重试当前任务
                        continue
                    curlevel_entries.append(new_history)
                    task_ptr += 1
                    executor_fail_count = 0
                    continue

                elif name == "start_executor":
                    subtask_ind = args.get("indicators", [])
                    cur_type = task_plan[task_ptr].get("output_type", "") if task_ptr < len(task_plan) else ""
                    is_deliv = "交付" in cur_type

                    success, new_history = self._executor_loop(
                        cur_task=cur_task_desc,
                        indicators=subtask_ind,
                        depth=depth,
                        func_tools=func_tools or [],
                        stop_signal=stop_signal,
                        stream_callback=stream_callback,
                        full_history=history + curlevel_entries,
                        memory_space=memory_space
                    )
                    new_history["is_deliverable"] = is_deliv
                    curlevel_entries.append(new_history)

                    if success:
                        task_ptr += 1
                        tmp_task_plan_completed_cnt += 1
                        executor_fail_count = 0
                        if stream_callback:
                            progress = tmp_task_plan_completed_cnt / tmp_task_plan_total if tmp_task_plan_total > 0 else 0
                            if stream_callback("thinking", f"完成任务:{cur_task_desc}\n", progress):
                                abort_item: HistoryItem = {
                                    "id": gen_memory_id(),
                                    "source": "runtime",
                                    "role": "assistant",
                                    "summary": "已终止",
                                    "content": "已终止",
                                    "is_deliverable": False,
                                    "tool_calls": None,
                                    "tool_call_id": None
                                }
                                return False, abort_item
                    else:
                        executor_fail_count += 1
                        # 记录执行失败原因到临时历史
                        curlevel_entries.append({
                            "id": gen_memory_id(),
                            "source": "runtime",
                            "role": "assistant",
                            "summary": f"任务执行失败: {new_history.get('summary', '')}",
                            "content": f"任务执行失败: {new_history.get('content', new_history.get('summary', ''))}",
                            "is_deliverable": False,
                            "tool_calls": None,
                            "tool_call_id": None
                        })
                        print(f"失败 after _executor_loop: {new_history.get('content', new_history.get('content', ''))}")
                        if executor_fail_count >= max_fail_per_task:
                            fail_item: HistoryItem = {
                                "id": gen_memory_id(),
                                "source": "runtime",
                                "role": "assistant",
                                "summary": f"[深度{depth}] 当前任务执行失败次数达到上限，任务失败",
                                "content": f"[深度{depth}] 当前任务执行失败次数达到上限，任务失败",
                                "is_deliverable": False,
                                "tool_calls": None,
                                "tool_call_id": None
                            }
                            return False, fail_item
                        # 继续循环重试该任务
                    continue

                else:
                    # 失败场景8: 未知工具调用 - 不新增历史，重试该任务
                    self.logger.warning("[深度%d] Worker 调用了未知工具: %s，重试", depth, name)
                    continue

            # ---------- 所有子任务执行完毕，进行整体校验 ----------
            deliverable_items = [it for it in curlevel_entries if it.get("is_deliverable", False)]
            merged = self.merge_deliverable_history_items(deliverable_items) if deliverable_items else None

            if merged is None:
                success = False
                reason = "无任何交付成果"
                final_resp = ""
            else:
                success, reason, final_resp = self._validate_task(
                    task_desc=main_task,
                    indicators=indicators,
                    result_item=merged,
                    depth=depth,
                    stop_signal=stop_signal,
                    stream_callback=stream_callback
                )

            if success:
                # 校验通过，构造最终结果
                if merged is None:
                    merged = {
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": "",
                        "content": "",
                        "is_deliverable": True,
                        "tool_calls": None,
                        "tool_call_id": None
                    }
                merged["content"] = final_resp
                merged["summary"] = f"校验通过: {reason}\n" + merged["summary"]
                self._task_plan_completed_cnt = tmp_task_plan_completed_cnt
                return True, merged
            else:
                # 校验失败，记录原因，准备重试
                failure_reasons.append(reason)
                if stream_callback:
                    stream_callback("thinking",
                                    f"本轮校验失败: {reason}，准备重试 ({attempt + 1}/{self.max_validation_retries})\n",
                                    None)
                continue  # 进入下一次尝试

        # 所有尝试均失败
        fail_item: HistoryItem = {
            "id": gen_memory_id(),
            "source": "runtime",
            "role": "assistant",
            "summary": f"任务流校验重试次数用尽，最后失败原因: {failure_reasons[-1] if failure_reasons else '未知'}",
            "content": f"任务流校验重试次数用尽，最后失败原因: {failure_reasons[-1] if failure_reasons else '未知'}",
            "is_deliverable": False,
            "tool_calls": None,
            "tool_call_id": None
        }
        return False, fail_item

    # ── 递归子任务 ──
    def execute_subtask(
            self,
            task: str,
            parent_uplevel: list[HistoryItem],
            depth: int,
            stop_signal: dict,
            uplevel_task_plan_num: int,
            stream_callback=None,
            memory_space=None
    ) -> tuple[bool, HistoryItem]:
        """
        执行子任务（或根任务，当 depth=0 时）

        Args:
            task: 任务描述
            parent_uplevel: 父级历史（根任务时为 memory 历史）
            depth: 当前深度（0 表示根任务）
            stop_signal: 中断信号
            uplevel_task_plan_num: 上层任务计划数（用于进度计算）
            func_tools: 预筛选的工具列表（仅用于执行器，不用于 Master 决策）
            stream_callback: 流式回调
            memory_space: 记忆空间

        Returns:
            (success, HistoryItem)
        """

        print("execute_subtask")
        if depth > self.max_depth:
            history_item: HistoryItem = {
                "id": gen_memory_id(),
                "source": "runtime",
                "summary": f"超过最大深度",
                "content": f"超过最大深度",
                "is_deliverable": True,
                "role": "assistant",
                "tool_call_id": None,
                "tool_calls": None
            }
            return False, history_item

        # 局部上下文初始化
        sub_uplevel_history = parent_uplevel.copy()
        failure_reasons = []  # 记录失败原因，用于上下文

        for attempt in range(self.max_validation_retries):
            # 若之前有失败原因，将其注入上下文
            local_context = sub_uplevel_history.copy()
            if failure_reasons:
                for reason in failure_reasons:
                    local_context.append({
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "role": "assistant",
                        "summary": f"上一轮失败原因: {reason}",
                        "content": f"上一轮失败原因: {reason}",
                        "is_deliverable": False,
                        "tool_calls": None,
                        "tool_call_id": None
                    })

            system_prompt = PROMPTS_SYSTEM["master_entry"].format(base_system_prompts=self.base_system_prompt)
            user_prompt = PROMPTS_USER["master_entry"].format(main_task=task)

            msgs = [{"role": "system", "content": system_prompt}]

            temp_history = []
            for item in local_context:
                new_item = item.copy()
                content = new_item.get("content")
                if content is None:
                    content = new_item.get("summary", "")
                new_item["content"] = f"[ID: {new_item['id']}] {content}"
                temp_history.append(new_item)

            history_messages = self._convert_history_to_messages(temp_history)
            msgs.extend(history_messages)
            msgs.append({"role": "user", "content": user_prompt})

            master_flow_schemas = [TOOL_SCHEMAS[name] for name in MASTER_TOOLS if name in TOOL_SCHEMAS]
            tools = master_flow_schemas
            name, args, err_msg = self._request_first_tool_call(
                messages=msgs,
                tools=tools,
                stream=self.stream,
                stop_signal=stop_signal,
                depth=depth,
                stream_callback=stream_callback
            )

            # 失败场景1: LLM 决策失败 - 不加历史，重试
            if err_msg:
                self.logger.warning("[深度%d] execute_subtask LLM决策失败 (尝试 %d/%d): %s",
                                    depth, attempt + 1, self.max_validation_retries, err_msg)
                continue

            # 失败场景2: 工具合法性校验失败 - 不加历史，重试
            if name not in MASTER_TOOLS:
                self.logger.warning("[深度%d] execute_subtask 调用非法工具: %s (尝试 %d/%d)",
                                    depth, name, attempt + 1, self.max_validation_retries)
                continue

            if not isinstance(args, dict):
                self.logger.warning("[深度%d] execute_subtask 工具参数格式无效 (尝试 %d/%d)",
                                    depth, attempt + 1, self.max_validation_retries)
                continue

            assert name is not None
            args = self._normalize_tool_args(tool_name=name, args=args)

            # ========== 分支处理 ==========
            if name == "start_executor_simple":
                main_task = args.get("main_task", task)
                simple_indicators = args.get("indicators", [])
                # 执行简单任务
                success, exec_result = self._executor_loop(
                    cur_task=main_task,
                    indicators=simple_indicators,
                    depth=depth,
                    func_tools=tools,
                    full_history=local_context,
                    memory_space=memory_space,
                    stop_signal=stop_signal,
                    stream_callback=stream_callback
                )

                if not success:
                    content = exec_result.get("content", "执行失败")
                    print(f"执行失败 after executor_simple: {content}")
                    history_item: HistoryItem = {
                        "id": gen_memory_id(),
                        "source": "runtime",
                        "summary": f"{content}",
                        "content": f"{content}",
                        "is_deliverable": True,
                        "role": "assistant",
                        "tool_call_id": None,
                        "tool_calls": None
                    }
                    return False, history_item

                # 校验交付物
                validate_success, reason, final_resp = self._validate_task(
                    task_desc=main_task,
                    indicators=simple_indicators,
                    result_item=exec_result,
                    depth=depth,
                    stop_signal=stop_signal,
                    stream_callback=stream_callback
                )

                # 失败场景4: 校验失败 - 加历史，重试
                if not validate_success:
                    failure_reasons.append(reason)
                    continue

                exec_result["content"] = final_resp
                exec_result["summary"] = f"校验通过: {reason}\n" + exec_result["summary"]

                return True, exec_result

            elif name == "plan_task":
                task_plan = args.get("task_plan", [])
                indicators = args.get("indicators", [])
                refined_main_task = args.get("main_task", task)

                print(f"当前任务:{refined_main_task}")
                print("生成执行计划:")
                for sub_task in task_plan:
                    print(f" - {sub_task.get('description', sub_task.get('id', '无描述'))}")
                print("验收指标:")
                for ind in indicators:
                    print(f" - {ind.get('description', ind.get('id', '无指标'))}")

                # 工具筛选（入口和递归都执行）
                relevant_tools = args.get("relevant_tools")
                filtered_func_tools, tool_err = self._filter_available_function_tools(relevant_tools)

                relevant_ids = args.get("relevant_history_ids", [])
                worker_history = local_context
                if relevant_ids:
                    worker_history = self._filter_history_by_ids(relevant_ids, local_context)
                # 启动 Worker 执行任务流
                success, new_history = self._worker_loop(
                    main_task=refined_main_task,
                    indicators=indicators,
                    task_plan=task_plan,
                    uplevel_task_plan_num=uplevel_task_plan_num,
                    history=worker_history,
                    stop_signal=stop_signal,
                    stream_callback=stream_callback,
                    memory_space=memory_space,
                    depth=depth,
                    func_tools=filtered_func_tools
                )

                # 失败场景6: Worker 执行失败 - 加历史，重试
                if not success:
                    content = new_history.get("content", "Worker执行失败")
                    failure_reasons.append(content)
                    print(f"执行失败 after _worker_loop: {content}")
                    continue

                self.logger.info(
                    "[SUBTASK EXIT] depth=%d success=%s",
                    depth,
                    success
                )
                return True, new_history

            else:
                # 失败场景7: 未知分支 - 不加历史，重试
                self.logger.warning("[深度%d] execute_subtask 未知工具: %s，重试 (尝试 %d/%d)",
                                    depth, name, attempt + 1, self.max_validation_retries)
                continue

        # 所有尝试失败
        history_item: HistoryItem = {
            "id": gen_memory_id(),
            "source": "runtime",
            "summary": f"子任务执行失败，重试次数用尽，最后原因: {failure_reasons[-1] if failure_reasons else '未知'}",
            "content": f"子任务执行失败，重试次数用尽，最后原因: {failure_reasons[-1] if failure_reasons else '未知'}",
            "is_deliverable": True,
            "role": "assistant",
            "tool_call_id": None,
            "tool_calls": None
        }
        return False, history_item

    def _validate_task(
            self,
            task_desc: str,
            indicators: List[Dict],
            result_item: HistoryItem,
            depth: int,
            stop_signal: dict,
            stream_callback=None
    ) -> tuple[bool, str, str]:
        """调用 LLM 使用 work_verify_submit 工具校验交付物，返回 (success, reason, finalResp)"""
        temp_history = []

        print(f"in: [{indicators}]\n{result_item}")
        item_copy = result_item.copy()
        content = item_copy.get("content")
        if content is None:
            content = item_copy.get("summary", "")
        item_copy["content"] = f"[交付物] {content}"
        temp_history.append(item_copy)
        historical_messages = self._convert_history_to_messages(temp_history)

        user_prompt = PROMPTS_USER["worker_verify"].format(
            main_task=task_desc,
            indicators=json.dumps(indicators, ensure_ascii=False)
        )
        msgs = historical_messages + [{"role": "user", "content": user_prompt}]

        verify_tools = [TOOL_SCHEMAS["work_verify_submit"]]


        if stream_callback("thinking", "验收结果...\n", None):
            return False, f"已终止", f"已终止"
        name, args, err_msg = self._request_first_tool_call(
            messages=msgs,
            tools=verify_tools,
            depth=depth,
            stream=self.stream,
            stop_signal=stop_signal,
            stream_callback=stream_callback
        )
        if err_msg or name != "work_verify_submit":
            return False, f"校验流程异常: {err_msg or '非法工具调用'}", ""

        print(f"validate_task: {msgs}")

        args = self._normalize_tool_args(tool_name=name, args=args or {})
        success = bool(args.get("success", False))
        reason = str(args.get("reason", ""))
        final_resp = str(args.get("finalResp", ""))
        stream_callback("thinking", "验收完成\n", None)
        return success, reason, final_resp

    @staticmethod
    def _check_word_count(text: str) -> int:
        # TODO: 需要支持英文/混合文本
        chinese = re.findall(r'[\u4e00-\u9fff]', text)
        return len(chinese) if chinese else len(text.split())


    @staticmethod
    def _load_memory_history(memory_space) -> List[HistoryItem]:
        if memory_space is None:
            return []

        # 获取所有摘要
        summaries = memory_space.get_all_summaries()
        if not summaries:
            return []

        # 获取所有 memory_ids
        memory_ids = list(summaries.keys())

        # 获取完整记忆数据（包含 role, content, tool_calls, tool_call_id）
        memories_data = memory_space.get_memory(memory_ids)

        result = []
        for mid, s in summaries.items():
            data = memories_data.get(mid, {})
            result.append(
                HistoryItem(
                    id=mid,
                    source="memory",
                    summary=s,
                    content=data.get("content"),  # 从数据库获取完整内容
                    role=data.get("role", "unknown"),  # 从数据库获取 role
                    is_deliverable=False,
                    tool_calls=data.get("tool_calls"),  # 从数据库获取 tool_calls
                    tool_call_id=data.get("tool_call_id")  # 从数据库获取 tool_call_id
                )
            )
        return result


if __name__ == "__main__":
    pass
