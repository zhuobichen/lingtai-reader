# -*- coding: utf-8 -*-
"""AI Agent: 记忆管理 / 工具注册 / Agent 循环 / 上下文构建 / LLM 调用。"""
import os
import json
import time
import re
import urllib.parse
import urllib.request
import urllib.error

from .constants import *
from .store import *

__all__ = [
    "make_tool_result", "TokenEstimator", "ContextCompressor", "MemoryManager",
    "MEMORY", "ToolRegistry", "AgentLoopController", "ContextBuilder",
    "AI_SYSTEM_PROMPT", "TOOL_DEFS", "AI_TOOLS", "register_default_tools",
    "extract_actions", "call_llm_api", "call_llm_stream", "ACTION_PATTERN",
]

# __ACTION__:<type>:<arg> —— 工具用它把"必须在浏览器里执行的动作"回传给前端。
# 提成常量是因为两处必须完全一致: 这里抽取动作, routes.py 在最终回答里把它清掉。
# 不一致的话, 动作指令会原样漏进聊天气泡给用户看到。
ACTION_PATTERN = r'__ACTION__:([a-z_]+):([^\s"\'\\]+)'

# ---- 联网搜索 + 下载书籍 ----
def _tool_web_search(args, context):
    """联网搜索: 使用 Bing 搜索互联网"""
    query = args.get("query", "").strip()
    if not query:
        return make_tool_result(False, error="搜索关键词不能为空", retryable=False)
    try:
        import re as _re
        # Bing 中文分词有问题: 加引号反而拆词(人工智能→人工)
        # 解决: 不加引号 + setlang=en-US + mkt=en-US 让 Bing 用英文索引
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&count=10&setlang=en-US&mkt=en-US"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # 解析 Bing 搜索结果
        results = []
        # Bing 结果在 <li class="b_algo"> 块中
        items = _re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, _re.DOTALL)
        for item in items:
            # 提取标题和 URL (在 <h2><a href="URL">TITLE</a></h2> 中)
            link_match = _re.search(r'<h2[^>]*><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a></h2>', item, _re.DOTALL)
            if not link_match:
                continue
            raw_url = link_match.group(1)
            title_html = link_match.group(2)
            title = _re.sub(r'<[^>]+>', '', title_html).strip()
            # 提取摘要 (在 <p class="b_lineclamp...">SNIPPET</p> 中)
            snip_match = _re.search(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', item, _re.DOTALL)
            snippet = ""
            if snip_match:
                snippet = _re.sub(r'<[^>]+>', '', snip_match.group(1)).strip()[:200]
            if title and raw_url:
                results.append({"title": title, "url": raw_url, "snippet": snippet})
        # 额外提取页面中所有 PDF/EPUB 直链 (在 b_algo 之外的链接中)
        all_links = _re.findall(r'href="(https?://[^"]+)"', html)
        file_links = []
        for l in all_links:
            lower = l.lower()
            if lower.endswith('.pdf') or lower.endswith('.epub') or lower.endswith('.txt') or lower.endswith('.mobi') or lower.endswith('.azw3'):
                # 去重
                if l not in file_links:
                    file_links.append(l)
        if not results:
            return make_tool_result(True, data={"results": [], "message": "未找到相关结果, 请尝试其他关键词"})
        return make_tool_result(True, data={
            "results": results[:10],
            "count": len(results[:10]),
            "query": query,
            "file_links": file_links[:5],
            "file_links_count": len(file_links),
            "tip": "results 是网页结果; file_links 是直接的 PDF/EPUB/TXT 下载链接, 可直接用于 download_book 工具" if file_links else "",
        })
    except Exception as e:
        return make_tool_result(False, error="搜索失败: {}".format(str(e)), retryable=True)


def _tool_download_book(args, context):
    """从 URL 下载书籍并自动导入书架"""
    url = args.get("url", "").strip()
    title = args.get("title", "").strip()
    if not url:
        return make_tool_result(False, error="下载 URL 不能为空", retryable=False)
    if not url.startswith(("http://", "https://")):
        return make_tool_result(False, error="URL 必须以 http:// 或 https:// 开头", retryable=False)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            # 从 URL 或 Content-Disposition 提取文件名
            cd = resp.headers.get("Content-Disposition", "")
            fname = ""
            if "filename=" in cd:
                import re as _re
                m = _re.search(r'filename="?([^";\n]+)"?', cd)
                if m:
                    fname = m.group(1)
            if not fname:
                # 从 URL 路径提取
                path_part = urllib.parse.urlparse(url).path
                fname = os.path.basename(path_part) or "download"
            # 确保有扩展名
            ext = os.path.splitext(fname)[1].lower()
            if not ext or ext not in SUPPORTED_EXT:
                # 尝试从 Content-Type 推断
                ct = resp.headers.get("Content-Type", "").lower()
                ct_map = {"application/pdf": ".pdf", "application/epub+zip": ".epub", "text/plain": ".txt"}
                for k, v in ct_map.items():
                    if k in ct:
                        ext = v
                        break
                if not ext:
                    return make_tool_result(False, error="无法确定文件格式, 请指定正确的下载链接 (支持 PDF/EPUB/TXT/MOBI/AZW3)", retryable=False)
                fname = fname + ext if not fname.endswith(ext) else fname
            # 沙箱验证: 确保文件名安全
            fname = os.path.basename(fname)
            dest = os.path.join(BOOKS_DIR, fname)
            dest = os.path.abspath(dest)
            if not validate_sandbox_path(dest):
                return make_tool_result(False, error="文件名不安全", retryable=False)
            # 如果文件已存在, 添加序号
            if os.path.exists(dest):
                base, ext2 = os.path.splitext(fname)
                for i in range(2, 100):
                    candidate = os.path.join(BOOKS_DIR, "{}_{}{}".format(base, i, ext2))
                    if not os.path.exists(candidate):
                        dest = candidate
                        fname = os.path.basename(candidate)
                        break
            # 下载文件
            data = resp.read()
            if len(data) < 100:
                return make_tool_result(False, error="下载内容过小, 可能是无效链接", retryable=False)
            with open(dest, "wb") as f:
                f.write(data)
            # 自动导入到书架。
            # key 必须是裸文件名 —— scan_books() 返回的 id 就是裸文件名, 两边不一致
            # 会导致中文/空格书名的 meta 查不到, title 静默退化成文件名。
            bid = fname
            lib = load_library()
            meta = lib.setdefault("books", {}).setdefault(bid, {})
            meta["title"] = title or os.path.splitext(fname)[0]
            meta["addedAt"] = int(time.time())
            save_library(lib)
            return make_tool_result(True, data={
                "bookId": bid,
                "fileName": fname,
                "title": meta["title"],
                "size": len(data),
                "message": "已下载《{}》并导入书架 ({})".format(meta["title"], _format_size(len(data))),
            })
    except urllib.error.HTTPError as e:
        return make_tool_result(False, error="下载失败 (HTTP {}): {}".format(e.code, e.reason), retryable=True)
    except Exception as e:
        return make_tool_result(False, error="下载失败: {}".format(str(e)), retryable=True)


# --------------------------------------------------------------------------- #
#  Agent 架构: MemoryManager / ToolRegistry / AgentLoopController /
#               ContextBuilder / 工具定义 / LLM 调用
# --------------------------------------------------------------------------- #

# ---- 标准化工具结果 ----
def make_tool_result(ok, data=None, error="", retryable=False):
    """构造统一的 ToolResult: {ok, data, error, retryable}"""
    return {"ok": bool(ok), "data": data, "error": error, "retryable": bool(retryable)}


# ---- 0. TokenEstimator ----
class TokenEstimator:
    """粗略估算 token 数 (中文≈1.5字/token, 英文≈4字/token)"""

    @staticmethod
    def estimate(text):
        if not text:
            return 0
        text = str(text)
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4)

    @staticmethod
    def estimate_messages(messages):
        total = 0
        for m in messages:
            total += TokenEstimator.estimate(m.get("content", ""))
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    total += TokenEstimator.estimate(json.dumps(tc, ensure_ascii=False))
        return total


