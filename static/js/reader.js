// reader.js — 阅读器：按格式分发 (PDF / EPUB / TXT / CBZ)，统一翻页/缩放/进度控制
import API from './api.js';
import Notes from './notes.js';
import { extractPdfText } from './pdftext.js';

const UNSUPPORTED = new Set(['mobi', 'azw3', 'docx', 'fb2', 'cbr']);

const ui = {
  area: document.getElementById('reader-area'),
  stage: document.querySelector('.reader-stage'),
  bar: document.querySelector('.reader-bar'),
  title: document.getElementById('reader-title'),
  fmt: document.getElementById('reader-fmt'),
  pageInfo: document.getElementById('page-info'),
  pct: document.getElementById('progress-pct'),
  slider: document.getElementById('progress-slider'),
  prev: document.getElementById('page-prev'),
  next: document.getElementById('page-next'),
  zIn: document.getElementById('btn-zoom-in'),
  zOut: document.getElementById('btn-zoom-out'),
  fit: document.getElementById('btn-fit'),
  theme: document.getElementById('btn-theme'),
  mode: document.getElementById('btn-mode'),
  auto: document.getElementById('btn-auto'),
  toc: document.getElementById('btn-toc'),
  notes: document.getElementById('btn-notes'),
  tocPanel: document.getElementById('toc-panel'),
  tocList: document.getElementById('toc-list'),
  tocEmpty: document.getElementById('toc-empty'),
  tocBookName: document.getElementById('toc-book-name'),
  zoomLvl: document.getElementById('zoom-level'),
  back: document.getElementById('reader-back'),
  foot: document.querySelector('.reader-foot'),
};

let active = null;     // 当前阅读器实例
let seeking = false;   // 用户正在拖动进度条
let saveTimer = null;
let lightTheme = false;
// 阅读模式: 0=horizontal(左右翻页), 1=vertical(上下分页), 2=scroll(卷轴连续滚动)
let readerMode = parseInt(localStorage.getItem('reader-mode') || '0');
if (isNaN(readerMode) || readerMode < 0 || readerMode > 2) readerMode = 0;

function showTool(groups) {
  // groups: {zoom, fit, theme, mode, toc}
  ui.zIn.hidden = ui.zOut.hidden = ui.zoomLvl.hidden = !groups.zoom;
  ui.fit.hidden = !groups.fit;
  ui.theme.hidden = !groups.theme;
  ui.mode.hidden = !groups.mode;
  ui.toc.hidden = !groups.toc;
  // auto 按钮在卷轴模式(mode=2)或 TXT 阅读器时显示
  updateAutoVisibility();
}

function updateAutoVisibility() {
  const isScroll = readerMode === 2 || active?.constructor?.name === 'TXTReader';
  ui.auto.hidden = !isScroll;
}

// ============================ 自动阅读 ============================
let _autoReading = false;
let _autoRAF = null;
let _autoSpeed = parseFloat(localStorage.getItem('auto-read-speed')) || 1.5; // px/frame

function getScrollContainer() {
  if (active?._scrollMode) return ui.area;       // PDF 卷轴
  if (active?.box) return active.box;             // TXT
  return null;
}

function startAutoRead() {
  if (_autoReading) return;
  _autoReading = true;
  ui.auto.classList.add('active');
  ui.auto.querySelector('span').textContent = '暂停';
  const container = getScrollContainer();
  if (!container) return;

  function step() {
    if (!_autoReading) return;
    const c = getScrollContainer();
    if (!c) { stopAutoRead(); return; }
    // 到底部自动停止
    if (c.scrollTop + c.clientHeight >= c.scrollHeight - 2) {
      stopAutoRead();
      return;
    }
    c.scrollTop += _autoSpeed;
    _autoRAF = requestAnimationFrame(step);
  }
  _autoRAF = requestAnimationFrame(step);
}

function stopAutoRead() {
  _autoReading = false;
  if (_autoRAF) cancelAnimationFrame(_autoRAF);
  _autoRAF = null;
  ui.auto.classList.remove('active');
  ui.auto.querySelector('span').textContent = '自动';
}

function toggleAutoRead() {
  if (_autoReading) stopAutoRead();
  else startAutoRead();
}

