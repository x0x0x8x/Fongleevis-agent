# app/agent/routes.py

import json
import queue
from flask import Blueprint, request, jsonify, Response, stream_with_context
from . import get_agent
from .api import agent_config

agent_bp = Blueprint('agent', __name__, url_prefix='/api/agent')


# ========== 会话管理 ==========

@agent_bp.route('/sessions', methods=['GET'])
def list_sessions():
    """列出所有会话（包含元数据）"""
    agent = get_agent()
    include_metadata = request.args.get('include_metadata', 'true').lower() == 'true'
    sessions = agent.list_sessions(include_metadata=include_metadata)
    #print(sessions)
    return jsonify({"success": True, "data": sessions})


@agent_bp.route('/sessions', methods=['POST'])
def create_session():
    """
    创建新会话
    Request body:
        session_id: 可选，会话ID
        name: 可选，会话名称
        description: 可选，会话描述
        tags: 可选，标签列表
        model: 可选，LLM模型
        temperature: 可选，温度参数
    """
    data = request.json or {}
    session_id = data.get('session_id')
    name = data.get('name')
    description = data.get('description')
    tags = data.get('tags')
    model = data.get('model')
    temperature = data.get('temperature')
    agent = get_agent()
    session = agent.create_session(
        session_id=session_id,
        name=name,
        description=description,
        tags=tags,
        model=model,
        temperature=temperature
    )

    # 获取元数据
    metadata = agent.get_session_metadata(session.session_id) or {}

    return jsonify({
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


@agent_bp.route('/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    agent = get_agent()
    session = agent.get_session(session_id)
    print(f"id:{session_id}")

    if not session:
        return jsonify({"success": False, "error": f"会话 {session_id} 不存在"}), 404

    metadata = agent.get_session_metadata(session_id)
    #print(f"metadata = {metadata}")
    messages = []
    try:
        if session._memory_space:
            messages = session._memory_space.get_messages_with_metadata(order_by="created_at ASC")
        #print(f"✅ 加载 {len(messages)} 条消息")
    except Exception as e:
        print(f"⚠️ 提取记忆消息失败: {e}")

    return jsonify({
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


@agent_bp.route('/sessions/<session_id>', methods=['PUT'])
def update_session(session_id):
    """
    更新会话元数据
    Request body:
        name: 可选，会话名称
        description: 可选，会话描述
        tags: 可选，标签列表
    """
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": f"会话 {session_id} 不存在"}), 404

    data = request.json or {}
    name = data.get('name')
    description = data.get('description')
    tags = data.get('tags')

    updated = False
    if name is not None:
        if agent.update_session_name(session_id, name):
            updated = True
    if description is not None:
        if agent.update_session_description(session_id, description):
            updated = True
    if tags is not None:
        if agent.update_session_tags(session_id, tags):
            updated = True

    if not updated and (name is None and description is None and tags is None):
        return jsonify({"success": False, "error": "没有提供要更新的字段"}), 400

    # 获取更新后的元数据
    metadata = agent.get_session_metadata(session_id) or {}

    return jsonify({
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


@agent_bp.route('/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    agent = get_agent()
    success = agent.delete_session(session_id)
    if not success:
        return jsonify({"success": False, "error": "会话不存在或删除失败"}), 404
    return jsonify({"success": True, "message": "会话已删除"})


@agent_bp.route('/sessions/by-name/<name>', methods=['GET'])
def get_session_by_name(name):
    """根据名称查找会话"""
    agent = get_agent()
    session = agent.get_session_by_name(name)
    if not session:
        return jsonify({"success": False, "error": f"会话 '{name}' 不存在"}), 404

    metadata = agent.get_session_metadata(session.session_id) or {}

    return jsonify({
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


# ========== 会话获取或创建 ==========
@agent_bp.route('/sessions/get-or-create', methods=['POST'])
def get_or_create_session():
    """
    获取已有会话，若不存在则创建
    Request body:
        session_id: 必填，会话ID
        name: 可选，会话名称
        description: 可选，会话描述
        tags: 可选，标签列表
        model: 可选，LLM模型
        temperature: 可选，温度参数
    """
    data = request.json or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({"success": False, "error": "缺少 session_id"}), 400

    agent = get_agent()
    session = agent.get_or_create_session(
        session_id=session_id,
        name=data.get('name'),
        description=data.get('description'),
        tags=data.get('tags'),
        model=data.get('model'),
        temperature=data.get('temperature')
    )

    metadata = agent.get_session_metadata(session.session_id) or {}

    return jsonify({
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


@agent_bp.route('/sessions/<session_id>/memories/<memory_id>', methods=['DELETE'])
def delete_memory(session_id, memory_id):
    """删除单条记忆"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": f"会话 {session_id} 不存在"}), 404

    try:
        success = session.delete_memory(memory_id)
        if success:
            return jsonify({"success": True, "message": "记忆已删除"})
        else:
            return jsonify({"success": False, "error": "记忆不存在或删除失败"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@agent_bp.route('/sessions/<session_id>/memories', methods=['DELETE'])
def clear_memories(session_id):
    """清空会话的所有记忆"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": f"会话 {session_id} 不存在"}), 404

    try:
        deleted_count = session.clear_all_memories()
        return jsonify({
            "success": True,
            "message": f"已清空 {deleted_count} 条记忆",
            "deleted_count": deleted_count
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@agent_bp.route('/sessions/<session_id>/memories', methods=['GET'])
def list_memories(session_id):
    """获取会话的所有记忆摘要"""
    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": f"会话 {session_id} 不存在"}), 404

    try:
        memory_ids = session.get_all_memory_ids()
        summaries = {}
        if session._memory_space:
            summaries = session._memory_space.get_all_summaries()

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "total": len(summaries),
                "memories": [
                    {
                        "id": mid,
                        "summary": summary
                    }
                    for mid, summary in summaries.items()
                ]
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========== 聊天接口 ==========
import queue
import threading
import secrets
# ==================== 安全校验相关 ====================
verify_queue = queue.Queue()
pending_verifies = {}  # verify_id → threading.Event
pending_lock = threading.Lock()


@agent_bp.route('/verify', methods=['POST'])
def verify_tool():
    """客户端提交工具安全校验结果"""
    data = request.json or {}
    verify_id = data.get('verify_id')
    is_safe = data.get('is_safe')
    reason = data.get('reason', '')

    # ========== 校验 verify_id ==========
    if not verify_id:
        print(f"[VERIFY] 拒绝: 缺少 verify_id", flush=True)
        return jsonify({"success": False, "error": "缺少 verify_id"}), 400

    with pending_lock:
        pending = pending_verifies.get(verify_id)
        if not pending:
            print(f"[VERIFY] 拒绝: verify_id 不存在或已过期: {verify_id[:8]}...", flush=True)
            return jsonify({"success": False, "error": "verify_id 无效或已过期"}), 400

        pending["result"][0] = is_safe
        pending["result"][1] = reason
        pending["event"].set()
        pending_verifies.pop(verify_id, None)

    print(f"[VERIFY] 通过: verify_id={verify_id[:8]}..., is_safe={is_safe}", flush=True)
    return jsonify({"success": True})


@agent_bp.route('/chat', methods=['POST'])
def chat():
    data = request.json or {}
    message = data.get('message')
    session_id = data.get('session_id')
    model = data.get('model')
    stream = data.get('stream', True)

    if not message:
        return jsonify({"success": False, "error": "消息内容不能为空"}), 400

    if not session_id:
        return jsonify({"success": False, "error": "缺少 session_id 参数"}), 400

    # ========== 定义安全校验回调 ==========
    def safe_verify_callback(verify_id: str, timeouut:int=300) -> tuple[bool, str]:
        """
        安全校验回调
        verify_id: 由调用方生成的 verify_id
        """
        event = threading.Event()
        result = [False, "安全校验超时"]

        with pending_lock:
            pending_verifies[verify_id] = {
                "event": event,
                "result": result
            }

        print(f"[SAFE_VERIFY] 等待确认: verify_id={verify_id[:8]}...", flush=True)

        # 等待前端确认（60秒超时）
        event.wait(timeout=timeouut)

        # 清理
        with pending_lock:
            pending_verifies.pop(verify_id, None)

        return result[0], result[1]

    agent = get_agent()
    session = agent.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": f"会话 {session_id} 不存在"}), 404

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

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
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
            return jsonify({
                "success": True,
                "content": result.get('reply', ''),
                "stats": result.get('stats', {}),
                "session_id": session_id
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500


# ========== 配置 ==========

@agent_bp.route('/config/default-model', methods=['GET'])
def get_default_model():
    agent = get_agent()
    return jsonify({"success": True, "data": {"default_model": agent.get_default_model()}})


@agent_bp.route('/config/default-model', methods=['PUT'])
def set_default_model():
    data = request.json or {}
    model = data.get('model')
    if not model:
        return jsonify({"success": False, "error": "缺少 model 参数"}), 400
    agent = get_agent()
    agent.set_default_model(model)
    return jsonify({"success": True, "message": f"默认模型已设置为 {model}"})


# ========== 认证 ==========

@agent_bp.route('/verify-password', methods=['POST'])
def verify_password():
    data = request.get_json()
    if data and data.get('password') == 'Jhzchfl008!':
        return jsonify({'success': True})
    return jsonify({'success': False}), 401


# ========== 健康检查 ==========

@agent_bp.route('/health', methods=['GET'])
def health():
    agent = get_agent()
    return jsonify({
        "success": True,
        "status": "healthy",
        "gateway": "LLMGateway",
        "total_requests": agent._stats.get("total_requests", 0)
    })

# ========== 配置 ============
# ========== 配置管理 ==========

@agent_bp.route('/config', methods=['GET'])
def get_config():
    """
    获取 Agent 配置

    Query parameters:
        path: 可选，配置路径（如 "executor.max_retries"）
        flatten: 可选，是否扁平化返回（true/false）
        full: 可选，是否返回完整配置（包含所有模块）
    """

    try:
        path = request.args.get('path')
        flatten = request.args.get('flatten', 'false').lower() == 'true'
        full = request.args.get('full', 'false').lower() == 'true'

        if full:
            # 返回完整配置（包含所有模块）
            config_data = agent_config.get_all_config()
            return jsonify({
                "success": True,
                "data": config_data
            })

        # 获取 Agent 配置
        config_value = agent_config.get_config(path, flatten=flatten)

        return jsonify({
            "success": True,
            "data": config_value
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@agent_bp.route('/config', methods=['PUT'])
def update_config():
    """
    更新 Agent 配置

    Request body:
        updates: 必填，更新字段字典
                支持点号路径或嵌套字典
                例如: {"executor.max_retries": 20, "defaults.model": "gpt-4"}
                或: {"executor": {"max_retries": 20}}
        save_to_file: 可选，是否保存到文件，默认 true

    Request body 也可以直接是更新字段，不需要包裹在 updates 中
    """

    try:
        data = request.json or {}

        # 检测是否使用了 updates 包裹
        if 'updates' in data:
            updates = data['updates']
            save_to_file = data.get('save_to_file', True)
        else:
            updates = data
            save_to_file = data.get('save_to_file', True)
            # 移除 save_to_file 字段（如果存在）
            if 'save_to_file' in updates:
                del updates['save_to_file']

        if not updates:
            return jsonify({
                "success": False,
                "error": "没有提供要更新的配置"
            }), 400

        # 执行更新
        print("update config: ", updates)
        print("save to file: ", save_to_file)
        result = agent_config.update_config(updates, save_to_file=save_to_file)

        return jsonify({
            "success": result['success'],
            "message": result['message'],
            "updated_fields": result['updated_fields'],
            "failed_fields": result.get('failed_fields', [])
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@agent_bp.route('/config/reset', methods=['POST'])
def reset_config():
    """
    重置配置为默认值
    """
    try:
        # 安全获取 keep_other_modules
        keep_other_modules = True
        if request.data and request.is_json:
            try:
                data = request.get_json()
                if data:
                    keep_other_modules = data.get('keep_other_modules', True)
            except:
                pass

        result = agent_config.reset_config_to_default(keep_other_modules=keep_other_modules)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@agent_bp.route('/config/reload', methods=['POST'])
def reload_config():
    """
    重新加载配置文件
    """
    try:
        # 安全获取 config_path，避免空请求体报错
        config_path = None
        if request.data and request.is_json:
            try:
                data = request.get_json()
                if data:
                    config_path = data.get('config_path')
            except:
                # 如果解析失败，忽略，使用默认路径
                pass

        result = agent_config.reload_config(config_path)

        if result.get("success"):
            return jsonify({
                "success": True,
                "message": result.get("message", "配置已重新加载")
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("message", "重新加载失败")
            }), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500