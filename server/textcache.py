# -*- coding: utf-8 -*-
"""书籍文字抽取缓存。

PDF 读不出文字是灵台的老问题: 标准库解不了, 得靠 PyMuPDF 或前端 pdf.js
抽一次。抽完把纯文本落在这里, 之后所有读取都命中缓存, 不必重复抽。

几个刻意的设计:

- **正文与元数据分两个文件**。``<key>.txt`` 是纯正文, 不带任何头部 ——
  这样切片就是 ``text[offset:offset+n]``, 不用先剥头, 页码偏移也不会因为
  头部长度而整体漂移。
- **key 必须是纯 ASCII**。中文书名直接拿来做文件名, 在 Windows 上会引入
  一堆编码问题, 而且大小写不敏感的文件系统还会让不同书名撞到同一个文件。
- **失效只看 stat, 不哈希内容**。同名文件被覆盖时 mtime 必然变; 这就够了,
  而且不用为了判断先把整个文件读一遍。
"""
import os
import re
import json
import time
import hashlib

from .constants import TEXT_CACHE_DIR

__all__ = [
    "cache_key", "read_cached_text", "read_cached_meta",
    "write_cached_text", "invalidate_cache",
]


def cache_key(book_id):
    """把书籍 id 映射成一个纯 ASCII 的缓存 key。"""
    stem = re.sub(r'[^0-9A-Za-z._-]', '_', book_id or "")[:60]
    digest = hashlib.sha1((book_id or "").encode("utf-8")).hexdigest()[:10]
    return "{}-{}".format(stem, digest)


def _paths(book_id):
    k = cache_key(book_id)
    return (os.path.join(TEXT_CACHE_DIR, k + ".txt"),
            os.path.join(TEXT_CACHE_DIR, k + ".json"))


def read_cached_meta(book_id):
    """只读元数据, 不校验新鲜度。读不到返回 None。"""
    _, meta_path = _paths(book_id)
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _is_fresh(meta, src_path):
    """缓存是否仍然对应磁盘上的那个文件。"""
    if not meta:
        return False
    try:
        st = os.stat(src_path)
    except OSError:
        return False
    return (meta.get("srcName") == os.path.basename(src_path)
            and meta.get("size") == st.st_size
            and meta.get("mtime") == int(st.st_mtime))


def read_cached_text(book_id, src_path):
    """读缓存正文。返回 (text, meta); 未命中或已失效返回 (None, None)。"""
    text_path, _ = _paths(book_id)
    meta = read_cached_meta(book_id)
    if not _is_fresh(meta, src_path) or not os.path.isfile(text_path):
        return None, None
    try:
        with open(text_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), meta
    except Exception:
        return None, None


def write_cached_text(book_id, src_path, text, source, extra=None):
    """写入缓存。先写 .tmp 再 os.replace, 避免读到写了一半的文件。"""
    os.makedirs(TEXT_CACHE_DIR, exist_ok=True)
    text_path, meta_path = _paths(book_id)
    try:
        st = os.stat(src_path)
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    meta = {
        "srcName": os.path.basename(src_path),
        "size": size,
        "mtime": mtime,
        "source": source,          # client | pymupdf | stdlib
        "chars": len(text),
        "createdAt": int(time.time()),
    }
    if extra:
        meta.update(extra)
    try:
        tmp = text_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, text_path)

        tmp = meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, meta_path)
    except Exception:
        return None
    return meta


def invalidate_cache(book_id):
    """删掉一本书的缓存 (正文 + 元数据)。返回是否真的删掉了东西。"""
    removed = False
    for p in _paths(book_id):
        try:
            if os.path.isfile(p):
                os.remove(p)
                removed = True
        except Exception:
            pass
    return removed
