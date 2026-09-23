# -*- coding: utf-8 -*-
"""路径常量与沙箱工具 (零第三方依赖)。"""
import os

__all__ = [
    "ROOT", "BOOKS_DIR", "STATIC_DIR", "DATA_DIR",
    "LIBRARY_FILE", "AI_CONFIG_FILE", "MEMORY_FILE",
    "SANDBOX_DIRS", "validate_sandbox_path", "SUPPORTED_EXT",
    "TEXT_CACHE_DIR", "LIBRARY_SOURCE_FILE",
    "DEFAULT_AI_ENDPOINT", "DEFAULT_AI_MODEL", "default_library_source",
    "TEXT_DEFAULT_CHARS", "TEXT_MAX_CHARS",
]

# constants.py 位于 <root>/server/ 下, 向上两级才是仓库根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS_DIR = os.path.join(ROOT, "books")
STATIC_DIR = os.path.join(ROOT, "static")
DATA_DIR = os.path.join(ROOT, "data")
LIBRARY_FILE = os.path.join(DATA_DIR, "library.json")
AI_CONFIG_FILE = os.path.join(DATA_DIR, "ai_config.json")
MEMORY_FILE = os.path.join(DATA_DIR, "agent_memory.json")
LIBRARY_SOURCE_FILE = os.path.join(DATA_DIR, "library_source.json")

# 文字抽取缓存: 前端 pdf.js 抽取的结果、后端 PyMuPDF 抽取的结果都落在这里。
# 放在 DATA_DIR 内 → 天然在沙箱范围内, 不需要放宽 SANDBOX_DIRS。
TEXT_CACHE_DIR = os.path.join(DATA_DIR, "text_cache")

# ---- AI 默认值 (仅作默认, 设置面板可覆盖) ----
DEFAULT_AI_ENDPOINT = "https://api.deepseek.com/v1"
DEFAULT_AI_MODEL = "deepseek-flash"

# 单次取书正文的长度 (字符)。默认模型上下文 1M token, 一本几百页的书也不过
# 几十万字符, 所以默认值可以给得很宽松; TEXT_MAX_CHARS 是硬上限, 超了钳制。
TEXT_DEFAULT_CHARS = 24000
TEXT_MAX_CHARS = 200000


def default_library_source():
    """书库来源的默认值。

    不硬编码任何绝对路径 (这个仓库可能被公开)。策略: 看灵台仓库的
    同级目录下有没有一个叫 ebook-library 的目录, 有就用它。
    """
    guess = os.path.join(os.path.dirname(ROOT), "ebook-library")
    return guess if os.path.isdir(guess) else ""

# ---- Agent 沙箱: 所有文件操作限制在这些目录内 ----
SANDBOX_DIRS = [os.path.abspath(BOOKS_DIR), os.path.abspath(DATA_DIR)]

def validate_sandbox_path(path):
    """验证路径在沙箱目录内, 防止目录遍历攻击"""
    if not path:
        return False
    abs_path = os.path.abspath(path)
    for sandbox_dir in SANDBOX_DIRS:
        if abs_path.startswith(sandbox_dir + os.sep) or abs_path == sandbox_dir:
            return True
    return False

SUPPORTED_EXT = {
    ".pdf": "pdf", ".epub": "epub", ".txt": "txt",
    ".mobi": "mobi", ".azw3": "azw3", ".fb2": "fb2",
    ".cbz": "cbz", ".cbr": "cbr", ".docx": "docx",
}

for d in (BOOKS_DIR, STATIC_DIR, DATA_DIR, TEXT_CACHE_DIR):
    os.makedirs(d, exist_ok=True)

