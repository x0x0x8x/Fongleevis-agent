from typing import List, Dict, Optional, Any
from .common import BaseSkill, SkillResult
from pathlib import Path
import importlib.util
import sys
import json

class SkillManager:
    def __init__(self):
        self.base_skills: List[BaseSkill] = []
        self.dynamic_skills: List[BaseSkill] = []
        self.all_skills: Dict[str, BaseSkill] = {}

        base_tool_cnt = self._load_base_skills()
        if base_tool_cnt == 0:
            raise RuntimeError("No base skills loaded! System cannot start without base skills.")
        self._load_dynamic_skills()
        skill_names = list(self.all_skills.keys())
        print(f"Loaded {len(skill_names)} skills: {', '.join(skill_names)}")

    def _load_dynamic_skills(self) -> int:
        """加载动态技能"""
        current_dir = Path(__file__).parent
        config_path = current_dir / "dynamic_skills.json"

        if not config_path.exists():
            print(f"Warning: Dynamic skills config not found: {config_path}")
            return 0

        config = self._load_skills_from_config(config_path)

        for skill_name, skill_config in config.items():
            # 检查是否启用
            if not skill_config.get("enabled", True):
                print(f"Skipping disabled dynamic skill: {skill_name}")
                continue

            skill_path = skill_config.get("path")
            if not skill_path:
                print(f"Warning: Dynamic skill '{skill_name}' has no path, skipping")
                continue

            # 加载技能
            skill_instance = self._load_skill_from_path(skill_name, skill_path)
            if skill_instance is not None:
                self.dynamic_skills.append(skill_instance)

        # 重新聚合所有技能
        self._aggregate_skills()
        return len(self.dynamic_skills)

    @staticmethod
    def _load_skill_from_path(skill_name: str, skill_path: str) -> Optional[BaseSkill]:
        """从指定路径加载单个技能（支持依赖）"""
        try:
            path_obj = Path(skill_path)
            if not path_obj.exists():
                print(f"Warning: Skill path not found: {skill_path}")
                return None

            init_file = path_obj / "__init__.py"
            if not init_file.exists():
                print(f"Warning: No __init__.py found in {skill_path}")
                return None

            # 1. 添加技能父目录到 sys.path
            # 例如:
            # skills/
            #   browser/
            #       __init__.py
            #       common.py
            #
            # 加入 skills/
            parent_dir = str(path_obj.parent)

            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)

            # 2. 保留项目根目录支持绝对导入
            project_root = path_obj.parent.parent.parent.parent

            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            # 3. 将 skill 目录作为 package 加载
            import importlib.util

            package_name = skill_name

            spec = importlib.util.spec_from_file_location(
                package_name,
                init_file,
                submodule_search_locations=[str(path_obj)]
            )

            if spec is None or spec.loader is None:
                print(f"Warning: Cannot load module from {skill_path}")
                return None

            module = importlib.util.module_from_spec(spec)

            # 注册 package
            sys.modules[package_name] = module

            # 执行 __init__.py
            spec.loader.exec_module(module)

            # 获取 Skill 类
            if not hasattr(module, "Skill"):
                print(f"Warning: Module '{skill_name}' has no 'Skill' class")
                return None

            skill_class = getattr(module, "Skill")
            skill_instance = skill_class()

            # 保存路径，方便后续卸载检查
            skill_instance._path = skill_path

            if hasattr(skill_instance, "init"):
                skill_instance.init()

            print(f"Loaded skill: {skill_name} from {skill_path}")
            return skill_instance

        except Exception as e:
            print(f"Error loading skill '{skill_name}': {e}")
            import traceback
            traceback.print_exc()
            return None

    @staticmethod
    def _load_skills_from_config(config_path: Path) -> Dict[str, Any]:
        """从配置文件读取技能配置"""
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_base_skills(self) -> int:
        """加载基础技能"""
        current_dir = Path(__file__).parent
        config_path = current_dir / "base_skills.json"

        config = self._load_skills_from_config(config_path)

        for skill_name, skill_config in config.items():
            # 检查是否启用
            if not skill_config.get("enabled", True):
                print(f"Skipping disabled skill: {skill_name}")
                continue

            skill_path = skill_config.get("path")
            if not skill_path:
                print(f"Warning: Skill '{skill_name}' has no path, skipping")
                continue

            # 加载技能
            skill_instance = self._load_skill_from_path(skill_name, skill_path)
            if skill_instance is not None:
                self.base_skills.append(skill_instance)

        return len(self.base_skills)

    def _aggregate_skills(self) -> Dict[str, BaseSkill]:
        """聚合所有技能为一个字典"""
        skills = {}
        for skill in self.base_skills:
            if not hasattr(skill, 'name'):
                raise AttributeError(f"Skill {skill} has no 'name' attribute")
            if skill.name in skills:
                raise ValueError(f"Duplicate skill name in base_skills: {skill.name}")
            skills[skill.name] = skill
        for skill in self.dynamic_skills:
            if not hasattr(skill, 'name'):
                raise AttributeError(f"Skill {skill} has no 'name' attribute")
            if skill.name in skills:
                raise ValueError(f"Duplicate skill name in dynamic_skills: {skill.name}")
            skills[skill.name] = skill
        self.all_skills = skills
        return skills

    def regist_new_dynamic_skills(self, name: str) -> bool:
        """注册新的动态技能（单个）"""
        current_dir = Path(__file__).parent
        config_path = current_dir / "dynamic_skills.json"

        if not config_path.exists():
            print(f"Warning: Dynamic skills config not found: {config_path}")
            return False

        try:
            config = self._load_skills_from_config(config_path)
        except Exception as e:
            print(f"Error loading config: {e}")
            return False

        # 查找指定名称的技能配置
        if name not in config:
            print(f"Warning: Skill '{name}' not found in dynamic_skills.json")
            return False

        skill_config = config[name]

        # 检查是否启用
        if not skill_config.get("enabled", True):
            print(f"Warning: Skill '{name}' is disabled in config")
            return False

        skill_path = skill_config.get("path")
        if not skill_path:
            print(f"Warning: Skill '{name}' has no path in config")
            return False

        # 检查是否已存在同名技能
        if name in self.all_skills:
            print(f"Warning: Skill '{name}' already exists, skipping")
            return False

        # 检查路径是否已被其他技能使用
        for existing_skill in self.dynamic_skills:
            if hasattr(existing_skill, '_path') and existing_skill._path == skill_path:
                print(f"Warning: Path '{skill_path}' already used by skill '{existing_skill.name}', skipping")
                return False

        # 加载技能
        skill_instance = self._load_skill_from_path(name, skill_path)
        if skill_instance is None:
            print(f"Warning: Failed to load skill '{name}' from {skill_path}")
            return False

        # 添加到动态技能列表
        self.dynamic_skills.append(skill_instance)

        # 重新聚合所有技能
        self._aggregate_skills()

        print(f"Successfully registered dynamic skill: {name}")
        return True

    def unregist_dynamic_skills(self, name: str) -> bool:
        """注销动态技能（单个）"""
        # 检查技能是否存在
        if name not in self.all_skills:
            print(f"Warning: Skill '{name}' not found")
            return False

        # 检查是否是基础技能（基础技能不允许注销）
        for skill in self.base_skills:
            if skill.name == name:
                print(f"Warning: Cannot unregister base skill '{name}'")
                return False

        # 从 dynamic_skills 中移除
        for i, skill in enumerate(self.dynamic_skills):
            if skill.name == name:
                # 清理技能资源
                if hasattr(skill, 'drop'):
                    try:
                        skill.drop()
                    except Exception as e:
                        print(f"Warning: Error dropping skill '{name}': {e}")

                # 从列表中移除
                removed = self.dynamic_skills.pop(i)
                print(f"Removed dynamic skill: {name}")

                # 重新聚合
                self._aggregate_skills()
                return True

        print(f"Warning: Skill '{name}' not found in dynamic_skills")
        return False

    def execute(self, skill_name: str, **kwargs) -> SkillResult:
        """执行指定技能"""
        skill = self.all_skills.get(skill_name)

        if skill is None:
            return SkillResult(
                success=False,
                error_message=f"Skill not found: {skill_name}",
                data=None,
            )
        return skill.execute(**kwargs)

    def get_all_tool_descriptions(self) -> List[dict]:
        """获取所有技能的工具描述（用于 LLM）"""
        descriptions = []
        for skill in self.all_skills.values():
            if not hasattr(skill, 'get_tool_description'):
                raise AttributeError(f"Skill '{skill.name}' does not have 'get_tool_description' method")
            descriptions.append(skill.get_tool_description())
        return descriptions

    def drop(self):
        """清理所有技能资源"""
        for skill in self.base_skills:
            if hasattr(skill, 'drop'):
                skill.drop()
        for skill in self.dynamic_skills:
            if hasattr(skill, 'drop'):
                skill.drop()


# 全局实例
skill_manager: Optional[SkillManager] = None

def init_skill_manager():
    """初始化技能管理器"""
    global skill_manager
    if skill_manager is None:
        skill_manager = SkillManager()
    return skill_manager

# 模块加载时自动初始化
init_skill_manager()