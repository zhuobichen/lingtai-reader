// library.js — 书库面板: 读取外部电子书仓库, 把书导进本地书架
//
// 面板只列**文件确实存在**的书。仓库里的卡片写着"已下载"但文件其实不在的
// 情况是真实存在的, 所以卡片只用来补标题和作者, 不作书目来源。

import API, { humanSize } from './api.js';
import { refreshShelf } from './bookshelf.js';

const el = {};

function cache() {
  el.modal = document.getElementById('library-modal');
  el.close = document.getElementById('lib-close');
  el.path = document.getElementById('lib-path');
  el.save = document.getElementById('lib-save');
  el.refresh = document.getElementById('lib-refresh');
  el.status = document.getElementById('lib-status');
  el.list = document.getElementById('lib-list');
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function setStatus(msg, kind) {
  el.status.textContent = msg || '';
  el.status.dataset.kind = kind || '';
}

export async function openLibraryPanel() {
  if (!el.modal) cache();
  el.modal.hidden = false;
  setStatus('');
  const cfg = await API.getLibrarySource();
  if (!el.path.value) el.path.value = cfg.path || '';
  if (!cfg.valid && cfg.path) setStatus(cfg.reason || '路径不可用', 'warn');
  await refresh();
}

function closePanel() {
  el.modal.hidden = true;
}

async function refresh() {
  setStatus('扫描中…');
  const d = await API.scanLibrary();
  if (!d.valid) {
    el.list.innerHTML = '';
    setStatus(d.reason || '书库不可用', 'warn');
    return;
  }
  const items = d.items || [];
  if (!items.length) {
    el.list.innerHTML = '<div class="lib-empty">这个书库里没有可导入的书</div>';
    setStatus('');
    return;
  }
  el.list.innerHTML = items.map(renderItem).join('');
  bindImportButtons();
  setStatus(`共 ${items.length} 本`);
}

function renderItem(it) {
  const size = humanSize(it.size);
  const author = it.author ? escapeHtml(it.author) : '';
  const action = it.imported
    ? '<span class="lib-badge">已在书架</span>'
    : `<button class="btn-secondary btn-sm lib-import" data-file="${escapeHtml(it.file)}">导入</button>`;
  return `
    <div class="lib-item">
      <div class="lib-item-main">
        <div class="lib-item-title">${escapeHtml(it.title)}</div>
        <div class="lib-item-sub">${author}${author ? ' · ' : ''}${escapeHtml(it.format)} · ${size}</div>
        <div class="lib-item-file">${escapeHtml(it.file)}</div>
      </div>
      <div class="lib-item-action">${action}</div>
    </div>`;
}

function bindImportButtons() {
  el.list.querySelectorAll('.lib-import').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const file = btn.dataset.file;
      btn.disabled = true;
      btn.textContent = '导入中…';
      try {
        const r = await API.importFromLibrary(file);
        btn.outerHTML = '<span class="lib-badge">已在书架</span>';
        setStatus(`已导入《${r.fileName}》`);
        // 导完让书架立刻显示出来
        refreshShelf();
      } catch (e) {
        btn.disabled = false;
        btn.textContent = '导入';
        setStatus(e.message, 'warn');
      }
    });
  });
}

export function bindLibraryPanel() {
  if (!el.modal) cache();
  el.close.addEventListener('click', closePanel);
  el.modal.addEventListener('click', (e) => {
    if (e.target === el.modal) closePanel();
  });

  el.save.addEventListener('click', async () => {
    const path = el.path.value.trim();
    el.save.disabled = true;
    try {
      await API.setLibrarySource(path);
      setStatus('路径已保存');
      await refresh();
    } catch (e) {
      setStatus(e.message, 'warn');
    } finally {
      el.save.disabled = false;
    }
  });

  el.refresh.addEventListener('click', refresh);
}
