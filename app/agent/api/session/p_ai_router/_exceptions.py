"""
_exceptions.py
网关自定义业务异常体系
【渐进迁移方案】
当前：仅定义异常类，现有代码依旧抛出原生异常，不改动原有抛出逻辑
后续迭代：逐步替换各处原生Exception为对应业务异常
顶层路由统一捕获本模块异常，标准化返回OpenAI格式错误报文
"""

class RouterBaseError(Exception):
    """AI 转发网关所有业务异常基类
    顶层捕获可通过 isinstance 判断是否为网关主动抛出业务错误
    """
    pass


class ModelNotFoundError(RouterBaseError):
    """resolve_model 找不到指定模型别名"""
    pass


class UpstreamRequestError(RouterBaseError):
    """上游服务商请求异常：连接失败、超时、非200状态码等网络/远端故障"""
    pass


class InvalidParamError(RouterBaseError):
    """请求入参校验失败、格式非法、参数冲突"""
    pass


class RateLimitExceedError(RouterBaseError):
    """新增：限流触发快速失败（配套_limiter快速失败模式）"""
    pass

"""
# _router_api 顶层捕获伪代码
try:
    await handle_request()
except ModelNotFoundError as e:
    return openai_error_response(message=str(e), code="model_not_found", status=404)
except InvalidParamError as e:
    return openai_error_response(message=str(e), code="invalid_parameter", status=400)
except UpstreamRequestError as e:
    return openai_error_response(message=str(e), code="upstream_failure", status=502)
except RateLimitExceedError as e:
    return openai_error_response(message=str(e), code="rate_limit_exceeded", status=429)
except Exception as e:
    # 兜底未知异常
    ...
"""