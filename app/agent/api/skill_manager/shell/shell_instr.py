from typing import Type
import platform
from .common import BaseSkill, SkillResult
#from app.agent.api.skill_manager.common import BaseSkill, SkillResult

if platform.system() == "Windows":
    from .myshell import PtyShell
    #from app.agent.api.skill_manager.shell.myshell import PtyShell
else:
    from .myshell_linux import PtyShell

class ShellSkill(BaseSkill):
    name = "shell"
    def __init__(self):
        self.shell = None

    def init(self, **kwargs):
        base_command = kwargs.get("base_command")
        self.shell = PtyShell(base_cmd=base_command)
        pass

    def execute(self, **kwargs) -> SkillResult:
        if self.shell is None:
            return SkillResult(
                success=False,
                error_message="No shell available",
                data=None,
            )
        command = kwargs.get("command")
        if not command or command.strip() == "":
            return SkillResult(
                success=False,
                error_message="No command provided or command is empty",
                data=None,
            )

        try:
            output, err = self.shell.send(command)
            if err:
                return SkillResult(
                    success=False,
                    error_message="Shell Error",
                    data=None,
                )

            return SkillResult(
                success=True,
                error_message=None,
                data=output,
            )

        except Exception as e:
            print("shell skill error:", e)
            return SkillResult(
                success=False,
                error_message="Shell Error",
                data=None,
            )

    def get_tool_description(self) -> dict:
        """返回 LLM 格式的 function 结构工具说明"""
        if platform.system() == "Windows":
            description = "windows环境下的cmd终端。只支持cmd命令.不得使用linux指令."
        else:
            description = "linux环境下的shell终端。不得使用windows指令."

        return {
            "type": "function",
            "function": {
                "name": ShellSkill.name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "直接输入命令行窗口的命令."
                        }
                    },
                    "required": ["command"]
                }
            }
        }

    def drop(self):
        self.shell.drop()
        pass


if __name__ == "__main__":
    shell = ShellSkill()
    shell.init()
    resp = shell.execute(command="echo hi")
    print(resp)
    pass
