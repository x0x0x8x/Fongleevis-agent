#!/bin/bash

echo "====================================="
echo "  Fongleevis Agent Launcher"
echo "====================================="
echo ""

# ========================================
# 查找 Python
# ========================================
PYTHON_EXE=""

# 1. 检查 python3
if command -v python3 &> /dev/null; then
    PYTHON_EXE="python3"
# 2. 检查 python
elif command -v python &> /dev/null; then
    PYTHON_EXE="python"
else
    echo "[ERROR] Python not found!"
    echo ""
    echo "====================================="
    echo "  请安装 Python 3.10 或更高版本"
    echo "====================================="
    echo ""
    echo "  Ubuntu/Debian:"
    echo "    sudo apt update"
    echo "    sudo apt install python3 python3-venv python3-pip"
    echo ""
    echo "  Fedora/RHEL:"
    echo "    sudo dnf install python3 python3-virtualenv python3-pip"
    echo ""
    echo "  macOS:"
    echo "    brew install python3"
    echo ""
    echo "  或从官网下载: https://www.python.org/downloads/"
    echo ""
    echo "====================================="
    echo ""
    read -p "按 Enter 退出..."
    exit 1
fi

# ========================================
# Python 找到了，检查版本
# ========================================
echo "[OK] Found Python: $PYTHON_EXE"
$PYTHON_EXE --version

# 检查版本 >= 3.10
$PYTHON_EXE -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Python version too old! Need 3.10+"
    echo ""
    read -p "按 Enter 退出..."
    exit 1
fi

echo "[OK] Python version >= 3.10"
echo ""

# ========================================
# 检查 venv 是否可用
# ========================================
if ! $PYTHON_EXE -c "import venv" 2>/dev/null; then
    echo ""
    echo "[ERROR] Python venv module not available!"
    echo ""
    echo "====================================="
    echo "  请安装 python3-venv"
    echo "====================================="
    echo ""
    
    if [ -f /etc/debian_version ]; then
        echo "  Ubuntu/Debian 用户执行："
        echo "    sudo apt update"
        echo "    sudo apt install python3-venv"
    elif [ -f /etc/fedora-release ]; then
        echo "  Fedora/RHEL 用户执行："
        echo "    sudo dnf install python3-virtualenv"
    elif [ -f /etc/arch-release ]; then
        echo "  Arch 用户执行："
        echo "    sudo pacman -S python-virtualenv"
    else
        echo "  请根据你的系统安装 python3-venv"
        echo "    Ubuntu/Debian: sudo apt install python3-venv"
        echo "    Fedora:       sudo dnf install python3-virtualenv"
        echo "    Arch:         sudo pacman -S python-virtualenv"
    fi
    
    echo ""
    echo "  安装完成后重新运行本脚本"
    echo "====================================="
    echo ""
    read -p "按 Enter 退出..."
    exit 1
fi

echo "[OK] venv module available"
echo ""

# ========================================
# 执行 launcher.py
# ========================================
$PYTHON_EXE launcher.py

echo ""
read -p "按 Enter 退出..."