def _cfg_int(config, key, default):
    """从配置里取一个正整数; 缺失 / 非法 / 非正数都退回默认值。"""
    try:
        v = int((config or {}).get(key) or 0)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


# ---- 0b. ContextCompressor ----
class ContextCompressor:
    """上下文压缩: 当消息历史超 token 预算时, 调 LLM 生成结构化摘要替换旧消息

    压缩原则: 压缩后的信息必须保证 Agent 能无缝继续下一阶段工作。
    即: 保留用户意图、已完成操作及关键结果、未完成任务、工具调用参数与返回数据。
    """

    # 这组值原本是 12000/8000/6/800 —— 那是按 8k~32k 上下文模型定的尺码。
    # 默认模型现在是 1M 上下文的 deepseek-flash, 压到 12k 是纯粹的浪费,
    # 而且压缩过程会顺手把书籍正文挤掉(见 _summarize_tool_result)。
    # 保留这组类常量作为兜底, 实际取值可在设置里用 context_budget 覆盖。
    TOKEN_BUDGET = 120000      # 总 token 预算 (留给模型回复空间)
    SUMMARY_THRESHOLD = 96000  # 超过此值触发压缩
    KEEP_RECENT = 8            # 压缩时保留最近 N 条消息 (不截断)
    TOOL_RESULT_KEEP = 6000    # 工具返回结果在摘要请求中的保留长度 (字符)

    def __init__(self, config):
        self.config = config
        # 实例属性遮蔽类属性, 只影响这一个实例
        self.TOKEN_BUDGET = _cfg_int(config, "context_budget", self.TOKEN_BUDGET)
        self.SUMMARY_THRESHOLD = int(self.TOKEN_BUDGET * 0.8)

    def should_compress(self, messages):
        """检查是否需要压缩"""
        return TokenEstimator.estimate_messages(messages) > self.SUMMARY_THRESHOLD

    def compress(self, messages):
        """压缩消息历史: 保留 system + 最近 N 条, 中间部分生成结构化摘要

        摘要包含:
        1. 用户核心意图
        2. 已调用工具及其参数
        3. 工具返回的关键数据 (不截断重要结果)
        4. Agent 已得出的结论
        5. 未完成的任务 / 下一步计划
        """
        if not self.should_compress(messages):
            return messages

        # 分离 system 消息和工具调用链
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= self.KEEP_RECENT:
            return messages

        # 智能选择保留边界: 不在 tool_calls → tool 的中间截断
        split_idx = len(non_system) - self.KEEP_RECENT
        # 向前调整, 确保不切断 tool_calls→tool 配对
        while split_idx > 0:
            msg = non_system[split_idx]
            role = msg.get("role", "")
            if role == "tool":
                # 检查前一条是否有对应的 tool_calls
                prev = non_system[split_idx - 1] if split_idx > 0 else None
                if prev and prev.get("tool_calls"):
                    split_idx -= 2
                    continue
            break
        to_compress = non_system[:split_idx]
        keep_recent = non_system[split_idx:]

        if not to_compress:
            return messages

        # 构建摘要请求
        summary_prompt = self._build_summary_prompt(to_compress)
        summary = self._generate_summary(summary_prompt)

        if summary:
            summary_msg = {
                "role": "system",
                "content": "[对话摘要 - 压缩自 {} 条历史消息]\n{}".format(
                    len(to_compress), summary),
            }
            return system_msgs + [summary_msg] + keep_recent
        else:
            # 摘要失败, 降级为简单裁剪 + 关键信息提取
            key_info = self._extract_key_info(to_compress)
            note = {"role": "system",
                    "content": "[历史已裁剪] 之前 {} 条消息已省略。\n{}".format(
                        len(to_compress), key_info)}
            return system_msgs + [note] + keep_recent

    def _build_summary_prompt(self, messages):
        """构建摘要请求: 要求 LLM 生成结构化摘要, 保留 Agent 继续工作所需的信息"""
        lines = []
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")

            if role == "tool":
                # 工具返回结果: 尝试解析 JSON, 提取关键数据
                tool_content = self._summarize_tool_result(content)
                lines.append("[工具返回] {}".format(tool_content))

            elif m.get("tool_calls"):
                # 工具调用: 保留工具名和参数
                parts = []
                for tc in m["tool_calls"]:
                    fn_name = tc.get("function", {}).get("name", "")
                    fn_args = tc.get("function", {}).get("arguments", "")
                    parts.append("{}({})".format(fn_name, fn_args))
                lines.append("[Agent调用工具] {}".format(" | ".join(parts)))

            elif role == "user":
                lines.append("用户: {}".format(content))

            elif role == "assistant":
                lines.append("Agent: {}".format(content))

            else:
                lines.append("{}: {}".format(role, content))

        return (
            "你需要将以下对话历史压缩为结构化摘要, 要求:\n"
            "1. 保留用户的原始意图和请求\n"
            "2. 列出已调用的工具名及其参数\n"
            "3. 保留工具返回的关键数据 (如书籍列表、笔记内容、分类等)\n"
            "4. 记录 Agent 已得出的结论或已完成的操作\n"
            "5. 如果有未完成的任务, 明确写出下一步该做什么\n"
            "6. 摘要长度控制在 400 字以内\n\n"
            "对话历史:\n" + "\n".join(lines))

    def _summarize_tool_result(self, content):
        """提取工具返回结果中的关键数据, 避免截断丢失信息"""
        try:
            result = json.loads(content)
            if result.get("ok"):
                data = result.get("data", {})
                # 书籍列表: 保留标题和 ID
                if "books" in data:
                    books = data["books"]
                    items = ["《{}》({})".format(
                        b.get("title", b.get("id", "")), b.get("id", ""))
                        for b in books[:20]]
                    return "找到 {} 本书: {}".format(
                        data.get("count", len(books)),
                        "; ".join(items) + ("..." if len(books) > 20 else ""))
                # 书籍内容: 保留较大片段, 并告知总量与继续读取的方式。
                # 原来是 data["content"][:500] —— 正文一变长, 模型就只看得到开头,
                # 却以为自己读完了整本书, 表现为"总结得极浅"。而且这个截断是静默的,
                # 只改 get_book_content 的 max_chars 而不同步改这里, 等于白改。
                if "content" in data:
                    seg = data["content"]
                    total = data.get("totalChars") or len(seg)
                    head = seg[:3000]
                    if data.get("hasMore"):
                        note = "...[已截断, 全文共 {} 字, 可用 offset={} 继续读取]".format(
                            total, data.get("nextOffset"))
                    elif len(seg) > 3000:
                        note = "...[已截断, 本段共 {} 字]".format(len(seg))
                    else:
                        note = ""
                    return "书籍文本(共{}字): {}{}".format(total, head, note)
                # 分类列表
                if "categories" in data:
                    cats = data["categories"]
                    return "分类: {}".format(
                        "; ".join("{}({}本)".format(c["name"], c["count"]) for c in cats))
                # 其他数据: 保留 JSON
                return json.dumps(data, ensure_ascii=False)[:600]
            else:
                return "失败: {}".format(result.get("error", "未知错误"))
        except (json.JSONDecodeError, TypeError):
            return content[:600] if content else "(空)"

    def _extract_key_info(self, messages):
        """降级方案: 摘要失败时, 从消息中提取关键信息 (不调 LLM)"""
        user_msgs = []
        tool_calls = []
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                user_msgs.append(m["content"][:200])
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tool_calls.append("{}({})".format(
                        tc.get("function", {}).get("name", ""),
                        tc.get("function", {}).get("arguments", "")[:100]))
        parts = []
        if user_msgs:
            parts.append("用户请求: " + " | ".join(user_msgs[:3]))
        if tool_calls:
            parts.append("已调用工具: " + " | ".join(tool_calls[:5]))
        return "\n".join(parts) if parts else "(无关键信息)"

    def _generate_summary(self, prompt):
        """调用 LLM 生成摘要"""
        try:
            resp = call_llm_api(self.config, [{"role": "user", "content": prompt}])
            if "error" not in resp:
                return resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            pass
        return None


