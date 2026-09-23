# -*- coding: utf-8 -*-
"""书库: 读取外部电子书仓库, 把里面的书导入本地书架。

这是整套代码里**唯一**允许读取 BOOKS_DIR 之外的地方, 所以校验比别处严:

- 路径 realpath 之后必须仍是个目录, 且不能落在本仓库内部(否则会自己导入自己)
- 路径比较统一走 normcase —— Windows 文件系统大小写不敏感, 直接 startswith
  是漏的, ``d:\\...`` 这种写法能绕过 ``D:\\...`` 的前缀检查
- 文件名一律取 basename, 拒绝任何相对路径成分
- 导入目的地恒为 BOOKS_DIR, 不由调用方指定

注意 constants.SANDBOX_DIRS 一个字节都没动。那是被所有 Agent 工具共用的全局
常量, 为了读一个书库去放宽它, 等于把所有工具的范围(包括能写 ai_config.json
的那部分)一起放宽。
"""
import os
import re
import json
import time
import shutil

from .constants import (
    ROOT, BOOKS_DIR, DATA_DIR, SUPPORTED_EXT,
    LIBRARY_SOURCE_FILE, default_library_source,
)

__all__ = [
    "load_sources_config", "save_sources_config", "active_source_path",
    "validate_source", "scan_source", "import_book", "safe_dest_in_books",
]

MAX_IMPORT_BYTES = 512 * 1024 * 1024   # 单本 512 MB 上限


def _norm(p):
    """统一的大小写不敏感绝对路径, 用于比较。"""
    return os.path.normcase(os.path.realpath(p))


# --------------------------------------------------------------------------- #
#  配置: data/library_source.json
# --------------------------------------------------------------------------- #
def load_sources_config():
    if not os.path.exists(LIBRARY_SOURCE_FILE):
        return {"path": default_library_source()}
    try:
        with open(LIBRARY_SOURCE_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {"path": default_library_source()}
    if not isinstance(cfg, dict):
        return {"path": default_library_source()}
    cfg.setdefault("path", default_library_source())
    return cfg


def save_sources_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = LIBRARY_SOURCE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIBRARY_SOURCE_FILE)


def active_source_path():
    return (load_sources_config().get("path") or "").strip()


# --------------------------------------------------------------------------- #
#  校验
# --------------------------------------------------------------------------- #
def validate_source(path):
    """检查一个书库路径能不能用。返回 (ok, 说明)。"""
    if not path or not str(path).strip():
        return False, "路径为空"
    if not os.path.isdir(path):
        return False, "不是一个目录"
    real = _norm(path)
    root = _norm(ROOT)
    if real == root or real.startswith(root + os.sep):
        return False, "不能指向灵台仓库自己"
    # 至少得有 files/ 或 books/, 防止随手填个 C:\ 就被当成书库
    if not (os.path.isdir(os.path.join(path, "files"))
            or os.path.isdir(os.path.join(path, "books"))):
        return False, "目录里既没有 files/ 也没有 books/"
    return True, ""


