# fastapi_framework/app/agent/routes.py

import json
import os
import asyncio
import queue
import threading
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel

from .deps import get_agent
from .api import agent_config

router = APIRouter(prefix="/agent", tags=['agent'])
STATIC_DIR = r"C:\my\workspace\website\fastapi_framework\app\static\agent"


# ========== Pydantic 请求模型 ==========
class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None


class UpdateSessionRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class ChatRequest(BaseModel):
    message: str
    session_id: str
    model: Optional[str] = None
    stream: bool = True


class GetOrCreateSessionRequest(BaseModel):
    session_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None


class VerifyPasswordRequest(BaseModel):
    password: str


class UpdateConfigRequest(BaseModel):
    updates: Dict[str, Any]
    save_to_file: bool = True


class ResetConfigRequest(BaseModel):
    keep_other_modules: bool = True


class ReloadConfigRequest(BaseModel):
    config_path: Optional[str] = None


class VerifyToolRequest(BaseModel):
    verify_id: str
    is_safe: bool
    reason: str = ""


# ========== 安全校验相关（全局） ==========
verify_queue = queue.Queue()
pending_verifies: Dict[str, Dict[str, Any]] = {}
pending_lock = threading.Lock()


# ========== 会话管理 ==========
@router.get('/sessions')
async def list_sessions(include_metadata: bool = Query(True)):
    """列出所有会话"""
    agent = get_agent()
    sessions = agent.list_sessions(include_metadata=include_metadata)
    return JSONResponse({"success": True, "data": sessions})


@router.post('/sessions')
async def create_session(payload: CreateSessionRequest):
    """创建新会话"""
    agent = get_agent()
    session = agent.create_session(
        session_id=payload.session_id,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
        model=payload.model,
        temperature=payload.temperature
    )
    metadata = agent.get_session_metadata(session.session_id) or {}
    return JSONResponse({
        "success": True,
        "data": {
            "session_id": session.session_id,
            "work_dir": str(session.work_dir),
            "name": metadata.get('name', ''),
            "description": metadata.get('description', ''),
            "tags": metadata.get('tags', []),
            "created_at": metadata.get('created_at'),
            "updated_at": metadata.get('updated_at'),
        }
    })


@router.get('/sessions/{session_id}')
async def get_session(session_id: str):
    """获取会话详情"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    metadata = agent.get_session_metadata(session_id) or {}
    messages = []
    try:
        if session._memory_space:
            messages = session._memory_space.get_messages_with_metadata(order_by="created_at ASC")
    except Exception as e:
        print(f"⚠️ 提取记忆消息失败: {e}")
    return JSONResponse({
        "success": True,
        "data": {
            "session_id": session.session_id,
            "work_dir": str(session.work_dir),
            "name": metadata.get('name', ''),
            "description": metadata.get('description', ''),
            "tags": metadata.get('tags', []),
            "created_at": metadata.get('created_at'),
            "updated_at": metadata.get('updated_at'),
            "memory_count": metadata.get('memory_count', 0),
            "messages": messages,
        }
    })


@router.put('/sessions/{session_id}')
async def update_session(session_id: str, payload: UpdateSessionRequest):
    """更新会话元数据"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    updated = False
    if payload.name is not None:
        if agent.update_session_name(session_id, payload.name):
            updated = True
    if payload.description is not None:
        if agent.update_session_description(session_id, payload.description):
            updated = True
    if payload.tags is not None:
        if agent.update_session_tags(session_id, payload.tags):
            updated = True
    if not updated and (payload.name is None and payload.description is None and payload.tags is None):
        raise HTTPException(status_code=400, detail="没有提供要更新的字段")
    metadata = agent.get_session_metadata(session_id) or {}
    return JSONResponse({
        "success": True,
        "message": "会话已更新",
        "data": {
            "session_id": session_id,
            "name": metadata.get('name', ''),
            "description": metadata.get('description', ''),
            "tags": metadata.get('tags', []),
            "updated_at": metadata.get('updated_at'),
        }
    })


@router.delete('/sessions/{session_id}')
async def delete_session(session_id: str):
    """删除会话"""
    agent = get_agent()
    success = agent.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在或删除失败")
    return JSONResponse({"success": True, "message": "会话已删除"})