# ---- 1. MemoryManager ----
class MemoryManager:
    """长期记忆 (data/agent_memory.json) + 短期记忆 (按会话 in-memory) + 会话摘要 (持久化)"""

    _DEFAULT = {
        "profile": {"language": "zh"},
        "preferences": {"summaryStyle": "detailed", "outputLanguage": "zh"},
        "readingHabits": {"favoriteTopics": [], "frequentlyReadBooks": []},
        "stableFacts": [],  # [{id, content, type, confidence, source, createdAt, updatedAt}]
        "sessionSummaries": {},  # {session_id: summary_string} 持久化
    }

    SHORT_TERM_MAX = 20   # 单个会话最大短时消息数
    SHORT_TERM_KEEP = 12  # 超限时保留最近的消息条数

    def __init__(self):
        self._memory = {}
        self._short_term = {}  # session_id -> [messages]
        self._config = None    # 由外部 set_config 注入, 供 ContextCompressor 使用
        self.load_memory()

    def set_config(self, config):
        """注入 AI 配置, 使 _summarize_old 能调用 LLM 生成摘要"""
        self._config = config

    # ---- 长期记忆 ----
    def load_memory(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    self._memory = json.load(f)
            except Exception:
                self._memory = json.loads(json.dumps(self._DEFAULT))
        else:
            self._memory = json.loads(json.dumps(self._DEFAULT))
        # 补全缺失的字段
        for k, v in self._DEFAULT.items():
            if k not in self._memory:
                self._memory[k] = json.loads(json.dumps(v))
        return self._memory

    def save_memory(self):
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._memory, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MEMORY_FILE)

    def add_fact(self, content, type="fact", confidence=0.8, source="user"):
        now = int(time.time())
        fact = {
            "id": "fact-{}-{}".format(now, id(content) & 0xffff),
            "content": content,
            "type": type,
            "confidence": float(confidence),
            "source": source,
            "createdAt": now,
            "updatedAt": now,
        }
        self._memory.setdefault("stableFacts", []).append(fact)
        self.save_memory()
        return fact

    def remove_fact(self, fact_id):
        facts = self._memory.get("stableFacts", [])
        before = len(facts)
        self._memory["stableFacts"] = [f for f in facts if f.get("id") != fact_id]
        if len(self._memory["stableFacts"]) != before:
            self.save_memory()
            return True
        return False

    def get_relevant_memory(self, query):
        """简单的关键词匹配: 检索与 query 相关的长期事实"""
        query_lower = (query or "").lower()
        keywords = [w for w in re.split(r'[\s,，。.!！?？、；;:：()（）]+', query_lower) if w]
        facts = self._memory.get("stableFacts", [])
        relevant = []
        for fact in facts:
            content_lower = fact.get("content", "").lower()
            if any(kw in content_lower for kw in keywords):
                relevant.append(fact)
        # 若无关键词匹配, 返回全部 (帮助 Agent 在无明确线索时仍可见记忆概貌)
        if not relevant and keywords:
            relevant = list(facts)
        return relevant

    # ---- 会话摘要 (持久化到 agent_memory.json) ----
    def get_session_summary(self, session_id):
        summaries = self._memory.get("sessionSummaries", {})
        return summaries.get(session_id, "")

    def set_session_summary(self, session_id, summary):
        summaries = self._memory.setdefault("sessionSummaries", {})
        summaries[session_id] = summary
        self.save_memory()

    # ---- 短期记忆 ----
    def get_short_term(self, session_id):
        return self._short_term.get(session_id, [])

    def add_short_term(self, session_id, message):
        msgs = self._short_term.setdefault(session_id, [])
        msgs.append(message)
        if len(msgs) > self.SHORT_TERM_MAX:
            self._summarize_old(session_id)

    def _summarize_old(self, session_id):
        """超出上限时通过 ContextCompressor 生成摘要压缩较早的消息 (保留 system + 最近 N 条)"""
        msgs = self._short_term.get(session_id, [])
        if len(msgs) <= self.SHORT_TERM_MAX:
            return
        # 优先使用 LLM 摘要 (需注入 config)
        if self._config:
            try:
                compressor = ContextCompressor(self._config)
                system_msgs = [m for m in msgs if m.get("role") == "system"]
                non_system = [m for m in msgs if m.get("role") != "system"]
                if len(non_system) > self.SHORT_TERM_KEEP:
                    to_compress = non_system[:-self.SHORT_TERM_KEEP]
                    keep_recent = non_system[-self.SHORT_TERM_KEEP:]
                    prompt = compressor._build_summary_prompt(to_compress)
                    summary = compressor._generate_summary(prompt)
                    if summary:
                        summary_msg = {
                            "role": "system",
                            "content": "[对话摘要] 以下是之前对话的要点:\n" + summary,
                        }
                        self._short_term[session_id] = system_msgs + [summary_msg] + keep_recent
                        return
            except Exception:
                pass
        # 降级: 简单裁剪 (保留首条 + 最近 N 条)
        first = msgs[0] if msgs else None
        recent = msgs[-self.SHORT_TERM_KEEP:]
        dropped = len(msgs) - self.SHORT_TERM_KEEP - (1 if first else 0)
        summary = {
            "role": "system",
            "content": "[历史对话已压缩] 之前 {} 条消息已被省略, 请基于最近的上下文继续。".format(max(dropped, 0)),
        }
        new_list = ([first, summary] if first else [summary]) + recent
        self._short_term[session_id] = new_list

    def trim_short_term(self, messages):
        """对纯文本消息列表做安全裁剪 (不破坏 tool_calls 结构)"""
        if len(messages) <= self.SHORT_TERM_MAX:
            return messages
        first = messages[0]
        rest = messages[1:]
        if len(rest) <= self.SHORT_TERM_KEEP:
            return messages
        dropped = len(rest) - self.SHORT_TERM_KEEP
        note = {"role": "system",
                "content": "[历史对话已压缩] 之前 {} 条消息已省略。".format(dropped)}
        return [first, note] + rest[-self.SHORT_TERM_KEEP:]