function showAutoSpeedPopup() {
  // 创建速度调节浮层
  let popup = document.getElementById('auto-speed-popup');
  if (popup) { popup.remove(); return; }
  popup = document.createElement('div');
  popup.id = 'auto-speed-popup';
  popup.className = 'auto-speed-popup';
  popup.innerHTML = `
    <div class="auto-speed-header">
      <span>自动阅读速度</span>
      <button class="auto-speed-close">×</button>
    </div>
    <div class="auto-speed-body">
      <input type="range" id="auto-speed-slider" min="0.3" max="8" step="0.1" value="${_autoSpeed}">
      <span id="auto-speed-val">${_autoSpeed.toFixed(1)}</span>
    </div>
    <div class="auto-speed-hint">数值越大滚动越快</div>
  `;
  document.body.appendChild(popup);
  const btnRect = ui.auto.getBoundingClientRect();
  popup.style.right = '20px';
  popup.style.bottom = '70px';
  popup.querySelector('#auto-speed-slider').addEventListener('input', (e) => {
    _autoSpeed = parseFloat(e.target.value);
    localStorage.setItem('auto-read-speed', _autoSpeed);
    popup.querySelector('#auto-speed-val').textContent = _autoSpeed.toFixed(1);
  });
  popup.querySelector('.auto-speed-close').addEventListener('click', () => popup.remove());
}

function applyMode() {
  const modeNames = ['horizontal', 'vertical', 'scroll'];
  ui.stage.dataset.mode = modeNames[readerMode];
  updateModeIcon();
  active?.setMode?.(readerMode);
  updateAutoVisibility();
  if (readerMode !== 2 && active?.constructor?.name !== 'TXTReader') stopAutoRead();
}

function updateModeIcon() {
  const svg = ui.mode.querySelector('svg');
  if (readerMode === 0) {
    // 左右翻页: 左右双向箭头
    svg.innerHTML = '<path d="M9 7l-5 5 5 5"/><path d="M15 7l5 5-5 5"/>';
    ui.mode.title = '当前: 左右翻页 | 点击切换为上下分页';
  } else if (readerMode === 1) {
    // 上下分页: 双向下箭头
    svg.innerHTML = '<path d="M7 13l5 5 5-5"/><path d="M7 6l5 5 5-5"/>';
    ui.mode.title = '当前: 上下分页 | 点击切换为卷轴模式';
  } else {
    // 卷轴模式: 三条横线(表示连续滚动)
    svg.innerHTML = '<path d="M4 8h16M4 12h16M4 16h16"/>';
    svg.setAttribute('stroke-width', '2');
    ui.mode.title = '当前: 卷轴模式 | 点击切换为左右翻页';
  }
}

// ---- 目录面板 ----
let _tocChapters = [];
async function toggleTOC(force) {
  const show = force !== undefined ? force : ui.tocPanel.hidden;
  if (show) {
    ui.tocPanel.hidden = false;
    ui.tocPanel.classList.add('open');
    if (!_tocChapters.length && active?.getChapters) {
      ui.tocList.innerHTML = '<div style="padding:16px;color:var(--text-dim);">加载中…</div>';
      _tocChapters = await active.getChapters();
      renderTOC();
    }
  } else {
    ui.tocPanel.classList.remove('open');
    setTimeout(() => { ui.tocPanel.hidden = true; }, 250);
  }
}
function renderTOC() {
  if (!_tocChapters.length) {
    ui.tocEmpty.hidden = false;
    ui.tocList.innerHTML = '';
    return;
  }
  ui.tocEmpty.hidden = true;
  const html = _tocChapters.flatMap(ch => renderTOCItem(ch, false).concat((ch.children || []).map(sub => renderTOCItem(sub, true)))).join('');
  ui.tocList.innerHTML = html;
  ui.tocList.querySelectorAll('.toc-item').forEach(el => {
    el.addEventListener('click', () => {
      const page = parseInt(el.dataset.page);
      const href = el.dataset.href;
      if (href && active?.seekByHref) active.seekByHref(href);
      else if (page > 0) active?.seekByPage?.(page);
      else if (el.dataset.progress) active?.seek?.(parseFloat(el.dataset.progress));
    });
  });
}
function renderTOCItem(ch, isSub) {
  const cls = isSub ? 'toc-item sub' : 'toc-item';
  const pageLabel = ch.page ? `<span class="toc-page">P${ch.page}</span>` : '';
  const dataAttrs = ch.href ? `data-href="${ch.href}"` : (ch.page ? `data-page="${ch.page}"` : `data-progress="${ch.progress || 0}"`);
  return `<div class="${cls}" ${dataAttrs}>${escapeHtml(ch.title)}${pageLabel}</div>`;
}

