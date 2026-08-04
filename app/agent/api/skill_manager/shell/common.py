from abc import ABC, abstractmethod
from typing import TypedDict, Optional, Any, Literal

class SkillResult(TypedDict):
    success: bool
    error_message: Optional[str]
    data: Optional[str]

class BaseSkill(ABC):
    """
    所有Skill公共注册接口
    """
    name: str
    @abstractmethod
    def init(self):
        #初始化
        pass


    @abstractmethod
    def execute(self, **kwargs) -> SkillResult:
        #执行技能
        pass

    @abstractmethod
    def drop(self):
        #销毁
        pass

    @abstractmethod
    def get_tool_description(self) -> dict:
        #获取LLM格式的function结构
        pass