# 全局 MemoryManager 实例
MEMORY = MemoryManager()


# ---- 2. ToolRegistry ----
class ToolRegistry:
    """工具注册表: 每个 tool 返回统一 ToolResult"""

    def __init__(self):
        self._tools = {}  # name -> {"handler": fn, "schema": dict}

    def register(self, name, handler, schema):
        self._tools[name] = {"handler": handler, "schema": schema}

    def get_schemas(self):
        """返回 OpenAI function-calling 格式的工具列表"""
        return [self._tools[n]["schema"] for n in self._tools]

    def has(self, name):
        return name in self._tools

    def execute(self, name, args, context=None):
        if not self.has(name):
            return make_tool_result(False, error="未知工具: {}".format(name), retryable=False)
        handler = self._tools[name]["handler"]
        try:
            return handler(args, context)
        except Exception as e:
            return make_tool_result(False, error="工具执行出错: {}".format(e), retryable=True)


# ---- 3. AgentLoopController ----
class AgentLoopController:
    """Agent 循环控制: 步数 / 重复 / 失败 / 超时 限制 + 步骤日志"""

    MAX_STEPS = 12           # 最大思考步数 (长上下文下允许多读几轮)
    MAX_SAME_TOOL_CALLS = 2
    MAX_FAILURES = 2
    # 单步超时原本 30 秒 —— 是按 8k 上下文的调用耗时定的。往 1M 上下文里塞
    # 一本书的正文, 单次 LLM 调用轻松超过 30 秒, 于是下一轮 should_stop()
    # 会判"当前步骤执行超时"直接中断。不放宽的话, 长上下文必然随机超时,
    # 问题会从"提不出文本"变成"莫名其妙地失败", 反而更难定位。
    TIMEOUT_PER_STEP = 120  # 单步超时 (秒)
    MAX_TOTAL_TIME = 600    # Agent 总执行超时 (秒)

    def __init__(self, config=None):
        config = config or {}
        self.TIMEOUT_PER_STEP = _cfg_int(config, "step_timeout", self.TIMEOUT_PER_STEP)
        self.MAX_TOTAL_TIME = _cfg_int(config, "total_timeout", self.MAX_TOTAL_TIME)
        self.step = 0
        self.failures = 0
        self.call_history = []  # [(name, args_key)]
        self.start_time = time.time()
        self.step_start = self.start_time
        self._log = []  # [{"step", "tool", "ok", "duration"}]

    def check_duplicate(self, name, args):
        """返回 (是否重复, 已调用次数)"""
        args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
        count = sum(1 for (n, a) in self.call_history if n == name and a == args_key)
        return count >= self.MAX_SAME_TOOL_CALLS, count

    def record_call(self, name, args):
        args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
        self.call_history.append((name, args_key))

    def record_failure(self):
        self.failures += 1

    def begin_step(self):
        """标记新一步开始, 重置单步计时"""
        self.step_start = time.time()

    def should_stop(self):
        if self.step >= self.MAX_STEPS:
            return True, "已达到最大思考步数({}步)限制。请基于已获取的信息, 直接回答用户的问题, 不要再调用工具。".format(self.MAX_STEPS)
        if self.failures >= self.MAX_FAILURES:
            return True, "工具调用连续失败次数达到上限({}次)。请基于已获取的信息, 直接回答用户的问题。".format(self.MAX_FAILURES)
        elapsed = time.time() - self.start_time
        if elapsed > self.MAX_TOTAL_TIME:
            return True, "Agent 执行总时长已超过 {} 秒上限。请基于已获取的信息, 直接回答用户的问题。".format(self.MAX_TOTAL_TIME)
        if self.step > 0:
            step_elapsed = time.time() - self.step_start
            if step_elapsed > self.TIMEOUT_PER_STEP:
                return True, "当前步骤执行超时({}秒)。请基于已获取的信息, 直接回答用户的问题。".format(self.TIMEOUT_PER_STEP)
        return False, ""

    def log_step(self, step, tool_name, result_ok):
        """记录执行日志 (便于调试)"""
        self._log.append({
            "step": step,
            "tool": tool_name,
            "ok": bool(result_ok),
            "duration": round(time.time() - self.step_start, 2),
        })

    def get_log(self):
        """返回执行日志"""
        return list(self._log)


# ---- 4. ContextBuilder ----
class ContextBuilder:
    """组装上下文: 系统提示 + 长期记忆 + 会话摘要 + 当前书籍上下文 + 短期历史 (按需压缩)"""

    def __init__(self, memory_manager, config=None):
        self.memory = memory_manager
        self.config = config
        self.compressor = ContextCompressor(config) if config else None

    def build(self, messages, context, session_id="default"):
        parts = [AI_SYSTEM_PROMPT]

        mem = self.memory.load_memory()
        # 用户偏好
        prefs = mem.get("preferences", {})
        if prefs:
            parts.append("\n\n[用户偏好]\n" + json.dumps(prefs, ensure_ascii=False, indent=2))
        # 阅读习惯
        habits = mem.get("readingHabits", {})
        if habits and (habits.get("favoriteTopics") or habits.get("frequentlyReadBooks")):
            parts.append("\n\n[阅读习惯]\n" + json.dumps(habits, ensure_ascii=False, indent=2))
        # 长期事实
        facts = mem.get("stableFacts", [])
        if facts:
            fact_lines = ["- {}".format(f.get("content", "")) for f in facts]
            parts.append("\n\n[长期记忆 - 已知事实]\n" + "\n".join(fact_lines))

        # 会话摘要 (来自之前对话, 持久化)
        session_summary = self.memory.get_session_summary(session_id)
        if session_summary:
            parts.append("\n\n[本会话历史摘要]\n" + session_summary)

        # 当前阅读上下文
        book_ctx = self._build_book_context(context)
        if book_ctx:
            parts.append("\n\n[当前阅读上下文]\n" + book_ctx)

        system_content = "".join(parts)
        built = [{"role": "system", "content": system_content}] + messages

        # 按需压缩 (token 预算超出时调 LLM 生成摘要)
        if self.compressor and self.compressor.should_compress(built):
            built = self.compressor.compress(built)

        return built

    def _build_book_context(self, context):
        if not context:
            return ""
        book_id = context.get("currentBookId")
        if not book_id:
            return "当前未打开任何书籍。"
        if not book_path(book_id):
            return "当前书籍 ID: {} (文件不存在于书架)。".format(book_id)
        lib = load_library().get("books", {})
        meta = lib.get(book_id, {})
        title = meta.get("title") or os.path.splitext(book_id)[0]
        author = meta.get("author", "")
        cat = meta.get("category", "未分类")
        progress = meta.get("progress", 0)
        page = context.get("currentPage") or meta.get("page", 0)
        lines = [
            "当前书籍: 《{}》{}".format(title, " - " + author if author else ""),
            "书籍ID: {}".format(book_id),
            "分类: {}".format(cat),
            "阅读进度: {:.0f}%".format(progress * 100),
            "当前页码: {}".format(page),
        ]
        return "\n".join(lines)


