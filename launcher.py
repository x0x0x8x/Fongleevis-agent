#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fongleevis Agent Launcher
自动检测环境、创建虚拟环境、安装依赖并启动 Agent
"""

import os
import sys
import subprocess
import platform
import venv
from pathlib import Path

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_info(msg):   print(f"{Colors.CYAN}[INFO]{Colors.RESET} {msg}")
def print_ok(msg):     print(f"{Colors.GREEN}[OK]{Colors.RESET} {msg}")
def print_error(msg):  print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}")
def print_warn(msg):   print(f"{Colors.YELLOW}[WARN]{Colors.RESET} {msg}")
def print_step(msg):   print(f"\n{Colors.BOLD}{Colors.BLUE}▶ {msg}{Colors.RESET}")

def get_venv_paths():
    project_dir = Path(__file__).parent.absolute()
    venv_dir = project_dir / '.venv'
    if platform.system() == 'Windows':
        python_exe = venv_dir / 'Scripts' / 'python.exe'
        pip_exe = venv_dir / 'Scripts' / 'pip.exe'
    else:
        python_exe = venv_dir / 'bin' / 'python'
        pip_exe = venv_dir / 'bin' / 'pip'
    return venv_dir, python_exe, pip_exe

def create_venv(venv_dir):
    if venv_dir.exists():
        print_info("虚拟环境已存在，跳过创建")
        return True
    print_step("创建虚拟环境...")
    try:
        venv.create(venv_dir, with_pip=True, clear=False)
        print_ok("虚拟环境创建成功")
        return True
    except Exception as e:
        print_error(f"创建虚拟环境失败: {e}")
        return False

def get_requirements_file():
    """根据平台返回对应的 requirements 文件"""
    project_dir = Path(__file__).parent.absolute()
    
    system = platform.system()
    
    if system == 'Windows':
        req_file = project_dir / 'requirements_windows.txt'
        if req_file.exists():
            return req_file
    elif system == 'Linux':
        req_file = project_dir / 'requirements_linux.txt'
        if req_file.exists():
            return req_file
    # macOS 也走 Linux 的
    elif system == 'Darwin':
        req_file = project_dir / 'requirements_linux.txt'
        if req_file.exists():
            return req_file
    
    # 如果平台专用文件不存在，回退到通用 requirements.txt
    return project_dir / 'requirements.txt'

def install_dependencies(pip_exe):
    project_dir = Path(__file__).parent.absolute()
    
    req_file = get_requirements_file()
    
    if not req_file.exists():
        print_error(f"未找到依赖文件: {req_file.name}")
        print_info("请创建对应的 requirements 文件")
        return False
    
    print_step("安装依赖...")
    print_info(f"使用依赖文件: {req_file.name}")
    print_info("使用 PyPI 官方源...")
    print("")
    
    result = subprocess.run([
        str(pip_exe), "install", "-r", str(req_file),
        "--timeout", "300"
    ])
    
    if result.returncode != 0:
        print_error("依赖安装失败")
        return False
    
    print_ok("依赖安装完成")
    return True

def find_entry_file():
    project_dir = Path(__file__).parent.absolute()
    for entry in ['run.py', 'main.py']:
        if (project_dir / entry).exists():
            return entry
    return None

def ensure_venv_available():
    try:
        import venv
        return True
    except ImportError:
        print_error("Python venv 模块不可用")
        if platform.system() == 'Linux':
            print("Ubuntu/Debian: sudo apt install python3-venv")
            print("Fedora:       sudo dnf install python3-virtualenv")
        input("按 Enter 退出...")
        sys.exit(1)

def main():
    print(f"""
{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════╗
║   Fongleevis Agent Launcher        ║
║   Autonomous Agent Runtime         ║
╚═══════════════════════════════════════╝{Colors.RESET}
    """)
    
    print_info(f"操作系统: {platform.system()}")
    ensure_venv_available()
    print_ok(f"Python {platform.python_version()}")
    
    venv_dir, python_exe, pip_exe = get_venv_paths()
    
    if not create_venv(venv_dir):
        input("按 Enter 退出...")
        sys.exit(1)
    
    if not python_exe.exists():
        print_error("虚拟环境创建失败")
        input("按 Enter 退出...")
        sys.exit(1)
    
    if not install_dependencies(pip_exe):
        print_warn("依赖安装失败，尝试继续启动...")
    
    entry_file = find_entry_file()
    if not entry_file:
        print_error("未找到入口文件 (run.py 或 main.py)")
        input("按 Enter 退出...")
        sys.exit(1)
    print_info(f"入口文件: {entry_file}")
    
    print_step("启动 Fongleevis Agent...")
    print(f"{Colors.BOLD}{Colors.GREEN}════════════════════════════════════════{Colors.RESET}\n")
    
    try:
        # 以项目目录为工作目录拉起入口，确保 run.py 相对路径与 sys.path 正确
        subprocess.run([str(python_exe), str(entry_file)], cwd=str(Path(__file__).parent.absolute()))
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}用户中断{Colors.RESET}")
    except Exception as e:
        print_error(f"启动失败: {e}")
    
    print(f"\n{Colors.BOLD}{Colors.GREEN}════════════════════════════════════════{Colors.RESET}")
    print_info("Agent 已退出")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}用户中断{Colors.RESET}")
    except Exception as e:
        print_error(f"错误: {e}")
    finally:
        input("按 Enter 退出...")