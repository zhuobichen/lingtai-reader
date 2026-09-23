// ai.js — AI 助手：悬浮按钮 + 对话面板 + 流式响应 + 设置 + Agent 上下文
import API from './api.js';
import { extractPdfText } from './pdftext.js';

let history = [];        // 对话历史 [{role, content}]
let sending = false;     // 是否正在发送/接收
let el = {};             // DOM 元素缓存
let contextProvider = null; // 回调：获取当前阅读上下文
let statusIcon = null;   // 状态指示器图标

// ---- 聊天记录持久化 ----
const HISTORY_KEY = 'reader-ai-history';
const HISTORY_MAX = 20;

function saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-HISTORY_MAX)));
  } catch {}
}

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw) {
      history = JSON.parse(raw);
      // 重新渲染历史消息
      for (const msg of history) {
        if (msg.role === 'user') {
          addMessage('user', msg.content);
        } else {
          addMessage('assistant', msg.content);
        }
      }
    }
  } catch {
    history = [];
  }
}

function clearHistory() {
  history = [];
  try { localStorage.removeItem(HISTORY_KEY); } catch {}
}

const SVG_ROBOT = `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
  <rect x="4" y="7" width="16" height="12" rx="2.5"/>
  <path d="M12 7V4"/>
  <circle cx="12" cy="3" r="1.2" fill="currentColor"/>
  <circle cx="9" cy="12" r="1.5" fill="currentColor"/>
  <circle cx="15" cy="12" r="1.5" fill="currentColor"/>
  <path d="M9 16h6"/>
  <path d="M2 12v2M22 12v2"/>
</svg>`;

const SVG_GEAR = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="3"/>
  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
</svg>`;

const SVG_CLOSE = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

const SVG_SEND = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/></svg>`;
const SVG_CLEAR = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9"/><path d="M3 4v5h5"/></svg>`;

/** 使元素可拖拽，位置持久化到 localStorage */
function _makeDraggable(el) {
  const STORAGE_KEY = 'ai-fab-pos';
  // 恢复保存的位置
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (saved && typeof saved.x === 'number' && typeof saved.y === 'number') {
      el.style.left = saved.x + 'px';
      el.style.top = saved.y + 'px';
      el.style.right = 'auto';
      el.style.bottom = 'auto';
    }
  } catch {}

  let dragging = false;
  let startX, startY, origX, origY;
  let hasMoved = false;

  el.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    hasMoved = false;
    startX = e.clientX;
    startY = e.clientY;
    const rect = el.getBoundingClientRect();
    origX = rect.left;
    origY = rect.top;
    el.style.transition = 'none';
    el.style.left = origX + 'px';
    el.style.top = origY + 'px';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) hasMoved = true;
    let nx = origX + dx;
    let ny = origY + dy;
    // 限制在视口内
    const size = el.offsetWidth;
    nx = Math.max(4, Math.min(window.innerWidth - size - 4, nx));
    ny = Math.max(4, Math.min(window.innerHeight - size - 4, ny));
    el.style.left = nx + 'px';
    el.style.top = ny + 'px';
    // 拖拽时同步移动面板（如果面板已打开）
    syncPanelToFAB();
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    el.style.transition = '';
    if (hasMoved) {
      el.classList.add('dragged');
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          x: parseInt(el.style.left),
          y: parseInt(el.style.top),
        }));
      } catch {}
    }
  });

  // 点击时如果拖拽过则不触发打开
  el.addEventListener('click', (e) => {
    if (hasMoved) { e.preventDefault(); e.stopPropagation(); hasMoved = false; }
  }, true);
}

/** 构建 FAB 与对话面板 DOM */
function buildUI() {
  // ---- 悬浮按钮 ----
  const fab = document.createElement('button');
  fab.id = 'ai-fab';
  fab.className = 'ai-fab';
  fab.title = 'AI 助手';
  fab.setAttribute('aria-label', 'AI 助手');
  fab.innerHTML = SVG_ROBOT;
  document.body.appendChild(fab);

  // ---- 拖拽 FAB ----
  _makeDraggable(fab);

  // ---- 对话面板 ----
  const panel = document.createElement('div');
  panel.id = 'ai-panel';
  panel.className = 'ai-panel';
  panel.hidden = true;
  panel.innerHTML = `
    <div class="ai-header">
      <div class="ai-title-wrap">
        <span class="ai-avatar-sm">${SVG_ROBOT}</span>
        <span class="ai-title">AI 助手</span>
      </div>
      <div class="ai-header-tools">
        <button id="ai-clear-btn" class="ai-icon-btn" title="新对话">${SVG_CLEAR}</button>
        <button id="ai-settings-btn" class="ai-icon-btn" title="设置">${SVG_GEAR}</button>
        <button id="ai-close-btn" class="ai-icon-btn" title="关闭">${SVG_CLOSE}</button>
      </div>
    </div>
    <div class="ai-messages" id="ai-messages"></div>
    <div class="ai-input-area">
      <textarea id="ai-input" rows="1" placeholder="输入消息，Enter 发送 / Shift+Enter 换行"></textarea>
      <button id="ai-send" class="ai-send-btn" title="发送">${SVG_SEND}</button>
    </div>`;
  document.body.appendChild(panel);

  // 缓存元素
  el.fab = fab;
  el.panel = panel;
  el.messages = panel.querySelector('#ai-messages');
  el.input = panel.querySelector('#ai-input');
  el.send = panel.querySelector('#ai-send');
  el.settingsBtn = panel.querySelector('#ai-settings-btn');
  el.closeBtn = panel.querySelector('#ai-close-btn');
}