@router.get('/sessions/by-name/{name}')
async def get_session_by_name(name: str):
    """根据名称查找会话"""
    agent = get_agent()
    session = agent.get_session_by_name(name)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 '{name}' 不存在")
    metadata = agent.get_session_metadata(session.session_id) or {}
    return JSONResponse({
        "success": True,
        "data": {
            "session_id": session.session_id,
            "work_dir": str(session.work_dir),
            "name": metadata.get('name', ''),
            "description": metadata.get('description', ''),
            "tags": metadata.get('tags', []),
            "created_at": metadata.get('created_at'),
            "updated_at": metadata.get('updated_at'),
        }
    })


@router.post('/sessions/get-or-create')
async def get_or_create_session(payload: GetOrCreateSessionRequest):
    """获取已有会话，若不存在则创建"""
    agent = get_agent()
    session = agent.get_or_create_session(
        session_id=payload.session_id,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
        model=payload.model,
        temperature=payload.temperature
    )
    metadata = agent.get_session_metadata(session.session_id) or {}
    return JSONResponse({
        "success": True,
        "data": {
            "session_id": session.session_id,
            "work_dir": str(session.work_dir),
            "name": metadata.get('name', ''),
            "description": metadata.get('description', ''),
            "tags": metadata.get('tags', []),
            "created_at": metadata.get('created_at'),
            "updated_at": metadata.get('updated_at'),
        }
    })


