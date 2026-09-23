# -*- coding: utf-8 -*-
"""数据层: 图书馆 / 回收站 / 笔记 / 进度存储, 以及书籍文本提取等工具函数。"""
import os
import sys
import json
import time
import re
import html as html_mod
import zipfile
import urllib.parse

from .constants import *
from . import textcache

__all__ = [
    "load_library", "save_library", "load_ai_config", "save_ai_config",
    "scan_books", "_format_size", "book_path", "get_all_categories",
    "TRASH_RETENTION_SECONDS", "clean_expired_trash", "restore_from_trash",
    "permanently_delete_from_trash", "extract_book_text", "get_book_meta",
    "read_book_text", "pdf_extract_backend",
]

# --------------------------------------------------------------------------- #
#  数据层
# --------------------------------------------------------------------------- #
def load_library():
    if not os.path.exists(LIBRARY_FILE):
        return {"books": {}, "trash": {}}
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"books": {}, "trash": {}}
    # 确保必要字段存在 (兼容旧版 library.json)
    if "books" not in data:
        data["books"] = {}
    # trash: {book_id: {"title": str, "deletedAt": timestamp, "originalCategory": str, "path": str}}
    if "trash" not in data:
        data["trash"] = {}
    return data


def save_library(data):
    tmp = LIBRARY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIBRARY_FILE)


def load_ai_config():
    if not os.path.exists(AI_CONFIG_FILE):
        return {"api_key": "", "endpoint": "", "model": ""}
    try:
        with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"api_key": "", "endpoint": "", "model": ""}


def save_ai_config(cfg):
    tmp = AI_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AI_CONFIG_FILE)


