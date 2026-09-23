# -*- coding: utf-8 -*-
"""HTTP 请求处理器 (路由)。"""
import os
import json
import time
import re
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler

from .constants import *
from .store import *
from .ai import *
from . import textcache
from . import library

# --------------------------------------------------------------------------- #
#  HTTP 处理器
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "ShelfReader/2.0"

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if isinstance(body, (bytes, bytearray)):
            self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        if body and isinstance(body, (bytes, bytearray)):
            self.wfile.write(body)

    def _json(self, code, obj, extra_headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), headers=extra_headers)

    def _sse_write(self, data):
        """发送 SSE 数据块"""
        try:
            payload = json.dumps(data, ensure_ascii=False)
            self.wfile.write("data: {}\n\n".format(payload).encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    # ---- GET ----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        parts = [p for p in path.split("/") if p != ""]

        if path in ("", "/"):
            return self._serve_static_file("index.html", "/")
        if parts and parts[0] == "api":
            return self._api_get(parts, parsed)
        if parts and parts[0] == "static":
            rel = os.path.join(*parts[1:]) if len(parts) > 1 else ""
            return self._serve_static_file(rel, path)
        if parts and parts[0] in ("css", "js", "vendor", "assets"):
            return self._serve_static_file(os.path.join(*parts), path)
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---- POST ----
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p != ""]

        # /api/books/upload
        if parts == ["api", "books", "upload"]:
            return self._api_upload_book()

        # /api/books/<id>/<action>
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books":
            book_id = parts[2]
            action = parts[3]
            if action == "progress":
                return self._api_save_progress(book_id)
            if action == "meta":
                return self._api_save_meta(book_id)
            if action == "notes":
                return self._api_notes(book_id)
            if action == "category":
                return self._api_set_category(book_id)
            if action == "text":
                # 前端用 pdf.js 抽完文字后回传, 服务端落缓存
                return self._api_save_book_text(book_id)

        # /api/categories
        if parts == ["api", "categories"]:
            return self._api_create_category()

        # /api/ai/config
        if parts == ["api", "ai", "config"]:
            return self._api_save_ai_config()

        # /api/ai/memory
        if parts == ["api", "ai", "memory"]:
            return self._api_memory_add()

        # /api/ai/chat
        if parts == ["api", "ai", "chat"]:
            return self._api_ai_chat()

        # /api/trash/restore  -> 从回收站恢复书籍
        if parts == ["api", "trash", "restore"]:
            return self._api_trash_restore()

        # /api/trash/empty  -> 清空回收站过期项 (>30天)
        if parts == ["api", "trash", "empty"]:
            return self._api_trash_empty()

        # /api/books/reorder  -> 交换两本书的位置
        if parts == ["api", "books", "reorder"]:
            return self._api_reorder_books()

        # /api/library/source  -> 设置书库路径
        if parts == ["api", "library", "source"]:
            return self._api_library_source_set()

        # /api/library/import  -> 从书库导入一本书
        if parts == ["api", "library", "import"]:
            return self._api_library_import()

        if len(parts) == 2 and parts[0] == "api" and parts[1] == "library":
            return self._api_save_library()

        self._json(404, {"error": "unknown endpoint"})

    # ---- DELETE ----
    def do_DELETE(self):
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p != ""]

        # /api/books/<id>
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "books":
            return self._api_delete_book(parts[2])

        # /api/trash/<book_id>  -> 永久删除回收站中的书籍
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "trash":
            return self._api_trash_delete_permanent(urllib.parse.unquote(parts[2]))

        # /api/categories/<name>
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "categories":
            return self._api_delete_category(urllib.parse.unquote(parts[2]))

        # /api/ai/memory/<id>
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "ai" and parts[2] == "memory":
            return self._api_memory_delete(urllib.parse.unquote(parts[3]))

        # /api/books/<id>/text  -> 作废该书的文字缓存 (用于"重新提取")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" and parts[3] == "text":
            return self._api_delete_book_text(parts[2])

        self._json(404, {"error": "unknown endpoint"})

    # ---- 静态文件 ----
    def _serve_static_file(self, rel, original_path):
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.join(STATIC_DIR, rel)
        full = os.path.abspath(full)
        if not full.startswith(os.path.abspath(STATIC_DIR) + os.sep) and full != os.path.abspath(STATIC_DIR):
            return self._send(403, b"forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif full.endswith(".mjs"):
            ctype = "application/javascript; charset=utf-8"
        return self._stream_file(full, ctype)

    def _stream_file(self, full, ctype):
        size = os.path.getsize(full)
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if range_header and range_header.startswith("bytes="):
            try:
                r = range_header[6:].split("-")
                start = int(r[0]) if r[0] else 0
                end = int(r[1]) if len(r[1]) and r[1] else size - 1
                if end >= size:
                    end = size - 1
                partial = True
            except ValueError:
                pass
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(full, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---- API GET ----
    def _api_get(self, parts, parsed):
        # /api/capabilities -> 后端能力探测。前端据此决定要不要用 pdf.js 抽文字:
        # 后端能抽(PyMuPDF 已装)就绝不能让前端抽 —— pdf.js 抽中文会丢汉字。
        if parts == ["api", "capabilities"]:
            backend = pdf_extract_backend()
            return self._json(200, {
                "pdf_backend": backend,
                "client_extract_needed": not backend,
            })

        # /api/library/source -> 当前书库路径及其可用性
        if parts == ["api", "library", "source"]:
            return self._api_library_source_get()

        # /api/library/scan -> 扫描书库, 列出可导入的书
        if parts == ["api", "library", "scan"]:
            return self._api_library_scan()

        # /api/books
        if parts == ["api", "books"]:
            lib = load_library()
            trash = lib.get("trash", {})
            books = scan_books()
            # 过滤掉回收站中的书籍 (文件仍在磁盘, 但不应出现在书架)
            books = [b for b in books if b["id"] not in trash]
            for b in books:
                meta = get_book_meta(lib, b["id"])
                b["title"] = meta.get("title") or os.path.splitext(b["name"])[0]
                b["author"] = meta.get("author", "")
                b["progress"] = meta.get("progress", 0)
                b["lastRead"] = meta.get("lastRead", 0)
                b["hasCover"] = bool(meta.get("cover"))
                b["category"] = meta.get("category", "")
                # bookshelf.js 的"自定义排序"靠 position 排序, 但这里从来没下发过它,
                # 于是刷新后全是 undefined, 一并落到 99999, 排序退化成按 mtime。
                b["position"] = meta.get("position")
            return self._json(200, {"books": books})

        # /api/library
        if parts == ["api", "library"]:
            return self._json(200, load_library())

        # /api/categories
        if parts == ["api", "categories"]:
            cats = get_all_categories()
            lib = load_library().get("books", {})
            counts = {}
            for meta in lib.values():
                c = meta.get("category", "")
                if c:
                    counts[c] = counts.get(c, 0) + 1
            result = [{"name": c, "count": counts.get(c, 0)} for c in cats]
            return self._json(200, {"categories": result})

        # /api/ai/config
        if parts == ["api", "ai", "config"]:
            cfg = load_ai_config()
            # 脱敏
            safe = {
                "endpoint": cfg.get("endpoint", ""),
                "model": cfg.get("model", ""),
                "has_key": bool(cfg.get("api_key")),
            }
            return self._json(200, safe)

        # /api/ai/memory
        if parts == ["api", "ai", "memory"]:
            return self._api_memory_get()

        # /api/trash  -> 回收站列表
        if parts == ["api", "trash"]:
            return self._api_trash_list()

        # /api/notes/all  -> 所有书的笔记聚合
        if parts == ["api", "notes", "all"]:
            return self._api_all_notes()

        # /api/books/<id>/file
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" and parts[3] == "file":
            full = book_path(parts[2])
            if not full:
                return self._json(404, {"error": "book not found"})
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.lower().endswith(".epub"):
                ctype = "application/epub+zip"
            elif full.lower().endswith(".pdf"):
                ctype = "application/pdf"
            return self._stream_file(full, ctype)

        # /api/books/<id>/text  -> 文字缓存状态 (前端据此决定要不要启动 pdf.js 抽取)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" and parts[3] == "text":
            return self._api_book_text_status(parts[2])

        # /api/books/<id>/progress
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" and parts[3] == "progress":
            lib = load_library().get("books", {})
            meta = lib.get(parts[2], {})
            return self._json(200, {
                "progress": meta.get("progress", 0),
                "cfi": meta.get("cfi"),
                "page": meta.get("page", 0),
                "total": meta.get("total", 0),
            })

        # /api/books/<id>/notes
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" and parts[3] == "notes":
            lib = load_library().get("books", {})
            meta = lib.get(parts[2], {})
            notes = meta.get("notes", [])
            notes.sort(key=lambda n: n.get("createdAt", 0))
            return self._json(200, {"notes": notes})

        return self._json(404, {"error": "unknown endpoint"})

    # ---- 分类 API ----
    def _api_create_category(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        name = payload.get("name", "").strip()
        old_name = payload.get("old_name", "").strip()
        if not name:
            return self._json(400, {"error": "name required"})
        lib = load_library()
        cats = lib.setdefault("categories", [])
        if old_name:
            # 重命名
            if old_name in cats:
                cats.remove(old_name)
            if name not in cats:
                cats.append(name)
            for meta in lib.get("books", {}).values():
                if meta.get("category") == old_name:
                    meta["category"] = name
        else:
            if name not in cats:
                cats.append(name)
        save_library(lib)
        self._json(200, {"ok": True})

    def _api_delete_category(self, name):
        lib = load_library()
        cats = lib.get("categories", [])
        if name in cats:
            cats.remove(name)
        # 清除书籍的分类引用
        for meta in lib.get("books", {}).values():
            if meta.get("category") == name:
                meta["category"] = ""
        save_library(lib)
        self._json(200, {"ok": True})

    def _api_set_category(self, book_id):
        if not book_path(book_id):
            return self._json(404, {"error": "book not found"})
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        cat = payload.get("category", "").strip()
        lib = load_library()
        meta = lib.setdefault("books", {}).setdefault(book_id, {})
        meta["category"] = cat
        cats = lib.setdefault("categories", [])
        if cat and cat not in cats:
            cats.append(cat)
        save_library(lib)
        self._json(200, {"ok": True})

    # ---- 书籍删除 (软删除: 移入回收站, 不删除文件) ----
    def _api_delete_book(self, book_id):
        full = book_path(book_id)
        if not full:
            return self._json(404, {"error": "book not found"})
        # book_id 来自 URL 路径, 可能是 URL 编码的; 统一解码为原始文件名
        decoded = urllib.parse.unquote(book_id)
        lib = load_library()
        books = lib.get("books", {})
        # library.json 中 key 可能是原始文件名或 URL 编码, 两种都尝试
        meta = books.get(decoded) or books.get(book_id) or {}
        title = meta.get("title", decoded)
        # 移入回收站, 不删除文件; trash key 用原始文件名 (与 scan_books 返回的 id 一致)
        trash = lib.setdefault("trash", {})
        trash[decoded] = {
            "title": title,
            "deletedAt": int(time.time()),
            "originalCategory": meta.get("category", ""),
            "path": full,
        }
        books.pop(decoded, None)
        books.pop(book_id, None)
        save_library(lib)
        self._json(200, {"ok": True, "title": title, "message": "已移入回收站"})

    # ---- 回收站 API ----
    def _api_trash_list(self):
        # 访问回收站时自动清理过期项
        expired = clean_expired_trash()
        lib = load_library()
        trash = lib.get("trash", {})
        now = int(time.time())
        items = []
        for bid, info in trash.items():
            deleted_at = info.get("deletedAt", 0)
            age = now - deleted_at
            # 剩余保留时间 (秒), 不少于 0
            remain = max(0, TRASH_RETENTION_SECONDS - age)
            items.append({
                "bookId": bid,
                "title": info.get("title", bid),
                "deletedAt": deleted_at,
                "originalCategory": info.get("originalCategory", ""),
                "path": info.get("path", ""),
                "ageSeconds": age,
                "remainSeconds": remain,
            })
        # 按删除时间倒序 (最新删除在前)
        items.sort(key=lambda x: x.get("deletedAt", 0), reverse=True)
        return self._json(200, {
            "trash": items,
            "count": len(items),
            "expiredRemoved": len(expired),
        })

    def _api_trash_restore(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        book_id = payload.get("book_id", "")
        if not book_id:
            return self._json(400, {"error": "book_id required"})
        info, err = restore_from_trash(book_id)
        if err:
            return self._json(404, {"error": "该书不在回收站中"})
        return self._json(200, {
            "ok": True,
            "bookId": book_id,
            "title": info.get("title", book_id),
            "message": "已从回收站恢复《{}》".format(info.get("title", book_id)),
        })

    def _api_trash_empty(self):
        """清空回收站中所有过期项 (>30天), 并返回被清理的数量"""
        expired = clean_expired_trash()
        return self._json(200, {
            "ok": True,
            "expiredRemoved": len(expired),
            "removed": expired,
            "message": "已清理 {} 本过期书籍".format(len(expired)),
        })

    def _api_trash_delete_permanent(self, book_id):
        """永久删除回收站中的一本书 (删除文件 + 移出 trash)"""
        info, err = permanently_delete_from_trash(book_id)
        if err:
            return self._json(404, {"error": "该书不在回收站中"})
        return self._json(200, {
            "ok": True,
            "bookId": book_id,
            "title": info.get("title", book_id),
            "message": "已永久删除《{}》".format(info.get("title", book_id)),
        })

    # ---- 书籍上传 ----
    def _api_upload_book(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "expected multipart/form-data"})
        body = self._read_body()
        # 解析 multipart
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[9:].strip('"')
                break
        if not boundary:
            return self._json(400, {"error": "no boundary"})
        boundary_bytes = ("--" + boundary).encode()
        sections = body.split(boundary_bytes)
        saved = []
        for section in sections:
            if not section or section == b"--\r\n" or section == b"--":
                continue
            # 去掉 \r\n 前缀
            section = section.strip(b"\r\n")
            if not section:
                continue
            header_end = section.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            header_str = section[:header_end].decode("utf-8", errors="replace")
            file_data = section[header_end + 4:]
            # 去掉结尾 \r\n
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]
            # 提取文件名
            fname = None
            for line in header_str.split("\r\n"):
                m = re.search(r'filename="(.+?)"', line)
                if m:
                    fname = m.group(1)
                    break
            if not fname:
                continue
            fname = os.path.basename(fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXT:
                continue
            dest = os.path.join(BOOKS_DIR, fname)
            # 避免覆盖
            if os.path.exists(dest):
                base, e = os.path.splitext(fname)
                fname = f"{base}_{int(time.time())}{e}"
                dest = os.path.join(BOOKS_DIR, fname)
            with open(dest, "wb") as f:
                f.write(file_data)
            saved.append(fname)
        self._json(200, {"ok": True, "saved": saved})

    # ---- AI 配置 ----
    # ---- 书库 (外部电子书仓库) ----
    def _api_library_source_get(self):
        path = library.active_source_path()
        ok, why = library.validate_source(path)
        return self._json(200, {"path": path, "valid": ok, "reason": why})

    def _api_library_source_set(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        path = (payload.get("path") or "").strip()
        ok, why = library.validate_source(path)
        if not ok:
            return self._json(400, {"error": why})
        library.save_sources_config({"path": path, "updatedAt": int(time.time())})
        self._json(200, {"ok": True, "path": path})

    def _api_library_scan(self):
        path = library.active_source_path()
        ok, why = library.validate_source(path)
        if not ok:
            return self._json(200, {"path": path, "valid": False,
                                    "reason": why, "items": []})
        self._json(200, {"path": path, "valid": True, "items": library.scan_source(path)})

    def _api_library_import(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        info, err = library.import_book(library.active_source_path(),
                                        payload.get("file") or "")
        if err:
            return self._json(400, {"error": err})
        self._json(200, dict({"ok": True}, **info))

    # ---- 书籍文字缓存 (PDF 得靠前端 pdf.js 抽一次) ----
    # 回传正文的大小上限。_read_body() 是按 Content-Length 一次性全读进内存的,
    # 不设上限就等于把本地服务变成内存放大器。
    MAX_TEXT_BODY = 32 * 1024 * 1024

    def _api_book_text_status(self, book_id):
        """前端据此决定要不要启动抽取。"""
        bid = urllib.parse.unquote(book_id)
        full = book_path(bid)
        if not full:
            return self._json(404, {"error": "book not found"})
        text, meta = textcache.read_cached_text(bid, full)
        if text is None:
            return self._json(200, {"cached": False})
        return self._json(200, {
            "cached": True,
            "chars": meta.get("chars"),
            "source": meta.get("source"),
            "pageCount": meta.get("pageCount"),
        })

    def _api_save_book_text(self, book_id):
        """接收前端 pdf.js 抽好的正文并落缓存。"""
        bid = urllib.parse.unquote(book_id)
        full = book_path(bid)
        if not full:
            return self._json(404, {"error": "book not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > self.MAX_TEXT_BODY:
            return self._json(413, {"error": "正文过大 (上限 {} MB)".format(
                self.MAX_TEXT_BODY // (1024 * 1024))})
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        text = payload.get("text") or ""
        if not text.strip():
            return self._json(400, {"error": "text 为空"})
        extra = {}
        if payload.get("pageCount"):
            extra["pageCount"] = payload["pageCount"]
        if payload.get("pageOffsets"):
            extra["pageOffsets"] = payload["pageOffsets"]
        meta = textcache.write_cached_text(bid, full, text, "client", extra)
        if meta is None:
            return self._json(500, {"error": "写入缓存失败"})
        return self._json(200, {"ok": True, "chars": meta["chars"], "source": meta["source"]})

    def _api_delete_book_text(self, book_id):
        """作废缓存, 供"重新提取"用。"""
        bid = urllib.parse.unquote(book_id)
        removed = textcache.invalidate_cache(bid)
        self._json(200, {"ok": True, "removed": removed})

    def _api_save_ai_config(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        cfg = load_ai_config()
        # 只在真的传了非空 key 时才覆盖。原来这里用 `if "api_key" in payload`,
        # 而前端每次都带这个字段(哪怕是空串) —— 于是用户第二次点保存就把已存的
        # key 抹成空串, 是静默的数据丢失, 且 UI 上明明承诺的是"留空则不修改"。
        # 要显式清空请传 clear_key: true。
        if payload.get("clear_key"):
            cfg["api_key"] = ""
        elif payload.get("api_key"):
            cfg["api_key"] = payload["api_key"]
        if "endpoint" in payload:
            cfg["endpoint"] = payload["endpoint"]
        if "model" in payload:
            cfg["model"] = payload["model"]
        save_ai_config(cfg)
        self._json(200, {"ok": True})

    # ---- Agent 记忆 API ----
    def _api_memory_get(self):
        mem = MEMORY.load_memory()
        return self._json(200, mem)

    def _api_memory_add(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        content = (payload.get("content", "") or "").strip()
        if not content:
            return self._json(400, {"error": "content required"})
        type_ = payload.get("type", "fact")
        confidence = payload.get("confidence", 0.8)
        source = payload.get("source", "user")
        fact = MEMORY.add_fact(content, type_, confidence, source)
        return self._json(200, {"ok": True, "fact": fact})

    def _api_memory_delete(self, fact_id):
        if not fact_id:
            return self._json(400, {"error": "id required"})
        ok = MEMORY.remove_fact(fact_id)
        if not ok:
            return self._json(404, {"error": "fact not found"})
        return self._json(200, {"ok": True})

    # ---- AI Agent 对话 (流式 SSE) ----
    def _api_ai_chat(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})

        config = load_ai_config()
        if not config.get("api_key"):
            return self._json(400, {"error": "请先在设置中配置 AI API Key"})

        messages = payload.get("messages", [])
        if not messages:
            return self._json(400, {"error": "messages required"})

        context = payload.get("context", {}) or {}
        session_id = payload.get("session_id", "default")

        # 注入配置, 使记忆 / 上下文压缩模块可调用 LLM
        MEMORY.set_config(config)

        # 安全裁剪前端传入的纯文本历史 (不破坏 tool_calls 结构)
        messages = MEMORY.trim_short_term(messages)

        # 组装上下文 (注入会话摘要 + 长期记忆 + 书籍上下文, 按需压缩)
        ctx_builder = ContextBuilder(MEMORY, config)
        registry = ToolRegistry()
        register_default_tools(registry)
        controller = AgentLoopController(config)

        llm_messages = ctx_builder.build(messages, context, session_id)
        collected_actions = []
        final_content = ""

        # ---- Agent 循环 ----
        # 先发送 SSE 头部
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        while True:
            stop, reason = controller.should_stop()
            if stop:
                final_content = reason
                break

            controller.step += 1
            controller.begin_step()
            import sys as _sys
            print("[Agent] Step {} starting...".format(controller.step), file=_sys.stderr, flush=True)

            # 发送步骤状态到前端
            self._sse_write({"step": controller.step, "type": "thinking", "content": "正在思考..."})

            _t0 = time.time()
            response = call_llm_api(config, llm_messages, registry.get_schemas())
            _t1 = time.time()
            print("[Agent] Step {} LLM call took {:.1f}s".format(controller.step, _t1 - _t0), file=_sys.stderr, flush=True)
            if "error" in response:
                final_content = "AI 调用失败: {}".format(response["error"])
                print("[Agent] Step {} error: {}".format(controller.step, response["error"]), file=_sys.stderr, flush=True)
                break

            choices = response.get("choices", [])
            if not choices:
                final_content = "AI 返回了空响应, 请稍后重试。"
                break
            msg = choices[0].get("message", {})
            tool_calls = msg.get("tool_calls")

            # 没有工具调用 -> 最终回答
            if not tool_calls:
                final_content = msg.get("content", "") or ""
                # 发送推理过程(如果有)
                reasoning = msg.get("reasoning_content", "") or ""
                if reasoning:
                    self._sse_write({"type": "reasoning", "content": reasoning})
                print("[Agent] Step {} final answer ({} chars)".format(controller.step, len(final_content)), file=_sys.stderr, flush=True)
                break

            # 追加带 tool_calls 的 assistant 消息
            llm_messages.append(msg)

            # 执行每个工具调用
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                print("[Agent] Step {} calling tool: {}".format(controller.step, fn_name), file=_sys.stderr, flush=True)
                # 发送工具调用状态
                tool_label = {"list_books": "查看书架", "set_book_category": "设置分类", "list_categories": "查看分类", "search_books": "搜索书籍", "get_book_info": "获取书籍信息", "batch_categorize": "批量分类", "web_search": "联网搜索", "download_book": "下载书籍"}.get(fn_name, fn_name)
                self._sse_write({"type": "tool", "tool": fn_name, "label": tool_label})
                _tt0 = time.time()
                try:
                    fn_args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                except Exception:
                    fn_args = {}

                # 重复调用检测
                is_dup, dup_count = controller.check_duplicate(fn_name, fn_args)
                if is_dup:
                    result = make_tool_result(
                        False,
                        error="已重复调用工具 {} (相同参数 {} 次)。请直接使用之前该工具返回的结果, 不要再次调用。".format(fn_name, dup_count),
                        retryable=False,
                    )
                else:
                    controller.record_call(fn_name, fn_args)
                    result = registry.execute(fn_name, fn_args, context)

                # 失败计数
                result_ok = bool(result.get("ok"))
                if not result_ok:
                    controller.record_failure()

                # 记录步骤日志 (便于调试)
                controller.log_step(controller.step, fn_name, result_ok)
                _tt1 = time.time()
                print("[Agent] Step {} tool {} done ({:.1f}s) ok={}".format(controller.step, fn_name, _tt1 - _tt0, result_ok), file=_sys.stderr, flush=True)

                # 序列化为工具结果内容
                result_str = json.dumps(result, ensure_ascii=False)

                # 从工具结果中收集动作指令
                for act in extract_actions(result_str):
                    if act not in collected_actions:
                        collected_actions.append(act)

                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_str,
                })

            # 上下文增长后按需压缩 (避免 token 超限)
            if ctx_builder.compressor and ctx_builder.compressor.should_compress(llm_messages):
                llm_messages = ctx_builder.compressor.compress(llm_messages)
                # 清理可能因压缩产生的孤立 tool 消息 (其 tool_call_id 已不在上下文中)
                llm_messages = self._sanitize_messages(llm_messages)

            # 循环继续: 检查步数/失败/超时上限 (should_stop 在下一轮开头判断)

        # ---- 流式输出最终回答 ----
        # 从最终回答中提取动作指令
        for act in extract_actions(final_content):
            if act not in collected_actions:
                collected_actions.append(act)

        # 清除最终回答中的动作指令行 (用与 extract_actions 同一个模式, 免得漏掉新动作)
        clean_content = re.sub(ACTION_PATTERN, '', final_content).strip()

        if not clean_content and not collected_actions:
            clean_content = "(AI 未返回内容, 请检查 AI 配置或稍后重试。)"

        # 逐字发送最终内容
        chunk_size = 4
        for i in range(0, len(clean_content), chunk_size):
            chunk = clean_content[i:i + chunk_size]
            data = json.dumps({"content": chunk}, ensure_ascii=False)
            self.wfile.write("data: {}\n\n".format(data).encode("utf-8"))
            self.wfile.flush()

        # 发送动作
        if collected_actions:
            data = json.dumps({"actions": collected_actions}, ensure_ascii=False)
            self.wfile.write("data: {}\n\n".format(data).encode("utf-8"))
            self.wfile.flush()

        # 结束标记
        self.wfile.write(b"data: {\"done\": true}\n\n")
        self.wfile.flush()

        # ---- 保存会话摘要 (best-effort, 在响应发送后执行, 不影响用户体验) ----
        self._save_session_summary(session_id, llm_messages, config)

    @staticmethod
    def _sanitize_messages(messages):
        """移除孤立的 tool 消息 (其 tool_call_id 在前序 assistant 消息中不存在)"""
        valid_call_ids = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m.get("tool_calls", []):
                    cid = tc.get("id")
                    if cid:
                        valid_call_ids.add(cid)
        return [m for m in messages
                if m.get("role") != "tool" or m.get("tool_call_id") in valid_call_ids]

    def _save_session_summary(self, session_id, messages, config):
        """生成并保存会话摘要 (best-effort), 与已有摘要合并后持久化"""
        try:
            # 提取本轮最后一条用户消息与最终回答
            last_user = ""
            last_assistant = ""
            tool_names = []
            for m in messages:
                role = m.get("role", "")
                if role == "user":
                    last_user = (m.get("content", "") or "")[:300]
                if role == "assistant":
                    c = m.get("content", "") or ""
                    if c:
                        last_assistant = c[:300]
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        tool_names.append(tc.get("function", {}).get("name", ""))
            if not last_user and not last_assistant:
                return
            prompt = ("请用中文将以下本轮对话摘要为 100 字以内的要点, 保留关键信息:\n"
                      "用户: {}\n助手: {}\n工具调用: {}").format(
                last_user, last_assistant, ", ".join(tool_names) or "无")
            compressor = ContextCompressor(config)
            summary = compressor._generate_summary(prompt)
            if summary:
                existing = MEMORY.get_session_summary(session_id)
                if existing:
                    combined = existing + "\n" + summary[:200]
                else:
                    combined = summary[:300]
                MEMORY.set_session_summary(session_id, combined[:800])
        except Exception:
            pass

    # ---- 原有 API ----
    def _api_save_progress(self, book_id):
        if not book_path(book_id):
            return self._json(404, {"error": "book not found"})
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        lib = load_library()
        meta = lib.setdefault("books", {}).setdefault(book_id, {})
        meta["progress"] = float(payload.get("progress", 0))
        if payload.get("cfi") is not None:
            meta["cfi"] = payload["cfi"]
        if payload.get("page") is not None:
            meta["page"] = int(payload["page"])
        if payload.get("total") is not None:
            meta["total"] = int(payload["total"])
        meta["lastRead"] = int(time.time())
        save_library(lib)
        self._json(200, {"ok": True})

    def _api_save_meta(self, book_id):
        if not book_path(book_id):
            return self._json(404, {"error": "book not found"})
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        lib = load_library()
        meta = lib.setdefault("books", {}).setdefault(book_id, {})
        for k in ("title", "author", "cover", "category"):
            if k in payload:
                meta[k] = payload[k]
        save_library(lib)
        self._json(200, {"ok": True})

    def _api_save_library(self):
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        save_library(payload)
        self._json(200, {"ok": True})

    def _api_notes(self, book_id):
        if not book_path(book_id):
            return self._json(404, {"error": "book not found"})
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        lib = load_library()
        meta = lib.setdefault("books", {}).setdefault(book_id, {})
        notes = meta.setdefault("notes", [])
        now = int(time.time())
        op = payload.get("op", "save")
        if op == "delete":
            note_id = payload.get("id")
            meta["notes"] = [n for n in notes if n.get("id") != note_id]
        else:
            note_id = payload.get("id") or f"note-{now}-{id(payload) & 0xffff}"
            existing = None
            for n in notes:
                if n.get("id") == note_id:
                    existing = n
                    break
            if existing:
                existing["content"] = payload.get("content", existing.get("content", ""))
                existing["page"] = payload.get("page", existing.get("page", 0))
                existing["progress"] = payload.get("progress", existing.get("progress", 0))
                existing["updatedAt"] = now
            else:
                notes.append({
                    "id": note_id, "content": payload.get("content", ""),
                    "page": payload.get("page", 0), "progress": payload.get("progress", 0),
                    "createdAt": now, "updatedAt": now,
                })
        meta["notes"].sort(key=lambda n: n.get("createdAt", 0))
        save_library(lib)
        self._json(200, {"ok": True, "notes": meta.get("notes", [])})

    def _api_all_notes(self):
        """聚合所有书的笔记, 返回 [{bookId, bookTitle, bookFormat, category, note}]"""
        lib = load_library()
        books_meta = lib.get("books", {})
        # 扫描书架上的所有书
        all_books = scan_books()
        result = []
        for b in all_books:
            bid = b["id"]
            meta = books_meta.get(bid, {})
            notes = meta.get("notes", [])
            for n in notes:
                result.append({
                    "id": n.get("id"),
                    "content": n.get("content", ""),
                    "page": n.get("page", 0),
                    "progress": n.get("progress", 0),
                    "createdAt": n.get("createdAt", 0),
                    "updatedAt": n.get("updatedAt", 0),
                    "bookId": bid,
                    "bookTitle": meta.get("title") or b.get("title") or b.get("name") or bid,
                    "bookFormat": b.get("format", ""),
                    "category": meta.get("category", ""),
                })
        # 按更新时间倒序
        result.sort(key=lambda x: x.get("updatedAt", 0), reverse=True)
        return self._json(200, {"notes": result})

    def _api_reorder_books(self):
        """交换两本书的自定义排序位置"""
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "bad json"})
        id1 = payload.get("id1")
        id2 = payload.get("id2")
        if not id1 or not id2:
            return self._json(400, {"error": "id1 and id2 required"})
        lib = load_library()
        books = lib.setdefault("books", {})
        meta1 = books.setdefault(id1, {})
        meta2 = books.setdefault(id2, {})
        pos1 = meta1.get("position")
        pos2 = meta2.get("position")
        # 如果都没有 position, 初始化为当前文件顺序
        if pos1 is None and pos2 is None:
            all_books = scan_books()
            for i, b in enumerate(all_books):
                bid = b["id"]
                books.setdefault(bid, {})["position"] = i
            pos1 = books[id1].get("position")
            pos2 = books[id2].get("position")
        elif pos1 is None:
            pos1 = pos2 + 1 if pos2 is not None else 0
        elif pos2 is None:
            pos2 = pos1 + 1 if pos1 is not None else 0
        # 交换
        meta1["position"] = pos2
        meta2["position"] = pos1
        save_library(lib)
        return self._json(200, {"ok": True})

    def log_message(self, *args):
        pass