# ---- 系统提示 ----
AI_SYSTEM_PROMPT = """你是「阅微」, 一个本地电子书书架的智能 AI 助手, 基于 Agent 架构运行。你可以通过调用工具来完成用户的请求。

## 安全沙箱
- 你运行在沙箱环境中, 所有文件操作被限制在书籍目录和数据目录内。
- 你无法访问系统其他文件, 无法执行系统命令, 无法访问网络以外的资源。
- 这确保了你的操作不会影响用户的系统安全。

## 你的能力 (可用工具)
{TOOL_LIST}

## 行为规则
- 用简洁友好的中文回答。
- 当用户要求总结一本书时, 先调用 get_book_content 获取内容, 再进行总结。
  长书是分段返回的: hasMore=true 说明还有没读到的部分, 用 nextOffset 接着读;
  需要通读全书才能回答的问题(如"整体脉络"), 应当连续翻页直到 hasMore=false 再下结论,
  不要只看了开头就当作读完了整本。若返回 status=need_client_extract, 说明这本 PDF
  还没提取过文字, 用完全相同的参数再调一次(get_book_content 每次返回的内容都不同,
  重调时参数必须一致)。
- **当用户要求分类多本书时, 先调用 list_books 获取书单, 然后直接用 batch_categorize 一次性完成全部分类, 不要逐本调用 categorize_book。**
- **当用户要求下载书籍时: 1) 先用 web_search 搜索 "书名 作者 pdf" (不要加引号, 不要加 filetype), 2) 检查返回的 file_links 字段是否有 PDF 直链, 3) 如果有就调用 download_book 下载, 4) 如果没有 file_links, 换个搜索词再搜一次 (如英文书名), 5) 最多搜索 3 次, 不要无限搜索.**
- **重要: 搜索次数不要超过 3 次, 如果 3 次都没找到 PDF 直链, 就基于知识库推荐书籍并告知用户.**
- **不要在同一 step 中调用 3 次 web_search, 每次只搜 1 个关键词, 看结果再决定下一步.**
- 如果用户提到偏好或重要信息, 主动调用 remember_preference 保存到长期记忆。
- 不要用相同参数重复调用同一个工具, 请直接使用之前返回的结果。
- 工具调用失败时不要无限重试, 基于已有信息回答即可。
- 每个工具返回统一结构 {ok, data, error, retryable}; ok 为 false 时表示失败。
- 当操作需要在浏览器执行时 (如打开书籍), 工具会返回 __ACTION__ 指令, 你也可以在最终回答中包含它。

## 记忆机制
- 你拥有长期记忆, 可以记住用户的偏好、阅读习惯和重要事实。
- 主动使用 remember_preference 保存用户告诉你的偏好或事实。
- 回答时可参考 recall_memory 检索到的相关记忆, 以及上下文中注入的长期记忆摘要。"""


# ---- 工具执行函数 (每个返回 ToolResult) ----
def _tool_list_books(args, context):
    books = scan_books()
    lib = load_library()
    trash = lib.get("trash", {})
    data = []
    for b in books:
        # 排除回收站中的书籍
        if b["id"] in trash:
            continue
        meta = get_book_meta(lib, b["id"])
        title = meta.get("title") or os.path.splitext(b["name"])[0]
        data.append({
            "id": b["id"],
            "title": title,
            "author": meta.get("author", ""),
            "format": b["format"],
            "category": meta.get("category", "未分类"),
            "progress": meta.get("progress", 0),
        })
    return make_tool_result(True, data={"books": data, "count": len(data)})


def _tool_find_books(args, context):
    q = (args.get("query", "") or "").lower()
    books = scan_books()
    lib = load_library()
    trash = lib.get("trash", {})
    data = []
    for b in books:
        # 排除回收站中的书籍
        if b["id"] in trash:
            continue
        meta = get_book_meta(lib, b["id"])
        title = meta.get("title") or os.path.splitext(b["name"])[0]
        author = meta.get("author", "")
        if q in title.lower() or q in author.lower() or q in b["id"].lower():
            data.append({
                "id": b["id"],
                "title": title,
                "author": author,
                "category": meta.get("category", "未分类"),
            })
    return make_tool_result(True, data={"books": data, "count": len(data)})


def _tool_get_book_content(args, context):
    bid = args.get("book_id", "")
    full = book_path(bid)
    if not full:
        return make_tool_result(False, error="找不到这本书: {}".format(bid), retryable=False)
    ext = os.path.splitext(full)[1].lower()
    fmt = SUPPORTED_EXT.get(ext, "")
    r = read_book_text(bid, full, fmt,
                       offset=args.get("offset") or 0,
                       limit=args.get("limit"),
                       page=args.get("page"))
    if not r.get("ok"):
        if r.get("status") == "need_client":
            # PDF 还没抽过文字。注意这不是"格式不支持", 而是"还没抽" ——
            # 必须 retryable=True, 否则连续两次失败会让 Agent 提前停机,
            # 用户看到的是"AI 突然不理人了", 完全归因不到 PDF 上。
            return make_tool_result(True, data={
                "content": "",
                "format": fmt,
                "bookId": bid,
                "status": "need_client_extract",
                "message": ("这本书是 PDF, 后端还没提取过它的文字(本机没装 PyMuPDF), "
                            "所以现在读不到内容。已通过下面的指令通知前端用 pdf.js 开始提取, "
                            "这通常要几秒到一分钟。请如实告诉用户: 文字正在提取中, "
                            "等一会儿再问一次。不要立刻重试, 重试也还是空的。"
                            "__ACTION__:extract_text:{}".format(bid)),
            })
        return make_tool_result(False, error="无法提取 {} 格式书籍的文本内容: {}".format(fmt or ext, bid), retryable=False)
    return make_tool_result(True, data={
        "content": r["content"],
        "format": fmt,
        "bookId": bid,
        "totalChars": r["totalChars"],
        "offset": r["offset"],
        "length": r["length"],
        "hasMore": r["hasMore"],
        "nextOffset": r["nextOffset"],
        "source": r["source"],
        "pageCount": r.get("pageCount"),
    })