def scan_books():
    result = []
    if not os.path.isdir(BOOKS_DIR):
        return result
    for name in sorted(os.listdir(BOOKS_DIR), key=str.lower):
        full = os.path.join(BOOKS_DIR, name)
        if os.path.isdir(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXT:
            continue
        st = os.stat(full)
        result.append({
            "id": name, "name": name, "ext": ext,
            "format": SUPPORTED_EXT[ext], "size": st.st_size, "mtime": int(st.st_mtime),
        })
    return result


def _format_size(n):
    if n < 1024:
        return "{} B".format(n)
    elif n < 1024 * 1024:
        return "{:.1f} KB".format(n / 1024)
    else:
        return "{:.1f} MB".format(n / (1024 * 1024))


def book_path(book_id):
    """安全获取书籍路径 (沙箱验证: 防止目录遍历)"""
    name = urllib.parse.unquote(book_id)
    name = os.path.basename(name)
    if not name:
        return None
    full = os.path.join(BOOKS_DIR, name)
    full = os.path.abspath(full)
    if not validate_sandbox_path(full):
        return None
    if not os.path.isfile(full):
        return None
    return full


def get_book_meta(lib, book_id):
    """取一本书的元数据, 兼容历史上被写坏成 URL 编码的 key。

    download_book 曾经用 urllib.parse.quote(fname) 当 library.json 的 key,
    而 scan_books() 返回的 id 是裸文件名 —— 于是中文/空格书名的 meta 查不到,
    标题静默退化成文件名。这里兜一下, 让已经写坏的历史条目也能恢复正确标题,
    不需要写迁移脚本。
    """
    books = lib.get("books", {})
    meta = books.get(book_id)
    if meta is None:
        quoted = urllib.parse.quote(book_id)
        if quoted != book_id:
            meta = books.get(quoted)
    return meta or {}


def get_all_categories():
    """从 library.json 收集所有分类"""
    lib = load_library()
    cats = lib.get("categories", [])
    # 也扫描书籍自定义分类
    for meta in lib.get("books", {}).values():
        c = meta.get("category")
        if c and c not in cats:
            cats.append(c)
    return sorted(cats)


# --------------------------------------------------------------------------- #
#  回收站 (trash)
# --------------------------------------------------------------------------- #
TRASH_RETENTION_SECONDS = 30 * 86400  # 回收站保留 30 天


def clean_expired_trash():
    """清理超过 30 天的回收站项目 (永久删除文件并移出 trash)"""
    lib = load_library()
    trash = lib.get("trash", {})
    if not trash:
        return []
    now = int(time.time())
    expired = []
    for bid, info in list(trash.items()):
        if now - info.get("deletedAt", 0) > TRASH_RETENTION_SECONDS:
            # 永久删除文件
            try:
                p = info.get("path", "")
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
            trash.pop(bid, None)
            expired.append(bid)
    if expired:
        save_library(lib)
    return expired


def restore_from_trash(book_id):
    """从回收站恢复一本书到书架, 返回 (info, error)"""
    lib = load_library()
    trash = lib.get("trash", {})
    if book_id not in trash:
        return None, "not in trash"
    info = trash.pop(book_id)
    # 恢复到 books (用原始文件名作 key, 与 scan_books 返回的 id 一致)
    books = lib.setdefault("books", {})
    books[book_id] = {
        "progress": info.get("progress", 0),
        "category": info.get("originalCategory", ""),
    }
    # 保留原有元数据 (标题/作者/封面等) 若存在则不覆盖
    save_library(lib)
    return info, None


def permanently_delete_from_trash(book_id):
    """永久删除回收站中的一本书 (删除文件并移出 trash), 返回 (info, error)"""
    lib = load_library()
    trash = lib.get("trash", {})
    if book_id not in trash:
        return None, "not in trash"
    info = trash.pop(book_id)
    # 永久删除文件
    try:
        p = info.get("path", "")
        if p and os.path.exists(p):
            os.remove(p)
    except Exception:
        pass
    save_library(lib)
    return info, None


# --------------------------------------------------------------------------- #
#  书籍文本提取 (供 AI 读取 / 分页)
# --------------------------------------------------------------------------- #
def _text_from_txt(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    # 粗探: 替换符密度高说明不是 UTF-8, 试 GBK
    if "\ufffd" in text:
        try:
            text = raw.decode("gbk", errors="replace")
        except Exception:
            pass
    return text


def _text_from_epub(path):
    with zipfile.ZipFile(path) as z:
        html_files = sorted([f for f in z.namelist()
                             if f.endswith(('.html', '.xhtml', '.htm'))])
        parts = []
        # 原来只取前 30 个 html —— 长书会被腰斩, 加了分页之后这个上限会
        # 变成新的瓶颈(读到一半就没有了)。读几百个小文件很便宜, 放宽到 300。
        for f in html_files[:300]:
            try:
                content = z.read(f).decode("utf-8", errors="replace")
                clean = re.sub(r'<[^>]+>', ' ', content)
                clean = html_mod.unescape(clean)
                clean = re.sub(r'\s+', ' ', clean).strip()
                if clean:
                    parts.append(clean)
            except Exception:
                continue
        return "\n\n".join(parts)


def _text_from_fb2(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    clean = re.sub(r'<[^>]+>', ' ', content)
    return html_mod.unescape(clean)


def _text_from_pdf_pymupdf(path):
    """用 PyMuPDF 抽 PDF 全文。没装 / 抽失败都返回 None。

    设 LINGTAI_NO_FITZ=1 可强制跳过这一层, 用来验证"没装 PyMuPDF"时的回退路径。

    这一层不是可有可无的: pdf.js 抽中文 PDF 会丢掉几乎全部汉字 —— 实测
    《大语言模型》第 2 页, pdf.js 得 207 字符且无中文, PyMuPDF 得 1063 字符
    且完整可读。所以对中文书来说 PyMuPDF 是唯一靠谱的抽取器, pdf.js 只适合
    纯拉丁文的 PDF。
    """
    if os.environ.get("LINGTAI_NO_FITZ"):
        return None
    fitz = None
    for mod in ("pymupdf", "fitz"):   # 新版改名为 pymupdf, fitz 是兼容旧名
        try:
            fitz = __import__(mod)
            break
        except Exception:
            continue
    if fitz is None:
        return None
    try:
        doc = fitz.open(path)
        try:
            parts = []
            for i, page in enumerate(doc, 1):
                parts.append("\n\n[第 {} 页]\n".format(i))
                parts.append(page.get_text("text"))
            return "".join(parts)
        finally:
            doc.close()
    except Exception as e:
        sys.stderr.write("[text] PyMuPDF 抽取失败 {}: {}\n".format(path, e))
        return None




def pdf_extract_backend():
    """后端能用的 PDF 抽取器名字; 没有可用返回空串。

    这个值决定前端要不要出动 pdf.js。必须挡住: pdf.js 抽中文 PDF 会丢掉
    几乎全部汉字(实测《大语言模型》第 2 页只得 207 字符且无中文), 而缓存是
    "先写先赢"的 —— 一旦这份残缺正文落进缓存, PyMuPDF 就再也轮不上了。
    """
    if os.environ.get("LINGTAI_NO_FITZ"):
        return ""
    for mod in ("pymupdf", "fitz"):
        try:
            __import__(mod)
            return "pymupdf"
        except Exception:
            continue
    return ""


def _raw_book_text(path, fmt, book_id=None):
    """拿整本书的纯文本。返回 (text 或 None, 来源标签)。

    PDF 是分层的: 缓存 -> PyMuPDF -> 交给前端。前两层都拿不到时返回
    "need_client", 由上层请前端用 pdf.js 抽一次再回传。
    """
    bid = book_id or os.path.basename(path)

    if fmt == "pdf":
        # L0 缓存: 前端 pdf.js 抽过的, 或上次 PyMuPDF 抽过的
        text, _meta = textcache.read_cached_text(bid, path)
        if text is not None:
            return text, "cache"
        # L1 PyMuPDF (装了才走)
        text = _text_from_pdf_pymupdf(path)
        if text:
            textcache.write_cached_text(bid, path, text, "pymupdf")
            return text, "pymupdf"
        # 都没有 —— 需要前端出手
        return None, "need_client"

    try:
        if fmt == "txt":
            return _text_from_txt(path), "txt"
        if fmt == "epub":
            return _text_from_epub(path), "epub"
        if fmt == "fb2":
            return _text_from_fb2(path), "fb2"
    except Exception as e:
        sys.stderr.write("[text] 提取失败 {} ({}): {}\n".format(path, fmt, e))
        return None, "error"
    return None, "unsupported"


def extract_book_text(path, fmt, max_chars=8000, offset=0, book_id=None):
    """提取书籍文本, 返回切片后的字符串。

    向后兼容: 老的 extract_book_text(path, fmt) 行为不变。
    需要分页信息(总长 / 是否还有更多 / 来源)请用 read_book_text()。
    """
    text, _src = _raw_book_text(path, fmt, book_id)
    if not text:
        return ""
    if max_chars is None:
        return text[offset:]
    return text[offset:offset + max_chars]


def read_book_text(book_id, path, fmt, offset=0, limit=None, page=None):
    """给 AI 工具用的读取入口, 带分页信息。

    page 是 1 基页码, 优先于 offset —— 让模型能说"读第 30 页"。
    """
    try:
        limit = int(limit) if limit else TEXT_DEFAULT_CHARS
    except (TypeError, ValueError):
        limit = TEXT_DEFAULT_CHARS
    limit = max(1, min(limit, TEXT_MAX_CHARS))

    text, source = _raw_book_text(path, fmt, book_id)
    meta = textcache.read_cached_meta(book_id) or {}

    if text is None:
        return {"ok": False, "status": source, "content": "",
                "totalChars": 0, "source": source}

    total = len(text)
    if page is not None:
        offsets = meta.get("pageOffsets") or []
        try:
            p = int(page)
            if 1 <= p <= len(offsets):
                offset = offsets[p - 1]
        except (TypeError, ValueError):
            pass
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0

    chunk = text[offset:offset + limit]
    end = offset + len(chunk)
    has_more = end < total
    return {
        "ok": True,
        "status": "ok",
        "content": chunk,
        "totalChars": total,
        "offset": offset,
        "length": len(chunk),
        "hasMore": has_more,
        "nextOffset": end if has_more else None,
        "source": source,
        "pageCount": meta.get("pageCount"),
    }