# --------------------------------------------------------------------------- #
#  扫描
# --------------------------------------------------------------------------- #
def _parse_cards(cards_dir):
    """读 books/*.md 卡片, 抽出标题/作者, 以及它指向哪些文件。

    卡片只是用来给文件补标题和作者的, 不是书目的来源 ——
    卡片写着"已下载"但文件并不存在的情况是真实存在的。
    """
    cards = []
    if not os.path.isdir(cards_dir):
        return cards
    for name in sorted(os.listdir(cards_dir)):
        if not name.lower().endswith(".md"):
            continue
        try:
            with open(os.path.join(cards_dir, name), "r", encoding="utf-8",
                      errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        title = ""
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip().strip("《》").strip()
                break
        author = ""
        m = re.search(r'^\|\s*作者\s*\|\s*([^|]+)\|', text, re.M)
        if m:
            author = m.group(1).strip()
        cards.append({
            "card": name,
            "title": title,
            "author": author,
            "refs": _card_refs(text),
        })
    return cards


def _card_refs(text):
    """抽出卡片里指向的文件名。

    两种写法都要认:
      - ``files/LLMBook.pdf`` 直接给出文件名
      - ``files/AI-Agents-in-Depth-zh-CN.pdf`` + ``.epub``
        第二个只写了扩展名, 得拼到前一个候选的词干上
    """
    refs = []
    for tok in re.findall(r'`([^`]+)`', text):
        tok = tok.strip()
        if tok.startswith("files/"):
            refs.append(os.path.basename(tok))
        elif tok.startswith(".") and refs:
            refs.append(os.path.splitext(refs[-1])[0] + tok)
    return refs


def _match_card(cards, filename):
    for c in cards:
        if filename in c["refs"]:
            return c
    return None


def scan_source(path):
    """列出书库里**文件确实存在**的书。

    以 files/ 的实际内容为准。当前书架已有同名文件的会标 imported。
    """
    files_dir = os.path.join(path, "files")
    if not os.path.isdir(files_dir):
        return []
    cards = _parse_cards(os.path.join(path, "books"))
    try:
        existing = set(os.listdir(BOOKS_DIR)) if os.path.isdir(BOOKS_DIR) else set()
    except OSError:
        existing = set()

    items = []
    for name in sorted(os.listdir(files_dir), key=str.lower):
        full = os.path.join(files_dir, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXT:
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        card = _match_card(cards, name)
        items.append({
            "file": name,
            "format": SUPPORTED_EXT[ext],
            "size": size,
            "title": (card or {}).get("title") or os.path.splitext(name)[0],
            "author": (card or {}).get("author") or "",
            "card": (card or {}).get("card") or "",
            "imported": name in existing,
        })
    return items


# --------------------------------------------------------------------------- #
#  导入
# --------------------------------------------------------------------------- #
def safe_dest_in_books(fname):
    """把文件名落成 BOOKS_DIR 下一个不冲突的路径。返回 (路径, 最终文件名)。"""
    fname = os.path.basename(fname)
    stem, ext = os.path.splitext(fname)
    dest = os.path.join(BOOKS_DIR, fname)
    if not os.path.exists(dest):
        return dest, fname
    for i in range(2, 100):
        cand = "{}_{}{}".format(stem, i, ext)
        dest = os.path.join(BOOKS_DIR, cand)
        if not os.path.exists(dest):
            return dest, cand
    raise RuntimeError("同名文件过多, 无法命名")


def import_book(source_path, filename):
    """把书库里的一个文件复制进书架。返回 (info, error)。

    外部文件必须先物理复制进 BOOKS_DIR —— book_path() 会把任何路径压成
    basename 再拼回 BOOKS_DIR, 外部路径根本引用不了。
    """
    ok, why = validate_source(source_path)
    if not ok:
        return None, why

    name = os.path.basename(filename or "")
    if not name or name != (filename or "").replace("\\", "/").split("/")[-1]:
        return None, "文件名非法"
    ext = os.path.splitext(name)[1].lower()
    if ext not in SUPPORTED_EXT:
        return None, "不支持的格式: {}".format(ext or "(无扩展名)")

    src = os.path.join(source_path, "files", name)
    real_src = _norm(src)
    real_root = _norm(os.path.join(source_path, "files"))
    if not real_src.startswith(real_root + os.sep):
        return None, "文件越界"
    if not os.path.isfile(src):
        return None, "文件不存在"

    try:
        size = os.path.getsize(src)
    except OSError:
        return None, "读不到文件大小"
    if size > MAX_IMPORT_BYTES:
        return None, "文件过大 ({} MB)".format(size // (1024 * 1024))

    os.makedirs(BOOKS_DIR, exist_ok=True)
    dest, final_name = safe_dest_in_books(name)
    try:
        shutil.copy2(src, dest)
    except Exception as e:
        return None, "复制失败: {}".format(e)

    return {
        "bookId": final_name,      # 裸文件名, 与 scan_books() 的 id 一致
        "fileName": final_name,
        "size": size,
    }, None