@router.delete('/sessions/{session_id}/memories/{memory_id}')
async def delete_memory(session_id: str, memory_id: str):
    """删除单条记忆"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    try:
        success = session.delete_memory(memory_id)
        if success:
            return JSONResponse({"success": True, "message": "记忆已删除"})
        else:
            raise HTTPException(status_code=404, detail="记忆不存在或删除失败")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/sessions/{session_id}/memories')
async def clear_memories(session_id: str):
    """清空会话的所有记忆"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    try:
        deleted_count = session.clear_all_memories()
        return JSONResponse({
            "success": True,
            "message": f"已清空 {deleted_count} 条记忆",
            "deleted_count": deleted_count
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/sessions/{session_id}/memories')
async def list_memories(session_id: str):
    """获取会话的所有记忆摘要"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    try:
        summaries = {}
        if session._memory_space:
            summaries = session._memory_space.get_all_summaries()
        return JSONResponse({
            "success": True,
            "data": {
                "session_id": session_id,
                "total": len(summaries),
                "memories": [
                    {"id": mid, "summary": summary}
                    for mid, summary in summaries.items()
                ]
            }
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== 认证 ==========
@router.post('/verify-password')
async def verify_password(payload: VerifyPasswordRequest):
    """验证管理密码"""
    if payload.password == 'Jhzchfl008!':
        return JSONResponse({'success': True})
    raise HTTPException(status_code=401, detail="密码错误")


# ========== 配置管理 ==========
@router.get('/config/default-model')
async def get_default_model():
    """获取默认模型"""
    agent = get_agent()
    return JSONResponse({"success": True, "data": {"default_model": agent.get_default_model()}})


@router.put('/config/default-model')
async def set_default_model(payload: dict = Body(...)):
    """设置默认模型"""
    model = payload.get('model')
    if not model:
        raise HTTPException(status_code=400, detail="缺少 model 参数")
    agent = get_agent()
    agent.set_default_model(model)
    return JSONResponse({"success": True, "message": f"默认模型已设置为 {model}"})


@router.get('/config')
async def get_config(
        path: Optional[str] = Query(None),
        flatten: bool = Query(False),
        full: bool = Query(False)
):
    """
    获取 Agent 配置

    Query parameters:
        path: 可选，配置路径（如 "executor.max_retries"）
        flatten: 可选，是否扁平化返回
        full: 可选，是否返回完整配置（包含所有模块）
    """
    try:
        if full:
            config_data = agent_config.get_all_config()
            return JSONResponse({"success": True, "data": config_data})
        config_value = agent_config.get_config(path, flatten=flatten)
        return JSONResponse({"success": True, "data": config_value})
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/config')
async def update_config(request: Request):
    """
    更新 Agent 配置

    Request body:
        updates: 更新字段字典（支持点号路径或嵌套字典）
        save_to_file: 是否保存到文件，默认 true
    """
    try:
        data = await request.json()

        if 'updates' in data:
            updates = data['updates']
            save_to_file = data.get('save_to_file', True)
        else:
            updates = data
            save_to_file = data.get('save_to_file', True)
            if 'save_to_file' in updates:
                del updates['save_to_file']

        if not updates:
            raise HTTPException(status_code=400, detail="没有提供要更新的配置")

        print("update config: ", updates)
        print("save to file: ", save_to_file)
        result = agent_config.update_config(updates, save_to_file=save_to_file)

        return JSONResponse({
            "success": result.get('success', True),
            "message": result.get('message', '配置已更新'),
            "updated_fields": result.get('updated_fields', []),
            "failed_fields": result.get('failed_fields', [])
        })
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/config/reset')
async def reset_config(payload: ResetConfigRequest = Body(default_factory=lambda: ResetConfigRequest())):
    """重置配置为默认值"""
    try:
        result = agent_config.reset_config_to_default(keep_other_modules=payload.keep_other_modules)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/config/reload')
async def reload_config(payload: ReloadConfigRequest = Body(default_factory=lambda: ReloadConfigRequest())):
    """重新加载配置文件"""
    try:
        result = agent_config.reload_config(payload.config_path)
        if result.get("success"):
            return JSONResponse({
                "success": True,
                "message": result.get("message", "配置已重新加载")
            })
        else:
            raise HTTPException(status_code=500, detail=result.get("message", "重新加载失败"))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ========== 工具安全校验 ==========
@router.post('/verify')
async def verify_tool(payload: VerifyToolRequest):
    """客户端提交工具安全校验结果"""
    verify_id = payload.verify_id
    is_safe = payload.is_safe
    reason = payload.reason

    if not verify_id:
        print(f"[VERIFY] 拒绝: 缺少 verify_id", flush=True)
        raise HTTPException(status_code=400, detail="缺少 verify_id")

    with pending_lock:
        pending = pending_verifies.get(verify_id)
        if not pending:
            print(f"[VERIFY] 拒绝: verify_id 不存在或已过期: {verify_id[:8]}...", flush=True)
            raise HTTPException(status_code=400, detail="verify_id 无效或已过期")

        pending["result"][0] = is_safe
        pending["result"][1] = reason
        pending["event"].set()
        pending_verifies.pop(verify_id, None)

    print(f"[VERIFY] 通过: verify_id={verify_id[:8]}..., is_safe={is_safe}", flush=True)
    return JSONResponse({"success": True})


# ========== 聊天接口 ==========
@router.post('/chat')
async def chat(payload: ChatRequest):
    """聊天接口"""
    message = payload.message
    session_id = payload.session_id
    model = payload.model
    stream = payload.stream

    if not message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id 参数")

    # 定义安全校验回调
    def safe_verify_callback(verify_id: str, timeout: int = 300) -> tuple[bool, str]:
        event = threading.Event()
        result = [False, "安全校验超时"]

        with pending_lock:
            pending_verifies[verify_id] = {
                "event": event,
                "result": result
            }

        print(f"[SAFE_VERIFY] 等待确认: verify_id={verify_id[:8]}...", flush=True)
        event.wait(timeout=timeout)

        with pending_lock:
            pending_verifies.pop(verify_id, None)

        return result[0], result[1]

    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    if stream:
        def generate():
            try:
                gen = agent.chat_stream(
                    message=message,
                    session_id=session_id,
                    model=model,
                    safe_verify_callback=safe_verify_callback
                )
                for chunk in gen:
                    yield chunk
            except Exception as e:
                import traceback
                traceback.print_exc()
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )
    else:
        try:
            result = agent.chat_sync(
                message=message,
                session_id=session_id,
                model=model,
            )
            return JSONResponse({
                "success": True,
                "content": result.get('reply', ''),
                "stats": result.get('stats', {}),
                "session_id": session_id
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))


# ========== 健康检查 ==========
@router.get('/health')
async def health():
    agent = get_agent()
    return JSONResponse({
        "success": True,
        "status": "healthy",
        "gateway": "LLMGateway",
        "total_requests": agent._stats.get("total_requests", 0)
    })


# ========== 静态文件服务（放在最后） ==========
@router.get("/")
async def serve_index():
    """访问 /agent/ 时返回 index.html"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="index.html not found")


@router.get("/{path:path}")
async def serve_static(path: str):
    """访问 /agent/xxx 时返回对应静态文件"""
    file_path = os.path.join(STATIC_DIR, path)
    print(f"[DEBUG] 请求路径: {path}")
    print(f"[DEBUG] 完整文件路径: {file_path}")
    print(f"[DEBUG] 文件是否存在: {os.path.exists(file_path)}")

    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)

    # SPA 支持
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")

    raise HTTPException(status_code=404, detail="File not found")