def _tool_get_book_metadata(args, context):
    bid = args.get("book_id", "")
    if not book_path(bid):
        return make_tool_result(False, error="找不到这本书: {}".format(bid), retryable=False)
    lib = load_library().get("books", {})
    meta = lib.get(bid, {})
    title = meta.get("title") or os.path.splitext(bid)[0]
    return make_tool_result(True, data={
        "bookId": bid,
        "title": title,
        "author": meta.get("author", ""),
        "category": meta.get("category", "未分类"),
        "progress": meta.get("progress", 0),
        "lastRead": meta.get("lastRead", 0),
    })


def _tool_categorize_book(args, context):
    bid = args.get("book_id", "")
    cat = args.get("category", "")
    if not book_path(bid):
        return make_tool_result(False, error="找不到这本书: {}".format(bid), retryable=False)
    lib = load_library()
    meta = lib.setdefault("books", {}).setdefault(bid, {})
    meta["category"] = cat
    cats = lib.setdefault("categories", [])
    if cat and cat not in cats:
        cats.append(cat)
    save_library(lib)
    return make_tool_result(True, data={
        "bookId": bid,
        "category": cat,
        "message": "已将《{}》分类为「{}」".format(meta.get("title", bid), cat),
    })


def _tool_batch_categorize(args, context):
    """批量设置多本书的分类, 一次调用完成."""
    assignments = args.get("assignments", [])
    if not assignments:
        return make_tool_result(False, error="缺少 assignments 参数", retryable=False)
    lib = load_library()
    cats = lib.setdefault("categories", [])
    results = []
    for item in assignments:
        bid = item.get("book_id", "")
        cat = item.get("category", "")
        if not book_path(bid):
            results.append({"book_id": bid, "ok": False, "error": "找不到"})
            continue
        meta = lib.setdefault("books", {}).setdefault(bid, {})
        meta["category"] = cat
        if cat and cat not in cats:
            cats.append(cat)
        results.append({"book_id": bid, "ok": True, "category": cat, "title": meta.get("title", bid)})
    save_library(lib)
    return make_tool_result(True, data={
        "results": results,
        "count": len(results),
        "message": "已批量分类 {} 本书".format(len(results)),
    })


def _tool_list_categories(args, context):
    cats = get_all_categories()
    lib = load_library().get("books", {})
    counts = {}
    for meta in lib.values():
        c = meta.get("category", "未分类")
        counts[c] = counts.get(c, 0) + 1
    data = [{"name": c, "count": counts.get(c, 0)} for c in cats]
    return make_tool_result(True, data={"categories": data, "count": len(data)})


def _tool_rename_category(args, context):
    old_name = (args.get("old_name", "") or "").strip()
    new_name = (args.get("new_name", "") or "").strip()
    if not old_name or not new_name:
        return make_tool_result(False, error="old_name 和 new_name 都不能为空", retryable=False)
    lib = load_library()
    cats = lib.setdefault("categories", [])
    if old_name not in cats:
        # 也检查书籍中是否有此分类
        has_books = any(m.get("category") == old_name for m in lib.get("books", {}).values())
        if not has_books:
            return make_tool_result(False, error="分类「{}」不存在".format(old_name), retryable=False)
    # 更新分类列表
    if old_name in cats:
        cats.remove(old_name)
    if new_name not in cats:
        cats.append(new_name)
    # 更新所有书籍的分类引用
    affected = 0
    for meta in lib.get("books", {}).values():
        if meta.get("category") == old_name:
            meta["category"] = new_name
            affected += 1
    save_library(lib)
    return make_tool_result(True, data={
        "oldName": old_name, "newName": new_name,
        "affectedBooks": affected,
        "message": "已将分类「{}」重命名为「{}」({}本书受影响)".format(old_name, new_name, affected),
    })


def _tool_delete_category(args, context):
    name = (args.get("name", "") or "").strip()
    if not name:
        return make_tool_result(False, error="分类名称不能为空", retryable=False)
    lib = load_library()
    cats = lib.get("categories", [])
    if name not in cats:
        return make_tool_result(False, error="分类「{}」不存在".format(name), retryable=False)
    cats.remove(name)
    # 该分类下的书籍归入未分类
    affected = 0
    for meta in lib.get("books", {}).values():
        if meta.get("category") == name:
            meta["category"] = ""
            affected += 1
    save_library(lib)
    return make_tool_result(True, data={
        "deletedCategory": name,
        "affectedBooks": affected,
        "message": "已删除分类「{}」, {}本书归入未分类".format(name, affected),
    })


def _tool_delete_book(args, context):
    bid = args.get("book_id", "")
    full = book_path(bid)
    if not full:
        return make_tool_result(False, error="找不到这本书: {}".format(bid), retryable=False)
    lib = load_library()
    meta = get_book_meta(lib, bid)
    title = meta.get("title", bid)
    # 软删除: 移入回收站, 不删除文件
    trash = lib.setdefault("trash", {})
    trash[bid] = {
        "title": title,
        "deletedAt": int(time.time()),
        "originalCategory": meta.get("category", ""),
        "path": full,
    }
    lib.get("books", {}).pop(bid, None)
    save_library(lib)
    return make_tool_result(True, data={
        "bookId": bid,
        "title": title,
        "message": "已将《{}》移入回收站 (30天后永久删除, 可用 restore_book 恢复)".format(title),
    })


def _tool_list_trash(args, context):
    """列出回收站中的所有书籍"""
    # 访问回收站时自动清理过期项
    expired = clean_expired_trash()
    lib = load_library()
    trash = lib.get("trash", {})
    now = int(time.time())
    data = []
    for bid, info in trash.items():
        deleted_at = info.get("deletedAt", 0)
        age = now - deleted_at
        remain = max(0, TRASH_RETENTION_SECONDS - age)
        # 剩余天数 (向上取整)
        remain_days = (remain + 86399) // 86400
        data.append({
            "bookId": bid,
            "title": info.get("title", bid),
            "deletedAt": deleted_at,
            "originalCategory": info.get("originalCategory", "未分类"),
            "remainDays": remain_days,
        })
    data.sort(key=lambda x: x.get("deletedAt", 0), reverse=True)
    return make_tool_result(True, data={
        "trash": data,
        "count": len(data),
        "expiredRemoved": len(expired),
        "message": "回收站共有 {} 本书".format(len(data)),
    })


def _tool_restore_book(args, context):
    """从回收站恢复一本书到书架"""
    bid = args.get("book_id", "")
    if not bid:
        return make_tool_result(False, error="book_id 不能为空", retryable=False)
    info, err = restore_from_trash(bid)
    if err:
        return make_tool_result(False, error="《{}》不在回收站中".format(bid), retryable=False)
    title = info.get("title", bid)
    return make_tool_result(True, data={
        "bookId": bid,
        "title": title,
        "message": "已从回收站恢复《{}》到书架".format(title),
    })


