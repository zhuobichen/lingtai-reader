// 用 pdf.js 抽取 PDF 全文, 回传给后端缓存。
//
// 背景: 灵台的 PDF 一直只有渲染(画成 canvas 位图), 从没有文字提取,
// 所以后端的 extract_book_text 对 PDF 一律返回空串, AI 读不了任何 PDF。
//
// 后端那侧本来设计了分层(缓存 → PyMuPDF → 前端), 但本机根本没装 PyMuPDF,
// 于是"回退层"实际上就是唯一能跑的路径。好在 pdf.js 早就 vendored 在
// static/vendor/ 里了 —— 它自带 getTextContent, 只是这个项目从来没用过。
//
// 抽出来的正文会 POST 到 /api/books/<id>/text 落缓存, 之后 AI 工具的
// get_book_content 就能读到, 不必依赖浏览器还开着。

import API from './api.js';

// 同一本书的抽取只跑一次。打开一本 PDF 时, 阅读器渲染、书架取封面、
// AI 抽取会各自 getDocument —— 不拦一下就是三份 11MB 的重复请求。
const inflight = new Map();

const YIELD_EVERY = 5;   // 每 N 页让出一次事件循环, 否则大书会把标签页冻住

// 后端能力只探一次
let capsPromise = null;

export function isExtracting(bookId) {
  return inflight.has(bookId);
}

/**
 * 前端到底该不该出手抽。后端能抽时就不该 —— 见下面 extractPdfText 的说明。
 */
function _clientExtractNeeded() {
  if (!capsPromise) {
    capsPromise = API.getCapabilities().catch(() => ({ client_extract_needed: false }));
  }
  return capsPromise.then((c) => !!c.client_extract_needed);
}

/**
 * 抽取一本书的全文并落缓存。
 * @param {string} bookId
 * @param {{onProgress?: (done: number, total: number) => void}} opts
 * @returns {Promise<{ok: boolean, chars?: number, pageCount?: number, skipped?: boolean}>}
 */
export function extractPdfText(bookId, opts = {}) {
  if (inflight.has(bookId)) return inflight.get(bookId);
  const task = _run(bookId, opts).finally(() => inflight.delete(bookId));
  inflight.set(bookId, task);
  return task;
}

async function _run(bookId, { onProgress } = {}) {
  // 后端装得动 PyMuPDF 时就别插手。缓存是先写先赢的, 而 pdf.js 抽中文 PDF
  // 会丢掉几乎全部汉字(实测第 2 页只得 207 字符且无中文, PyMuPDF 得 1063 字符),
  // 一旦这份残缺正文落进缓存, 后端那个靠谱的抽取器就再也轮不上了。
  if (!(await _clientExtractNeeded())) {
    return { ok: false, skipped: true, reason: '后端已具备 PDF 抽取能力, 无需前端出手' };
  }

  const pdfjsLib = window.pdfjsLib;
  if (!pdfjsLib) throw new Error('pdf.js 未加载, 无法提取文字');

  const doc = await pdfjsLib.getDocument({ url: API.fileUrl(bookId) }).promise;
  try {
    const parts = [];
    const pageOffsets = [];
    let cursor = 0;
    const total = doc.numPages;

    for (let n = 1; n <= total; n++) {
      const page = await doc.getPage(n);
      const content = await page.getTextContent();

      // 页码标记也要计入偏移, 否则后端的 page -> offset 换算会整体漂移
      pageOffsets.push(cursor);
      const marker = `\n\n[第 ${n} 页]\n`;
      parts.push(marker);
      cursor += marker.length;

      const body = _pageText(content);
      parts.push(body);
      cursor += body.length;

      if (onProgress) onProgress(n, total);
      if (n % YIELD_EVERY === 0) await new Promise((r) => setTimeout(r, 0));
    }

    const text = parts.join('');
    await API.saveBookText(bookId, text, { pageCount: total, pageOffsets });
    return { ok: true, chars: text.length, pageCount: total };
  } finally {
    try { doc.destroy(); } catch { /* 销毁失败不影响结果 */ }
  }
}

/**
 * 把一页的 textContent 拼成文本。
 * pdf.js 给的是带坐标的碎片, 没有现成的换行; hasEOL 是它标好的行尾信号,
 * 不处理这个的话整页会连成一行、中文之间还会因为碎片边界丢空格。
 */
function _pageText(content) {
  let out = '';
  for (const item of content.items) {
    if (typeof item.str === 'string') out += item.str;
    if (item.hasEOL) out += '\n';
  }
  return out;
}