function bindControls() {
  ui.prev.addEventListener('click', () => active?.prev());
  ui.next.addEventListener('click', () => active?.next());
  ui.zIn.addEventListener('click', () => active?.zoom?.(1));
  ui.zOut.addEventListener('click', () => active?.zoom?.(-1));
  ui.fit.addEventListener('click', () => active?.fit?.());
  ui.theme.addEventListener('click', () => { lightTheme = !lightTheme; applyTheme(); });
  ui.mode.addEventListener('click', () => {
    readerMode = ((readerMode || 0) + 1) % 3;
    localStorage.setItem('reader-mode', String(readerMode));
    applyMode();
  });
  ui.toc.addEventListener('click', () => toggleTOC());
  document.getElementById('toc-close').addEventListener('click', () => toggleTOC(false));
  ui.slider.addEventListener('input', () => {
    seeking = true;
    ui.pct.textContent = Math.round(ui.slider.value) + '%';
  });
  ui.slider.addEventListener('change', () => {
    const f = parseFloat(ui.slider.value) / 100;
    active?.seek?.(f);
    seeking = false;
  });
  document.addEventListener('keydown', onKey);

  // 自动阅读: 点击切换开始/暂停, 长按(>500ms)弹出速度调节
  let autoTimer = null;
  let autoLongPressed = false;
  ui.auto.addEventListener('mousedown', () => {
    autoLongPressed = false;
    autoTimer = setTimeout(() => {
      autoLongPressed = true;
      showAutoSpeedPopup();
    }, 500);
  });
  ui.auto.addEventListener('mouseup', () => {
    clearTimeout(autoTimer);
    if (!autoLongPressed) toggleAutoRead();
  });
  ui.auto.addEventListener('mouseleave', () => clearTimeout(autoTimer));

  // 点击阅读区域切换顶部标题栏和底部工具栏同时展开/收起
  ui.area.addEventListener('click', () => {
    ui.foot.classList.toggle('collapsed');
    ui.bar.classList.toggle('collapsed');
  });
}

function onKey(e) {
  if (!active) return;
  if (location.hash.startsWith('#/book/')) {
    // 卷轴模式用上下方向键滚动, 不拦截
    if (readerMode === 2) return;
    if (readerMode === 1) {
      if (e.key === 'ArrowDown') { e.preventDefault(); active.next(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active.prev(); }
    } else {
      if (e.key === 'ArrowRight') { e.preventDefault(); active.next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); active.prev(); }
    }
  }
}

function applyTheme() {
  document.documentElement.dataset.theme = lightTheme ? 'light' : 'dark';
  active?.setTheme?.(lightTheme ? 'light' : 'dark');
}

function updateProgress(frac, info) {
  lastFrac = frac;
  if (!seeking) {
    ui.slider.value = Math.max(0, Math.min(100, frac * 100));
    ui.pct.textContent = Math.round(frac * 100) + '%';
  }
  if (info != null) ui.pageInfo.textContent = info;
  scheduleSave(frac);
}

function scheduleSave(frac) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => active?.save(frac), 900);
}

// 即时保存：页面关闭/切换标签时，不再等防抖，直接保存
let lastFrac = 0;
function flushSave() {
  if (!active) return;
  clearTimeout(saveTimer);
  active?.save(lastFrac);
}
window.addEventListener('beforeunload', flushSave);
document.addEventListener('visibilitychange', () => { if (document.hidden) flushSave(); });

// ----------------------------- 入口 -----------------------------
async function open(book) {
  // 在 cleanup 之前保存笔记面板偏好（cleanup 会调用 Notes.close 重置偏好）
  const _notesAutoOpen = localStorage.getItem('notes-auto-open') !== 'false';
  cleanup();
  // 恢复偏好（被 cleanup 中的 Notes.close 覆盖了）
  localStorage.setItem('notes-auto-open', _notesAutoOpen ? 'true' : 'false');
  // 重置目录面板
  _tocChapters = [];
  ui.tocPanel.classList.remove('open');
  ui.tocPanel.hidden = true;
  ui.tocBookName.textContent = book.title || book.name;
  ui.title.textContent = book.title || book.name;
  ui.fmt.textContent = book.format.toUpperCase();
  ui.area.innerHTML = '';
  ui.pageInfo.textContent = '加载中…';
  ui.slider.value = 0;
  // 默认收起顶部栏和底部栏
  ui.bar.classList.add('collapsed');
  ui.foot.classList.add('collapsed');
  ui.pct.textContent = '0%';

  const saved = await API.getProgress(book.id);
  const resume = saved?.progress ? saved : null;

  // 加载笔记（每本书隔离）并自动展开
  Notes.load(
    book.id,
    book.title || book.name,
    () => active?.getLocation?.() || { page: 0, progress: 0 },
    (progress, page) => {
      if (progress > 0) active?.seek?.(progress);
      else if (page) active?.seekByPage?.(page);
    },
  ).then(() => {
    // 自动展开笔记面板（如果用户之前没关过）
    if (localStorage.getItem('notes-auto-open') !== 'false') Notes.open();
  });

  try {
    if (book.format === 'pdf') active = new PDFReader(book, resume);
    else if (book.format === 'epub') active = await EPUBReader.create(book, resume);
    else if (book.format === 'txt') active = new TxtReader(book, resume);
    else if (book.format === 'cbz') active = await CBZReader.create(book, resume);
    else active = new UnsupportedReader(book);
    await active.start();
    // 应用阅读模式（水平/垂直）
    applyMode();
  } catch (err) {
    showError(err);
  }
}

