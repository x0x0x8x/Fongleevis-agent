# app/ai_service/config.py
from pathlib import Path

# 直接计算 BASE_DIR
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ==================== AI 服务配置 ====================
OLLAMA_BASE = "http://localhost:11434"
NVIDIA_API_KEY = "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"

DEBUG_PRINT_GATEWAY = False

# ==================== 模型路由表 ====================
MODEL_ROUTING_TABLE = {
    "mistral-nemo-minitron-8b": (
        "nvidia",
        "nvidia/mistral-nemo-minitron-8b-base",
        "completion"
    ),
    "nemotron-3-nano-30b-a3b": (
        "nvidia",
        "nvidia/nemotron-3-nano-30b-a3b",
        "completion"
    ),
    "local-qwen3:8b": (
        "local",
        "qwen3:8b",
        "chat"
    ),
    "llama-3.1-nemotron-ultra-253b-v1": (
        "nvidia",
        "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "completion"
    ),
    "minimax-m2.5": (
        "nvidia",
        "minimaxai/minimax-m2.5",
        "completion"
    ),
    "minimaxai/minimax-m2.5": (
        "nvidia",
        "minimaxai/minimax-m2.5",
        "completion"
    ),
    "qwen3-235b-a22b": (
        "nvidia",
        "qwen/qwen3-235b-a22b",
        "completion"
    ),
}

DEFAULT_MODEL = "mistral-nemo-minitron-8b"