def _tool_delete_book_permanent(args, context):
    """永久删除回收站中的一本书 (删除文件, 不可恢复)"""
    bid = args.get("book_id", "")
    if not bid:
        return make_tool_result(False, error="book_id 不能为空", retryable=False)
    info, err = permanently_delete_from_trash(bid)
    if err:
        return make_tool_result(False, error="《{}》不在回收站中".format(bid), retryable=False)
    title = info.get("title", bid)
    return make_tool_result(True, data={
        "bookId": bid,
        "title": title,
        "message": "已永久删除《{}》, 文件已被移除".format(title),
    })


def _tool_open_book(args, context):
    bid = args.get("book_id", "")
    if not book_path(bid):
        return make_tool_result(False, error="找不到这本书: {}".format(bid), retryable=False)
    return make_tool_result(True, data={"action": "__ACTION__:open_book:{}".format(bid), "bookId": bid})


def _tool_create_note(args, context):
    bid = args.get("book_id", "")
    content = args.get("content", "")
    if not book_path(bid):
        return make_tool_result(False, error="找不到这本书: {}".format(bid), retryable=False)
    if not content:
        return make_tool_result(False, error="笔记内容不能为空", retryable=False)
    lib = load_library()
    meta = lib.setdefault("books", {}).setdefault(bid, {})
    notes = meta.setdefault("notes", [])
    now = int(time.time())
    note = {
        "id": "note-{}-{}".format(now, id(content) & 0xffff),
        "content": content,
        "page": meta.get("page", 0),
        "progress": meta.get("progress", 0),
        "createdAt": now,
        "updatedAt": now,
    }
    notes.append(note)
    notes.sort(key=lambda n: n.get("createdAt", 0))
    save_library(lib)
    return make_tool_result(True, data={
        "noteId": note["id"],
        "message": "已为《{}》创建笔记".format(meta.get("title", bid)),
    })


def _tool_remember_preference(args, context):
    content = args.get("content", "")
    type_ = args.get("type", "preference")
    confidence = args.get("confidence", 0.8)
    if not content:
        return make_tool_result(False, error="记忆内容不能为空", retryable=False)
    fact = MEMORY.add_fact(content, type_, confidence, source="agent")
    return make_tool_result(True, data={"factId": fact["id"], "message": "已保存到长期记忆"})


def _tool_recall_memory(args, context):
    query = args.get("query", "")
    facts = MEMORY.get_relevant_memory(query)
    prefs = MEMORY.load_memory().get("preferences", {})
    return make_tool_result(True, data={"facts": facts, "preferences": prefs, "count": len(facts)})


def _tool_get_reading_context(args, context):
    if not context:
        return make_tool_result(True, data={"message": "当前没有阅读上下文"})
    bid = context.get("currentBookId")
    if not bid:
        return make_tool_result(True, data={"message": "当前没有打开的书籍"})
    if not book_path(bid):
        return make_tool_result(False, error="当前书籍文件不存在: {}".format(bid), retryable=False)
    lib = load_library().get("books", {})
    meta = lib.get(bid, {})
    title = meta.get("title") or os.path.splitext(bid)[0]
    return make_tool_result(True, data={
        "bookId": bid,
        "title": title,
        "author": meta.get("author", ""),
        "category": meta.get("category", "未分类"),
        "progress": meta.get("progress", 0),
        "currentPage": context.get("currentPage", 0),
        "currentProgress": context.get("currentProgress", 0),
        "currentLabel": context.get("currentLabel", ""),
    })


# ---- 工具定义 (schema + handler) ----
TOOL_DEFS = [
    {
        "name": "list_books",
        "description": "列出书架上的所有书籍, 包括书名、作者、格式、分类和阅读进度",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_list_books,
    },
    {
        "name": "find_books",
        "description": "按关键词搜索书籍 (匹配书名、作者或文件名)",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
        "handler": _tool_find_books,
    },
    {
        "name": "get_book_content",
        "description": "获取一本书的文本内容, 用于总结或分析。长书会分段返回: "
                       "返回 hasMore=true 时, 用 nextOffset 作为下一次的 offset 继续读; "
                       "也可以用 page 直接跳到第 N 页。PDF 首次读取可能需要前端先提取文字, "
                       "此时 status=need_client_extract, 用相同参数再调一次即可。",
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "书籍ID (文件名)"},
                "offset": {"type": "integer", "description": "起始字符偏移, 默认 0"},
                "limit": {"type": "integer", "description": "本次取多少字符, 默认 24000, 上限 200000"},
                "page": {"type": "integer", "description": "跳到第 N 页 (1 基), 优先于 offset"},
            },
            "required": ["book_id"],
        },
        "handler": _tool_get_book_content,
    },
    {
        "name": "get_book_metadata",
        "description": "获取书籍的元数据: 标题、作者、分类、阅读进度",
        "parameters": {
            "type": "object",
            "properties": {"book_id": {"type": "string", "description": "书籍ID"}},
            "required": ["book_id"],
        },
        "handler": _tool_get_book_metadata,
    },
    {
        "name": "categorize_book",
        "description": "为书籍设置分类",
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "书籍ID"},
                "category": {"type": "string", "description": "分类名称"},
            },
            "required": ["book_id", "category"],
        },
        "handler": _tool_categorize_book,
    },
    {
        "name": "batch_categorize",
        "description": "批量设置多本书的分类(一次调用完成, 比逐本调用快很多)",
        "parameters": {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "description": "分类分配列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "book_id": {"type": "string", "description": "书籍ID"},
                            "category": {"type": "string", "description": "分类名称"},
                        },
                        "required": ["book_id", "category"],
                    },
                },
            },
            "required": ["assignments"],
        },
        "handler": _tool_batch_categorize,
    },
    {
        "name": "list_categories",
        "description": "列出所有已创建的分类及每个分类的书籍数量",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_list_categories,
    },
    {
        "name": "rename_category",
        "description": "重命名一个分类, 该分类下所有书籍的分类引用会同步更新",
        "parameters": {
            "type": "object",
            "properties": {
                "old_name": {"type": "string", "description": "当前分类名称"},
                "new_name": {"type": "string", "description": "新的分类名称"},
            },
            "required": ["old_name", "new_name"],
        },
        "handler": _tool_rename_category,
    },
    {
        "name": "delete_category",
        "description": "删除一个分类, 该分类下的书籍将归入未分类",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "要删除的分类名称"}},
            "required": ["name"],
        },
        "handler": _tool_delete_category,
    },
    {
        "name": "delete_book",
        "description": "从书架移除一本书 (移入回收站, 30天后永久删除, 可用 restore_book 恢复)",
        "parameters": {
            "type": "object",
            "properties": {"book_id": {"type": "string", "description": "书籍ID"}},
            "required": ["book_id"],
        },
        "handler": _tool_delete_book,
    },
    {
        "name": "list_trash",
        "description": "列出回收站中的所有书籍 (含删除时间、原分类、剩余保留天数)",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_list_trash,
    },
    {
        "name": "restore_book",
        "description": "从回收站恢复一本书到书架 (放回原分类)",
        "parameters": {
            "type": "object",
            "properties": {"book_id": {"type": "string", "description": "回收站中的书籍ID"}},
            "required": ["book_id"],
        },
        "handler": _tool_restore_book,
    },
    {
        "name": "delete_book_permanent",
        "description": "永久删除回收站中的一本书 (删除文件, 不可恢复)",
        "parameters": {
            "type": "object",
            "properties": {"book_id": {"type": "string", "description": "回收站中的书籍ID"}},
            "required": ["book_id"],
        },
        "handler": _tool_delete_book_permanent,
    },
    {
        "name": "open_book",
        "description": "在阅读器中打开一本书 (前端会执行打开操作)",
        "parameters": {
            "type": "object",
            "properties": {"book_id": {"type": "string", "description": "书籍ID"}},
            "required": ["book_id"],
        },
        "handler": _tool_open_book,
    },
    {
        "name": "create_note",
        "description": "为书籍创建一条笔记",
        "parameters": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "书籍ID"},
                "content": {"type": "string", "description": "笔记内容"},
            },
            "required": ["book_id", "content"],
        },
        "handler": _tool_create_note,
    },
    {
        "name": "remember_preference",
        "description": "将用户偏好或重要事实保存到长期记忆, 以便以后回忆",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的内容"},
                "type": {"type": "string", "description": "类型: preference/fact/habit", "default": "preference"},
                "confidence": {"type": "number", "description": "置信度 0-1", "default": 0.8},
            },
            "required": ["content"],
        },
        "handler": _tool_remember_preference,
    },
    {
        "name": "recall_memory",
        "description": "从长期记忆中检索与查询相关的记忆",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "检索关键词"}},
            "required": ["query"],
        },
        "handler": _tool_recall_memory,
    },
    {
        "name": "get_reading_context",
        "description": "获取用户当前正在阅读的书籍上下文 (当前书、页码、进度)",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_get_reading_context,
    },
    {
        "name": "web_search",
        "description": "联网搜索互联网, 获取搜索结果(标题、URL、摘要). 用于查找书籍下载链接、获取最新信息等",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
        "handler": _tool_web_search,
    },
    {
        "name": "download_book",
        "description": "从指定 URL 下载书籍文件并自动导入书架. 支持 PDF/EPUB/TXT/MOBI/AZW3 格式. 下载前可先用 web_search 搜索下载链接",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "书籍文件的下载 URL (http/https)"},
                "title": {"type": "string", "description": "书籍标题 (可选, 用于书架显示)"},
            },
            "required": ["url"],
        },
        "handler": _tool_download_book,
    },
]