/** 设置弹窗（在 index.html 中预置） */
function bindSettings() {
  const modal = document.getElementById('ai-settings');
  if (!modal) return;
  const endpointInp = modal.querySelector('#ai-set-endpoint');
  const keyInp = modal.querySelector('#ai-set-key');
  const modelInp = modal.querySelector('#ai-set-model');
  const saveBtn = modal.querySelector('#ai-set-save');
  const closeBtn = modal.querySelector('#ai-set-close');

  el.settingsModal = modal;
  el.settingsEndpoint = endpointInp;
  el.settingsKey = keyInp;
  el.settingsModel = modelInp;

  // 打开设置
  el.settingsBtn.addEventListener('click', () => openSettings());
  if (closeBtn) closeBtn.addEventListener('click', () => { modal.hidden = true; });
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.hidden = true; });

  // 保存配置
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const cfg = {
        api_key: keyInp.value.trim(),
        endpoint: endpointInp.value.trim(),
        model: modelInp.value.trim(),
      };
      saveBtn.disabled = true;
      saveBtn.textContent = '保存中…';
      try {
        await API.saveAIConfig(cfg);
        aiConfig.has_key = true;
        modal.hidden = true;
        keyInp.value = '';
        keyInp.placeholder = '已保存（留空则不修改）';
      } catch (e) {
        alert(e.message || '保存失败');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
      }
    });
  }
}

let aiConfig = { has_key: false };  // 缓存配置状态

/** 加载配置并预填设置表单 */
async function loadConfig() {
  const cfg = await API.getAIConfig();
  aiConfig = cfg;
  if (el.settingsEndpoint) el.settingsEndpoint.value = cfg.endpoint || '';
  if (el.settingsModel) el.settingsModel.value = cfg.model || '';
  if (el.settingsKey) {
    if (cfg.has_key) {
      el.settingsKey.placeholder = '已配置（留空则不修改）';
    } else {
      el.settingsKey.placeholder = '输入 API Key';
    }
  }
  return cfg;
}

function openSettings() {
  loadConfig();
  el.settingsModal.hidden = false;
}

/** 根据FAB当前位置定位面板（无 guard，无条件定位） */
function positionPanelToFAB() {
  if (!el.panel || !el.fab) return;
  const fabRect = el.fab.getBoundingClientRect();
  const panelW = Math.min(380, window.innerWidth - 48);
  let left = fabRect.right - panelW;
  left = Math.max(12, Math.min(window.innerWidth - panelW - 12, left));
  if (fabRect.top > window.innerHeight * 0.5) {
    el.panel.style.left = left + 'px';
    el.panel.style.right = 'auto';
    el.panel.style.bottom = (window.innerHeight - fabRect.top + 8) + 'px';
    el.panel.style.top = 'auto';
  } else {
    el.panel.style.left = left + 'px';
    el.panel.style.right = 'auto';
    el.panel.style.top = (fabRect.bottom + 8) + 'px';
    el.panel.style.bottom = 'auto';
  }
}

/** 拖拽时同步移动面板（仅面板已打开时） */
function syncPanelToFAB() {
  if (!el.panel || el.panel.hidden || !el.panel.classList.contains('open')) return;
  positionPanelToFAB();
}

/** 打开面板 */
function openPanel() {
  positionPanelToFAB();
  el.panel.hidden = false;
  el.fab.classList.add('active');
  requestAnimationFrame(() => el.panel.classList.add('open'));
  setTimeout(() => el.input.focus(), 60);
  // 首次打开显示建议引导
  if (history.length === 0 && !el.messages.querySelector('.ai-suggestions')) {
    showSuggestions();
  }
}

