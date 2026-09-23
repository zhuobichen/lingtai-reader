// app.js — 入口与路由
import { initBookshelf, refreshShelf, refreshCategories } from './bookshelf.js';
import { Reader } from './reader.js';
import Notes from './notes.js';
import AI from './ai.js';
import API from './api.js';
import { openLibraryPanel, bindLibraryPanel } from './library.js';

// 配置 PDF.js worker（本地化）
if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.js';
  pdfjsLib.disableWorker = false;
}

// 浏览器缩放时, UI 元素反向缩放保持固定大小
const _baseDPR = window.devicePixelRatio;
function adjustUIZoom() {
  const browserZoom = window.devicePixelRatio / _baseDPR;
  if (Math.abs(browserZoom - 1) > 0.02) {
    document.documentElement.style.setProperty('--ui-zoom', (1 / browserZoom).toFixed(4));
  } else {
    document.documentElement.style.setProperty('--ui-zoom', '1');
  }
}
window.addEventListener('resize', () => requestAnimationFrame(adjustUIZoom));
adjustUIZoom();

// 为 AI 提供当前阅读上下文
function getReaderContext() {
  const hash = location.hash;
  const m = hash.match(/^#\/book\/(.+)$/);
  const bookId = m ? decodeURIComponent(m[1]) : '';
  const loc = Reader.getLocation ? Reader.getLocation() : {};
  return {
    currentBookId: bookId,
    currentPage: loc.page || 0,
    currentProgress: loc.progress || 0,
    currentLabel: loc.label || '',
  };
}

const viewShelf = document.getElementById('view-shelf');
const viewReader = document.getElementById('view-reader');

function showView(name) {
  viewShelf.hidden = name !== 'shelf';
  viewReader.hidden = name !== 'reader';
  document.body.dataset.view = name;
}

async function route() {
  const hash = location.hash.replace(/^#/, '');
  const m = hash.match(/^\/book\/(.+)$/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    showView('reader');
    try {
      const books = await API.getBooks();
      const book = books.find(b => b.id === id);
      if (!book) { throw new Error('找不到这本书，可能已被移出 books 文件夹'); }
      await Reader.open(book);
    } catch (err) {
      Reader.cleanup();
      document.getElementById('reader-area').innerHTML =
        `<div class="unsupported"><h3>无法打开</h3><p class="unsupported-tip">${escapeHtml(err.message)}</p></div>`;
    }
  } else {
    Reader.cleanup();
    showView('shelf');
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// 文件上传处理
async function handleUpload(files) {
  if (!files || !files.length) return;
  let ok = 0, fail = 0;
  for (const file of files) {
    try {
      await API.uploadBook(file);
      ok++;
    } catch { fail++; }
  }
  if (ok > 0) {
    refreshShelf();
    refreshCategories();
  }
  if (fail > 0) {
    alert(`导入完成：成功 ${ok} 本，失败 ${fail} 本`);
  }
}

// ---- 主题切换 ----
function initTheme() {
  const saved = localStorage.getItem('reader-theme') || 'dark';
  applyTheme(saved);
  const btn = document.getElementById('btn-theme-toggle');
  if (btn) {
    btn.addEventListener('click', () => {
      const current = document.body.dataset.theme === 'light' ? 'light' : 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      localStorage.setItem('reader-theme', next);
    });
  }
}

function applyTheme(theme) {
  if (theme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
    document.body.dataset.theme = 'light';
  } else {
    document.documentElement.removeAttribute('data-theme');
    document.body.dataset.theme = 'dark';
  }
  const darkIcon = document.getElementById('theme-icon-dark');
  const lightIcon = document.getElementById('theme-icon-light');
  if (darkIcon && lightIcon) {
    darkIcon.style.display = theme === 'dark' ? '' : 'none';
    lightIcon.style.display = theme === 'light' ? '' : 'none';
  }
}

// ---- 回收站 ----
function initTrash() {
  const btn = document.getElementById('btn-trash');
  if (btn) btn.addEventListener('click', openTrashModal);
}

async function openTrashModal() {
  // 关闭已有
  document.getElementById('trash-modal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'trash-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-box trash-modal-box">
      <div class="modal-header">
        <h3>回收站</h3>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">✕</button>
      </div>
      <div class="trash-list" id="trash-list"><p style="text-align:center;color:var(--text-mute);padding:40px 0;">加载中...</p></div>
      <div class="trash-footer" id="trash-footer" style="display:none;">
        <button class="btn-text" id="btn-empty-trash">清空过期项</button>
      </div>
    </div>`;
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
  await loadTrashList();
}

async function loadTrashList() {
  const listEl = document.getElementById('trash-list');
  const footerEl = document.getElementById('trash-footer');
  try {
    const data = await API.getTrash();
    const items = data.trash || [];
    if (!items.length) {
      listEl.innerHTML = '<p style="text-align:center;color:var(--text-mute);padding:40px 0;">回收站为空</p>';
      return;
    }
    listEl.innerHTML = items.map(item => {
      const title = decodeURIComponent(item.title || item.bookId || '');
      const days = Math.ceil(item.remainSeconds / 86400);
      const date = new Date(item.deletedAt * 1000).toLocaleDateString('zh-CN');
      return `<div class="trash-item">
        <div class="trash-item-info">
          <span class="trash-item-title">${escapeHtmlSafe(title)}</span>
          <span class="trash-item-meta">${date} · 剩余 ${days} 天</span>
        </div>
        <div class="trash-item-actions">
          <button class="btn-sm" data-act="restore" data-id="${escapeHtmlSafe(item.bookId)}">恢复</button>
          <button class="btn-sm btn-danger" data-act="permanent" data-id="${escapeHtmlSafe(item.bookId)}">永久删除</button>
        </div>
      </div>`;
    }).join('');
    footerEl.style.display = 'flex';
    // 绑定按钮
    listEl.querySelectorAll('[data-act="restore"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        try {
          await API.restoreBook(id);
          loadTrashList();
          refreshShelf();
          refreshCategories();
        } catch (e) { alert(e.message || '恢复失败'); }
      });
    });
    listEl.querySelectorAll('[data-act="permanent"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        if (!await confirmModal('永久删除', '永久删除后无法恢复，确定吗？', { confirmText: '永久删除', danger: true })) return;
        try {
          await API.deleteBookPermanent(id);
          loadTrashList();
        } catch (e) { alert(e.message || '永久删除失败'); }
      });
    });
    const emptyBtn = document.getElementById('btn-empty-trash');
    if (emptyBtn) emptyBtn.onclick = async () => {
      try {
        await API.emptyTrash();
        loadTrashList();
      } catch (e) { alert(e.message || '清空失败'); }
    };
  } catch (e) {
    listEl.innerHTML = `<p style="text-align:center;color:var(--danger);">加载失败: ${e.message}</p>`;
  }
}

function escapeHtmlSafe(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

// ---- 通用确认对话框 (替代原生 confirm) ----
export function confirmModal(title, message, { confirmText = '确定', cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal-box" style="width:380px;">
        <div class="modal-header">
          <h3>${escapeHtmlSafe(title)}</h3>
        </div>
        <div class="modal-body" style="padding:20px;">
          <p style="font-size:14px;line-height:1.6;color:var(--text-dim);margin:0;">${escapeHtmlSafe(message)}</p>
        </div>
        <div style="display:flex;gap:10px;justify-content:flex-end;padding:0 20px 16px;">
          <button class="btn-sm" data-act="cancel" style="min-width:64px;">${escapeHtmlSafe(cancelText)}</button>
          <button class="btn-sm ${danger ? 'btn-danger' : ''}" data-act="ok" style="min-width:72px;">${escapeHtmlSafe(confirmText)}</button>
        </div>
      </div>`;
    const close = (result) => { overlay.remove(); resolve(result); };
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close(false);
      if (e.target.dataset.act === 'cancel') close(false);
      if (e.target.dataset.act === 'ok') close(true);
    });
    document.addEventListener('keydown', function esc(e) {
      if (e.key === 'Escape') { close(false); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(overlay);
    overlay.querySelector('[data-act="ok"]').focus();
  });
}

function bind() {
  initTheme();
  initTrash();
  Reader.init();
  Notes.init();
  AI.init();
  AI.setContextProvider(getReaderContext);
  Reader.back().addEventListener('click', () => { location.hash = '#/'; });
  window.addEventListener('hashchange', route);

  // 笔记中心
  initNotesCenter();

  // 书库面板
  bindLibraryPanel();
  const btnLibrary = document.getElementById('btn-library');
  if (btnLibrary) btnLibrary.addEventListener('click', openLibraryPanel);

  // 文件上传
  const fileInput = document.getElementById('file-input');
  const btnUpload = document.getElementById('btn-upload');
  if (btnUpload && fileInput) {
    btnUpload.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => {
      handleUpload(e.target.files);
      e.target.value = '';
    });
  }

  // 拖拽导入：仅书架视图+仅文件类型才触发
  let dragCounter = 0;
  function isFileDrag(e) {
    return e.dataTransfer && e.dataTransfer.types && Array.from(e.dataTransfer.types).includes('Files');
  }
  function isShelfView() {
    const shelf = document.getElementById('view-shelf');
    return shelf && !shelf.hidden;
  }
  window.addEventListener('dragover', (e) => { if (isFileDrag(e) && isShelfView()) e.preventDefault(); });
  window.addEventListener('drop', (e) => {
    if (!isFileDrag(e) || !isShelfView()) return;
    e.preventDefault();
    const files = [...(e.dataTransfer?.files || [])];
    const valid = files.filter(f => /\.(pdf|epub|txt|mobi|azw3|fb2|cbz|cbr|docx)$/i.test(f.name));
    if (valid.length) handleUpload(valid);
  });
}

bind();

// ---- 笔记中心 ----
function initNotesCenter() {
  const btn = document.getElementById('btn-notes-center');
  const modal = document.getElementById('notes-center-modal');
  const closeBtn = document.getElementById('notes-center-close');
  const listEl = document.getElementById('notes-center-list');
  const emptyEl = document.getElementById('notes-center-empty');
  if (!btn || !modal) return;

  async function openModal() {
    modal.hidden = false;
    listEl.innerHTML = '<div style="padding:20px;color:var(--text-dim);text-align:center;">加载中…</div>';
    emptyEl.hidden = true;
    try {
      const notes = await API.getAllNotes();
      if (!notes.length) {
        listEl.innerHTML = '';
        emptyEl.hidden = false;
        return;
      }
      listEl.innerHTML = notes.map(n => {
        const date = new Date(n.updatedAt || n.createdAt).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
        const locLabel = n.page ? `第${n.page}页` : `${Math.round((n.progress || 0) * 100)}%`;
        return `<div class="nc-note-card" data-book-id="${escapeHtmlSafe(n.bookId)}" data-page="${n.page || 0}" data-progress="${n.progress || 0}">
          <div class="nc-note-meta">
            <span class="nc-book-title">${escapeHtmlSafe(n.bookTitle)}</span>
            <span class="nc-note-loc">${escapeHtmlSafe(locLabel)} · ${date}</span>
          </div>
          <div class="nc-note-content">${escapeHtmlSafe(n.content)}</div>
        </div>`;
      }).join('');
      listEl.querySelectorAll('.nc-note-card').forEach(card => {
        card.addEventListener('click', () => {
          const bookId = card.dataset.bookId;
          const page = parseInt(card.dataset.page);
          const progress = parseFloat(card.dataset.progress);
          modal.hidden = true;
          // 跳转到对应书和位置
          location.hash = `#/book/${encodeURIComponent(bookId)}`;
          // 延迟后定位到笔记位置
          setTimeout(() => {
            if (progress > 0) Reader.seek(progress);
            else if (page > 0) Reader.seekByPage(page);
          }, 1500);
        });
      });
    } catch (e) {
      listEl.innerHTML = `<div style="padding:20px;color:var(--text-dim);">加载失败: ${escapeHtmlSafe(e.message)}</div>`;
    }
  }

  btn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', () => { modal.hidden = true; });
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });
}

initBookshelf();
route();
