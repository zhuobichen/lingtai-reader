// api.js — 与本地 Python 服务器通信的薄封装
const API = {
  async getBooks() {
    const r = await fetch('/api/books');
    if (!r.ok) throw new Error('获取书架失败');
    return (await r.json()).books || [];
  },

  fileUrl(id) {
    return `/api/books/${encodeURIComponent(id)}/file`;
  },

  async saveProgress(id, payload) {
    try {
      await fetch(`/api/books/${encodeURIComponent(id)}/progress`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) { /* 进度保存失败不阻塞阅读 */ }
  },

  async getProgress(id) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(id)}/progress`);
      if (!r.ok) return null;
      return await r.json();
    } catch { return null; }
  },

  async saveMeta(id, meta) {
    try {
      await fetch(`/api/books/${encodeURIComponent(id)}/meta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(meta),
      });
    } catch { /* 元数据保存失败不阻塞 */ }
  },

  async getNotes(id) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(id)}/notes`);
      if (!r.ok) return [];
      return (await r.json()).notes || [];
    } catch { return []; }
  },

  async saveNote(id, payload) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(id)}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op: 'save', ...payload }),
      });
      if (!r.ok) return [];
      return (await r.json()).notes || [];
    } catch { return []; }
  },

  async deleteNote(id, noteId) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(id)}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op: 'delete', id: noteId }),
      });
      if (!r.ok) return [];
      return (await r.json()).notes || [];
    } catch { return []; }
  },

  // ---- 分类 ----
  async getCategories() {
    try {
      const r = await fetch('/api/categories');
      if (!r.ok) return [];
      return (await r.json()).categories || [];
    } catch { return []; }
  },

  async createCategory(name) {
    try {
      const r = await fetch('/api/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error('新建分类失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  async renameCategory(oldName, newName) {
    try {
      const r = await fetch('/api/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName, old_name: oldName }),
      });
      if (!r.ok) throw new Error('重命名分类失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  async deleteCategory(name) {
    try {
      const r = await fetch(`/api/categories/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error('删除分类失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  async setCategory(bookId, category) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(bookId)}/category`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category }),
      });
      if (!r.ok) throw new Error('设置分类失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  async deleteBook(bookId) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(bookId)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error('移出书架失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  async getTrash() {
    const r = await fetch('/api/trash');
    if (!r.ok) throw new Error('获取回收站失败');
    return await r.json();
  },

  async restoreBook(bookId) {
    const r = await fetch('/api/trash/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ book_id: bookId }),
    });
    if (!r.ok) throw new Error('恢复失败');
    return await r.json();
  },

  async deleteBookPermanent(bookId) {
    const r = await fetch(`/api/trash/${encodeURIComponent(bookId)}`, { method: 'DELETE' });
    if (!r.ok) throw new Error('永久删除失败');
    return await r.json();
  },

  async emptyTrash() {
    const r = await fetch('/api/trash/empty', { method: 'POST' });
    if (!r.ok) throw new Error('清空回收站失败');
    return await r.json();
  },

  async uploadBook(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/books/upload', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('上传书籍失败');
    return await r.json();
  },

  // ---- 书籍排序 ----
  async reorderBooks(id1, id2) {
    try {
      const r = await fetch('/api/books/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id1, id2 }),
      });
      if (!r.ok) throw new Error('排序失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  // ---- 所有笔记聚合 ----
  async getAllNotes() {
    try {
      const r = await fetch('/api/notes/all');
      if (!r.ok) return [];
      return (await r.json()).notes || [];
    } catch { return []; }
  },

  // ---- AI ----
  async getAIConfig() {
    try {
      const r = await fetch('/api/ai/config');
      if (!r.ok) return { endpoint: '', model: '', has_key: false };
      return await r.json();
    } catch { return { endpoint: '', model: '', has_key: false }; }
  },

  async saveAIConfig(cfg) {
    try {
      // 只在真的填了 key 时才带上这个字段。带空串会让后端把已存的 key 覆盖成空,
      // 而设置面板承诺的是"留空则不修改"。
      const body = { endpoint: cfg.endpoint, model: cfg.model };
      if (cfg.api_key) body.api_key = cfg.api_key;
      if (cfg.clear_key) body.clear_key = true;
      const r = await fetch('/api/ai/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error('保存AI配置失败');
      return await r.json();
    } catch (e) { throw e; }
  },

  // AI 对话：返回 fetch Response（SSE 流），由调用方读取流
  async aiChat(messages, context = {}) {
    const r = await fetch('/api/ai/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, context }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.error || 'AI请求失败');
    }
    return r;
  },

  // ---- 书库 (外部电子书仓库) ----
  async getLibrarySource() {
    try {
      const r = await fetch('/api/library/source');
      if (!r.ok) return { path: '', valid: false, reason: '' };
      return await r.json();
    } catch { return { path: '', valid: false, reason: '' }; }
  },

  async setLibrarySource(path) {
    const r = await fetch('/api/library/source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || '保存书库路径失败');
    return d;
  },

  async scanLibrary() {
    try {
      const r = await fetch('/api/library/scan');
      if (!r.ok) return { valid: false, items: [], reason: '扫描失败' };
      return await r.json();
    } catch { return { valid: false, items: [], reason: '扫描失败' }; }
  },

  async importFromLibrary(file) {
    const r = await fetch('/api/library/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || '导入失败');
    return d;
  },

  // ---- 书籍文字缓存 (PDF 需要前端用 pdf.js 抽一次) ----
  // 后端能力探测。拿不到时保守地当作"后端能抽", 免得前端抢着写出残缺正文。
  async getCapabilities() {
    try {
      const r = await fetch('/api/capabilities');
      if (!r.ok) return { pdf_backend: '', client_extract_needed: false };
      return await r.json();
    } catch { return { pdf_backend: '', client_extract_needed: false }; }
  },

  async getBookTextStatus(bookId) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(bookId)}/text`);
      if (!r.ok) return { cached: false };
      return await r.json();
    } catch { return { cached: false }; }
  },

  async saveBookText(bookId, text, extra = {}) {
    const r = await fetch(`/api/books/${encodeURIComponent(bookId)}/text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, ...extra }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.error || '保存正文失败');
    }
    return await r.json();
  },

  async deleteBookText(bookId) {
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(bookId)}/text`, { method: 'DELETE' });
      return await r.json();
    } catch { return { ok: false }; }
  },
};

// 文件体积友好显示
function humanSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

export default API;
export { humanSize };