/** 空状态建议 */
function showSuggestions() {
  const div = document.createElement('div');
  div.className = 'ai-suggestions';
  const tips = [
    '总结这本书的主要内容',
    '帮我找一本关于历史的书',
    '这本书的作者是谁？',
    '帮我给书架上的书分类',
  ];
  div.innerHTML = `<div class="ai-suggestion-title">试试这些：</div>` +
    tips.map(t => `<button class="ai-suggestion-chip">${t}</button>`).join('');
  div.querySelectorAll('.ai-suggestion-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      el.input.value = btn.textContent;
      el.input.focus();
      div.remove();
    });
  });
  el.messages.appendChild(div);
}

/** 关闭面板 */
function closePanel() {
  el.panel.classList.remove('open');
  el.fab.classList.remove('active');
  setTimeout(() => { el.panel.hidden = true; }, 240);
}

function togglePanel() {
  if (el.panel.hidden || !el.panel.classList.contains('open')) openPanel();
  else closePanel();
}

/** 追加一条消息气泡 */
function addMessage(role, content) {
  const wrap = document.createElement('div');
  wrap.className = 'ai-msg ' + (role === 'user' ? 'ai-msg-user' : 'ai-msg-ai');
  if (role === 'user') {
    wrap.innerHTML = `<div class="ai-bubble ai-bubble-user"></div>`;
    wrap.querySelector('.ai-bubble').textContent = content;
  } else {
    wrap.innerHTML = `
      <div class="ai-avatar">${SVG_ROBOT}</div>
      <div class="ai-bubble ai-bubble-ai"></div>`;
    wrap.querySelector('.ai-bubble').textContent = content;
  }
  el.messages.appendChild(wrap);
  scrollToBottom();
  return wrap.querySelector('.ai-bubble');
}

/** 正在思考指示器 */
function showTyping() {
  const wrap = document.createElement('div');
  wrap.className = 'ai-msg ai-msg-ai ai-typing-wrap';
  wrap.id = 'ai-typing';
  wrap.innerHTML = `
    <div class="ai-avatar">${SVG_ROBOT}</div>
    <div class="ai-bubble ai-bubble-ai ai-typing"><span></span><span></span><span></span></div>`;
  el.messages.appendChild(wrap);
  scrollToBottom();
}

function removeTyping() {
  const t = document.getElementById('ai-typing');
  if (t) t.remove();
}

function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

/** 发送消息并流式接收回复 */
async function send() {
  if (sending) return;
  const text = el.input.value.trim();
  if (!text) return;

  // 检查 AI 是否已配置
  if (!aiConfig.has_key) {
    addMessage('assistant', '请先配置 AI API Key。点击右上角设置按钮填写。');
    openSettings();
    return;
  }

  // 移除建议引导
  el.messages.querySelector('.ai-suggestions')?.remove();
  addMessage('user', text);
  history.push({ role: 'user', content: text });
  // 历史上限：保留最近 20 条（约 10 轮对话）
  if (history.length > 20) {
    history = history.slice(-20);
  }
  saveHistory();
  el.input.value = '';
  autoGrow();
  sending = true;
  el.send.disabled = true;

  showTyping();
  let aiBubble = null;
  let firstChunk = true;
  let statusEl = null;

  // 获取当前阅读上下文
  const ctx = contextProvider ? contextProvider() : {};

  try {
    const resp = await API.aiChat(history, ctx);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n');
      buffer = parts.pop();
      for (const line of parts) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        let data;
        try { data = JSON.parse(payload); } catch { continue; }

        // 思考状态
        if (data.type === 'thinking') {
          if (firstChunk) {
            removeTyping();
            firstChunk = false;
            // 创建状态提示
            const wrap = document.createElement('div');
            wrap.className = 'ai-msg ai-msg-ai';
            wrap.innerHTML = `<div class="ai-avatar">${SVG_ROBOT}</div><div class="ai-bubble ai-bubble-ai ai-status-bubble"><span class="ai-status-icon">○</span> <span class="ai-status-text">${data.content}</span></div>`;
            el.messages.appendChild(wrap);
            statusEl = wrap.querySelector('.ai-status-text');
            statusIcon = wrap.querySelector('.ai-status-icon');
            scrollToBottom();
          }
        }
        // 工具调用
        if (data.type === 'tool') {
          if (statusEl) {
            statusEl.textContent = data.label || data.tool;
            if (statusIcon) statusIcon.textContent = '◉';
          }
        }
        // 推理过程
        if (data.type === 'reasoning') {
          if (statusEl) {
            const statusWrap = statusEl.closest('.ai-msg');
            statusWrap?.remove();
            statusEl = null;
          }
          if (firstChunk) { removeTyping(); firstChunk = false; }
          // 显示推理过程(折叠)
          const wrap = document.createElement('div');
          wrap.className = 'ai-msg ai-msg-ai';
          wrap.innerHTML = `<div class="ai-avatar">${SVG_ROBOT}</div><div class="ai-bubble ai-bubble-ai ai-reasoning-bubble"><details><summary class="ai-reasoning-toggle">💭 推理过程</summary><div class="ai-reasoning-content"></div></details></div>`;
          wrap.querySelector('.ai-reasoning-content').textContent = data.content;
          el.messages.appendChild(wrap);
          scrollToBottom();
        }
        // 正式回答内容 (跳过有 type 的消息, 避免思考/推理文本混入)
        if (data.content && !data.type) {
          if (statusEl) {
            statusEl.closest('.ai-msg')?.remove();
            statusEl = null;
          }
          if (firstChunk) { removeTyping(); firstChunk = false; }
          if (!aiBubble) {
            aiBubble = addMessage('assistant', '');
          }
          aiBubble.textContent += data.content;
          scrollToBottom();
        }
        if (data.actions) {
          executeActions(data.actions, statusEl);
        }
        if (data.done) {
          removeTyping();
        }
      }
    }
    removeTyping();
    // 清理状态提示
    if (statusEl) {
      statusEl.closest('.ai-msg')?.remove();
    }

    // 收集完整 AI 回复到历史
    if (aiBubble) {
      history.push({ role: 'assistant', content: aiBubble.textContent });
      saveHistory();
    } else if (firstChunk) {
      // 未收到任何内容
      removeTyping();
      aiBubble = addMessage('assistant', '（没有收到回复，请检查 AI 配置或稍后重试）');
    }
  } catch (e) {
    removeTyping();
    if (statusEl) statusEl.closest('.ai-msg')?.remove();
    if (!aiBubble) aiBubble = addMessage('assistant', '');
    aiBubble.textContent += `\n[出错] ${e.message || e}`;
  } finally {
    sending = false;
    el.send.disabled = false;
    scrollToBottom();
  }
}