function showError(err) {
  ui.area.innerHTML = `<div class="unsupported"><h3>无法打开此书</h3><p class="unsupported-tip">${escapeHtml(err.message || String(err))}</p></div>`;
  showTool({});
}

function cleanup() {
  stopAutoRead();
  flushSave();
  clearTimeout(saveTimer);
  if (active?.destroy) { try { active.destroy(); } catch {} }
  active = null;
  ui.area.innerHTML = '';
  Notes.close();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// =====================================================================
//  PDF 阅读器
// =====================================================================
class PDFReader {
  constructor(book, resume) { this.book = book; this.resume = resume; this.page = 1; this.scale = 1.2; this.pdf = null; this.rendering = false; this.pending = null; this._scrollMode = false; this._scrollCanvases = new Map(); this._scrollObserver = null; this._scrollBusy = false; this._userZoom = false; }
  async start() {
    showTool({ zoom: true, fit: true, theme: false, mode: true, toc: true });
    this.canvas = document.createElement('canvas');
    this.canvas.id = 'pdf-canvas';
    ui.area.appendChild(this.canvas);
    this.pdf = await pdfjsLib.getDocument({ url: API.fileUrl(this.book.id) }).promise;
    this.total = this.pdf.numPages;
    if (this.resume?.page) this.page = Math.min(this.resume.page, this.total);
    await this.fit();
    this._warmTextCache();
  }
  // 后台预热文字缓存。后端读不出 PDF 的文字(本机没装 PyMuPDF), 得靠这边的
  // pdf.js 抽一次; 等用户问 AI"这本书讲什么"时才现抽就太晚了 —— 那时 AI 已经
  // 拿到空结果了。所以打开就抽, 抽完落缓存, 之后随时可查。
  async _warmTextCache() {
    try {
      const st = await API.getBookTextStatus(this.book.id);
      if (st.cached) return;            // 已经有缓存, 不必重抽
    } catch { return; }
    // 延后一点, 先让首页渲染完, 别跟阅读抢主线程
    setTimeout(() => {
      extractPdfText(this.book.id).catch(() => { /* 抽不动就算了, 不打扰阅读 */ });
    }, 1500);
  }
  // ---- 卷轴模式: 连续渲染所有页面 ----
  async _enterScrollMode() {
    if (this._scrollBusy) return;
    this._scrollBusy = true;
    this._scrollMode = true;
    try {
      // 移除单页 canvas
      this.canvas?.remove();
      this.canvas = null;
      // 创建滚动容器
      this._scrollContainer = document.createElement('div');
      this._scrollContainer.className = 'pdf-scroll-container';
      ui.area.appendChild(this._scrollContainer);
      // 计算缩放和页面尺寸
      const basePage = await this.pdf.getPage(1);
      const base = basePage.getViewport({ scale: 1 });
      const avail = ui.area.clientWidth - 48;
      this.scale = avail / base.width;
      // 预计算每页高度, 用于设置占位高度
      const pageH = base.height * this.scale;
      // 为每页创建占位 div + canvas, 设置预设高度确保可滚动
      for (let i = 1; i <= this.total; i++) {
        const wrap = document.createElement('div');
        wrap.className = 'pdf-scroll-page';
        wrap.dataset.page = i;
        wrap.style.minHeight = Math.round(pageH) + 'px';
        const canvas = document.createElement('canvas');
        canvas.style.display = 'block';
        canvas.style.margin = '0 auto';
        wrap.appendChild(canvas);
        this._scrollContainer.appendChild(wrap);
        this._scrollCanvases.set(i, { wrap, canvas, rendered: false });
      }
      // IntersectionObserver 懒渲染
      this._scrollObserver = new IntersectionObserver((entries) => {
        entries.forEach(e => {
          if (e.isIntersecting) {
            const pg = parseInt(e.target.dataset.page);
            this._renderScrollPage(pg);
          }
        });
      }, { root: ui.area, rootMargin: '300px 0px' });
      this._scrollCanvases.forEach(({ wrap }) => this._scrollObserver.observe(wrap));
      // 滚动到当前页
      const target = this._scrollCanvases.get(this.page);
      if (target) target.wrap.scrollIntoView();
      // 监听滚动更新进度 — ui.area 是实际滚动元素
      ui.area.addEventListener('scroll', this._onScrollView = () => this._onScrollUpdate());
    } catch (e) {
      console.error('enterScrollMode failed:', e);
      this._exitScrollMode();
      await this.render();
    } finally {
      this._scrollBusy = false;
    }
  }
  async _renderScrollPage(pageNum) {
    const entry = this._scrollCanvases.get(pageNum);
    if (!entry || entry.rendered) return;
    entry.rendered = true;
    try {
      const page = await this.pdf.getPage(pageNum);
      const vp = page.getViewport({ scale: this.scale * (window.devicePixelRatio || 1) });
      const { canvas } = entry;
      canvas.width = vp.width; canvas.height = vp.height;
      canvas.style.width = (vp.width / (window.devicePixelRatio || 1)) + 'px';
      canvas.style.height = (vp.height / (window.devicePixelRatio || 1)) + 'px';
      canvas.style.display = 'block';
      canvas.style.margin = '0 auto';
      await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
    } catch (e) { entry.rendered = false; }
  }
  _onScrollUpdate() {
    if (!this._scrollContainer) return;
    const scrollTop = ui.area.scrollTop;
    const viewportH = ui.area.clientHeight;
    let currentPage = 1;
    for (const [pg, { wrap }] of this._scrollCanvases) {
      const top = wrap.offsetTop;
      if (top <= scrollTop + viewportH * 0.3) currentPage = pg;
    }
    if (currentPage !== this.page) {
      this.page = currentPage;
      updateProgress((this.page - 1) / Math.max(1, this.total - 1), `${this.page} / ${this.total}`);
    }
  }
  async _scrollFit() {
    const basePage = await this.pdf.getPage(1);
    const base = basePage.getViewport({ scale: 1 });
    const avail = ui.area.clientWidth - 48;
    this.scale = avail / base.width;
    // 重新渲染所有已渲染的页面
    for (const [pg, entry] of this._scrollCanvases) {
      if (entry.rendered) { entry.rendered = false; this._renderScrollPage(pg); }
    }
  }
  _exitScrollMode() {
    this._scrollMode = false;
    this._scrollObserver?.disconnect();
    this._scrollObserver = null;
    if (this._onScrollView) { ui.area.removeEventListener('scroll', this._onScrollView); this._onScrollView = null; }
    if (this._scrollContainer) {
      this._scrollContainer.remove();
      this._scrollContainer = null;
    }
    this._scrollCanvases.clear();
    // 恢复单页 canvas
    this.canvas = document.createElement('canvas');
    this.canvas.id = 'pdf-canvas';
    ui.area.appendChild(this.canvas);
  }
  async render() {
    if (this.rendering) { this.pending = this.page; return; }
    this.rendering = true;
    try {
      const page = await this.pdf.getPage(this.page);
      const vp = page.getViewport({ scale: this.scale * (window.devicePixelRatio || 1) });
      const ctx = this.canvas.getContext('2d');
      this.canvas.width = vp.width; this.canvas.height = vp.height;
      this.canvas.style.width = (vp.width / (window.devicePixelRatio || 1)) + 'px';
      this.canvas.style.height = (vp.height / (window.devicePixelRatio || 1)) + 'px';
      await page.render({ canvasContext: ctx, viewport: vp }).promise;
      this.updateZoomLabel();
      ui.area.scrollTop = 0;  // 翻页后回到顶部
      updateProgress((this.page - 1) / Math.max(1, this.total - 1), `${this.page} / ${this.total}`);
    } finally {
      this.rendering = false;
      if (this.pending) { const p = this.pending; this.pending = null; this.page = p; this.render(); }
    }
  }
  async fit() {
    const page = await this.pdf.getPage(this.page);
    const base = page.getViewport({ scale: 1 });
    const availW = ui.area.clientWidth - 48;
    const availH = ui.area.clientHeight - 48;
    // 同时考虑宽度和高度，取较小的缩放比，确保整页可见
    const scaleW = availW / base.width;
    const scaleH = availH / base.height;
    this.scale = Math.min(scaleW, scaleH);
    this._userZoom = false;
    await this.render();
  }
  zoom(dir) {
    this.scale *= dir > 0 ? 1.15 : 1 / 1.15;
    this.scale = Math.max(0.4, Math.min(6, this.scale));
    this._userZoom = true;
    this.render();
  }
  updateZoomLabel() { ui.zoomLvl.textContent = Math.round(this.scale * 100) + '%'; }
  next() { if (this.page < this.total) { this.page++; this._flipAnim('forward'); this.render(); } }
  prev() { if (this.page > 1) { this.page--; this._flipAnim('back'); this.render(); } }
  _flipAnim(dir) {
    if (!this.canvas) return;
    this.canvas.classList.remove('flip-anim', 'flip-back');
    void this.canvas.offsetWidth; // 触发重排
    this.canvas.classList.add(dir === 'forward' ? 'flip-anim' : 'flip-back');
  }
  seek(f) { this.page = Math.max(1, Math.min(this.total, Math.round(f * (this.total - 1)) + 1)); this._afterSeek(); }
  seekByPage(page) { this.page = Math.max(1, Math.min(this.total, page)); this._afterSeek(); }
  _afterSeek() {
    if (this._scrollMode) {
      // 卷轴模式: 滚动到目标页
      const entry = this._scrollCanvases.get(this.page);
      if (entry) entry.wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
      updateProgress((this.page - 1) / Math.max(1, this.total - 1), `${this.page} / ${this.total}`);
    } else {
      this.render();
    }
  }
  getLocation() { return { page: this.page, progress: (this.page - 1) / Math.max(1, this.total - 1), label: `第${this.page}页` }; }
  async getChapters() {
    try {
      const outline = await this.pdf.getOutline();
      if (!outline || !outline.length) return [];
      return await this._resolveOutline(outline);
    } catch (e) { return []; }
  }
  async _resolveOutline(items) {
    const result = [];
    for (const item of items) {
      let pageNum = 1;
      try {
        const dest = typeof item.dest === 'string' ? await this.pdf.getDestination(item.dest) : item.dest;
        if (dest && dest[0]) {
          const idx = await this.pdf.getPageIndex(dest[0]);
          pageNum = idx + 1;
        }
      } catch {}
      const chapter = { title: item.title, page: pageNum };
      if (item.items && item.items.length) {
        chapter.children = await this._resolveOutline(item.items);
      }
      result.push(chapter);
    }
    return result;
  }
  setMode(mode) {
    // mode: 0=horizontal, 1=vertical, 2=scroll
    // 清理旧的 wheel 监听
    if (this._onWheel) { ui.area.removeEventListener('wheel', this._onWheel); this._onWheel = null; }
    if (this._wheelLock) { clearTimeout(this._wheelLock); this._wheelLock = null; }
    // 卷轴模式切换
    if (mode === 2 && !this._scrollMode) {
      this._enterScrollMode();
      return;
    }
    if (mode !== 2 && this._scrollMode) {
      this._exitScrollMode();
      this.render();
    }
    if (mode === 1) {
      // 垂直模式：滚轮在页面边界时翻页
      this._onWheel = (e) => {
        if (this.rendering) return;
        const area = ui.area;
        const atBottom = area.scrollTop + area.clientHeight >= area.scrollHeight - 3;
        const atTop = area.scrollTop <= 3;
        if (e.deltaY > 0 && atBottom) {
          e.preventDefault();
          if (this._wheelLock) return;
          this.next();
          this._wheelLock = setTimeout(() => { this._wheelLock = null; }, 350);
        } else if (e.deltaY < 0 && atTop) {
          e.preventDefault();
          if (this._wheelLock) return;
          this.prev();
          this._wheelLock = setTimeout(() => { this._wheelLock = null; }, 350);
        }
      };
      ui.area.addEventListener('wheel', this._onWheel, { passive: false });
    } else if (mode === 0 && this.canvas) {
      this.render();
    }
  }
  save(frac) { API.saveProgress(this.book.id, { progress: frac, page: this.page, total: this.total }); }
  destroy() { window.removeEventListener('resize', this._onResize); if (this._onWheel) ui.area.removeEventListener('wheel', this._onWheel); if (this._wheelLock) clearTimeout(this._wheelLock); this._scrollObserver?.disconnect(); if (this._onScrollView) ui.area.removeEventListener('scroll', this._onScrollView); try { this.pdf?.destroy(); } catch {} }
}

// =====================================================================
//  EPUB 阅读器
// =====================================================================
class EPUBReader {
  static async create(book, resume) { return new EPUBReader(book, resume); }
  constructor(book, resume) { this.book = book; this.resume = resume; this.cfi = resume?.cfi || null; this.currentProgress = 0; }
  async start() {
    showTool({ zoom: true, fit: false, theme: true, mode: true, toc: true });
    this._fontScale = 1;
    this.host = document.createElement('div');
    this.host.id = 'epub-viewer';
    this.host.style.cssText = 'position:absolute;inset:0;';
    ui.area.appendChild(this.host);
    this.bookObj = ePub(API.fileUrl(this.book.id));
    await this.bookObj.ready;
    this.rendition = this.bookObj.renderTo(this.host, {
      width: '100%', height: '100%', flow: readerMode >= 1 ? 'scrolled' : 'paginated', spread: 'none', allowScriptedContent: false,
    });
    this.registerThemes();
    this.rendition.themes.select(lightTheme ? 'light' : 'dark');
    await this.rendition.display(this.cfi || undefined);
    this.rendition.on('relocated', (loc) => this.onRelocate(loc));
    await this.bookObj.locations.generate(1024);
  }
  registerThemes() {
    this.rendition.themes.register('dark', {
      body: { background: 'transparent !important', color: '#e8e0d4' },
      p: { color: '#e8e0d4', 'line-height': '1.8' },
      a: { color: '#d8a65a' },
      'h1,h2,h3': { color: '#ece4d6' },
    });
    this.rendition.themes.register('light', {
      body: { background: 'transparent !important', color: '#2a2118' },
      p: { color: '#2a2118', 'line-height': '1.8' },
      a: { color: '#a9702f' },
    });
  }
  onRelocate(loc) {
    const start = loc?.start || loc;
    this.cfi = start.cfi;
    let frac = start.percentage;
    if (frac == null && this.bookObj.locations.length()) {
      frac = this.bookObj.locations.percentageFromCfi(this.cfi);
    }
    frac = frac || 0;
    this.currentProgress = frac;
    const pct = Math.round(frac * 100);
    updateProgress(frac, `${pct}% · 第 ${Math.max(1, start.location || 1)} 处`);
  }
  zoom(dir) {
    if (!this.rendition) return;
    this._fontScale *= dir > 0 ? 1.1 : 1 / 1.1;
    this._fontScale = Math.max(0.6, Math.min(2.4, this._fontScale));
    // epub.js themes.fontSize 接受 CSS 值
    this.rendition.themes.fontSize((100 * this._fontScale) + '%');
    ui.zoomLvl.textContent = Math.round(this._fontScale * 100) + '%';
  }
  next() { this._epubAnim('forward'); this.rendition.next(); }
  prev() { this._epubAnim('back'); this.rendition.prev(); }
  _epubAnim(dir) {
    const viewer = document.getElementById('epub-viewer');
    if (!viewer) return;
    const child = viewer.firstElementChild;
    if (!child) return;
    child.style.opacity = '0';
    child.style.transform = dir === 'forward' ? 'translateX(30px)' : 'translateX(-30px)';
    requestAnimationFrame(() => {
      child.style.opacity = '1';
      child.style.transform = 'translateX(0)';
    });
  }
  seek(f) {
    if (!this.bookObj.locations.length()) return;
    const cfi = this.bookObj.locations.cfiFromPercentage(Math.min(0.999, Math.max(0, f)));
    this.cfi = cfi; this.rendition.display(cfi);
  }
  seekByPage(page) { /* EPUB 用 CFI 定位，走 seek(progress) 路径 */ }
  seekByHref(href) { if (this.rendition) { this.cfi = href; this.rendition.display(href); } }
  setTheme(t) { this.rendition?.themes.select(t); }
  getLocation() { return { page: 0, progress: this.currentProgress, label: `${Math.round(this.currentProgress * 100)}%` }; }
  async getChapters() {
    try {
      const nav = await this.bookObj.navigation.get();
      if (!nav || !nav.toc) return [];
      return nav.toc.map(item => ({
        title: item.label.trim(),
        href: item.href,
        progress: 0,
        children: item.subitems ? item.subitems.map(sub => ({ title: sub.label.trim(), href: sub.href, progress: 0 })) : [],
      }));
    } catch (e) { return []; }
  }
  setMode(mode) {
    if (!this.rendition) return;
    // mode: 0=horizontal(paginated), 1/2=vertical/scroll(scrolled)
    this.rendition.flow(mode >= 1 ? 'scrolled' : 'paginated');
  }
  save(frac) { API.saveProgress(this.book.id, { progress: frac, cfi: this.cfi }); }
  destroy() {
    window.removeEventListener('resize', this._onResize);
    try { this.rendition?.destroy(); } catch {}
    try { this.bookObj?.destroy(); } catch {}
  }
}

// =====================================================================
//  TXT 阅读器（按视口分页滚动）
// =====================================================================
class TxtReader {
  constructor(book, resume) { this.book = book; this.resume = resume; this._frac = 0; }
  async start() {
    showTool({ zoom: true, fit: false, theme: true, mode: true });
    this.box = document.createElement('div');
    this.box.className = 'txt-reader';
    ui.area.appendChild(this.box);
    const buf = await (await fetch(API.fileUrl(this.book.id))).arrayBuffer();
    this.text = decodeText(buf);
    this.box.textContent = this.text;
    await new Promise(r => requestAnimationFrame(r));
    this.box.fontSize = 17; this._scale = 1;
    this.box.addEventListener('scroll', () => this.onScroll(), { passive: true });
    if (this.resume?.progress) {
      this.box.scrollTop = this.resume.progress * (this.box.scrollHeight - this.box.clientHeight);
    }
    this.onScroll();
  }
  onScroll() {
    const max = this.box.scrollHeight - this.box.clientHeight;
    const frac = max > 0 ? this.box.scrollTop / max : 0;
    this._frac = frac;
    const pct = Math.round(frac * 100);
    updateProgress(frac, `${pct}%`);
  }
  zoom(dir) {
    this._scale *= dir > 0 ? 1.1 : 1 / 1.1;
    this._scale = Math.max(0.6, Math.min(2.4, this._scale));
    this.box.style.fontSize = (17 * this._scale) + 'px';
    ui.zoomLvl.textContent = Math.round(this._scale * 100) + '%';
  }
  next() { this.box.scrollBy({ top: this.box.clientHeight * 0.92, behavior: 'smooth' }); }
  prev() { this.box.scrollBy({ top: -this.box.clientHeight * 0.92, behavior: 'smooth' }); }
  seek(f) { const max = this.box.scrollHeight - this.box.clientHeight; this.box.scrollTop = f * max; }
  seekByPage(page) { /* TXT 无分页概念，忽略 */ }
  getLocation() { return { page: 0, progress: this._frac, label: `${Math.round(this._frac * 100)}%` }; }
  setMode(mode) { /* TXT 始终滚动；CSS 控制导航按钮显隐 */ }
  setTheme() {}
  save(frac) { API.saveProgress(this.book.id, { progress: frac }); }
  destroy() { window.removeEventListener('resize', this._onResize); }
}

// =====================================================================
//  CBZ 漫画阅读器
// =====================================================================
class CBZReader {
  static async create(book, resume) {
    const r = new CBZReader(book, resume);
    await r.load();
    return r;
  }
  constructor(book, resume) { this.book = book; this.resume = resume; this.idx = 0; }
  async load() {
    const buf = await (await fetch(API.fileUrl(this.book.id))).arrayBuffer();
    const zip = await JSZip.loadAsync(buf);
    this.entries = Object.values(zip.files)
      .filter(f => !f.dir && /\.(jpe?g|png|webp|gif|bmp)$/i.test(f.name))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
    this.total = this.entries.length;
  }
  async start() {
    showTool({ zoom: false, fit: false, theme: false, mode: true });
    this.wrap = document.createElement('div');
    this.wrap.className = 'cbz-reader';
    this.img = document.createElement('img');
    this.wrap.appendChild(this.img);
    ui.area.appendChild(this.wrap);
    if (this.resume?.page) this.idx = Math.min(this.resume.page, this.total - 1);
    await this.render();
  }
  async render() {
    if (!this.total) return;
    const blob = await this.entries[this.idx].async('blob');
    if (this._url) URL.revokeObjectURL(this._url);
    this._url = URL.createObjectURL(blob);
    this.img.src = this._url;
    updateProgress(this.total > 1 ? this.idx / (this.total - 1) : 0, `${this.idx + 1} / ${this.total}`);
  }
  next() { if (this.idx < this.total - 1) { this.idx++; this.render(); } }
  prev() { if (this.idx > 0) { this.idx--; this.render(); } }
  seek(f) { this.idx = Math.max(0, Math.min(this.total - 1, Math.round(f * (this.total - 1)))); this.render(); }
  seekByPage(page) { this.idx = Math.max(0, Math.min(this.total - 1, page - 1)); this.render(); }
  getLocation() { return { page: this.idx + 1, progress: this.total > 1 ? this.idx / (this.total - 1) : 0, label: `${this.idx + 1}/${this.total}` }; }
  setMode(mode) {
    if (this._onWheel) { ui.area.removeEventListener('wheel', this._onWheel); this._onWheel = null; }
    if (this._wheelLock) { clearTimeout(this._wheelLock); this._wheelLock = null; }
    if (mode >= 1) {
      this._onWheel = (e) => {
        if (Math.abs(e.deltaY) < 10) return;
        if (this._wheelLock) return;
        this._wheelLock = setTimeout(() => { this._wheelLock = null; }, 400);
        if (e.deltaY > 0) this.next();
        else this.prev();
      };
      ui.area.addEventListener('wheel', this._onWheel, { passive: true });
    }
  }
  save(frac) { API.saveProgress(this.book.id, { progress: frac, page: this.idx, total: this.total }); }
  destroy() { if (this._url) URL.revokeObjectURL(this._url); if (this._onWheel) ui.area.removeEventListener('wheel', this._onWheel); if (this._wheelLock) clearTimeout(this._wheelLock); }
}

// =====================================================================
//  不支持格式
// =====================================================================
class UnsupportedReader {
  constructor(book) { this.book = book; }
  async start() {
    showTool({});
    const tpl = document.getElementById('tpl-unsupported');
    const node = tpl.content.cloneNode(true);
    node.querySelector('.unsupported-fmt').textContent = `.${this.book.format}`;
    ui.area.innerHTML = '';
    ui.area.appendChild(node);
    ui.pageInfo.textContent = '—';
    updateProgress(0, '—');
  }
  getLocation() { return { page: 0, progress: 0, label: '' }; }
  next() {} prev() {} seek() {} save() {} setMode() {} destroy() {}
}

function decodeText(buf) {
  const utf8 = new TextDecoder('utf-8', { fatal: false }).decode(buf);
  // 简单启发：大量替换符则尝试 GBK
  const repl = (utf8.match(/\uFFFD/g) || []).length;
  if (repl > utf8.length * 0.02) {
    try { return new TextDecoder('gbk').decode(buf); } catch { return utf8; }
  }
  return utf8;
}

export const Reader = { open, cleanup, init: bindControls, applyTheme, back: () => ui.back, getLocation: () => active?.getLocation?.() || { page: 0, progress: 0, label: '' }, get active() { return active; }, seekByPage: (p) => active?.seekByPage?.(p), seek: (f) => active?.seek?.(f) };