def _render_tool_list():
    """把 TOOL_DEFS 渲染成系统提示词里的能力清单。

    这份清单以前是在提示词里手写的编号列表 —— 加到第 19 个工具时它还停在 17,
    已经和实际工具对不上了。改成从 TOOL_DEFS 生成, 以后加工具不用再记得同步这里。
    只取 description 的第一句, 免得把提示词撑得太长。
    """
    lines = []
    for i, d in enumerate(TOOL_DEFS, 1):
        desc = (d.get("description") or "").strip()
        first = re.split(r'[。;；]', desc)[0].strip()
        lines.append("{}. {} - {}".format(i, d["name"], first))
    return "\n".join(lines)


# 提示词里的 {TOOL_LIST} 占位符在这里落地 —— AI_SYSTEM_PROMPT 定义在 TOOL_DEFS
# 之前, 所以只能等 TOOL_DEFS 就绪之后再替换。
AI_SYSTEM_PROMPT = AI_SYSTEM_PROMPT.replace("{TOOL_LIST}", _render_tool_list())


# OpenAI function-calling 格式
AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": d["name"],
            "description": d["description"],
            "parameters": d["parameters"],
        },
    }
    for d in TOOL_DEFS
]


def register_default_tools(registry):
    """将所有内置工具注册到 ToolRegistry"""
    for d in TOOL_DEFS:
        schema = {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["parameters"],
            },
        }
        registry.register(d["name"], d["handler"], schema)


def extract_actions(text):
    """从文本中提取 __ACTION__:<type>:<arg> 动作指令。

    以前只认 open_book。改成泛化匹配, 新增动作类型时只要在下面加一个分支。
    """
    known = {"open_book", "extract_text"}
    actions = []
    for m in re.finditer(ACTION_PATTERN, text or ""):
        kind, arg = m.group(1).strip(), m.group(2).strip()
        if kind in known and arg:
            actions.append({"type": kind, "book_id": arg})
    return actions


# ---- LLM API 调用 ----
def call_llm_api(config, messages, tools=None):
    """调用 OpenAI 兼容 API (非流式), 用于工具调用轮次。带重试。"""
    endpoint = config.get("endpoint", "").rstrip("/")
    if not endpoint:
        endpoint = DEFAULT_AI_ENDPOINT
    url = "{}/chat/completions".format(endpoint)
    api_key = config.get("api_key", "")
    # 用 or 而不是 get(key, default): 配置里存在但为空串时也应退回默认模型
    model = config.get("model") or DEFAULT_AI_MODEL

    body = {"model": model, "messages": messages,
            "max_tokens": _cfg_int(config, "max_tokens", 8192)}
    if tools:
        body["tools"] = tools

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer {}".format(api_key))

    max_retries = 2
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            # 429/5xx 可重试
            if e.code in (429, 500, 502, 503) and attempt < max_retries:
                import time as _time
                _time.sleep(2 * (attempt + 1))
                # 重建 request (body 已被读)
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", "Bearer {}".format(api_key))
                last_err = "API错误 {}: {}".format(e.code, err_body[:300])
                continue
            return {"error": "API错误 {}: {}".format(e.code, err_body[:500])}
        except Exception as e:
            last_err = str(e)
            if attempt < max_retries:
                import time as _time
                _time.sleep(2 * (attempt + 1))
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", "Bearer {}".format(api_key))
                continue
            return {"error": str(e)}
    return {"error": last_err or "未知错误"}


def call_llm_stream(config, messages, tools=None):
    """调用 OpenAI 兼容 API (流式, 返回生成器)"""
    endpoint = config.get("endpoint", "").rstrip("/")
    if not endpoint:
        endpoint = DEFAULT_AI_ENDPOINT
    url = "{}/chat/completions".format(endpoint)
    api_key = config.get("api_key", "")
    model = config.get("model") or DEFAULT_AI_MODEL

    body = {"model": model, "messages": messages, "stream": True,
            "max_tokens": _cfg_int(config, "max_tokens", 8192)}
    if tools:
        body["tools"] = tools

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer {}".format(api_key))

    resp = urllib.request.urlopen(req, timeout=180)
    buf = b""
    for line in resp:
        buf += line
        while b"\n" in buf:
            line_bytes, buf = buf.split(b"\n", 1)
            line_str = line_bytes.decode("utf-8", errors="replace").strip()
            if not line_str or not line_str.startswith("data:"):
                continue
            payload = line_str[5:].strip()
            if payload == "[DONE]":
                return
            try:
                chunk = json.loads(payload)
                yield chunk
            except json.JSONDecodeError:
                continue
    # 处理剩余
    line_str = buf.decode("utf-8", errors="replace").strip()
    if line_str.startswith("data:") and line_str[5:].strip() != "[DONE]":
        try:
            yield json.loads(line_str[5:].strip())
        except Exception:
            pass