/** 执行 AI 返回的动作 */
function executeActions(actions, statusEl) {
  if (!Array.isArray(actions)) return;
  // 状态气泡是临时的 (回复结束时会被移除), 异步回写前先确认它还挂在树上
  const say = (msg) => {
    if (statusEl && statusEl.isConnected) statusEl.textContent = msg;
  };
  for (const a of actions) {
    if (a.type === 'open_book' && a.book_id) {
      location.hash = `#/book/${encodeURIComponent(a.book_id)}`;
    } else if (a.type === 'extract_text' && a.book_id) {
      // 后端读不出这本 PDF 的文字(PyMuPDF 没装), 需要我们这边用 pdf.js 抽一次
      // 回传。刻意不 await: SSE 流还在继续, 抽完自然会落缓存, 之后的工具调用
      // 就能读到。本次对话里 AI 多半还是会说"还没提取完", 这是实话。
      say('正在提取 PDF 文字…');
      extractPdfText(a.book_id, {
        onProgress: (done, total) => say(`正在提取 PDF 文字… ${done}/${total} 页`),
      })
        .then((r) => {
          if (r.skipped) say('后端会直接读取这本书的文字, 可以再问一次了');
          else say(`文字提取完成 (${r.chars} 字 / ${r.pageCount} 页), 现在可以再问一次`);
        })
        .catch((e) => say(`文字提取失败: ${e.message}`));
    }
  }
}

/** textarea 自适应高度 */
function autoGrow() {
  el.input.style.height = 'auto';
  el.input.style.height = Math.min(el.input.scrollHeight, 120) + 'px';
}

/** 清空对话，开始新会话 */
function clearChat() {
  if (history.length > 0 && !confirm('清空当前对话？')) return;
  clearHistory();
  el.messages.innerHTML = '';
  showSuggestions();
}

function bind() {
  el.fab.addEventListener('click', togglePanel);
  el.closeBtn.addEventListener('click', closePanel);
  el.send.addEventListener('click', send);
  const clearBtn = document.getElementById('ai-clear-btn');
  if (clearBtn) clearBtn.addEventListener('click', clearChat);
  el.input.addEventListener('input', autoGrow);
  el.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  // Esc 关闭
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el.panel.hidden) closePanel();
  });
}

function init() {
  buildUI();
  bindSettings();
  bind();
  loadConfig();
  loadHistory(); // 恢复历史聊天记录
}

function setContextProvider(fn) {
  contextProvider = fn;
}

export { init, openPanel, closePanel, setContextProvider };
export default { init, openPanel, closePanel, setContextProvider };
