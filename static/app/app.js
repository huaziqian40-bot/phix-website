/* =============================================================================
 * Pinghe Launcher Lite 网页版 SPA（vanilla JS，零依赖、零构建）
 * 版式与配色对齐客户端 Pinghe Launcher Lite；只调本站接口：
 *   GET  /me/、POST /auth/login/、POST /auth/logout/
 *   GET  /app/data/（服务器代抓平台数据）、GET /app/mail/<uid>/（邮件正文）
 *   POST /app/ai/chat/（只读 AI 查询）
 *   GET/POST /proxy/sync/objects/<name>/（日程读写、选课 / 账号只读）
 * cookie 由浏览器自动携带（credentials: same-origin），本文件不接触任何令牌。
 * 渲染一律走 textContent：全程不使用任何 HTML 字符串注入接口（无注入面）。
 * ========================================================================== */
'use strict';

/* ---------------------------------------------------------------- 基础工具 */

var $ = function (id) { return document.getElementById(id); };

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = String(text);   // 一律走 textContent，杜绝 HTML 注入
  return n;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function show(node, on) { if (node) node.hidden = !on; }

function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }

function pad2(n) { return (n < 10 ? '0' : '') + n; }

/** 本地时区 ISO 串（秒级、带偏移，不写 Z）——与桌面客户端的 created 格式一致。 */
function nowIso() {
  var d = new Date();
  var off = -d.getTimezoneOffset();
  var sign = off >= 0 ? '+' : '-';
  var abs = Math.abs(off);
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
    'T' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds()) +
    sign + pad2(Math.floor(abs / 60)) + ':' + pad2(abs % 60);
}

function todayStr() {
  var d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

/** 从今天起 +n 天的 YYYY-MM-DD（n 可为负；用于 ±14 天这种窗口）。 */
function dateStrOffset(n) {
  var d = new Date();
  d.setDate(d.getDate() + n);
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

/** 日期显示：2026-09-12 → 2026-09-12 周六（解析失败则原样返回）。 */
var WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
function prettyDay(day) {
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ''));
  if (!m) return String(day || '未定日期');
  var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return day + ' ' + WEEK[d.getDay()];
}

function weekdayOf(day) {
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ''));
  return m ? WEEK[new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getDay()] : '';
}

/** 时间字符串 → 分钟数（"08:30" / "8:30" / "08:30–09:10" 都能吃）。 */
function minutesOf(v) {
  var m = /(\d{1,2})\s*[:：]\s*(\d{2})/.exec(String(v == null ? '' : v));
  if (!m) return null;
  var h = Number(m[1]), mi = Number(m[2]);
  if (h > 23 || mi > 59) return null;
  return h * 60 + mi;
}

function hhmm(min) {
  var h = Math.floor(min / 60), m = Math.round(min % 60);
  return pad2(h) + ':' + pad2(m);
}

function nowMinutes() {
  var d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

/** 截止时间文本 → 可比较的排序键（空值排最后）。 */
function dueKey(v) {
  /* ISO 的 `T` 分隔符只在**紧挨数字**时才是分隔符：原来的 `.replace(/[T]/, ' ')`
     会把 `Thursday at 12:00 PM` 吃掉首字母变成 ` hursday`，直接解析失败。 */
  var s = String(v == null ? '' : v).replace(/(\d)[Tt](\d)/g, '$1 $2');
  var m = /(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{2}):(\d{2}))?/.exec(s);
  if (m) return m[1] + '-' + m[2] + '-' + m[3] + ' ' + (m[4] || '00') + ':' + (m[5] || '00');
  var rel = relativeDueKey(s);
  if (rel) return rel;
  return s ? '~' + s : '';
}

/** ManageBac 的**相对日期**形态：`Thursday at 12:00 PM` / `Mon 3:45 PM` / `Thursday`。
 *  ManageBac 对**最近一周内**的条目给这种"人话日期"（实测该账号 39/45 条都是它），
 *  绝对日期形态 `Sep 12, 11:59 PM` 由上面的正则吃。
 *  判定：**取最近过去的那一次**（往回找 ≤7 天）——这些条目在 ManageBac 上已经是
 *  过去/已结束的（实测样例是暑假作业与已出分的考试），按"最近过去"才不会把它们
 *  算成未来的 DDL。（若某些平台上它表示"本周期待发生"，往回找会差一周，
 *  所以这份映射只在**日期文本无法直接解析**时兜底使用，解析得出绝对日期的条目不受影响。） */
var WEEKDAY_KEY = {
  sun: 0, mon: 1, tue: 2, tues: 2, wed: 3, thu: 4, thur: 4, thurs: 4, fri: 5, sat: 6
};

function relativeDueKey(text) {
  var s = String(text == null ? '' : text).trim();
  if (!s) return '';
  var m = /^(sun|mon|tue|tues|wed|thu|thur|thurs|fri|sat)[a-z]*\b/i.exec(s.replace(/^on\s+/i, ''));
  if (!m) return '';
  var word = m[1].toLowerCase().slice(0, 3);
  var target = WEEKDAY_KEY[word];
  if (target == null) return '';
  var hh = '00', mm = '00';
  var t = /(\d{1,2}):(\d{2})\s*(am|pm)?/i.exec(s);
  if (t) {
    var h = Number(t[1]), mi = Number(t[2]);
    var ap = (t[3] || '').toLowerCase();
    if (ap === 'pm' && h < 12) h += 12;
    if (ap === 'am' && h === 12) h = 0;
    if (h >= 0 && h < 24 && mi >= 0 && mi < 60) { hh = pad2(h); mm = pad2(mi); }
  }
  var today = new Date();
  var back = ((today.getDay() - target) + 7) % 7;      // 今天的星期 → 目标星期，往回几天
  var d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - back);
  var key = prettyDayKey(d) + ' ' + hh + ':' + mm;
  var nowKey = todayStr() + ' ' + hhmm(nowMinutes());
  if (key > nowKey && back > 0) {                     // 往回找落在了未来 → 说明该按"上一次"再退一周
    d = new Date(d.getFullYear(), d.getMonth(), d.getDate() - 7);
    key = prettyDayKey(d) + ' ' + hh + ':' + mm;
  }
  return key;
}

function prettyDayKey(d) {
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

/** 解析得出的**真实日期**（yyyy-mm-dd）；解析不出来返回 ''（= 时间未知，不能丢）。 */
function dueDay(due) {
  var k = dueKey(due);
  return (!k || k.charAt(0) === '~') ? '' : k.slice(0, 10);
}

/* 首页 DDL 卡窗口：**±14 天**（今天 −14 天 ~ 今天 +14 天）。
   与桌面客户端（Pinghe Launcher Lite）同一口径；基准是**浏览器本地日期**。 */
var DDL_WINDOW_DAYS = 14;
/** 「最近 2 天内到期」= urgent（与客户端一致：用绝对差，已过期但刚过的也算）。 */
var DDL_URGENT_DAYS = 2;

/** 在首页 DDL 窗口内（前 14 天 ~ 后 14 天，按日期粒度）。 */
function inDdlWindow(due) {
  var day = dueDay(due);
  return !!day && day >= dateStrOffset(-DDL_WINDOW_DAYS) && day <= dateStrOffset(DDL_WINDOW_DAYS);
}

/** 距今天 ±2 天内到期 → 加粗/urgent（时间未知的一律不算 urgent）。 */
function isUrgentDue(due) {
  var day = dueDay(due);
  return !!day && day >= dateStrOffset(-DDL_URGENT_DAYS)
      && day <= dateStrOffset(DDL_URGENT_DAYS);
}

/** 兼容旧名：窗口 = ±14 天（首页 DDL 卡与课程页共用同一口径）。 */
function inWindow(due) { return inDdlWindow(due); }

function isPast(due) {
  var k = dueKey(due);
  if (!k || k.charAt(0) === '~') return false;
  return k < (todayStr() + ' ' + hhmm(nowMinutes()));
}

/* ------------------------------------------------------------------ 提示条 */

var toastTimer = null;
function toast(msg, ms) {
  var box = $('toast');
  if (!box) return;
  box.textContent = msg;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { box.hidden = true; }, ms || 3600);
}

/* -------------------------------------------------------------------- HTTP */

/** 会话失效统一出口：不跳站外页面，就地回到登录卡（网页端自成一体）。 */
function toLogin() {
  showLogin('登录状态已过期，请重新登录。');
}

function showLogin(msg) {
  var wrap = $('loginwrap');
  show(wrap, true);
  show($('app'), false);
  show($('boot'), false);
  if (msg) $('login-msg').textContent = msg;
}

/**
 * 请求本站接口。返回 {status, body}，body 一律是对象（解析失败为 {}）。
 * 401 时抛 Unauthorized 错误（调用方不再继续渲染业务数据）——登录接口除外，见 reqRaw。
 * opts.timeoutMs：超时毫秒数（超出即 abort，错误带 isTimeout）；
 * opts.signal：外部 AbortSignal（AI 页的「取消」用它）；
 * opts.onAbort：abort 发生时调用（用于区分「用户取消」与「网络失败」）。
 */
function req(method, path, body, opts) {
  opts = opts || {};
  var signal = opts.signal;
  var timer = null;
  var aborted = false;

  if (opts.timeoutMs && !signal) {                 // 没有外部 signal 时自建
    var ctl = (typeof AbortController === 'function') ? new AbortController() : null;
    if (ctl) signal = ctl.signal;
    timer = setTimeout(function () {
      aborted = true;
      if (ctl) ctl.abort();
    }, opts.timeoutMs);
  }

  var opt = { method: method, credentials: 'same-origin', headers: {} };
  if (signal) opt.signal = signal;
  if (body !== undefined) {
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      /* 附件走 multipart：**绝不能自己设 Content-Type** ——
         浏览器要给这段 body 补上带 boundary 的 Content-Type，手写会把 boundary 弄丢，
         服务端就解不出各部件（`email.message_from_bytes` 会直接判成非 multipart）。 */
      opt.body = body;
    } else {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
  }
  return fetch(path, opt).then(function (resp) {
    if (timer) { clearTimeout(timer); timer = null; }
    if (resp.status === 401 && !opts.allow401) {
      toLogin();
      var err = new Error('未登录');
      err.unauthorized = true;
      throw err;
    }
    return resp.text().then(function (t) {
      var j = {};
      if (t) { try { j = JSON.parse(t) || {}; } catch (e) { j = {}; } }
      return { status: resp.status, body: j };
    });
  }, function (err) {                              // fetch 层失败：网络中断 / abort / 超时
    if (timer) { clearTimeout(timer); timer = null; }
    if (opts.onAbort) opts.onAbort();
    var e2 = err instanceof Error ? err : new Error('网络错误');
    if (aborted) { e2.isTimeout = true; if (!e2.message) e2.message = '请求超时'; }
    else if (e2.name === 'AbortError') { e2.isAbort = true; if (!e2.message) e2.message = '已取消'; }
    throw e2;
  });
}

/** 把 401 当普通响应返回（登录接口用：401 = 账号或密码不对，不是「会话失效」）。 */
function reqRaw(method, path, body) {
  return req(method, path, body, { allow401: true });
}

function errText(res) {
  var e = res && res.body && res.body.error;
  return (e && e.message) || ('请求失败（HTTP ' + res.status + '）');
}

/* ---------------------------------------------------------- 同步对象读写 */

var OBJ = {
  schedule: 'schedule',
  lessons: 'settings.lessons',
  accounts: 'settings.accounts'
};

/**
 * 读同步对象：{payload:"<JSON 串>", revision:n}
 * → {doc, revision, raw, parsed}。404 / 空 payload 视为「还没有数据」。
 */
function getObject(name) {
  // 云端慢/被占住时不能无限等：给读同步对象也加超时，页面才有机会提示
  return req('GET', '/proxy/sync/objects/' + name + '/', undefined,
             { timeoutMs: 60000 }).then(function (res) {
    if (res.status === 404) return { doc: null, revision: 0, raw: '', parsed: true };
    if (res.status !== 200) throw new Error(errText(res));
    var payload = typeof res.body.payload === 'string' ? res.body.payload : '';
    var revision = typeof res.body.revision === 'number' ? res.body.revision : 0;
    var parsed = true;
    var doc = null;
    if (payload) {
      try { doc = JSON.parse(payload); } catch (e) { parsed = false; }
    }
    return { doc: doc, revision: revision, raw: payload, parsed: parsed };
  });
}

/**
 * 写同步对象：POST {"payload":<序列化串>,"base_revision":<GET 到的 revision>,device:"PHL Web"}
 * 409 = 云端有更新：提示 → 重新 GET → 同一改动重放一次（自动重试一次）。
 * change 是「纯函数改动」：接收最新 doc，返回要写回的新 doc；重放时基于最新版本重算。
 */
function putObject(name, ref, change) {
  var attempt = 0;
  function run() {
    var doc = change(ref.doc);
    var body = { payload: JSON.stringify(doc), base_revision: ref.revision, device: 'PHL Web' };
    return req('POST', '/proxy/sync/objects/' + name + '/', body).then(function (res) {
      if (res.status === 409 && attempt === 0) {
        attempt = 1;
        toast('云端有更新，已刷新，正在重试…');
        return getObject(name).then(function (fresh) {
          ref.doc = fresh.doc;
          ref.revision = fresh.revision;
          if (!fresh.parsed) throw new Error('云端数据无法解析，已停止写入以免覆盖');
          return run();      // 只自动重试一次；仍失败就把错误交给调用方
        });
      }
      if (res.status === 409) throw new Error('云端仍在更新，请稍后重试');
      if (res.status !== 200 && res.status !== 201) throw new Error(errText(res));
      var rev = typeof res.body.revision === 'number' ? res.body.revision : (ref.revision + 1);
      ref.doc = doc;
      ref.revision = rev;
      return rev;
    });
  }
  return run();
}

/* --------------------------------------------------- 通用只读渲染（兜底展示） */

var MAX_ROWS = 200;      // 单表最多 200 行
var MAX_STR = 400;       // 单个字符串显示上限

/** ISO 时间戳 → 友好的「YYYY-MM-DD HH:MM」（只截断显示，不改数据）。 */
var ISO_RE = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/;
function isIso(s) { return ISO_RE.test(s); }
function prettyStamp(v) {
  var s = String(v == null ? '' : v);
  var m = ISO_RE.exec(s);
  return m ? (m[1] + ' ' + m[2]) : s;
}

function cellText(v) {
  if (v == null) return '';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'object') {
    if (Array.isArray(v)) {                       // 表格单元格里是数组 → 用「、」连起来
      return v.map(cellText).filter(function (s) { return s !== ''; }).join('、');
    }
    var parts = [];
    Object.keys(v).forEach(function (k) {
      var s = cellText(v[k]);
      if (s !== '') parts.push(k + ': ' + s);
    });
    return parts.join(' · ');
  }
  return String(v);
}

function clampStr(s) {
  s = String(s);
  return s.length > MAX_STR ? (s.slice(0, MAX_STR) + ' …（共 ' + s.length + ' 字符）') : s;
}

/** 通用表格：rows 是 {cells:[...], attrs:{}}，attrs 用于搜索/排序的 data-*。 */
function buildTable(box, heads, rows) {
  var wrap = el('div', 'table-wrap');
  var t = el('table', 'rows');
  var thead = el('thead');
  var trh = el('tr');
  heads.forEach(function (h) { trh.appendChild(el('th', null, h)); });
  thead.appendChild(trh);
  t.appendChild(thead);
  var tbody = el('tbody');
  rows.forEach(function (row) {
    var tr = el('tr');
    Object.keys(row.attrs || {}).forEach(function (k) { tr.setAttribute(k, row.attrs[k]); });
    row.cells.forEach(function (c) { tr.appendChild(el('td', null, clampStr(c))); });
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);
  box.appendChild(wrap);
  return tbody;
}

/* ------------------------------------------------------------------ 状态 */

var st = {
  me: { username: '', is_staff: false },
  mode: 'loading',  // 'loading' 还没拿到任何数据 / 'live' 实时 / 'sync' 上次同步的快照 / 'none' 两边都没有
  schedule: null,   // {doc, revision, parsed} —— 唯一可写同步对象
  lessons: null,    // settings.lessons（选课：用户的「选择」，不是平台抓的）
  accounts: null,   // settings.accounts（只读账号名）
  schoolRev: null,  // school 同步对象（降级兜底）的修订号
  live: null,       // GET /app/data/ 的实时抓取结果（服务端抓的，不是同步快照）
  liveErr: '',
  liveErrCode: '',
  dataSrc: '',      // 这一轮数据是谁抓的：'本机直连' / '服务器抓取'（见 bridgeSourceLabel）
  mailOpen: null,   // 当前展开正文的那封邮件 uid
  mailRows: [],     // 邮件头列表（本地视图模型；点开一封会把它的 unread 就地改成 false）
  mailUnread: null, // 未读计数（服务端给的原值；null = 未知，绝不瞎猜）
  mailUnreadLast: null,     // 上一次**真的拿到过**的未读数（刷新失败时保住它，不显示 0）
  mailUnreadLastAt: '',     // 上面那个值的抓取时间（如实告诉用户这是什么时候的数）
  /* 课程活动流（ManageBac）：三个来源各自独立，一个失败不影响其它两个。
     null = 还没请求过；{} = 请求过（可能 ok / partial / unavailable，看 status）。 */
  coNotifs: null,           // GET /app/courses/notifications/  → {notifications, status, partial, …}
  coMsgs: null,             // GET /app/courses/messages/       → {messages, courses, sources, …}
  coActivityAt: '',         // 上面两个最后一次落地的时间（给人看）
  tabs: { objects: [] }   // 设置页的同步状态
};

/* --------------------------------------------------- 面板注册（渲染调度） */

var liveLoading = false;
var panelRenderers = {};
var panelOrder = [];

/** 注册一个面板：数据到了（或失败）由 applyLive 统一回调渲染。 */
function renderPanel(id, renderer) {
  if (!$((id))) return;
  if (!panelRenderers[id]) panelOrder.push(id);
  panelRenderers[id] = renderer;
  renderPanelNow(id);
}

/** 立刻重渲染某个已注册面板（渲染器自己的错误也变成可读文字，不白屏）。 */
function renderPanelNow(id) {
  var panel = $(id), renderer = panelRenderers[id];
  if (!panel || !renderer) return;
  clear(panel);
  try {
    renderer(panel);
  } catch (e) {
    clear(panel);
    panel.appendChild(el('p', 'empty',
      '这一块渲染失败了：' + ((e && e.message) || e) + '（数据没丢，点「刷新」可重试）'));
  }
}

/** 「选课（教学组）」卡片已删除（照 Pinghe Launcher Lite 样式重做为按科目分组的选课界面）。
 *  这个函数保留为 no-op，避免调用方报错。 */
function renderLessonsPanel() {
  // no-op: 旧的选课卡片已删除
}

function renderAllPanels() {
  panelOrder.forEach(function (id) { renderPanelNow(id); });
}

/** 面板内的可读提示条（错误红一点、信息淡一点）。 */
function panelMsg(id, text, isError) {
  var box = $(id);
  if (!box) return;
  if (!text) { box.hidden = true; box.textContent = ''; return; }
  box.hidden = false;
  box.textContent = text;              // 平台/后端原文，一律 textContent
  box.className = 'inline-msg' + (isError ? ' inline-msg--error' : '');
}

/** 空态/错误态里补一个明确的「重试」按钮（可用性：错误不只是一行灰字）。
 *  只在真的拿到过失败/空结果时才加，加载中不加。
 *  `pending=true` 时是「正在后台抓」那一态：文案本身以「正在」开头，
 *  但**必须**给用户一个「刷新」按钮（服务端抓完就只差这一次刷新）。 */
function retryInto(box, why, pending) {
  // UI精简：删除视图内的独立刷新/重试按钮，只保留顶栏统一的刷新按钮
  // 用户可以使用右上角的"↻ 刷新"按钮来重试
  return;
}

/* ============================ 实时抓取数据层（GET /app/data/）============================
 * 契约：200 {"ok":true, "edupage":{lessons:[{date,start,end,subject,group,room,teacher}], selected:[…]},
 *            "managebac":{courses:[…], tasks:[…]}, "mail":{unread, recent:[{uid,from,subject,date}]},
 *            "meta":{fetched_at, cache:"hit|miss", accounts:{edupage,managebac,mail}, errors:{平台:原因},
 *                    pending:["edupage"]}}
 * 某个平台失败 → 该段为空数组，原因在 meta.errors.<平台>，页面**原样展示**该原因；
 * **meta.pending 里的平台 = 正在后台抓（首屏不等它）**：EduPage 抓一次要几十秒，
 * 服务端会立刻返回空段 + errors.edupage = 「正在抓取…请稍后点刷新」，抓完写进 30 分钟缓存，
 * 点「刷新」或本页自动重试就能拿到 —— 这一态**按中性提示画**，不是红色错误；
 * 某个平台失败但同步对象里有数据 → 显示「以下为上次同步的数据（时间：…）」；
 * 两边都没有 → 空态（可读文案）。**任何情况都不白屏。**
 * ========================================================================== */

var DATA_PATH = '/app/data/';
var MAIL_PATH = '/app/mail/';          // 正文：GET /app/mail/<uid>/；标记已读：POST /app/mail/<uid>/read/
var MAIL_SEND_PATH = '/app/mail/send/';  // 发送邮件：POST /app/mail/send/
var DATA_TIMEOUT_MS = 90000;           // 实时抓取首次可能要几秒
var SCHOOL_OBJ = 'school';             // 降级兜底快照
var CACHE_TAG = { hit: '缓存命中', miss: '实时抓取' };

/* ------------------------- 本机直连（可选的本机抓取服务）-------------------------
 * 用户在自己电脑上跑 `local-bridge`（只绑 127.0.0.1:38123）时，网页端优先把抓取
 * 交给它：用**用户自己的网络与 IP**，服务器连不上学校平台时这条链路往往能用。
 * 没启动 / 探测失败 → 静默回落到服务器 /app/data/（契约完全一致，降级链不变）。
 *
 * 探测策略：GET /ping，超时 800ms，**整个会话只探一次**并把结论缓存下来
 * （探不到就再也不重试，免得每次开页面都卡 0.8 秒）。
 * 凭据：从同步对象 settings.accounts 取明文账号，只在请求体里传给本机服务
 * （本机服务只在内存里用，不落盘、不写日志），**绝不发给任何第三方**。
 * ---------------------------------------------------------------------------- */
var BRIDGE_PORT = 38123;              // 本机服务端口（网页端与 local-bridge 的约定）
try {
  // 只给测试/诊断留的可覆盖口子：正常页面里这个全局不存在，端口就是 38123
  if (window.__PHIX_BRIDGE_PORT__) BRIDGE_PORT = Number(window.__PHIX_BRIDGE_PORT__) || BRIDGE_PORT;
} catch (e) { /* 忽略 */ }
var BRIDGE_ORIGIN = 'http://127.0.0.1:' + BRIDGE_PORT;
var BRIDGE_PING_PATH = '/ping';
var BRIDGE_DATA_PATH = '/data';
var BRIDGE_MAIL_PATH = '/mail/';
var BRIDGE_MAIL_SEND = '/mail/send/';
var BRIDGE_PING_TIMEOUT_MS = 800;

/** 探测结论缓存（只探一次）；测试/CDP 里可以只要证这一层，不必真的起服务。 */
function bridgeVerdict(ok, accounts) {
  bridgeProbe = Promise.resolve(ok ? BRIDGE_SRC_LOCAL : '');
  bridgeOk = !!ok;
  bridgeTried = true;
  if (accounts !== undefined) bridgeAccounts = accounts;
  return bridgeProbe;
}

var bridgeProbe = null;      // Promise<'本机直连'|''>：探测结论（只探一次并缓存）
var bridgeOk = false;        // 探测到本机桥
var bridgeTried = false;     // 探测过程已结束（成功或失败都算）
var bridgePing = '';         // 只在「服务器抓取」且没探到桥时显示一次说明
var bridgeAccounts = null;   // 同步对象 settings.accounts 里的 accounts（明文，仅内存）

/** 探测本机桥：整个会话只做一次，结论缓存（失败不重试，避免每次卡顿）。 */
function bridgeProbeOnce() {
  if (bridgeProbe) return bridgeProbe;
  bridgeProbe = new Promise(function (resolve) {
    var done = false;
    var settle = function (ok) {
      if (done) return;
      done = true;
      bridgeOk = !!ok;          // 探测失败不是错误：本机桥是可选的，页面不提示、不报警
      bridgeTried = true;
      /* 探测是异步的：结论一出来就让页面（设置页 / 顶栏来源行）重绘一次，
         免得用户先看到「服务器抓取」再莫名其妙变成「本机直连」。 */
      try { applyLive(); } catch (e) { /* 渲染异常不吞掉数据流程 */ }
      resolve(bridgeOk ? BRIDGE_SRC_LOCAL : '');
    };
    if (typeof fetch !== 'function' || typeof AbortController !== 'function') {
      settle(false);            // 老浏览器没有 fetch/AbortController → 当没桥
      return;
    }
    var ctl = new AbortController();
    var timer = setTimeout(function () {
      try { ctl.abort(); } catch (e) { /* 忽略 */ }
      settle(false);
    }, BRIDGE_PING_TIMEOUT_MS);
    fetch(BRIDGE_ORIGIN + BRIDGE_PING_PATH, {
      method: 'GET', mode: 'cors', cache: 'no-store', signal: ctl.signal
    }).then(function (res) {
      return res.json()['catch'](function () { return null; })
        .then(function (body) { return { status: res.status, body: body }; });
    }).then(function (r) {
      clearTimeout(timer);
      settle(r.status === 200 && isObj(r.body) && r.body.service === 'phix-local-bridge');
    })['catch'](function () {
      clearTimeout(timer);
      settle(false);
    });
  });
  return bridgeProbe;
}

/** 同步对象 settings.accounts → accounts 映射（明文；只在内存里，不落任何地方）。 */
function bridgeLoadAccounts() {
  if (bridgeAccounts) return Promise.resolve(bridgeAccounts);
  var ref = st.accounts;
  var pick = function (doc) {
    var acc = (isObj(doc) && isObj(doc.accounts)) ? doc.accounts : null;
    if (acc && Object.keys(acc).length) bridgeAccounts = acc;
    return bridgeAccounts;
  };
  if (ref) return Promise.resolve(pick(ref.doc));
  return getObject(OBJ.accounts)['catch'](function () { return null; })
    .then(function (r) { return pick(r && r.doc); });
}

/** 本机桥可用吗（探测 + 有账号才用；没账号直接用服务器，省一次往返）。 */
function bridgeReady() {
  if (!bridgeTried) return false;
  return bridgeOk && !!bridgeAccounts;
}

var BRIDGE_SRC_LOCAL = '本机直连';
var BRIDGE_SRC_SERVER = '最新数据';

/** 数据来源一行文字（设置页 / 首页共用）。 */
function bridgeSourceLabel() {
  return bridgeReady() ? BRIDGE_SRC_LOCAL : BRIDGE_SRC_SERVER;
}

/** 数据来源说明（设置页给用户看的那段话）。 */
function bridgeSourceNote() {
  if (bridgeReady()) {
    return '本机直连：你电脑上的 phix 本机服务正在代抓，数据从你的电脑直连学校平台，'
         + '抓取走你自己的网络与 IP，平台账号只在你本机的内存里用一次（不落盘、不写日志）。'
         + '关掉本机服务窗口就会自动回到「服务器抓取」。';
  }
  if (bridgeOk) {
    return '服务器抓取：本机服务已启动，但云端同步对象 settings.accounts 里还没有平台账号，'
         + '所以这次仍由服务器代抓。到「个人中心 → 密码管理」补齐后再刷新即可走本机直连。';
  }
  if (bridgeTried) {
    return '服务器抓取：没有探测到本机直连服务（127.0.0.1:38123）。'
         + '这不是错误 —— 本机服务是可选的加速项：跑起来之后数据从你的电脑直连学校平台，'
         + '凭据不出本机，服务器连不上学校平台时通常也能用。';
  }
  return '正在检测本机直连服务…';
}

/** 本机桥请求：POST JSON，超时后放弃（让调用方回落到服务器）。 */
function bridgePost(path, body, timeoutMs) {
  var ctl = new AbortController();
  var timer = setTimeout(function () {
    try { ctl.abort(); } catch (e) { /* 忽略 */ }
  }, timeoutMs || DATA_TIMEOUT_MS);
  return fetch(BRIDGE_ORIGIN + path, {
    method: 'POST',
    mode: 'cors',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: ctl.signal
  }).then(function (res) {
    clearTimeout(timer);
    return res.json().then(function (parsed) {
      return { status: res.status, body: parsed };
    })['catch'](function () {
      return { status: res.status, body: {} };
    });
  }, function (err) {
    clearTimeout(timer);
    throw err;
  });
}

var dataPromise = null;
var syncPromise = null;

function payload() {
  var p = st.live;
  return isObj(p) ? p : {};
}

function pSeg(name) {
  var p = payload();
  return isObj(p[name]) ? p[name] : {};
}

function arrOf(seg, key) {
  var v = isObj(seg) ? seg[key] : null;
  return Array.isArray(v) ? v : [];
}

function numOf(seg, key) {
  var v = isObj(seg) ? seg[key] : null;
  return (typeof v === 'number' && isFinite(v)) ? v : null;
}

function metaAccounts(m) {
  return (isObj(m) && isObj(m.accounts)) ? m.accounts : {};
}

function metaErrors(m) {
  return (isObj(m) && isObj(m.errors)) ? m.errors : {};
}

/** 「已连接：demo-student」——只显示账号名，绝不显示密码。 */
function accountLine(platform, m) {
  var name = metaAccounts(m)[platform];
  return name ? ('已连接：' + String(name)) : '';
}

function plErr(key) {
  if (plPending(key)) return '';           // 正在后台抓 ≠ 出错（文案会在中性提示位里说）
  var v = metaErrors(payload().meta)[key];
  return (typeof v === 'string' && v) ? v : '';
}

/** 平台是否**正在后台抓**（`meta.pending` 点名了它）。
 *  EduPage 抓一次要几十秒，服务端先返回空段 + pending，抓完写缓存等下一次刷新。 */
function plPending(platform) {
  var meta = payload().meta;
  var list = isObj(meta) ? meta.pending : null;
  if (!Array.isArray(list)) return false;
  for (var i = 0; i < list.length; i++) {
    if (list[i] === platform) return true;
  }
  return false;
}

/** 「正在后台抓」这一态给用户看的话 + 刷新按钮（中性色，绝不画红）。 */
var PENDING_TEXT = '正在抓取 EduPage 课表（约 5 秒）…';
var PENDING_HINT = 'EduPage 课表正在后台抓取（约 5 秒）：抓完会自动显示，也可以点「刷新」立刻再取一次。';

function pendingNote() {
  return plPending('edupage') ? PENDING_TEXT : '';
}

/** 抓取时间 / cache 标记（课表、成绩、课程、邮箱、首页、设置共用）。 */
function updateLiveStamp(prefix) {
  var m = payload().meta;
  m = isObj(m) ? m : {};
  var fetched = typeof m.fetched_at === 'string' ? prettyStamp(m.fetched_at) : '';
  var cache = typeof m.cache === 'string' ? m.cache : '';
  var tag = $(prefix + '-cache');
  if (tag) {
    if (cache && CACHE_TAG[cache]) { tag.hidden = false; tag.textContent = CACHE_TAG[cache]; }
    else { tag.hidden = true; tag.textContent = ''; }
  }
  /* 顶栏常驻一行：这份数据是谁抓的（本机直连 / 服务器抓取） */
  var top = $('top-stamp');
  if (top) {
    var who = bridgeSourceLabel();
    top.textContent = (st.live ? who + ' · ' : '') + (fetched || '');
    top.title = bridgeReady()
      ? '本机直连：数据由你电脑上的本机服务抓取，凭据不出本机'
      : '服务器抓取：可选用本机直连服务让数据从你的电脑直连学校平台';
  }
}

/* ------------------------------------------------- 课表条目归一化与选课 */

/** 课表条目归一化：真实字段是 date/start/end/subject/group/room/teacher，其余写法一并兼容。 */
function normLesson(raw) {
  var r = isObj(raw) ? raw : {};
  var day = r.date || r.day || r.d || '';
  return {
    day: day ? String(day) : '',
    start: r.start || r.time || r.from || r.begin || '',
    end: r.end || r.to || r.finish || '',
    subject: r.subject || r.title || r.name || r.course || r.lesson || '',
    room: r.room || r.classroom || r.place || '',
    teacher: r.teacher || r.teachers || r.tutor || '',
    group: r.group || r.groups || r.classgroup || r.class || r.classes ||
           r.teaching_group || r.set || r.section || '',
    note: r.note || r.notes || r.info || ''
  };
}

function liveLessons() { return arrOf(pSeg('edupage'), 'lessons').map(normLesson); }
function liveSelected() {
  var v = pSeg('edupage').selected;
  return Array.isArray(v) ? v.map(function (x) { return cellText(x); })
                              .filter(function (x) { return x !== ''; }) : [];
}

/* ------------------------- 「我自己的课表」：按教学组过滤 ------------------------- */

/** 组名比较用的归一化：去所有空白（含全角空格/不换行空格）+ 转小写 + 去尾部标点。
 *  EduPage 里同一个组会出现 `A` / `A 组` / `A组` 这类写法差别，必须归一化后再比。 */
function groupKey(v) {
  var s = String(v == null ? '' : v);
  s = s.replace(/[\s\u00a0\u3000]/g, '').replace(/[、,，;；]+$/, '');
  try { s = s.normalize('NFKC'); } catch (e) { /* 老浏览器没有 normalize：忽略 */ }
  return s.toLowerCase();
}

/** 用户勾选的教学组集合（**按来源优先级取第一个非空的**，归一化去重、保序）。
 *
 *  优先级（越高越权威；**不是**把几个来源并起来 —— 并起来会让「平台回的全量课程组」
 *  把用户真正勾的那几个组淹没，过滤等于没做）：
 *    ① 同步对象 `settings.lessons`（`{groups:[…]}` / `{lessons:[{group}]}` / 裸数组 / 对象）
 *    ② 同步对象 `school.edupage.selected`（客户端抓取时写进去的「我勾的组」）
 *
 *  **不用接口 `edupage.selected` 做过滤**：实测管理员的 EduPage 账号回的它就是
 *  全 20 个课程组（A…Q、G1…G3），拿它当「我勾的组」一条都筛不掉，还会让页面
 *  假装"已按你的组过滤"。两个来源都空（含**存了空数组** `{"groups":[]}` 的情况）
 *  时**不过滤**，并在页面上如实说明。
 */
function personalGroups() {
  var fromLessons = uniqGroupNames(lessonGroupNames(st.lessons && st.lessons.doc));
  if (fromLessons.length) return fromLessons;
  var fromSync = uniqGroupNames(edupageFromSync().selected);
  if (fromSync.length) return fromSync;
  return [];
}

/** 组名去重：按 `groupKey` 归一化比较，保留第一次出现的原样写法。 */
function uniqGroupNames(list) {
  var out = [], seen = {};
  (list || []).forEach(function (name) {
    var text = cellText(name);
    var key = groupKey(text);
    if (!key || seen[key]) return;
    seen[key] = true;
    out.push(text);
  });
  return out;
}

/** 从任意形态的 lessons 文档里抽组名：`{groups:[…]}` / `{lessons:[{group}]}` / 裸数组 / `{组名:{…}}`。 */
function lessonGroupNames(doc) {
  var out = [];
  function add(v) {
    if (isObj(v)) {
      var nm = v.group || v.groups || v.name || v.title || v.subject;
      if (Array.isArray(nm)) { nm.forEach(add); return; }
      if (nm != null && String(nm) !== '') out.push(String(nm));
      return;
    }
    if (v != null && String(v) !== '') out.push(String(v));
  }
  if (Array.isArray(doc)) { doc.forEach(add); return out; }
  if (!isObj(doc)) return out;
  if (Array.isArray(doc.groups) && doc.groups.length) { doc.groups.forEach(add); return out; }
  var nested = arrAt(doc, ['lessons', 'items', 'selections', 'subjects']);
  if (nested && nested.length) { nested.forEach(add); return out; }
  Object.keys(doc).forEach(function (k) {
    var v = doc[k];
    // 空数组/空对象不算「组名」——`{"groups":[]}` 这种"存了空选课"的文档
    // 曾经被当成有一个叫 "groups" 的教学组（本轮踩到）。
    if (Array.isArray(v)) { if (v.length) out.push(k); return; }
    if (isObj(v)) { if (Object.keys(v).length) out.push(k); return; }
  });
  return out;
}

/** 按教学组过滤课表：**组名归一化比较**；`group` 为空的课（全年级统一活动）保留。
 *  返回 `{lessons, kept, dropped, groups, unknown, emptyGroups}`。 */
function filterLessonsByGroups(lessons, groups) {
  var list = lessons || [];
  var known = (groups || []).filter(function (g) { return groupKey(g) !== ''; });
  if (!known.length) {
    return { lessons: list.slice(), kept: list.length, dropped: 0,
             groups: [], unknown: countGrouplessLessons(list), emptyGroups: true };
  }
  var keySet = {};
  known.forEach(function (g) { keySet[groupKey(g)] = true; });
  var kept = [], dropped = 0, unknown = 0;
  list.forEach(function (l) {
    var g = lessonGroupText(l);
    if (!g) { kept.push(l); unknown++; return; }          // 没有组信息 → 保留
    if (isNationalScience(l)) { kept.push(l); return; }    // 国家理科 → 始终保留（见下）
    if (keySet[groupKey(g)]) kept.push(l); else dropped++;
  });
  return { lessons: kept, kept: kept.length, dropped: dropped, groups: known,
           unknown: unknown, emptyGroups: false };
}

function countGrouplessLessons(list) {
  var n = 0;
  (list || []).forEach(function (l) { if (!lessonGroupText(l)) n++; });
  return n;
}

/** 一条课表的组名文本（可能是数组/字符串，统一成一个字符串）。 */
function lessonGroupText(l) {
  var g = l && l.group;
  if (Array.isArray(g)) {
    var parts = [];
    g.forEach(function (x) { var t = cellText(x); if (t) parts.push(t); });
    return parts.join('、');
  }
  return cellText(g);
}

/** 课表条目归一化：真实字段是 date/start/end/subject/group/room/teacher，其余写法一并兼容。 */

/* ------------------------- 选课渲染（按科目分组，照 Pinghe Launcher Lite 样式）------------------------- */

var selOpen = new Set();   // 折叠展开状态（重渲染后保持）

/** 科目族归一化：去掉 HL/SL 后缀与数字，保留核心科目名（与客户端 subject_family 一致）。 */
function subjFamily(n) {
  return (n || '').trim().replace(/\s*(HL\s*\/\s*SL|HL|SL)\s*\d?\s*(\([^)]*\))?\s*$/, '$2').trim();
}

/** 教学组的上课时间列表 → 人话字符串（如 "周一 08:00 / 周三 10:45"）。 */
function fmtSecTimes(times) {
  return (times || []).map(function (t) { return t.day + ' ' + t.start; }).join(' / ');
}

/* ------------------------- 国家理科：**照抄 PLL**（D:\phl-lite-dev\hellopinghe\app\services.py） --------
 *
 * PLL 原文（services.py:39-50）：
 *     #: 国家理科: Edupage 把国家课程的理科拆成三张轮换课卡
 *     #: (国家物理/国家化学/国家生物, 组 G1/G2/G3, 同一时段并行),
 *     #: 对学生是同一门课 —— 选课与课表统一合并成"国家理科", 且默认选中。
 *     #: 只匹配理化生: Native Chinese/Geography/History/Politics 是独立科目。
 *     _NATIVE_SCIENCE_KEYS = ("native physics", "native chemistry", "native biology",
 *                             "国家物理", "国家化学", "国家生物", "国家理科")
 *     NATIVE_SCIENCE_LABEL = "国家理科"
 *
 *     def is_native_science(name: str) -> bool:
 *         fam = subject_family(name).lower()
 *         return any(k in fam for k in _NATIVE_SCIENCE_KEYS)
 *
 * PLL 原文（services.py:370-398，选课选项构建）：
 *     fam = subject_family(l.subject.name)          # → 下面这几行把三张卡并成一个条目
 *     if is_native_science(l.subject.name):
 *         # 国家理科: 三张轮换卡合成一个"默认必选"选项(无组, 不可取消)
 *         fam, group, teacher = NATIVE_SCIENCE_LABEL, "", "理科组"
 *         display = NATIVE_SCIENCE_LABEL
 *     …
 *     "default": native,          # 默认必选(前端锁定为已选)
 *
 * PLL 原文（services.py:428-454，card_selected —— 课表过滤三条规则）：
 *     ① 无教学组 = 全班必修(班会/国家课程这类 Edupage 不打组的课) → 显示;
 *     ② 国家理科三张轮换卡 → 默认必选;
 *     ③ 其余有组但没命中选课 → 不显示。
 *
 * PLL 原文（services.py:499-553，课表里**合并成一张卡**）：
 *     native: dict[tuple, dict] = {}   # (start,end) → 合并中的国家理科
 *     if is_native_science(subject):
 *         key = (start, end)           # 同一时段多张卡合并(房间/老师收集去重)
 *         …收集 rooms / teachers…
 *     for (start, end), b in native.items():
 *         out.append({"start": start, "end": end, "subject": NATIVE_SCIENCE_LABEL,
 *                     "teacher": (teachers[0] if len(teachers) == 1
 *                                 else (f"{teachers[0]} 等{len(teachers)}位" if teachers else "")),
 *                     "room": " · ".join(rooms), "group": "", "cancelled": b["cancelled"]})
 *
 * PLL 原文（ui/app.js:2237-2249，选课面板把它锁成已选）：
 *     /* 无组 = 全班必修课(班会/语文这类) 或 默认必选课(国家理科), 人人都有, 锁定为已选不可取消 *\/
 *     const label = whole ? (g.default ? "默认必选" : "全班必修") : `组${g.group}`;
 *     …<input type="checkbox" … ${chk}${whole ? " disabled" : ""}>
 */

/** 国家理科的键（照抄 PLL services.py:43-44）——**中英文两种写法都覆盖**：
 *  真实抓到的标题是 `Native Physics物理` / `Native Chemistry化学` / `Native Biology生物`，
 *  归一化（`subjFamily` 去 HL/SL 后缀 + 转小写）后含 "native physics" 等键。
 *  只匹配理化生：`Native Chinese语文` / `Native Geography地理` / `Native History历史` /
 *  `Native Politics思想政治` 是**独立科目**，不算国家理科。 */
var NATIVE_SCIENCE_KEYS = ['native physics', 'native chemistry', 'native biology',
                           '国家物理', '国家化学', '国家生物', '国家理科'];
var NATIVE_SCIENCE_LABEL = '国家理科';

/** 一条课是不是国家理科（照抄 PLL `is_native_science`）。 */
function isNationalScience(l) {
  var fam = subjFamily((l && l.subject) || '').toLowerCase();
  if (!fam) return false;
  for (var i = 0; i < NATIVE_SCIENCE_KEYS.length; i++) {
    if (fam.indexOf(NATIVE_SCIENCE_KEYS[i]) !== -1) return true;
  }
  return false;
}

/** 一条课是不是国家必修（课上始终显示的那些）：
 *  ① 无组（全班必修：班会 / 国家课程）② 国家理科（三张轮换卡）—— 与 PLL `card_selected` 同口径。 */
function isNationalRequired(l) {
  return lessonGroupText(l) === '' || isNationalScience(l);
}

/** **把国家理科的三张轮换卡合并成一条「国家理科」**（照抄 PLL services.py:499-553）。
 *
 *  - 同一时段（start–end）的多张卡并成一条，教室/老师收集去重；
 *  - 老师：1 位就写名字，多位写「首位 等N位」（PLL 同款）；
 *  - 教室：「 · 」连接；组号清空（合成一条，不再有 G1/G2/G3）；
 *  - `cancelled`：全部停课才算停课（`b["cancelled"] = b["cancelled"] and …`）。
 *  返回新的条目数组（顺序：先原样保留非国家理科的卡，再把合并出的卡按开始时间插进去）。 */
function mergeNationalScience(lessons) {
  var list = lessons || [];
  var merged = {};        // 'start|end' → {start,end,rooms:[],teachers:[],cancelled}
  var out = [];
  var days = {};          // 合并出的卡要落回哪一天
  list.forEach(function (l) {
    if (!isNationalScience(l)) { out.push(l); return; }
    var start = String(l.start || ''), end = String(l.end || '');
    var key = start + '|' + end;
    var b = merged[key];
    if (!b) {
      b = merged[key] = { start: start, end: end, rooms: [], teachers: [],
                          cancelled: true, day: l.day || '' };
    }
    var room = String(l.room || '').trim();
    if (room && b.rooms.indexOf(room) === -1) b.rooms.push(room);
    var teacher = String(l.teacher || '').trim();
    if (teacher && b.teachers.indexOf(teacher) === -1) b.teachers.push(teacher);
    b.cancelled = b.cancelled && !!l.cancelled;
    if (!b.day && l.day) b.day = l.day;
  });
  Object.keys(merged).forEach(function (key) {
    var b = merged[key];
    var teachers = b.teachers;
    out.push({
      date: b.day, day: b.day,
      start: b.start, end: b.end,
      subject: NATIVE_SCIENCE_LABEL,
      group: '',                                   // 合成一条 → 无组
      room: b.rooms.join(' · '),
      teacher: teachers.length === 1 ? teachers[0]
             : (teachers.length ? teachers[0] + ' 等' + teachers.length + '位' : ''),
      cancelled: b.cancelled,
      _nationalScience: true                        // 只用于渲染/详情，不参与过滤
    });
  });
  out.sort(function (a, b2) {
    return String(a.day || '').localeCompare(String(b2.day || ''))
        || String(a.start || '').localeCompare(String(b2.start || ''))
        || String(a.subject || '').localeCompare(String(b2.subject || ''));
  });
  return out;
}

/** 检查某个教学组是否在已选集合中（兼容旧版无组字段的存法）。 */
function grpChecked(checkedSet, fam, teacher, group) {
  return checkedSet.has(fam + '|' + teacher + '|' + group) ||
    checkedSet.has(fam + '|' + teacher + '|');
}

/** 渲染一个教学组行（checkbox + 组号 + 老师·教室·上课时间）。 */
function grpRowHTML(fam, g, checkedSet, pad, leadParts, autoCheck) {  /* leadParts: [textOrBold, '· '] —— 与 Pinghe Launcher Lite 同构但用纯 DOM 节点 */
  var whole = !g.group;
  var chk = (whole || autoCheck || grpChecked(checkedSet, fam, g.teacher, g.group))
    ? 'checked' : '';
  var label = whole ? (g['default'] ? '默认必选' : '全班必修') : ('组' + g.group);
  var rooms = (g.rooms || []).join(' ');
  var row = el('label', 'subject-row' + (whole ? ' wc' : ''));
  row.style.paddingLeft = pad + 'px';
  var inp = el('input', null);
  inp.type = 'checkbox';
  inp.setAttribute('data-sub', g.subject || '');
  inp.setAttribute('data-teacher', g.teacher || '');
  inp.setAttribute('data-group', g.group || '');
  if (chk) inp.checked = true;
  if (whole) inp.disabled = true;
  row.appendChild(inp);
  var span = el('span', 'pick-grow');
  /* leadParts: 数组 [text, boldText, text, ...] —— 奇数索引用 <b>，偶数索引纯文本 */
  if (leadParts) {
    leadParts.forEach(function (part, i) {
      if (i % 2 === 1) span.appendChild(el('b', null, part));
      else span.appendChild(document.createTextNode(part));
    });
  }
  span.appendChild(el('b', null, label));
  var detail = el('span', 'rooms');
  detail.textContent = g.teacher + (rooms ? ' · ' : '') + rooms + fmtSecTimes(g.times);
  span.appendChild(detail);
  row.appendChild(span);
  return row;
}

/** 无组课程（国家课程 / 年级统一安排）单独成一行：**没有复选框**，也不表现成「你勾了它」。
 *  以前这里是个 checked + disabled 的复选框 —— 看起来就像"系统替我选上了"，用户会以为是自己选的。
 *  课表语义完全不变：无组的课**始终**显示在课表里（学校的必修安排，客户端同款）。 */
function wholeRowHTML(fam, g) {
  var rooms = (g.rooms || []).join(' ');
  var row = el('div', 'subject-row wc pick-whole__row');
  row.setAttribute('data-whole', '1');           // 测试/脚本用：这是「全班必修」行，不是选课项
  row.setAttribute('data-sub', g.subject || '');
  row.setAttribute('data-group', '');
  row.appendChild(el('span', 'wc-badge', '全班必修'));
  var span = el('span', 'pick-grow');
  span.appendChild(el('b', null, g.subject || fam || '（未命名科目）'));
  var detail = el('span', 'rooms');
  detail.textContent = g.teacher + (rooms ? ' · ' : '') + rooms + fmtSecTimes(g.times);
  span.appendChild(detail);
  row.appendChild(span);
  return row;
}

/**「全班必修（自动包含，不用选）」区块：单独排在选课列表末尾，说明清楚这几门课的去向。
 *
 *  只有**无组的国家课程**（语文 / 地理 / 历史 / 思想政治 / 体育 / 班会）在这里 ——
 *  照 PLL 的语义：「无教学组 = 全班必修（Edupage 不打组的课）→ 显示」。
 *  **国家理科不在这一块**：它是 `default: true` 的「默认必选」选项，照 PLL 的样子
 *  出现在科目列表里（带一个锁定为已选的复选框、标签写「默认必选」）。 */
function wholeBlockHTML(items) {
  var box = el('div', 'pick-whole');
  var head = el('div', 'pick-whole__head');
  head.appendChild(el('b', null, '全班必修（自动包含，不用选）'));
  head.appendChild(el('span', 'rooms',
    '学校统一安排、没有教学组，每个学生都上 —— 这些课始终显示在你的课表里，'
    + '不在下面的勾选范围里，也不用手动勾。'));
  box.appendChild(head);
  (items || []).forEach(function (it) { box.appendChild(wholeRowHTML(it.fam, it.g)); });
  return box;
}

/** 按科目分组的选课界面（照 Pinghe Launcher Lite 的 `subjectPickerHTML`，ui/app.js:2251-2271）。
 *
 *  subjects 是 [{subject, groups: [{subject, group, teacher, rooms, times, default}]}]。
 *  **无组的行照旧照 PLL 渲染**：标签是 `g.default ? "默认必选" : "全班必修"`、
 *  复选框 `checked + disabled`（PLL ui/app.js:2237-2249 原文）。国家理科就是靠
 *  `default: true` + `group: ""` 走这条路的（`buildSubjectGroups` 把三张轮换卡并成一条），
 *  所以这里**不需要**任何国家理科特判 —— 与 PLL 完全同构。
 *  另外：无组的**全班必修**（班会/语文这类）照网页端既定做法单独排在末尾区块
 *  （`wholeBlockHTML`，没有复选框，不表现成「用户勾了它」）。 */
function subjectPickerHTML(subjects, filter, checkedSet, autoCheck) {
  var f = (filter || '').trim().toLowerCase();
  var filtered = subjects.filter(function (s) {
    return !f || s.subject.toLowerCase().indexOf(f) !== -1 ||
      (s.groups || []).some(function (g) { return (g.subject || '').toLowerCase().indexOf(f) !== -1; });
  });
  if (!filtered.length) return el('div', 'empty', '没有匹配的科目');
  var frag = document.createDocumentFragment();
  var whole = [];
  filtered.forEach(function (s) {
    var pickable = (s.groups || []).filter(function (g) {
      /* 无组的「全班必修」单独成区块；无组的「默认必选」（国家理科）留在科目列表里 */
      return !!g.group || g['default'];
    });
    (s.groups || []).forEach(function (g) {
      if (!g.group && !g['default']) whole.push({ fam: s.subject, g: g, subject: s.subject });
    });
    if (!pickable.length) return;            // 该科目只剩全班必修 → 只在末尾区块里出现
    var flat = pickable.length === 1;
    if (flat) {
      // 只有一个教学组：一行搞定，不用折叠
      var lead = s.subject === NATIVE_SCIENCE_LABEL ? null : [s.subject, ' · '];
      var row = grpRowHTML(s.subject, pickable[0], checkedSet, 8, lead, autoCheck);
      frag.appendChild(row);
    } else {
      // 多个教学组：折叠头 + 展开区
      var skey = 's:' + s.subject;
      var sopen = selOpen.has(skey);
      var head = el('div', 'fold-head');
      head.setAttribute('data-fold', skey);
      var tri = el('span', 'tri', sopen ? '▼' : '▶');
      head.appendChild(tri);
      var pickGrow = el('span', 'pick-grow');
      pickGrow.appendChild(el('b', null, s.subject));
      pickGrow.appendChild(el('span', 'rooms', pickable.length + ' 个教学组，选你的'));
      head.appendChild(pickGrow);
      frag.appendChild(head);
      var body = el('div', 'fold-body' + (sopen ? '' : ' hidden'));
      pickable.forEach(function (g) {
        body.appendChild(grpRowHTML(s.subject, g, checkedSet, 26, ''));
      });
      frag.appendChild(body);
    }
  });
  if (whole.length) frag.appendChild(wholeBlockHTML(whole));
  return frag;
}

/** 折叠展开事件绑定。 */
function bindPickerFolds(container) {
  var heads = container.querySelectorAll('.fold-head');
  for (var i = 0; i < heads.length; i++) {
    (function (h) {
      h.addEventListener('click', function () {
        var hidden = h.nextElementSibling.classList.toggle('hidden');
        h.querySelector('.tri').textContent = hidden ? '▶' : '▼';
        if (hidden) selOpen.delete(h.dataset.fold); else selOpen.add(h.dataset.fold);
      });
    })(heads[i]);
  }
}

/** 从选课面板收集用户勾选的教学组（{subject, teacher, group} 三元组列表）。 */
function collectPickerSelection(container) {
  var sel = [];
  var inputs = container.querySelectorAll('input[type=checkbox]:checked:not([disabled])');
  for (var i = 0; i < inputs.length; i++) {
    var c = inputs[i];
    sel.push({ subject: c.getAttribute('data-sub') || '',
               teacher: c.getAttribute('data-teacher') || '',
               group: c.getAttribute('data-group') || '' });
  }
  return sel;
}

/** 从课表条目按科目族聚合出选课界面需要的数据结构（**照抄 PLL `wizard_subject_options`**，
 *  services.py:361-407）：
 *
 *     fam = subject_family(l.subject.name)
 *     if is_native_science(l.subject.name):
 *         # 国家理科: 三张轮换卡合成一个"默认必选"选项(无组, 不可取消)
 *         fam, group, teacher = NATIVE_SCIENCE_LABEL, "", "理科组"
 *         display = NATIVE_SCIENCE_LABEL
 *     ent = grouped.setdefault((fam, group, teacher), {...})
 *     …
 *     "default": native,          # 默认必选(前端锁定为已选)
 *
 *  返回 `[{subject, groups: [{subject, group, teacher, rooms, times, default}]}]`。
 *  —— **国家理科的三张轮换卡（`Native Physics物理` G1 / `Native Chemistry化学` G2 /
 *  `Native Biology生物` G3）在这里被并成一条**「国家理科 / 无组 / 理科组 / default:true。
 *  其余科目按科目族归一化（History HL/SL2 → History），每个族下按 (group, teacher) 聚合。 */
function buildSubjectGroups(lessons) {
  var WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  var byFamily = {};
  var familyOrder = [];
  (lessons || []).forEach(function (l) {
    var fam = subjFamily(l.subject);
    if (!fam) return;
    var subject = l.subject;
    var group = l.group || '';
    var teacher = l.teacher || '';
    if (isNationalScience(l)) {
      /* 国家理科：三张轮换卡合成一个「默认必选」选项（无组、不可取消）—— PLL 原文 */
      fam = NATIVE_SCIENCE_LABEL;
      group = '';
      teacher = '理科组';
      subject = NATIVE_SCIENCE_LABEL;
      l = { subject: NATIVE_SCIENCE_LABEL, group: '', teacher: '理科组',
            room: l.room, start: l.start, day: l.day, cancelled: l.cancelled };
    }
    if (!byFamily[fam]) { byFamily[fam] = {}; familyOrder.push(fam); }
    var gkey = group + '|' + teacher;
    if (!byFamily[fam][gkey]) {
      byFamily[fam][gkey] = {
        subject: subject, group: group, teacher: teacher,
        rooms: {}, times: {},
        /* **默认必选**只给国家理科那一条（PLL services.py:392 `native = fam == NATIVE_SCIENCE_LABEL`）。
           普通无组课（班会 / 国家课程）是「全班必修」，网页端把它单独排在末尾区块、**不给复选框**
           （`subjectPickerHTML` 里 `!g.group && !g['default']` 走那一块）。 */
        'default': (fam === NATIVE_SCIENCE_LABEL)
      };
    }
    var entry = byFamily[fam][gkey];
    if (l.room) entry.rooms[l.room] = true;
    if (l.start) {
      // 计算周几
      var day = l.day || '';
      var wd = '';
      var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
      if (m) wd = WEEK[new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getDay()];
      if (wd) entry.times[wd + ' ' + l.start] = true;
    }
  });
  // 转成数组，按科目族排序
  var result = [];
  familyOrder.forEach(function (fam) {
    var groups = [];
    Object.keys(byFamily[fam]).forEach(function (gkey) {
      var g = byFamily[fam][gkey];
      groups.push({
        subject: g.subject, group: g.group, teacher: g.teacher,
        rooms: Object.keys(g.rooms), times: Object.keys(g.times).sort().map(function (t) {
          var parts = t.split(' ');
          return { day: parts[0], start: parts.slice(1).join(' ') };
        }),
        'default': g['default']
      });
    });
    // 全班必修 / 默认必选排前面（PLL：sorted by (group == "", group, teacher)）
    groups.sort(function (a, b) {
      if (a['default'] && !b['default']) return -1;
      if (!a['default'] && b['default']) return 1;
      return (a.group || '').localeCompare(b.group || '') || (a.teacher || '').localeCompare(b.teacher || '');
    });
    result.push({ subject: fam, groups: groups });
  });
  // 科目族排序（PLL: sorted(by_subject.items())）
  result.sort(function (a, b) { return String(a.subject).localeCompare(String(b.subject)); });
  return result;
}

/* ------------------- 降级路径：同步对象 school 里的上次同步快照 ------------------- */

/** 读一次同步对象 school（成功/失败都只请求一次）。
 *  一次读到的快照只补渲染**一次**：多次调用 ensureSync 不能反复重渲染
 *  （否则同一块面板会被重复追加，出现「表里两遍数据」）。 */
var syncApplied = false;

function ensureSync() {
  if (st.sync) {
    if (!syncApplied) { syncApplied = true; applyLive(); }
    return Promise.resolve(st.sync);
  }
  if (!syncPromise) {
    syncPromise = getObject(SCHOOL_OBJ).then(function (ref) {
      st.sync = ref;
      st.schoolRev = ref.revision;
      if (!syncApplied) { syncApplied = true; applyLive(); }
      return ref;
    })['catch'](function (e) { syncPromise = null; throw e; });
  }
  return syncPromise;
}

function syncDoc() {
  return (st.sync && st.sync.parsed && isObj(st.sync.doc)) ? st.sync.doc : null;
}

function hasData(v) {
  if (v == null) return false;
  if (Array.isArray(v)) return v.length > 0;
  if (isObj(v)) return Object.keys(v).length > 0;
  return true;
}

/** 在 doc 里找一段（顶层同名键优先，其次键名包含）。 */
function sectionOf(doc, name) {
  if (!isObj(doc)) return null;
  if (doc[name] != null) return doc[name];
  var keys = Object.keys(doc), lower = name.toLowerCase();
  for (var i = 0; i < keys.length; i++) {
    if (keys[i].toLowerCase().indexOf(lower) !== -1) return doc[keys[i]];
  }
  return null;
}

/** 按天字典 → 平铺条目（把日期键写回 date）。 */
function flattenDays(days) {
  if (!isObj(days)) return [];
  var out = [];
  Object.keys(days).forEach(function (day) {
    var arr = Array.isArray(days[day]) ? days[day] : [];
    arr.forEach(function (item) {
      if (isObj(item)) {
        var copy = {};
        Object.keys(item).forEach(function (k) { copy[k] = item[k]; });
        if (!copy.date && !copy.day) copy.date = day;
        out.push(copy);
      } else if (item != null && item !== '') {
        out.push({ date: day, subject: item });
      }
    });
  });
  return out;
}

/** 同步对象 school.edupage → {lessons:[], selected:[]}（days 形态优先）。 */
function edupageFromSync() {
  var sec = sectionOf(syncDoc(), 'edupage');
  if (Array.isArray(sec)) return { lessons: sec, selected: [] };
  if (!isObj(sec)) return { lessons: [], selected: [] };
  var list = Array.isArray(sec.lessons) && sec.lessons.length ? sec.lessons : null;
  var lessons = list || flattenDays(sec.days);
  var selected = Array.isArray(sec.selected) ? sec.selected.map(cellText)
                                    .filter(function (x) { return x !== ''; }) : [];
  return { lessons: lessons, selected: selected };
}

/** 同步对象里的某个平台段（managebac / mail）。 */
function segFromSync(name) {
  return sectionOf(syncDoc(), name);
}

/** 同步快照的时间：优先平台自己的 fetched_at/synced_at，再退回文档级时间。 */
function syncStamp() {
  var doc = syncDoc();
  var v = '';
  if (doc) {
    v = firstString(segFromSync('edupage'), ['fetched_at', 'synced_at', 'updated_at'])
     || firstString(segFromSync('managebac'), ['fetched_at', 'synced_at', 'updated_at'])
     || firstString(segFromSync('mail'), ['fetched_at', 'synced_at', 'updated_at'])
     || firstString(doc, ['updated_at', 'synced_at', 'fetched_at']);
  }
  return v ? prettyStamp(v) : '';
}

function firstString(doc, keys) {
  if (!isObj(doc)) return '';
  for (var i = 0; i < keys.length; i++) {
    var v = doc[keys[i]];
    if (typeof v === 'string' && v) return v;
  }
  return '';
}

/** 同步快照里的某个平台数据（没有则 null）。 */
function syncFallbackRaw(platform) {
  if (platform === 'edupage') return edupageFromSync().lessons;
  var seg = segFromSync(platform === 'managebac' ? 'managebac' : 'mail');
  if (!isObj(seg)) return null;
  if (platform === 'managebac') {
    return (Array.isArray(seg.courses) && seg.courses.length) ||
           (Array.isArray(seg.tasks) && seg.tasks.length) ? seg : null;
  }
  return (Array.isArray(seg.recent) && seg.recent.length) ? seg : null;
}

/** 整个 /app/data/ 都没成功时的提示语：
 *  accounts_not_configured 是「用户还没填平台账号」，指路个人中心，比笼统的抓取失败更有用。 */
function liveFailText(platformLabel) {
  if (st.liveErrCode === 'accounts_not_configured') {
    return '还没配置平台账号：请到「个人中心 → 密码管理」补齐 EduPage / ManageBac / 邮箱的账号密码，'
         + '服务器才能实时抓取' + (platformLabel ? '（' + platformLabel + '）' : '') + '。';
  }
  return st.liveErr || '实时抓取失败，请点右上角「刷新」重试。';
}

/** 降级提示条：写进给定的提示位，并返回用的时间串。 */
function syncNotice(msgId, platformLabel) {
  var stamp = syncStamp();
  var who = platformLabel || '';
  var head = (st.live && !st.liveErr && !hasData(plErr(platformLabel)))
    ? ('这次实时抓取没拿到' + who + '的数据')
    : ('这次实时抓取' + who + '没成功');
  var text = '以下为上次同步的数据' + (stamp ? '（时间：' + stamp + '）' : '（同步对象里没有时间戳）') +
             ' —— ' + head + '。';
  panelMsg(msgId, text, false);
  return stamp;
}

/* -------------------------------------------------- 每轮抓取后的统一分发 */

/** 把这一轮数据（含降级）分发到各派生片段，然后整页重渲染。
 *  任何渲染异常都就地吞掉（面板里已经有兜底文案），绝不把异常抛回 promise 链
 *  —— 否则整个数据流程会中断，页面停在 loading 上。 */
function applyLive() {
  try {
    st.syncReady = !!st.sync;
    st.mode = liveMode();
    st.ttLessons = resolveLessons();
    st.coCourses = resolveCourses();
    st.coTasks = resolveTasks();
    st.mailRows = resolveMail();
    st.mailUnread = resolveUnread();

    updateLiveStamp('tt');
    updateLiveStamp('mb');
    updateLiveStamp('mail');
    updateLiveStamp('home');
    updateLiveStamp('set');

    var live = !!st.live;
    panelMsg('tt-error', live ? plErr('edupage') : st.liveErr, true);
    panelMsg('mb-error', live ? plErr('managebac') : '', true);
    panelMsg('home-error', live ? firstPlatformError() : st.liveErr, true);
    panelMsg('set-error', live ? '' : st.liveErr, true);

    renderAllPanels();
    renderSchedule();
    renderMailAccounts(st.accounts);
    renderSettings();
    renderHomeNow();
    /* 工具条上的绿色「已连接」跟着这一轮数据走（课表面板没重画时也要更新，
       例如数据在日程视图里落地）——它是**状态**，不是课表内容的一部分。 */
    ttRenderConn();
    ttUpdateNowLine();      // 时间线只在课表可见时有意义，函数内部自己判
    updateTtExportState();  // 「⬇ 导出课表」的可用状态跟着这一轮数据走
    guardTabs();
    show($('mail-close'), false);      // 正文区没打开时不显示「返回列表」
  } catch (e) {
    // 兜底：任何一处渲染炸了也要让其余部分活着，并把原因摆到页面上
    panelMsg('set-error', '页面渲染出错：' + ((e && e.message) || e) + '（数据没丢，点「刷新」可重试）', true);
  }
}

/** 用哪一份数据渲染：live（实时）/ sync（上次同步的快照）/ none（还没有任何数据）。
 *  注意：抓取成功但「三个平台一条都没拿到」时不算 live —— 那和失败一样需要降级，
 *  否则页面会显示成「空数据」而不是「以下为上次同步的数据」。 */
function liveMode() {
  if (st.live && !st.liveErr) {
    var p = payload();
    if (hasData(pSeg('edupage')) || hasData(pSeg('managebac')) || hasData(pSeg('mail'))) return 'live';
    return st.syncReady ? 'sync' : 'none';
  }
  return st.syncReady ? 'sync' : 'none';
}

/** 首页只放一条最具代表性的平台错误（逐平台原因在各自视图里）。 */
function firstPlatformError() {
  return plErr('edupage') || plErr('managebac') || plErr('mail') || '';
}

/** 取一次实时数据；force=true 时加 ?force=1 绕过服务端缓存重新抓。并发调用共用同一个请求。
 *  数据源优先级：**本机直连（可选）→ 服务器 /app/data/**；两条路的响应结构完全一致，
 *  失败都走同一套降级链（服务器 → 同步快照 → 空态），所以这里只管把哪条路的结果塞进 st.live。
 */
function loadAppData(force) {
  if (force) { st.live = null; st.liveErr = ''; dataPromise = null; }
  if (dataPromise) return dataPromise;
  liveLoading = true;
  ensureSync()['catch'](function () {});   // 同步对象并行预取：抓到一半失败时立即有降级数据
  if (!bridgeTried) bridgeProbeOnce()['catch'](function () {});

  dataPromise = bridgeProbeOnce()
    .then(function () { return bridgeLoadAccounts(); })['catch'](function () { return null; })
    .then(function () {
      if (!bridgeReady()) return null;                     // 没桥 / 没账号 → 走服务器
      st.dataSrc = BRIDGE_SRC_LOCAL;
      return bridgePost(BRIDGE_DATA_PATH + (force ? '?force=1' : ''),
                        { accounts: bridgeAccounts }, DATA_TIMEOUT_MS)
        ['catch'](function () { return null; });           // 本机失败 → 静默回落服务器
    })
    .then(function (local) {
      if (local && local.status === 200 && local.body && local.body.ok !== false) return local;
      if (local && local.status === 409) return local;     // 本机明确说「没账号」→ 不再打服务器
      if (bridgeReady()) bridgeOk = false;                 // 本机这条路不通 → 本次会话不再走它
      st.dataSrc = BRIDGE_SRC_SERVER;
      return req('GET', DATA_PATH + (force ? '?force=1' : ''), undefined,
                 { timeoutMs: DATA_TIMEOUT_MS });
    })
    .then(function (res) {
      liveLoading = false;
      if (res && res.status !== 200) {
        // 后端在契约之外还会用 409 accounts_not_configured 表示「这个账号还没填平台账号」
        st.liveErrCode = (res.body && res.body.error && res.body.error.code) || '';
        st.liveErr = errText(res);
        st.live = null;
        return null;
      }
      if (res && res.body && res.body.ok === false) { st.liveErr = errText(res); st.live = null; return null; }
      st.liveErrCode = '';
      st.liveErr = '';
      st.live = (res && res.body) || {};
      return st.live;
    })['catch'](function (err) {
      liveLoading = false;
      if (err && err.unauthorized) return null;
      st.liveErrCode = '';
      st.live = null;
      st.liveErr = err && err.isTimeout
        ? '实时抓取超过 ' + Math.round(DATA_TIMEOUT_MS / 1000) + ' 秒还没回来，请稍后点「刷新」重试。'
        : ('实时抓取失败：' + ((err && err.message) || '网络错误') + '，请点「刷新」重试。');
      return null;
    })
    .then(function (r) {
      applyLive();
      dataPromise = null;
      // 同步快照是降级依据：它可能比实时数据晚到 —— ensureSync 内部会在首次就位时自己补一次渲染
      ensureSync()['catch'](function () {});
      schedulePendingRetry();
      /* 「抓完了」的收尾：无论走哪条路（本机直连 / 服务器 / 失败降级）
         都要把刷新按钮的 loading 摘掉，绝不留下一个转不完的按钮。 */
      finishRefreshButtons();
      return r;
    });
  return dataPromise;
}

/** 刷新时按钮的即时反馈：置灰 + 转圈 + 「正在抓取…」（同一颗按钮不重复入栈）。 */
var refreshingBtns = [];

function beginRefreshButtons(btns) {
  (btns || []).forEach(function (id) {
    var b = $(id);
    if (!b || b.getAttribute('data-refreshing') === '1') return;
    b.setAttribute('data-refreshing', '1');
    b.setAttribute('data-label', b.textContent);
    b.disabled = true;
    b.textContent = '⏳ 正在抓取…';
    refreshingBtns.push(id);
  });
}

function finishRefreshButtons() {
  var ids = refreshingBtns.slice();
  refreshingBtns.length = 0;
  ids.forEach(function (id) {
    var b = $(id);
    if (!b) return;
    b.removeAttribute('data-refreshing');
    b.disabled = false;
    b.textContent = b.getAttribute('data-label') || '↻ 刷新';
  });
}

/* ---- 「EduPage 正在后台抓」的自动重试 -------------------------------------------
 * 服务端首屏不等 EduPage（抓一次要几十秒）：先给空段 + meta.pending，
 * 抓完写进 30 分钟缓存。这里隔一会儿**静默**再取一次（非 force，免得又踢一次后台任务），
 * 用户在大多数情况下不用自己点刷新；次数封顶，不会一直空转。 */
var PENDING_RETRY_MS = 10000;
var PENDING_RETRY_MAX = 4;
var pendingRetryTimer = null;
var pendingRetries = 0;

function schedulePendingRetry() {
  if (!plPending('edupage')) { pendingRetries = 0; return; }
  if (pendingRetryTimer || pendingRetries >= PENDING_RETRY_MAX) return;
  pendingRetries += 1;
  pendingRetryTimer = setTimeout(function () {
    pendingRetryTimer = null;
    loadAppData(false);
  }, PENDING_RETRY_MS);
}

/** 同步快照还没读完（或这一轮还没开始）时，面板先说「正在读取」而不是空态（避免闪一下空）。 */
function pendingSync() {
  return st.mode === 'loading' || (!st.syncReady && (!st.live || !!st.liveErr));
}

/** 空结果的通用文案：有平台错误就说错误，否则说清楚是「这段没数据」。 */
function emptyText(what, platformKey, label) {
  var e = plErr(platformKey);
  if (e) return '暂时拿不到' + what + '（' + e + '）';
  if (plPending(platformKey)) return PENDING_TEXT;      // 正在后台抓：中性文案，不是错误
  if (st.mode === 'live') {
    return '这次抓取没有拿到' + what + '，同步对象里也没有快照。可以点右上角「刷新」重试；'
         + '若一直为空，请到客户端确认' + label + '账号是否还能登录并同步一次。';
  }
  return liveFailText(what);
}

/* ------------------------------------------------------ 派生数据（含降级） */

/** 某个平台这一段该用哪份数据：'live' 有实时数据 / 'sync' 降级快照 / 'none' 两边都没有。
 *  注意「抓取成功但这一平台一条都没拿到」（平台错误为空的空结果）也要降级，
 *  否则页面会显示成「这次没有数据」而不是「以下为上次同步的数据」。 */
function modeOf(platform, liveArr) {
  if (liveArr && liveArr.length) return 'live';
  return hasData(syncFallbackRaw(platform)) ? 'sync' : 'none';
}

/** 课表：优先 edupage.lessons（实时）；该平台出错 / 一条都没拿到时降级到同步对象（含 days 形态）。
 *  过滤逻辑升级：有 {subject,teacher,group} 列表时，按「科目族相同 AND（组号相同 OR 该条无组号）」过滤。
 *  这才是"我的课表"——避免字母撞车（E 组在别的科目也出现）。 */
function resolveLessons() {
  var snap = edupageFromSync();
  var mode = modeOf('edupage', liveLessons());
  var pending = plPending('edupage');
  var lessons = [], selected = [];

  if (mode === 'live') {
    lessons = liveLessons();
    selected = liveSelected();
    st.ttLabel = '服务器实时抓取';
    /* 这一段**不写账号名、不写教学组清单、不写过滤条数**：
       课表页关于连接状态只留工具条上那四个字「已连接」（见 ttRenderConn），
       账号名在设置 / 邮箱页看，教学组在选课面板里看。 */
    var info = [];
    if (pending) info.push(PENDING_HINT);        // 有旧数据在看，同时后台在刷新
    panelMsg('tt-info', info.join(' · '), false);
  } else if (mode === 'sync') {
    lessons = snap.lessons.map(normLesson);
    selected = snap.selected;
    st.ttLabel = '上次同步的快照';
    syncNotice('tt-info', 'EduPage');
    if (pending) {
      var box = $('tt-info');
      if (box) panelMsg('tt-info', PENDING_HINT + ' ' + box.textContent, false);
    }
  } else {
    st.ttLabel = '服务器实时抓取';
    if (pending) panelMsg('tt-info', PENDING_HINT, false);       // 中性提示，不是错误
    else if (pendingSync()) panelMsg('tt-info', '', false);
    else panelMsg('tt-info', '', false);        // 连不上时不在这里写账号名（工具条不亮绿就够了）
  }

  if (!selected.length && snap) selected = snap.selected;
  if (!selected.length) selected = lessonGroupNames(st.lessons && st.lessons.doc);
  st.ttSelected = selected;

  /* ---- 只保留「我自己的课表」：按选课三元组过滤 ----
     EduPage 原始课表是**全年级候选**（同一时段十几门并行课），必须按组筛一层；
     有三元组数据时：科目族相同 AND（组号相同 OR 该条无组号）→ 显示；
     没有组信息的课（年级统一活动）保留。
     **一次选修都没勾**时不铺开全年级候选，只留国家必修那部分（"国家必修不用勾选"），
     课表位置会同时给出选课入口。 */
  var picked = personalGroups();
  var filtered = filterLessonsByTriples(lessons);
  st.ttGroupInfo = {
    groups: picked,
    raw: lessons.length,
    kept: filtered.kept,
    dropped: filtered.dropped,
    unknown: filtered.unknown,
    empty: filtered.empty
  };
  /* 课表页**不再**写「只显示你选的课：39 / 273 条，已隐藏 234 条其它组的课」这类过滤细节
     （用户明确要求删掉）：过滤照旧生效（见 filterLessonsByTriples），只是不再把这些数字
     摆在课表上。选课相关的说明只在**选课面板**里说 —— 见 savePickerSelection 附近的文案。 */
  if (filtered.empty && mode !== 'sync') {
    /* 没选课且不是降级态时**不再**说「下面显示全年级候选课表」——课表位置会画空态卡 + 选课入口，
       那句提示只会让人以为「这些课都是我的」。清掉这一行，避免与空态卡自相矛盾。
       降级态（sync）时保留 syncNotice 写的「以下为上次同步的数据」提示。 */
    var tbox2 = $('tt-info');
    if (tbox2 && tbox2.textContent) panelMsg('tt-info', '', false);
  }
  /* **国家理科合并成一条「国家理科」**（照 PLL `personal()`）：同一时段的三张轮换卡
     （`Native Physics物理` G1 / `Native Chemistry化学` G2 / `Native Biology生物` G3）
     合成一条，教室/老师收集去重、老师多位写「首位 等N位」、组号清空、`cancelled` 取与。
     放在这里合并，是为了让课表、选课面板、兼容层三处**看到同一份数据**（PLL 也是这样：
     合并结果写进它的个人课表缓存，再喂给课表与选课）。 */
  return mergeNationalScience(filtered.lessons);
}

/** 按选课三元组过滤课表：科目族相同 AND（组号相同 OR 该条无组号）。
 *  返回 {lessons, kept, dropped, unknown, empty}。
 *  有三元组数据时用三元组过滤；没有时退回旧的 groups 字母过滤；两者都无 → 不过滤（empty）。 */
function filterLessonsByTriples(lessons) {
  var list = lessons || [];
  // 读取三元组选课数据
  var doc = st.lessons && st.lessons.doc;
  var triples = (isObj(doc) && Array.isArray(doc.lessons)) ? doc.lessons : [];
  if (triples.length) {
    // 构建过滤集：{ subjectFamily: Set<group> }
    var familyGroups = {};
    triples.forEach(function (row) {
      if (!isObj(row)) return;
      var fam = subjFamily(row.subject || '');
      var grp = (row.group || '').trim();
      if (!fam) return;
      if (!familyGroups[fam]) familyGroups[fam] = {};
      familyGroups[fam][grp] = true;   // 空组也记（表示该科目族下全班必修的课也要）
    });
    var kept = [], dropped = 0, unknown = 0;
    list.forEach(function (l) {
      var lFam = subjFamily(l.subject || '');
      var lGrp = (l.group || '').trim();
      if (!lFam) { kept.push(l); unknown++; return; }          // 没科目信息 → 保留
      /* 无组的课 = 学校的必修安排（国家课程 / 年级统一活动：语文、地理、Class meeting…）。
         它们**始终显示在课表里**，与用户勾了哪些组无关 —— 这既是选课界面「全班必修（自动包含，
         不用选）」区块对用户的承诺，也是旧的 groups 过滤路径（filterLessonsByGroups：
         `if (!g) { kept.push(l); unknown++; }`）一直以来的语义。
         之前这条三元组路径写成了「科目族也要被选中才保留」，两条路径不一致 → 这些课
         在任何网页端保存的选课下都会被静默隐藏（真实数据实测：只勾 G1 时 273 条里它们全不见）。
         注意：**只对无组课放宽**，有组课的过滤一点没松。 */
      if (!lGrp) { kept.push(l); return; }
      /* 国家理科（`Native Physics物理` G1 / `Native Chemistry化学` G2 / `Native Biology生物` G3）：
         照 PLL `card_selected` 的第 ② 条 —— 「国家理科三张轮换卡 → 默认必选」，
         与用户勾了什么无关。它们有组号（G1/G2/G3），所以必须在组匹配之前放行。
         （判据与 PLL `is_native_science` 完全一致：科目族里含 "native physics/chemistry/biology"
           或 "国家物理/化学/生物/国家理科" 之一；IB 选修课「Physics HL1 · 组 A」不含 Native 前缀，
           不在其中，仍按选课过滤。） */
      if (isNationalScience(l)) { kept.push(l); return; }
      var fg = familyGroups[lFam];
      if (!fg) { dropped++; return; }                           // 科目族不匹配 → 隐藏
      if (fg[lGrp]) { kept.push(l); return; }                   // 组号匹配 → 保留
      dropped++;
    });
    return { lessons: kept, kept: kept.length, dropped: dropped, unknown: unknown, empty: false };
  }
  // 没有三元组数据时退回旧的 groups 字母过滤
  var picked = personalGroups();
  if (picked.length) {
    var filtered = filterLessonsByGroups(list, picked);
    return { lessons: filtered.lessons, kept: filtered.kept, dropped: filtered.dropped,
             unknown: filtered.unknown, empty: false };
  }
  // 两者都无 → 不过滤（空态）。但国家必修照样先摘出来单独返回，见下。
  return { lessons: nestNationalOnly(list), kept: 0, dropped: 0, unknown: 0, empty: true,
           nationalOnly: true };
}

/** 「一次选修都没勾」时课表该显示什么：**只**显示国家必修那部分（无组国家课程 + 国家理科）。
 *
 *  —— 全年级候选（273 条并行课）绝不能当成"我的课表"铺开（那是上一轮修掉的坑），
 *  但国家必修本来就有课，一块都不显示会让人以为课表坏了。
 *  照 PLL 的 `personal()` 文档：「未跑向导的新账号会先看到必修课, 选完课后选修课自动出现」。
 *  国家理科在这一态里同样由 `mergeNationalScience` 合成一张「国家理科」卡。 */
function nestNationalOnly(list) {
  var out = [];
  (list || []).forEach(function (l) { if (isNationalRequired(l)) out.push(l); });
  return out;
}

/** 课程：名称 / 总评 / 单元数（实时优先，失败 / 空结果降级到同步快照）。 */
function mbCourses(seg) {
  var list = arrOf(seg || pSeg('managebac'), 'courses');
  return list.map(function (c) {
    var r = isObj(c) ? c : { name: c };
    var units = r.units || r.unit_count || r.units_count || r.topics;
    var n = Array.isArray(units) ? units.length
          : (units == null || units === '') ? '' : cellText(units);
    var grade = r.grade || r.overall || r.overall_grade || r.average || r.score ||
                r.term_grade || r.current_grade;
    /* ManageBac 的课程 id 是**数字**（`/student/classes/<id>/…` 的那一段）。
       它必须一路带到「课程详情」那里 —— 之前这里把 id 丢了，点课程时只好拿
       课程名去当 id，服务端校验不过 → 用户看到的就是「加载失败：HTTP 400」。 */
    var cid = (r.id != null && String(r.id) !== '') ? String(r.id)
            : ((r.classId != null && String(r.classId) !== '') ? String(r.classId)
            : ((r.class_id != null && String(r.class_id) !== '') ? String(r.class_id) : ''));
    return {
      classId: /^\d+$/.test(cid) ? cid : '',
      name: cellText(r.name || r.title || r.subject || r.course || '（未命名课程）'),
      grade: (grade == null || grade === '') ? '—' : cellText(grade),
      units: (n === '') ? '—' : String(n),
      unitsNum: (n === '' || isNaN(Number(n))) ? null : Number(n)
    };
  });
}

function resolveCourses() {
  var mode = modeOf('managebac', mbCourses());
  var courses = [];
  if (mode === 'live') {
    courses = mbCourses();
    st.mbLabel = '服务器实时抓取';
    panelMsg('mb-info', accountLine('managebac', payload().meta), false);
  } else if (mode === 'sync') {
    courses = mbCourses(segFromSync('managebac'));
    st.mbLabel = '上次同步的快照';
    syncNotice('mb-info', 'ManageBac');
  } else {
    st.mbLabel = '服务器实时抓取';
    if (pendingSync()) panelMsg('mb-info', '', false);
    else panelMsg('mb-info', plErr('managebac') ? '' : accountLine('managebac', payload().meta), false);
  }
  return courses;
}

/** 作业：课程 / 标题 / 截止时间 / 状态 / 分数（实时优先，失败 / 空结果降级到同步快照）。 */
function mbTasks(seg) {
  var list = arrOf(seg || pSeg('managebac'), 'tasks');
  return list.map(function (t) {
    var r = isObj(t) ? t : { title: t };
    var due = r.due || r.deadline || r.due_at || r.dueAt || r.due_date || r.date || '';
    var course = r.course || r.subject || r.class || r.lesson || '';
    var status = r.status || r.state || r.progress || '';
    var score = r.score || r.grade || r.mark || r.points || r.result || '';
    /* 服务端给的 `due` 现在权威形态是 `YYYY-MM-DD HH:MM`；解析不出来时才是原文
       （ManageBac 的「Thursday at 12:00 PM」）。`due_inferred` 表示这个日期是**推算**的
       —— 页面必须标明，不能拿推算日期当事实骗用户。`due_text` 是页面上那句人话原文。 */
    return {
      course: course === '' ? '—' : cellText(course),
      title: cellText(r.title || r.name || r.task || '（无标题）'),
      due: due === '' ? '—' : prettyStamp(due),
      dueRaw: dueKey(due),
      dueInferred: r.due_inferred === true,
      dueText: cellText(r.due_text || ''),
      status: status === '' ? '—' : cellText(status),
      score: (score === '' || score == null) ? '—' : cellText(score)
    };
  });
}

function resolveTasks() {
  if (modeOf('managebac', mbCourses().concat(mbTasks())) === 'sync') {
    return mbTasks(segFromSync('managebac'));
  }
  return mbTasks();
}

/** 邮件头（实时优先，该平台出错 / 一条都没拿到时降级到同步快照）。 */
function resolveMail() {
  var seg = null, snap = false;
  var live = arrOf(pSeg('mail'), 'recent');
  var mode = modeOf('mail', live);

  if (mode === 'live') {
    seg = pSeg('mail');
    panelMsg('mail-error', plErr('mail'), true);
  } else if (mode === 'sync') {
    seg = segFromSync('mail');
    snap = true;
    syncNotice('mail-error', '邮件');
  } else {
    seg = {};
    if (pendingSync()) panelMsg('mail-error', '', false);
    else panelMsg('mail-error', plErr('mail'), true);
  }

  st.mailSnap = snap;
  st.mailSeg = isObj(seg) ? seg : {};
  st.mailConnected = accountLine('mail', payload().meta);
  st.mailMode = snap ? 'sync' : 'live';
  var newRows = arrOf(st.mailSeg, 'recent').map(function (h) {
    var r = isObj(h) ? h : { subject: h };
    return {
      uid: (r.uid == null || r.uid === '') ? '' : String(r.uid),
      from: cellText(r.from || r.sender || r.from_name || r.name || '（未知发件人）'),
      subject: cellText(r.subject || r.title || '（无标题）'),
      date: prettyStamp(r.date || r.time || r.received || r.received_at || ''),
      /* 逐封真实已读状态：**只有明确的 false 才算已读，只有明确的 true 才画点**。
         字段缺失（老快照 / 老服务端）→ null → 不画点：绝不假设「未读」。 */
      unread: (r.unread === true) ? true : (r.unread === false ? false : null)
      /* 正文与附件**不在这里**：服务端只回邮件头（列表要快、要稳），
         正文由 renderMailList 排进后台预取队列后填进 mailBodyCache，
         回形针也从那里读（见 renderMailList）。 */
    };
  });
  /* 保留乐观已读状态：只用 mailOptimisticReads（用户本会话内点开过的邮件），
     不用旧 st.mailRows（可能包含上一轮 applyLive 的残留状态）。 */
  newRows.forEach(function (row) {
    if (row && row.uid && mailOptimisticReads.has(String(row.uid)) && row.unread === true) {
      row.unread = false;
    }
  });
  return newRows;
}

var mailOptimisticReads = new Set();   // 本会话内乐观已读的 uid 集合

/* ---------------------------------------------------------------- 邮件正文缓存
 *
 * 为什么正文不在邮件列表里：服务端把最新 10 封的正文塞进 /app/data/ 之后，
 * 真实学校邮箱上整个邮箱段冲过 45 秒预算，**连列表都拿不到**（用户实测）。
 * 所以拆成两步：
 *   1) `/app/data/` 只回邮件头（几百封也就几条 IMAP 命令），列表永远快；
 *   2) 列表画出来之后，前端在这里**逐封后台预取**最新 10 封的正文
 *      （走 `GET /app/mail/<uid>/`，实测 0.4 秒/封，每次一条独立连接），
 *      按 uid 存进这个缓存。
 * 用户点开时先查这个缓存 → 命中就直接渲染，看到的就是「点开即显」。
 */
var mailBodyCache = {};          // uid -> {body_text, body_html, attachments}
var mailPreloadQueue = [];       // 还在排队等预取的 uid
var mailPreloadBusy = false;     // 同一时刻只发一个（不把邮箱服务器打爆）
var mailPreloadTimer = null;     // 起跑前的短暂延迟（让用户的手动点击先发出去）
var MAIL_PRELOAD_N = 10;         // 预取最新多少封（用户要求「最近十条」）
var MAIL_PRELOAD_DELAY_MS = 400; // 列表画完到开始预取之间等多久

/** 开始/继续后台预取队列：串行，一次一封。 */
function mailPreloadPump() {
  if (mailPreloadBusy) return;
  var uid = mailPreloadQueue.shift();
  if (!uid) return;
  mailPreloadBusy = true;
  fetchMailBody(uid).then(function (res) {
    if (res && res.status === 200 && isObj(res.body) && isObj(res.body.mail)) {
      var m = res.body.mail;
      mailBodyCache[String(uid)] = {
        body_text: m.body_text || '',
        body_html: m.body_html || '',
        attachments: Array.isArray(m.attachments) ? m.attachments : []
      };
      /* 正文到手 → 列表补回形针；如果用户已经点开这一封，把它就地渲染出来 */
      renderMailList($('mail-heads'));
      if (String(st.mailOpen) === String(uid)) openMail(uid);
    }
  })['catch'](function () { /* 预取失败不影响任何事：点开时会按需再试一次 */ })
    .then(function () {
      mailPreloadBusy = false;
      mailPreloadPump();
    });
}

/** 把最新 N 封排进预取队列（已经在缓存里的跳过）。
 *
 *  队列非空或正在预取时**不重排** —— renderMailList 每取回一封都会重画一次，
 *  如果每次都重排队列就会自己踩自己。刷新时由 refreshLive 清空队列再重排。
 *  起跑前等一小会儿：用户看完列表往往立刻点开某一封，那一下必须**先发出去**，
 *  不能被后台预取抢在前面（预取只是提前量，不该跟用户的点击争）。
 */
function scheduleMailPreload(rows) {
  if (mailPreloadBusy || mailPreloadQueue.length || mailPreloadTimer) return;
  var newest = (rows || []).slice(0, MAIL_PRELOAD_N)
    .map(function (r) { return r && r.uid ? String(r.uid) : ''; })
    .filter(function (uid) { return uid && !mailBodyCache[uid]; });
  if (!newest.length) return;
  mailPreloadTimer = setTimeout(function () {
    mailPreloadTimer = null;
    /* 这段时间里用户可能已经点开了某一封 → 重新算一遍，跳过已在缓存里的 */
    mailPreloadQueue = newest.filter(function (uid) { return !mailBodyCache[uid]; });
    mailPreloadPump();
  }, MAIL_PRELOAD_DELAY_MS);
}

function resolveUnread() {
  /* 用源数据 unread 字段，减去乐观已读（用户点开过的邮件）。
     源字段是服务端的全局未读数（可能包含 recent 列表之外的邮件），
     乐观已读只减去用户在本会话内标记过的邮件。 */
  var n = numOf(st.mailSeg, 'unread');
  if (n == null && st.mailSnap) n = numOf(segFromSync('mail'), 'unread');
  if (n != null && mailOptimisticReads.size > 0) {
    n = Math.max(0, n - mailOptimisticReads.size);
  }
  return n;
}

/* =============================== ① 首页 =============================== */

function lessonLine(l) {
  return (l.start ? String(l.start) + (l.end ? '–' + String(l.end) : '') : '全天') +
         ' · ' + (l.subject || '（未命名科目）');
}

function lessonItem(l) {
  var row = el('div', 'item');
  row.appendChild(el('span', 'dim', l.start ? String(l.start) : '全天'));
  var grow = el('div', 'grow');
  grow.appendChild(el('div', null, l.subject || '（未命名科目）'));
  var extra = [];
  if (l.room) extra.push(String(l.room));
  if (l.teacher) extra.push(String(l.teacher));
  if (l.group) extra.push(String(l.group));
  if (extra.length) grow.appendChild(el('div', 'dim', extra.join(' · ')));
  row.appendChild(grow);
  if (l.end) row.appendChild(el('span', 'dim', String(l.end)));
  return row;
}

function renderHomeLessons(panel) {
  var lessons = st.ttLessons || [];
  var today = todayStr();
  var list = lessons.filter(function (l) { return l.day === today; })
    .sort(function (a, b) { return String(a.start).localeCompare(String(b.start)); });
  if (list.length) {
    list.forEach(function (l) { panel.appendChild(lessonItem(l)); });
    return;
  }
  if (pendingSync()) { panel.appendChild(el('p', 'muted', '正在读取同步对象…')); return; }
  panel.appendChild(el('p', 'empty', lessons.length
    ? '今天没有课。'
    : emptyText('课表', 'edupage', 'EduPage')));
}

function renderHomeEvents(panel) {
  var events = scheduleEvents(st.schedule && st.schedule.doc);
  var today = todayStr();
  var list = events.filter(function (e) { return String((e && e.day) || '') === today; })
    .sort(function (a, b) { return String(a.time || '~').localeCompare(String(b.time || '~')); });
  if (list.length) {
    list.forEach(function (e) {
      var row = el('div', 'item');
      row.appendChild(el('span', 'dim', e.time ? String(e.time) : '全天'));
      var grow = el('div', 'grow');
      grow.appendChild(el('div', null, e.title ? String(e.title) : '（无标题）'));
      if (e.note) grow.appendChild(el('div', 'dim', String(e.note)));
      row.appendChild(grow);
      panel.appendChild(row);
    });
    return;
  }
  panel.appendChild(el('p', 'empty', events.length ? '今天没有日程。' : '还没有日程，去「我的日程」记一笔。'));
}

/** 首页 DDL 卡：**±14 天**窗口（今天 −14 天 ~ 今天 +14 天），最近 2 天内的加粗。
 *
 *  口径与桌面客户端（Pinghe Launcher Lite）一致：窗口以**浏览器本地日期**为准，
 *  排序按截止时间升序；**时间解析不出来的条目不丢**，排在最后并标「时间未知」。
 *  客户端支持左滑移除 DDL（可恢复）；网页端是只读的，不做任何写操作，
 *  所以这里保留客户端的空态/说明文案结构，只去掉「左滑可删除」那句。
 */
function renderHomeDdl(panel) {
  if (!panel) return;
  /* 自己清空：正常路径由 renderPanelNow 先 clear，但**直接调用**（测试/一次性重渲染）时
     没有那一步 —— 不清就会在旧行下面再追加一份（本轮 CDP 用例就踩到了）。 */
  clear(panel);
  var all = st.coTasks || [];
  var known = [], unknown = [];
  all.forEach(function (t) {
    if (dueDay(t.dueRaw)) known.push(t); else unknown.push(t);
  });
  var tasks = known.filter(function (t) { return inDdlWindow(t.dueRaw); })
    .sort(function (a, b) { return String(a.dueRaw).localeCompare(String(b.dueRaw)); });
  if (!tasks.length && !all.length && pendingSync()) {
    panel.appendChild(el('p', 'muted', '正在读取同步对象…'));
    return;
  }
  /* 窗口口径 + 只读说明：只要有作业数据就常驻一行（空态时下面的 empty 已经说了口径） */
  if (all.length) {
    panel.appendChild(el('p', 'note',
      '窗口：今天 ±14 天（最近 2 天内到期的加粗）。只读：网页端不支持移除 DDL。'));
  }
  if (tasks.length) {
    tasks.slice(0, 20).forEach(function (t) {
      var urgent = isUrgentDue(t.dueRaw);
      var row = el('div', 'item ddl-item' + (urgent ? ' urgent' : ''));
      row.appendChild(el('span', 'dim', t.due));
      var grow = el('div', 'grow');
      grow.appendChild(el('div', null, t.title));
      grow.appendChild(el('div', 'dim', t.course + (t.status !== '—' ? ' · ' + t.status : '')
                                       + (t.dueInferred ? ' · 时间按本周推算' : '')));
      row.appendChild(grow);
      panel.appendChild(row);
    });
    if (tasks.length > 20) panel.appendChild(el('p', 'note', '共 ' + tasks.length + ' 条，只显示最近 20 条。'));
    /* 解析不出日期的不丢：放在最后并标「时间未知」 */
    if (unknown.length) {
      var sep = el('div', 'list-sep');
      sep.appendChild(el('span', null, '时间未知 · ' + unknown.length + ' 项'));
      panel.appendChild(sep);
      unknown.slice(0, 20).forEach(function (t) {
        var row = el('div', 'item ddl-item');
        row.appendChild(el('span', 'dim', '时间未知'));
        var grow = el('div', 'grow');
        grow.appendChild(el('div', null, t.title));
        grow.appendChild(el('div', 'dim', t.course + (t.status !== '—' ? ' · ' + t.status : '')));
        row.appendChild(grow);
        panel.appendChild(row);
      });
    }
    panel.appendChild(el('p', 'note',
      '只读：网页端不支持移除 DDL（客户端的左滑移除/恢复请用桌面客户端）。'));
    return;
  }
  panel.appendChild(el('p', 'empty', all.length
    ? '±14 天内没有 DDL（最近 2 天与 2 天内的会加粗）'
    : emptyText('作业列表', 'managebac', 'ManageBac')));
}

/* ------------------------- 首页「未读邮件」那一格：数字 + 数据时间 -------------------------
 *  用户的原始反馈是「未读邮件数量在主页并没有正确显示」。接口链路是对的
 *  （`mail.unread` → `st.mailUnread` → `#home-unread` 三者一致），真正的问题是**时机/时效**：
 *  邮件在服务端有 5 分钟缓存，首页那个数字最长可能比现实晚 5 分钟才变（在别处读过邮件也一样）。
 *  所以这里做三件事：
 *  ① 数字下面**如实写明这份数据是什么时候抓的**（`meta.fetched_at`，没有就写 —）
 *     —— 用户看到「抓取于 14:55」就知道这个数字的时点，而不是猜它坏了；
 *  ② 刷新失败时**保住上一次的已知值**（并注明时点），绝不显示 0 或 – 骗人；
 *  ③ 服务端/同步对象都没给 unread（null）时显示 –（未知就是未知，不猜 0）。
 * ------------------------------------------------------------------------------------ */

/** 首页未读数字的「数据时点」文案：这一轮数据的抓取时间，或上一次已知值的时间。 */
function homeUnreadSource() {
  var mode = modeOf('mail', arrOf(pSeg('mail'), 'recent'));
  if (mode === 'sync') {
    var ss = syncStamp();
    return ss ? ('同步快照 ' + ss) : '同步快照';
  }
  var m = payload().meta;
  return (isObj(m) && typeof m.fetched_at === 'string' && m.fetched_at)
    ? prettyStamp(m.fetched_at) : '';
}

/** 画未读数 + 时点。`st.mailUnread` 是**这一轮**抓到的值（可能 null = 未知/这一轮没拿到）。 */
function renderHomeUnread(stamp) {
  var box = $('home-unread');
  var note = $('home-unread-stamp');
  var n = st.mailUnread;

  if (n == null) {
    /* 这一轮没拿到数（接口没给 / 刷新失败 → 服务端也没给）。**有上一次的已知值就留着它**，
       并把它的抓取时间写清楚；一次都没拿到过才显示 –。 */
    if (st.mailUnreadLast != null) {
      n = st.mailUnreadLast;
      var at = st.mailUnreadLastAt ? prettyStamp(st.mailUnreadLastAt) : '';
      stamp = '上次已知值' + (at ? '（抓取于 ' + at + '）' : '') + ' · 这次没抓到，点「刷新」重试';
    } else {
      n = null;
    }
  } else {
    st.mailUnreadLast = n;
    st.mailUnreadLastAt = (isObj(payload().meta) && typeof payload().meta.fetched_at === 'string')
      ? payload().meta.fetched_at
      : (st.mailUnreadLastAt || '');
  }

  if (box) box.textContent = (n == null) ? '–' : String(n);
  if (note) note.textContent = '最新数据 ' + (n == null ? '—' : (stamp || '—'));
}

function renderHomeNow() {
  var t = st.ttLessons || [];
  var today = todayStr();
  var now = nowMinutes();
  var list = t.filter(function (l) { return l.day === today; });

  var cur = null, nxt = null;
  list.forEach(function (l) {
    var s = minutesOf(l.start), e = minutesOf(l.end);
    if (s != null && ((e != null && now >= s && now < e) || (e == null && now >= s && now < s + 45))) {
      if (!cur) cur = l;
    }
    if (s != null && s > now && !nxt) nxt = l;
  });

  var stamp = homeUnreadSource();
  renderHomeUnread(stamp);

  var curBox = $('home-current');
  if (curBox) {
    clear(curBox);
    if (cur) {
      curBox.appendChild(document.createTextNode(cur.subject || '（未命名科目）'));
      curBox.appendChild(el('small', null, lessonLine(cur) + (cur.room ? ' · ' + cur.room : '')));
    } else {
      curBox.textContent = list.length ? '现在没有课' : (t.length ? '今天没有课' : '—');
    }
  }

  var nxtBox = $('home-next');
  if (nxtBox) {
    clear(nxtBox);
    if (nxt) {
      nxtBox.appendChild(document.createTextNode(nxt.subject || '（未命名科目）'));
      nxtBox.appendChild(el('small', null, lessonLine(nxt) + (nxt.room ? ' · ' + nxt.room : '')));
    } else if (list.length) {
      nxtBox.textContent = '今天的课都上完了';
    } else {
      nxtBox.textContent = t.length ? '今天没有课' : '—';
    }
  }

  var info = $('home-info');
  if (info) {
    var label = (st.modeSyncLabel || (st.mode === 'sync' ? '上次同步的快照' : ''));
    if (label) panelMsg('home-info', '以下为上次同步的数据' +
      (syncStamp() ? '（时间：' + syncStamp() + '）' : '（同步对象里没有时间戳）') + ' —— 数据来源：' + label + '。', false);
    else if (st.mode === 'live') panelMsg('home-info',
      '数据来源：服务器实时抓取' + (accountLine('edupage', payload().meta) ? ' · ' + accountLine('edupage', payload().meta) : ''), false);
    else panelMsg('home-info', '', false);
  }
}

function updateHomeClock() {
  var d = new Date();
  var clock = $('home-clock');
  if (clock) clock.textContent = pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  var date = $('home-date');
  if (date) date.textContent = WEEK[d.getDay()] + ' · ' + todayStr();
}

var clockTimer = null;
function startClock() {
  updateHomeClock();
  if (!clockTimer) clockTimer = setInterval(updateHomeClock, 1000);
}

/* =========================== ② 我的课表 =========================== */
/* 版式 1:1 复制 客户端（Pinghe Launcher Lite）的「我的课表」：按节次(P1-P10/Lunch/晚自习)对齐、列=周一到周日，
   调色板/类名/CSS 规则都照搬（.tt-grid/.tt-head/.tt-corner/.tt-time/.tt-cell/.tt-restbar/
   .tt-lesson），点课程卡开详情。数据来源不同（我们读接口，PLL 读本地缓存），呈现一致。 */

/* 学校作息（与客户端完全一致） */
var PERIODS = [
  { name: 'P1', start: '08:00', end: '08:40' },
  { name: 'P2', start: '08:45', end: '09:25' },
  { name: 'P3', start: '09:35', end: '10:15' },
  { name: 'P4', start: '10:20', end: '11:00' },
  { name: 'P5', start: '11:05', end: '11:55' },
  { name: 'Lunch', start: '12:00', end: '12:40', rest: true },
  { name: 'P6', start: '12:45', end: '13:25' },
  { name: 'P7', start: '13:30', end: '14:10' },
  { name: 'P8', start: '14:15', end: '14:55' },
  { name: 'P9', start: '15:00', end: '15:40' },
  { name: 'P10', start: '15:45', end: '16:25' },
  { name: '晚自习', start: '18:00', end: '20:30', rest: true }
];

/** 课的开始时间落在哪个节次；不在任何时段返回 -1（归入「课外」）。 */
function periodOf(start) {
  var s = String(start || '');
  if (!s) return -1;
  for (var i = 0; i < PERIODS.length; i++) {
    if (s >= PERIODS[i].start && s < PERIODS[i].end) return i;
  }
  return -1;
}

/* 每个教学组一个自己的颜色：按 (科目族, 组号) 稳定散到调色板上（客户端同款）。 */
var TT_PALETTE = [
  ['#e3f2e3', '#1d6b3c'], ['#e3edf7', '#1d4f7c'], ['#fdeee3', '#a04d12'],
  ['#f3e8f7', '#6b2d8c'], ['#fde8ef', '#a01d55'], ['#e0f2f1', '#00695c'],
  ['#fff7dc', '#8a6d00'], ['#e8eaf6', '#303f9f'], ['#e0f7fa', '#006978'],
  ['#f9ebeb', '#8c1d1d'], ['#eef6e3', '#4a7c1d'], ['#efe3f2', '#6b1d7c'],
  ['#e3f6f0', '#0b6b5d'], ['#fbe9e0', '#8c3d1d'], ['#e9e9f2', '#3d3d8c'],
  ['#f2f0e3', '#6b641d']
];

function ttColor(l) {
  var k = subjFamily(l.subject) + '|' + String(l.group || '');
  var h = 0;
  for (var i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0;
  var pair = TT_PALETTE[h % TT_PALETTE.length];
  return '--tbg:' + pair[0] + ';--tfg:' + pair[1];
}

/* ---------------- 连堂：**照抄 PLL**（D:\phl-lite-dev\ui\app.js） ----------------
 *
 * PLL 原文（ui/app.js:270-273）：
 *     /* 连堂判定: 同科目族+同老师+同组+同教室才算"连续两节一样的课" *\/
 *     function ttSameKey(l) {
 *       return `${subjFamily(l.subject)}|${l.teacher}|${l.group || ""}|${l.room}|${l.cancelled ? 1 : 0}`;
 *     }
 *
 * PLL 原文（ui/app.js:297-308，「连堂预扫描」）：
 *     /* 连堂预扫描: 该天某时段只有一节、下一时段也只有同一节课 → 合并 *\/
 *     const spanStart = {}, consumed = new Set();
 *     for (let pi = 0; pi < PERIODS.length; pi++) {
 *       if (cells[pi].length !== 1 || consumed.has(pi)) continue;
 *       let n = 1;
 *       while (pi + n < PERIODS.length && cells[pi + n].length === 1 &&
 *              ttSameKey(cells[pi][0]) === ttSameKey(cells[pi + n][0])) n++;
 *       if (n > 1) { spanStart[pi] = n; for (let k = 1; k < n; k++) consumed.add(pi + k); }
 *     }
 *
 * PLL 原文（ui/app.js:338-339，合并块的落点写法）：
 *     spans.push(ttLessonHtml(ls[0], ti, false,
 *       `grid-column:${di + 2};grid-row:${r} / span ${b.spanStart[pi]}`));
 *
 * 三处要点，一条都不能少：
 *   ① 判据是**科目族**（`subjFamily`，去掉 HL/SL 层级后缀）而不是原始 subject ——
 *      同一门课的两节可能写作 `Physics HL1` / `Physics HL2`，按原始字符串比就判不出连堂
 *      （客户端注释：「课名会换(History HL/SL2 ↔ History HL2)，所以匹配/勾选一律用科目族」）。
 *   ② **不做时间间隔判断**：只看「该时段只有一节、下一时段也只有同一节课」。
 *   ③ 合并块是 `grid-row:R / span N` 的**直接子元素**（PLL 的 `spans` 最后入 DOM，
 *      盖在被跨过的空格上），第二行照常画格子但不放课卡。
 *
 * ⚠ 网页端**额外**吃一种形态（PLL 没有、真实 EduPage currenttt 里有）：
 *   服务端把连堂直接给成**一张跨两节的卡**（`durationperiods=2`，12:45–14:10 盖住第 7、8 节）。
 *   对这种卡，它自己就该跨行 —— 由 `ttSpanOf()` 按 end 越过几个节次算出来（见下）。
 *   两条路互不干扰：卡自己跨节优先，其次才是 PLL 的「相邻两格同一节课」合并。 */

/** 连堂判定：**同科目族** + 同老师 + 同组 + 同教室（照 PLL ui/app.js:271-273）。 */
function ttSameKey(l) {
  return [subjFamily(l.subject), l.teacher, l.group, l.room, l.cancelled ? 1 : 0].join('|');
}

/** "HH:MM" → 当天的分钟数；解析不了返回 null。 */
function ttMin(t) {
  var m = /^(\d{1,2}):(\d{2})$/.exec(String(t == null ? '' : t).trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** 网页端额外那条：**服务端给的跨节卡** —— 按课卡自己的 end 越过几个节次算它占几节。
 *  真实数据实测（三周 815 张课卡）：每周 79~81 张卡 `durationperiods=2`，
 *  形如 `Physics HL1 组A Jing Jiang A208 12:45–14:10`（= 第 7 节 + 第 8 节）。 */
function ttSpanOf(l, pi) {
  var end = ttMin(l && l.end);
  var start = ttMin(l && l.start);
  if (end === null || PERIODS[pi] == null) return 1;
  if (PERIODS[pi].rest) return 1;                  // 休息时段本身不跨
  if (start !== null && end <= start) return 1;    // end 不合理 → 只占一节
  var n = 1;
  var limit = Math.min(PERIODS.length, pi + 4);    // 最多 4 节，防脏数据把整列吞掉
  while (pi + n < limit) {
    var nxt = PERIODS[pi + n];
    if (nxt.rest) break;                           // 不吞休息时段
    var nextStart = ttMin(nxt.start);
    var thisEnd = ttMin(PERIODS[pi].end);
    if (nextStart === null || thisEnd === null) break;
    if (!(end > thisEnd && end >= nextStart)) break;  // 没越过这一节 → 到此为止
    n++;
  }
  return n;
}

/** "HH:MM" → 当天的分钟数；解析不了返回 null。 */
function ttMin(t) {
  var m = /^(\d{1,2}):(\d{2})$/.exec(String(t == null ? '' : t).trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** 网页端额外那条：**服务端给的跨节卡** —— 按课卡自己的 end 越过几个节次算它占几节。
 *  真实数据实测（三周 815 张课卡）：每周 79~81 张卡 `durationperiods=2`，
 *  形如 `Physics HL1 组A Jing Jiang A208 12:45–14:10`（= 第 7 节 + 第 8 节）。
 *  休息时段（Lunch / 晚自习）不能被吞进课卡。 */
function ttSpanOf(l, pi) {
  var end = ttMin(l && l.end);
  var start = ttMin(l && l.start);
  if (end === null || PERIODS[pi] == null) return 1;
  if (PERIODS[pi].rest) return 1;                  // 休息时段本身不跨
  if (start !== null && end <= start) return 1;    // end 不合理 → 只占一节
  var n = 1;
  var limit = Math.min(PERIODS.length, pi + 4);    // 最多 4 节，防脏数据把整列吞掉
  while (pi + n < limit) {
    var nxt = PERIODS[pi + n];
    if (nxt.rest) break;                           // 不吞休息时段
    var nextStart = ttMin(nxt.start);
    var thisEnd = ttMin(PERIODS[pi].end);
    if (nextStart === null || thisEnd === null) break;
    if (!(end > thisEnd && end >= nextStart)) break;  // 没越过这一节 → 到此为止
    n++;
  }
  return n;
}

/** 一天里的连堂预扫描（**PLL 的写法** + 网页端多吃的「服务端跨节卡」）。
 *
 *  返回 `{spanStart, consumed, merged, cover}`：
 *   - `spanStart[节次下标]` = `{n: 占几节, from: 代表这一块的课卡}`（n≥2 才跨行）；
 *   - `consumed[节次下标]`  = 被上面那张卡盖住的节次（**仍然照常画格子**，只是格子里不放课卡
 *     —— 直接删格子会让整行塌掉）；
 *   - `merged[节次下标]`    = 这一块是 PLL 那种「相邻两格同一节课」合并来的；
 *   - `cover[节次下标]`     = 合并块的**落点**（画跨行块的地方；PLL 把块画在合并的**第一格**
 *     位置，`grid-row:R / span N`，所以 cover 取 spanStart 那一格）。 */
function ttScanSpans(cells) {
  var spanStart = {}, consumed = {}, merged = {}, cover = {};
  /* ① 服务端给的跨节卡（真实数据里连堂就是这个形态）：一张卡自己就该跨行 */
  for (var pi = 0; pi < PERIODS.length; pi++) {
    if (consumed[pi]) continue;
    if (cells[pi].length !== 1) continue;          // 一格多张卡：各自占一格，不跨行
    var n = ttSpanOf(cells[pi][0], pi);
    if (n > 1) {
      spanStart[pi] = { n: n, from: cells[pi][0] };
      for (var k = 1; k < n && pi + k < PERIODS.length; k++) consumed[pi + k] = true;
    }
  }
  /* ② **PLL 的「连堂预扫描」原样**（ui/app.js:297-308）：
   *    「该天某时段只有一节、下一时段也只有同一节课 → 合并」（判据 = ttSameKey，同科目族
   *    +同老师+同组+同教室；**不做时间间隔判断**），合并块画在第一格、跨 n 行。 */
  for (var q = 0; q < PERIODS.length; q++) {
    if (cells[q].length !== 1 || consumed[q] || spanStart[q]) continue;
    var m = 1;
    while (q + m < PERIODS.length && cells[q + m].length === 1 && !consumed[q + m] &&
           ttSameKey(cells[q][0]) === ttSameKey(cells[q + m][0])) m++;
    if (m > 1) {
      spanStart[q] = { n: m, from: cells[q][0] };
      cover[q] = true;
      merged[q] = true;
      for (var k2 = 1; k2 < m; k2++) {
        consumed[q + k2] = true;
        merged[q + k2] = true;
      }
    }
  }
  return { spanStart: spanStart, consumed: consumed, merged: merged, cover: cover };
}

function isoToday() {
  var d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

/** 课表工具条上的**绿色「已连接」**：只写四个字（可带一个绿点），
 *  不带账号名、不带教学组清单、不带「隐藏了 N 条」这类技术细节。
 *
 *  数据到了且这一轮**没有平台错误**才亮绿：连不上 / 出错时**不显示绿色**
 *  —— 那种情况仍按既有错误条（#tt-error / 同步快照提示）展示，用户不会被绿色骗。
 *  选课相关的说明只在**选课面板**里说（见 pickerIntro）。 */
function ttConnected() {
  return !!(st.live || st.syncReady) && !plErr('edupage') && (st.ttLessons || []).length > 0;
}

function ttRenderConn() {
  var box = $('tt-conn');
  if (!box) return;
  var on = ttConnected();
  box.hidden = !on;
  box.title = on ? '已连上学校平台（EduPage）' : '';
}

/** 用户**一份选课数据都没有**吗？
 *
 *  这是「没选课」的判定，而不是「过滤后一条不剩」：两个来源（同步对象 `settings.lessons`、
 *  客户端写进 `school.edupage.selected` 的勾选）**都拿不到任何组名**才算没选课
 *  —— 包含 `{"groups":[]}` 这种「存了空选课」的文档。只要用户勾过组，
 *  哪怕这一周恰好一节课都没匹配上，也**不是**没选课（那时课表显示真正的空周，另有一套文案）。
 */
function noLessonPick() {
  return !!(st.ttGroupInfo && st.ttGroupInfo.empty);
}

/** 周一为一周第一天（与 客户端（Pinghe Launcher Lite）的周课表一致）。 */
function mondayOf(dayStr) {
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dayStr || ''));
  var d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date();
  var wd = (d.getDay() + 6) % 7;            // 周一=0
  d.setDate(d.getDate() - wd);
  return d;
}

function shiftDay(d, n) {
  var x = new Date(d.getTime());
  x.setDate(x.getDate() + n);
  return x;
}

function isoOfDate(d) {
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

var ttWeekStart = null;    // 当前显示的周一（Date）；null = 本周
var ttAllDays = [];        // 抓到的课表覆盖的日期（用来判断「本周没课但别的周有」）
var ttAutoJumped = false;  // 自动跳过周（**只有程序自己会设它**；用户自己翻周另记）
var ttUserNavigated = false; // 用户点过上一周/下一周/本周 → 之后不再自动改他看的周

/** 从条目里记下本次数据覆盖了哪些日期。 */
function ttRecordsWeekHint() {
  var days = {};
  (st.ttLessons || []).forEach(function (l) {
    var d = String(l.day || '');
    if (/^\d{4}-\d{2}-\d{2}$/.test(d)) days[d] = true;
  });
  ttAllDays = Object.keys(days).sort();
}

/** 本周七天里一节课都没有，但数据里有别的日子有课 → 先把那周显示出来。
 *  （数据源往往一次给两周：周日晚打开时本周是空的，直接显示空网格会让人以为没数据。）
 *
 *  2026-09-21 修（用户报「课表没东西了」）：原来只看**第一次**渲染就定死（`ttAutoJumped`）。
 *  冷启动时先渲染的是**上次同步的快照**（可能是上一周的），于是"本周没课"成立 →
 *  自动跳到上一周；等实时数据到了（本周其实有 35 节课），标记已经置位 →
 *  **永远跳不回来**，用户对着上一周的空网格，看起来就是"课表没了"。
 *  现在：只有**用户自己翻过周**才不再自动跳；否则每次数据刷新都重新判断，
 *  而且**优先回到本周**（本周有课就回去）。 */
function ttAutoJumpWeek() {
  if (ttUserNavigated) return false;
  var week = ttBuildWeek();
  var busy = week.some(function (d) { return d.lessons.length > 0; });
  if (busy) return false;

  var today = isoToday();
  var thisMonday = mondayOf(today);
  var thisSunday = isoOfDate(shiftDay(thisMonday, 6));
  var thisMondayIso = isoOfDate(thisMonday);
  var thisWeekHasLessons = (st.ttLessons || []).some(function (l) {
    var d = String(l.day || '');
    return d >= thisMondayIso && d <= thisSunday;
  });
  // ① 本周有课却看着别的周 → 回到本周（这是"数据到了要跳回来"那一步）
  if (thisWeekHasLessons) {
    if (isoOfDate(ttWeekStart || thisMonday) !== thisMondayIso) {
      ttWeekStart = null;
      ttAutoJumped = false;
      toast('课表已回到本周', 2600);
      return true;
    }
    return false;
  }
  // ② 本周确实没课 → 跳到最近的有课的那一周（原来那套）
  if (!ttAllDays.length) return false;
  var future = ttAllDays.filter(function (d) { return d >= today; });
  var target = (future.length ? future[0] : ttAllDays[ttAllDays.length - 1]);
  var monday = mondayOf(target);
  var targetIso = isoOfDate(monday);
  if (targetIso === thisMondayIso) return false;
  if (ttAutoJumped && ttWeekStart && isoOfDate(ttWeekStart) === targetIso) return false;
  ttWeekStart = monday;
  ttAutoJumped = true;
  toast('本周没有课，已跳到有课的 ' + week[0].day + ' 那一周', 4200);
  return true;
}

/** 选定这一周的 7 天（真实日期），并与抓到的课表条目对上。 */
function ttBuildWeek() {
  var start = ttWeekStart || mondayOf(isoToday());
  var monday = mondayOf(isoOfDate(start));
  var labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  var byDay = {};
  (st.ttLessons || []).forEach(function (l) {
    var d = String(l.day || '');
    (byDay[d] = byDay[d] || []).push(l);
  });
  var week = [];
  for (var i = 0; i < 7; i++) {
    var day = isoOfDate(shiftDay(monday, i));
    week.push({ day: day, label: labels[i], lessons: byDay[day] || [] });
  }
  return week;
}

/** 课卡（客户端（Pinghe Launcher Lite）的 .tt-lesson 结构：可选的时段行 + 科目 + 教室·老师）。 */
function ttLessonEl(l, ti, withTime, place) {
  var box = el('div', 'tt-lesson' + (l.cancelled ? ' cancelled' : ''));
  box.setAttribute('data-ti', String(ti));
  box.setAttribute('style', ttColor(l) + (place ? ';' + place : ''));
  box.tabIndex = 0;
  box.title = '点开看这节课的详情';
  if (withTime) {
    box.appendChild(el('span', 'rm', String(l.start || '') + (l.end ? '–' + String(l.end) : '')));
  }
  box.appendChild(el('b', null, l.subject ? String(l.subject) : '（未命名科目）'));
  box.appendChild(el('span', 'rm', String(l.room || '—') + (l.teacher ? ' · ' + String(l.teacher) : '')));
  return box;
}

/** 兼容层（给既有测试与脚本用）：整周课表附一个屏外单元，里面按天给出
 *  「.day-group__title + .evt（科目 / 教室 / 老师 / 教学组）」，与 客户端版式互不干扰：
 *  它 position:absolute 且 0 尺寸，不占网格、不影响视觉，但仍在无障碍树与 innerText 里。 */
function ttEvtEl(l) {
  var evt = el('div', 'evt');
  evt.appendChild(el('span', 'evt__time', String(l.start || '全天')));
  var main = el('div', 'evt__main');
  main.appendChild(el('div', 'evt__title', l.subject ? String(l.subject) : '（未命名科目）'));
  var meta = el('div', 'tt-meta');
  ['教室 ' + (l.room || '—'), '老师 ' + (l.teacher || '—'), '教学组 ' + (l.group || '—')]
    .forEach(function (t) { meta.appendChild(el('span', 'tt-tag', t)); });
  main.appendChild(meta);
  evt.appendChild(main);
  return evt;
}

function ttCompatCell(byDay) {
  var cell = el('div', 'tt-compat');
  cell.setAttribute('aria-hidden', 'false');
  (byDay || []).forEach(function (b) {
    var all = [];
    (b.cells || []).forEach(function (ls) { ls.forEach(function (l) { all.push(l); }); });
    (b.other || []).forEach(function (l) { all.push(l); });
    if (!all.length) return;
    cell.appendChild(el('div', 'day-group__title', (prettyDay(b.day) || b.day) + ' · ' + all.length + ' 节'));
    cell.appendChild(el('div', 'tt-head', b.label));
    all.forEach(function (l) { cell.appendChild(ttEvtEl(l)); });
  });
  return cell;
}

var ttFlat = [];        // 课卡扁平表：点击时按 data-ti 取回完整条目
var ttWeekData = null;  // 当前渲染的周数据

/** 选课空态卡里的「选择我的教学组」按钮 + 面板宿主。
 *
 *  面板本体（按科目分组的选课界面）现在直接内嵌在空态卡里，
 *  这些函数保留为兼容接口。 */
function ttPickBtn() {
  var card = $('tt-nopick');
  return card ? card.querySelector('.nopick__pick') : null;
}

function ttPickBody() {
  var card = $('tt-nopick');
  if (card) return card.querySelector('.nopick__picker');
  return null;
}

function ttNoPick() {
  return !!$('tt-nopick');
}

/** 没选课时的空态卡（替代「全年级 N 条」的假课表）。
 *  文案与选课界面都在这张卡里：用户一眼知道「该去勾选修课」，而不是以为课表坏了。
 *  选课界面按科目分组（照 Pinghe Launcher Lite 样式），直接内嵌在卡里。
 *
 *  **国家必修不在这张卡的勾选范围里**：它们（无组的国家课程 + 国家理科物理/化学/生物）
 *  本来就有课、不靠勾选，所以这一态下卡**下面**照常显示国家必修那一部分课表
 *  （见 renderTimetable 的 noLessonPick 分支）—— 文案也据此如实写。 */
function noPickCard(week) {
  var card = el('div', 'card nopick');
  card.id = 'tt-nopick';

  card.appendChild(el('div', 'card-title', '还没选择你的教学组'));

  var body = el('div', 'nopick__body');
  body.appendChild(el('p', 'nopick__lede',
    '还没选择你的选修课：在下面勾选你上的选修课教学组，之后这里只显示你自己的课表。'));
  var extra = [];
  if (st.ttGroupInfo && st.ttGroupInfo.raw) {
    extra.push('学校这一轮返回了 ' + st.ttGroupInfo.raw + ' 条全年级候选课，'
             + '其中**选修课**在你勾选之前一条都不会被当成你的课表显示。');
  }
  extra.push('国家理科（`国家物理` / `国家化学` / `国家生物` 三张轮换卡合成的一门）默认必选，'
           + '已经在下面的课表里；全班必修也一样。你只需要勾**选修课**。');
  extra.push('勾选保存在同步对象 settings.lessons：也可以在客户端「设置 → 选课」里勾，两边同步。');
  extra.push('只改你自己的选课设置，不会改动学校平台上的任何数据。');
  var ul = el('ul', 'nopick__notes');
  extra.forEach(function (t) { ul.appendChild(el('li', null, t)); });
  body.appendChild(ul);
  card.appendChild(body);

  /* ---- 选择按钮（测试 4.9 期望存在）----
     与工具条那颗 `#tt-pick-btn` 走**同一个**模态（`openPickerModal`），
     不再自己开一个「卡片内嵌面板」——那样会有两个选课界面、两套勾选状态。 */
  var pickBtn = el('button', 'primary nopick__pick', '选择我的教学组');
  pickBtn.type = 'button';
  pickBtn.id = 'tt-pick-open';
  pickBtn.setAttribute('aria-expanded', 'false');
  card.appendChild(pickBtn);

  /* ---- 空态卡里的选课界面（按科目分组，照 Pinghe Launcher Lite 样式）----
     `toggle-pick` 是**同页两个选课宿主**（`#nopick-list` / `#picker-list`）的
     互斥开关：模态打开时它被隐藏，卡片里就不再出现第二份列表（见 syncPickerHosts）。 */
  var pickerHost = el('div', 'nopick__picker toggle-pick');
  pickerHost.id = 'nopick-picker';
  // 搜索框
  var filterBox = el('input', 'pick-filter');
  filterBox.type = 'search';
  filterBox.placeholder = '搜索科目名…';
  filterBox.id = 'nopick-filter';
  pickerHost.appendChild(filterBox);
  // 选课列表
  var listWrap = el('div', 'pick-list');
  listWrap.id = 'nopick-list';
  pickerHost.appendChild(listWrap);
  // 按钮行
  var tools = el('div', 'row-tools nopick__tools');
  var save = el('button', 'primary nopick__save', '保存选课');
  save.type = 'button';
  save.id = 'nopick-save';
  tools.appendChild(save);
  var cancel = el('button', 'ghost nopick__cancel', '取消');
  cancel.type = 'button';
  cancel.id = 'nopick-cancel';
  tools.appendChild(cancel);
  tools.appendChild(el('span', 'muted small',
    '保存后这里立刻变成你自己的课表；客户端同步后也会用这份选课'));
  pickerHost.appendChild(tools);
  card.appendChild(pickerHost);

  /* 兼容层：屏外给出这一周的日期标题（与正常课表同一套 .day-group__title 结构），
     让「本周 / 上一周 / 下一周」这些既有断言依然拿得到范围文本。 */
  card.appendChild(ttCompatCell(week || []));
  return card;
}

/* --------- 选课界面：**一个渲染器，两个宿主**（空态卡 + 模态）---------
 *
 *  2026-09-16 修（用户反馈「点选择我的教学组，下面什么都没有了」）：
 *  以前这里是**三份互相不知道对方存在的渲染代码** ——
 *    ① 空态卡内嵌面板：渲染进 `#nopick-list`（只有课表里那张卡存在时才有）
 *    ② 工具条按钮的模态：渲染进 `#picker-list`（模态**自己**的列表）
 *    ③ 设置页的旧面板：渲染进 `#lesson-list`
 *  可①②**跑的是同一个函数**（旧 `renderNopickPicker` 里写死了 `$('nopick-list')`），
 *  于是点工具条按钮 → 模态确实打开了（`#picker-overlay` display:flex）→
 *  渲染却全部落进了课表里那张卡的 `#nopick-list`，模态自己的 `#picker-list` **从来没被填过**
 *  → 面板一片空白（实测 `#picker-list` children=0、getBoundingClientRect().height=0）。
 *
 *  现在合成**一条**渲染路径 `renderSubjectPickerInto(host, filterValue)`：谁要列表就把宿主传进来，
 *  数据（`liveLessons` / `buildSubjectGroups`）、已选集合、勾选草稿、保存逻辑全是一份，
 *  两个入口不可能再各画各的。 */

var pickerTouched = false;     // 用户在选课界面里动过勾选（草稿与已保存数据不一致）
var pickerDraftList = [];      // 勾选草稿（三元组列表）—— 两个宿主共用这一份

/** 已保存的选课 → 归一化三元组键集合（`科目族|老师|组号`）。
 *  `grpChecked` 就是按这个键比对的，所以这里与渲染用的是同一把尺子。 */
function savedTripleKeys() {
  var keys = new Set();
  var doc = st.lessons && st.lessons.doc;
  if (isObj(doc) && Array.isArray(doc.lessons)) {
    doc.lessons.forEach(function (row) {
      if (isObj(row)) keys.add(tripleKey(row));
    });
  }
  return keys;
}

/** 归一化三元组键（`科目族|老师|组号`）—— 全站只有这一处拼法，
 *  草稿、已保存数据、`grpChecked` 三者必须逐字一致才比得上。 */
function tripleKey(o) {
  o = isObj(o) ? o : {};
  return subjFamily(o.subject || '') + '|' + (o.teacher || '') + '|' + (o.group || '');
}

/** 没有三元组数据时的**兜底**：只按组名（归一化）比对的集合。
 *  `grpChecked` 对这个集合只走第二条（`fam|teacher|` 那条）—— 老写法这里放的是
 *  普通对象，`checkedSet.has is not a function` 直接把课表渲染炸掉（probe 实测踩到）。 */
function savedGroupKeys() {
  var keys = new Set();
  personalGroups().forEach(function (g) { keys.add(groupKey(g)); });
  return keys;
}

/** 归一化键 → 可写回 `settings.lessons` 的三元组（打开模态时预勾选当前选课用）。 */
function savedTripleList() {
  var rows = [];
  var doc = st.lessons && st.lessons.doc;
  if (isObj(doc) && Array.isArray(doc.lessons)) {
    doc.lessons.forEach(function (row) {
      if (!isObj(row)) return;
      rows.push({ subject: row.subject || '', teacher: row.teacher || '', group: row.group || '' });
    });
  }
  return rows;
}

/** 当前勾选草稿：**唯一的真来源**是两个宿主共用的这一份。
 *  `pickerTouched` 为假时以已保存的选课为准（否则每次重建都把用户没保存的勾选盖掉）。 */
function pickerDraft() {
  if (!pickerTouched) pickerDraftList = savedTripleList();
  return pickerDraftList;
}

/** 草稿（或未改动时=已保存的选课）→ `grpChecked` 用的键集合。
 *  **必须是 Set**：`grpChecked` 调的是 `.has()`。
 *  有三元组数据时用 `科目族|老师|组号`（精确到教学组），没有时退回组名集合。 */
function pickerDraftKeys() {
  var list = pickerDraft();
  if (!list.length && !pickerTouched) {
    var byGroup = savedGroupKeys();
    if (byGroup.size) return byGroup;      // 只有 `{groups:[…]}` 形态的旧数据
  }
  var keys = new Set();
  list.forEach(function (r) { keys.add(tripleKey(r)); });
  return keys;
}

/** 某个宿主里的勾选变化 → 同步进共享草稿（两个入口共用一份勾选状态）。 */
function syncPickerDraftFrom(host) {
  if (!host) return;
  pickerDraftList = collectPickerSelection(host);
  pickerTouched = true;
}

/** **唯一的渲染器**：把按科目分组的选课列表画进 `host`（`#nopick-list` 或 `#picker-list`）。
 *  返回画出来的教学组行数（0 = 没画出来，测试就盯这个数）。 */
function renderSubjectPickerInto(host, filterValue) {
  if (!host) return 0;
  clear(host);
  var lessons = liveLessons();
  var subjects = buildSubjectGroups(lessons);
  if (!subjects.length) {
    host.appendChild(el('p', 'empty', '课表数据还没加载，稍等片刻再刷新。'));
    return 0;
  }
  var keys = pickerDraftKeys();
  var frag = subjectPickerHTML(subjects, filterValue || '', keys, false);
  if (frag) host.appendChild(frag);
  bindPickerFolds(host);
  return host.querySelectorAll('.subject-row').length;
}

/** 空态卡里的选课列表（宿主 `#nopick-list`）。
 *  **模态开着时不画** —— 同一时刻只留一个选课界面（`syncPickerHosts` 会在关模态后补画）。 */
function renderNopickPicker() {
  if (pickerModalOpen()) return 0;
  var filterBox = $('nopick-filter');
  return renderSubjectPickerInto($('nopick-list'), filterBox ? filterBox.value : '');
}

/** 模态里的选课列表（宿主 `#picker-list` —— **这次漏的就是这一步**）。 */
function renderPickerList() {
  var filterBox = $('picker-filter');
  return renderSubjectPickerInto($('picker-list'), filterBox ? filterBox.value : '');
}

function pickerModalOpen() {
  var overlay = $('picker-overlay');
  return !!(overlay && !overlay.hidden);
}

/** 两个选课宿主互斥：模态开着 → 课表里那张卡的选课列表收起（避免同页两份列表）；
 *  模态关着 → 空态卡里的列表照旧可见（没选课时那是「一步可达」的入口）。 */
function syncPickerHosts() {
  var cardHost = $('nopick-picker');
  var modalOpen = pickerModalOpen();
  if (cardHost) {
    cardHost.hidden = modalOpen;
    if (modalOpen) {
      /* 模态开着时把卡片那份**清空**（不只是藏起来）：同页只留一份选课列表，
         也免得「看不见的那份」在测试/脚本里被当成真实内容读走。 */
      var cardList = $('nopick-list');
      if (cardList) clear(cardList);
    } else {
      renderNopickPicker();
    }
  }
  if (modalOpen) {
    var list = $('picker-list');
    /* 只补「还没画出来」的那次：已经画过就不重复重建（重建会丢掉刚勾的勾选态） */
    if (list && !list.querySelector('.subject-row')) renderPickerList();
  }
}

/** 保存选课（**两个入口共用这一条写回路径**，含 base_revision + 409 重放）。
 *  保存成功 → 关模态 + `applyLive()` 立刻按新选课过滤课表。 */
function savePickerSelection(o) {
  o = o || {};
  var host = o.overlay ? $('picker-list') : ($('nopick-list') || $('picker-list'));
  var sel = host ? collectPickerSelection(host) : [];
  if (!sel.length) { toast('请至少勾选一个教学组'); return; }
  var btn = $(o.btnId || 'nopick-save');
  var label = o.label || '保存选课';
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }

  function write(ref) {
    var base = isObj(ref.doc) ? JSON.parse(JSON.stringify(ref.doc)) : {};
    if (!isObj(base)) base = {};
    base.lessons = sel;
    // 同时派生 groups 字母表（兼容旧逻辑）
    var groupSet = {};
    sel.forEach(function (s) {
      var g = (s.group || '').trim();
      if (g) groupSet[g] = true;
    });
    base.groups = Object.keys(groupSet).sort();
    base.updated_at = nowIso();
    if (base.version == null) base.version = 1;
    if (!base.kind) base.kind = 'pinghe-lessons';
    return base;
  }
  var p = st.lessons
    ? putObject(OBJ.lessons, st.lessons, write)
    : getObject(OBJ.lessons).then(function (fresh) {
        st.lessons = fresh;
        return putObject(OBJ.lessons, st.lessons, write);
      });
  p.then(function (rev) {
    st.lessons.doc = write(st.lessons);
    st.lessons.revision = rev;
    st.lessons.parsed = true;
    /* 保存成功 → 草稿归零（下一次渲染=已保存的选课）＋关模态＋立刻按新组过滤课表。
       `applyLive()` 会整块重建课表（空态卡连同选课面板一起重建，不出残留面板）。 */
    pickerTouched = false;
    if (o.overlay) closePickerModal();
    toast('已保存到你的账号（' + sel.length + ' 条选课），客户端同步后也会用这份选课');
    applyLive();   // 立刻按新组过滤课表
  })['catch'](function (err) {
    if (err && err.unauthorized) return;
    toast(err.message || '保存失败');
    if (btn) { btn.disabled = false; btn.textContent = label; }
  });
}

/** 保存空态卡里的选课（写入 settings.lessons）。 */
function saveNopickPicker() {
  savePickerSelection({ btnId: 'nopick-save', label: '保存选课' });
}

/** 渲染整周课表。DOM/类名与客户端一致：
 *  #tt-week.tt-grid > (.tt-head | .tt-time | .tt-cell > .tt-lesson | .tt-restbar) */
function renderTimetable(panel) {
  var body = (panel && panel !== window) ? panel : $('tt-body');
  var host = $('tt-week');
  if (!body || !host) return;
  while (host.firstChild) host.removeChild(host.firstChild);
  ttFlat = [];
  host.style.gridTemplateRows = '';      // 上一轮量出来的行高不要留着

  ttRecordsWeekHint();
  ttAutoJumpWeek();
  /* 课表整块重建 = 数据是**刚同步/刚抓来**的 → 选课草稿回到「已保存的选课」，
     不让上一次没保存的勾选在重新抓取后还留着（弹窗打开时也会重置一次，见 openPickerModal）。 */
  pickerTouched = false;
  var week = ttBuildWeek();
  var lessons = (st.ttLessons || []);
  var dayKeys = [];
  lessons.forEach(function (l) {
    var d = l.day || '未定日期';
    if (dayKeys.indexOf(d) === -1) dayKeys.push(d);
  });
  if ($('tt-range')) $('tt-range').textContent = week[0].day + ' ~ ' + week[6].day;
  ttWeekData = { week: week };
  /* 导出按钮的可用状态跟着这一轮数据走：没加载完 → 禁用（见 updateTtExportState）。 */
  updateTtExportState();

  /* 工具条上的绿色「已连接」（只有四个字 + 绿点）。
     原先这里那行「已连接：账号 · 本周选课 N 个教学组：… · 只显示你选的课：39/273 条，已隐藏 234 条」
     已整体删除 —— 账号名 / 教学组清单 / 过滤条数都是技术细节，不摆在课表上。 */
  ttRenderConn();

  if (!lessons.length) {
    /* 2026-09-15 修：`{"groups":[]}` 这类"存了空选课"会把过滤结果清成 0 条，
       原先会先落进这个空数据分支、永远轮不到下面的选课卡。
       正确顺序：只要用户没选课且 EduPage 有全年级候选，就该直接给选课卡。 */
    /* 一条都没有：还没加载完（pending / 正在读同步对象）→ 导出按钮禁用；
       真的抓完了但这一份是空的 → 允许导出（空表 + 标题照画）。 */
    updateTtExportState();
    if (noLessonPick() && liveLessons().length > 0) {
      show($('tt-wait'), false);
      host.appendChild(noPickCard(week));
      renderNopickPicker();
      return;
    }
    show($('tt-wait'), false);
    var pending = plPending('edupage');
    var e = plErr('edupage');
    var why = pending ? PENDING_TEXT
      : (pendingSync() ? '正在读取同步对象…'
        : (e ? ('暂时拿不到课表（' + e + '）') : emptyText('课表', 'edupage', 'EduPage')));
    var box = el('div', 'empty' + (pending ? ' pending-hint' : ''));
    box.appendChild(document.createTextNode(why));
    if (pending) {
      /* 后台抓 EduPage：给一条中性说明 + 一个「刷新」按钮，绝不画成红色错误 */
      box.appendChild(el('div', 'note', PENDING_HINT));
    }
    retryInto(box, why, pending);
    host.appendChild(box);
    host.appendChild(ttCompatCell(week));
    return;
  }

  /* 用户**一份选课都没有**时：绝不把全年级候选课当成「我的课表」铺开。
     课表位置画一张空态卡（卡里**直接内嵌选课面板**，一步可达）；勾完保存立刻按新组渲染。
     判定只看「有没有选课数据」，不看过滤后剩几条 —— 已选课但本周没课是另一回事。

     **国家必修照常显示**（照 PLL `personal()`：「未跑向导的新账号会先看到必修课,
     选完课后选修课自动出现」）：无组的全班必修 + 国家理科（三张轮换卡合成一张）。 */
  if (noLessonPick()) {
    show($('tt-wait'), false);
    var natByDay = ttBuildByDay(week, isNationalRequired);
    if (!natByDay.some(function (b) { return b.any; })) {
      host.appendChild(noPickCard(week));      // 连国家必修都没有 → 只给选课卡
      renderNopickPicker();                    // 卡片已入 DOM，可以安全查询 #nopick-list
      syncPickerHosts();                       // 模态开着时：卡片里那份不画，勾选落在模态里
      return;
    }
    host.appendChild(noPickCard(week));
    renderNopickPicker();
    syncPickerHosts();
    var natHead = el('div', 'card tt-national');
    natHead.id = 'tt-national-head';
    natHead.appendChild(el('div', 'card-title', '国家必修（自动包含，不用选）'));
    natHead.appendChild(el('p', 'note',
      '下面这些是**全班必修**（班会 / 国家课程这类学校不打教学组的课）与**国家理科**'
      + '（`国家物理` / `国家化学` / `国家生物` 三张轮换卡在这里合成一门「国家理科」）——'
      + '学校统一安排、每个学生都有，**不需要勾选**就一直在你的课表里。'
      + '你只需要在上面选择**选修课**的教学组。'));
    host.appendChild(natHead);
    ttPaintWeek(host, natByDay, week);
    return;
  }

  /* 每个节次一行：行 1 = 日期表头，行 r = 第 (r-1) 个节次。
     国家理科三张轮换卡在这一步合并成一张「国家理科」卡（照 PLL `personal()`）。 */
  var byDay = ttBuildByDay(week, null);

  /* 画格子：行 1 = 左上角 + 七个日期表头（今天用金色）；列 1 = 节次栏，列 2..8 = 周一到周日。
     这一段抽成 `ttPaintWeek`，因为「没选课」分支只画国家必修那一部分时要用同一套画法。 */
  ttPaintWeek(host, byDay, week);
}

/** 一周数据 → 每天一张网格：把每节课塞进它开始的节次，并做**连堂预扫描**。
 *
 *  `only` 传 `isNationalRequired` 时只保留国家必修那部分 —— 「一次选修都没勾」的态
 *  就用它把国家必修如实画出来（见 renderTimetable）。
 *
 *  调用方已经把 `st.ttLessons` 里的国家理科合并过了（`mergeNationalScience`，照 PLL
 *  `personal()` 把同一时段的三张轮换卡合成一条「国家理科」），这里只管排格子。 */
function ttBuildByDay(week, only) {
  return (week || []).map(function (day) {
    var cells = PERIODS.map(function () { return []; });
    var other = [];
    (day.lessons || []).forEach(function (l) {
      if (only && !only(l)) return;
      var pi = periodOf(l.start);
      if (pi >= 0) cells[pi].push(l); else other.push(l);
    });
    /* 连堂预扫描：① 服务端给的跨节卡（end 越过下一节）② PLL 的「相邻两格同一节课」合并 */
    var scan = ttScanSpans(cells);    return { cells: cells, other: other, day: day.day, label: day.label,
             spanStart: scan.spanStart, consumed: scan.consumed,
             merged: scan.merged, cover: scan.cover,
             any: cells.some(function (c) { return c.length; }) || other.length > 0 };
  });
}

/** 把一周画到 #tt-week 上（行 1 = 表头，列 1 = 节次栏，列 2..8 = 周一到周日）。 */
function ttPaintWeek(host, byDay, week) {
  var today = isoToday();
  /* 行 1：左上角 + 七个日期表头（今天用金色） */
  var corner = el('div', 'tt-head tt-corner');
  corner.setAttribute('style', 'grid-row:1;grid-column:1');
  host.appendChild(corner);
  byDay.forEach(function (b, di) {
    var h = el('div', 'tt-head' + (b.day === today ? ' today' : ''), b.label);
    h.title = b.day;
    h.setAttribute('style', 'grid-row:1;grid-column:' + (di + 2));
    host.appendChild(h);
  });

  var pRows = [];                    // 正课行号（行高统一用；跨行块所在行由它自己撑，见 ttFitRows）
  var spans = [];                    // 连堂块（服务端跨节卡 / 旧式两卡合一）：最后入 DOM，盖住空格
  var r = 2;                         // 行 1 = 星期表头；P1 从第 2 行开始（照客户端 tt-grid）
  PERIODS.forEach(function (p, pi) {
    var busy = byDay.some(function (b) { return b.cells[pi].length; });
    var timeCell = el('div', 'tt-time' + (p.rest ? ' rest' : ''));
    timeCell.setAttribute('style', 'grid-row:' + r + ';grid-column:1');
    timeCell.appendChild(el('b', null, p.name));
    timeCell.appendChild(el('span', null, p.start));
    host.appendChild(timeCell);

    /* Lunch/晚自习整周没课 → 一条横幅，不占七列 */
    if (p.rest && !busy) {
      var bar = el('div', 'tt-restbar', p.name + ' ' + p.start + ' – ' + p.end);
      bar.setAttribute('style', 'grid-row:' + r + ';grid-column:2/-1');
      host.appendChild(bar);
      r++;
      return;
    }
    if (!p.rest) pRows.push(r);
    byDay.forEach(function (b, di) {
      var cell = el('div', 'tt-cell' + (p.rest ? ' rest' : ''));
      cell.setAttribute('style', 'grid-row:' + r + ';grid-column:' + (di + 2));
      var ls = b.cells[pi];
      /* `span` = 这一格上有一块跨行课卡（服务端跨节卡，或旧式合并块）。
         跨行卡只画在该块的**第一行**（`first`）：块从这一行开始往后跨，
         被跨过的那些行的格子照常画、里面**不放课卡**，由这张卡盖过去。 */
      var span = b.spanStart[pi] || null;
      var first = !!span && !b.consumed[pi - 1];
      if (span && b.cover[pi] && b.merged[pi]) {
        /* 连堂（旧式两卡合一）：整块必须作为 #tt-week 的**直接子元素**跨行 ——
           塞进 .tt-cell（flex 容器）里 grid-row 是无效的，那会让第二行整行空白（上一轮踩过的坑）。 */
        var tiS = ttFlat.length;
        ttFlat.push({ lesson: span.from, day: b.day, dayLabel: b.label });
        var blk = ttLessonEl(span.from, tiS, false,
          'grid-column:' + (di + 2) + ';grid-row:' + r + ' / span ' + span.n);
        blk.classList.add('tt-span');
        blk.setAttribute('data-span', String(span.n));
        blk.setAttribute('data-span-source', 'merge');
        spans.push(blk);
      } else if (first && b.merged[pi]) {
        /* 旧式合并块的第一行：格子空着（由后面的跨行块代表这一块） */
      } else if (first) {
        /* 服务端权威跨节（真实数据里连堂就是这个形态）：课卡本身跨行 —— 同样必须是
           #tt-week 的直接子元素，并且只出现在块的第一行上，后续行留空由它盖住。 */
        if (ls.length > 1) {
          /* 这一格还不止一张卡（同一时段多门并行课）→ 并行摘要照旧占一格，跨行不适用于它 */
          ttParallelInto(cell, ls, b);
        } else {
          var tiW = ttFlat.length;
          ttFlat.push({ lesson: span.from, day: b.day, dayLabel: b.label });
          var wide = ttLessonEl(span.from, tiW, false,
            'grid-column:' + (di + 2) + ';grid-row:' + r + ' / span ' + span.n);
          wide.classList.add('tt-span');
          wide.setAttribute('data-span', String(span.n));
          wide.setAttribute('data-span-source', 'server');
          spans.push(wide);
        }
      } else if (b.consumed[pi]) {
        /* 被跨过的行：格子照常画，里面不放课卡 */
      } else if (ls.length > 1) {
        /* 同一时段多门并行课（没选课 = 全年级候选、或勾了同一个科目的多个组）：
           折叠成一格「该时段有 N 门并行课」，点开才列出全部 —— 不挤在一格里。 */
        ttParallelInto(cell, ls, b);
      } else {
        ls.forEach(function (l) {
          var ti = ttFlat.length;
          ttFlat.push({ lesson: l, day: b.day, dayLabel: b.label });
          cell.appendChild(ttLessonEl(l, ti, false));
        });
      }
      host.appendChild(cell);
    });
    r++;
  });

  /* 不在任何节次的课（临时调课等）→ 底部「课外」一行 */
  if (byDay.some(function (b) { return b.other.length; })) {
    var rOther = r;
    var labelCell = el('div', 'tt-time');
    labelCell.setAttribute('style', 'grid-row:' + rOther + ';grid-column:1');
    labelCell.appendChild(el('b', null, '课外'));
    host.appendChild(labelCell);
    byDay.forEach(function (b, di) {
      var cell = el('div', 'tt-cell');
      cell.setAttribute('style', 'grid-row:' + rOther + ';grid-column:' + (di + 2));
      b.other.forEach(function (l) {
        var tiO = ttFlat.length;
        ttFlat.push({ lesson: l, day: b.day, dayLabel: b.label });
        cell.appendChild(ttLessonEl(l, tiO, true));
      });
      host.appendChild(cell);
    });
  }

  /* 连堂合并块最后入 DOM：z 序在后 → 正好盖住被跨过的那几行的空格 */
  spans.forEach(function (node) { host.appendChild(node); });

  /* 兼容层（屏外按天条目：既有断言与无障碍都在用它） */
  host.appendChild(ttCompatCell(byDay));
  show($('tt-wait'), false);
  /* 行高统一：量的是**自然高度**，所以要在这个视图真的可见时才算得准（不可见时量到 0，
     ttFitRows 会自己跳过，等 showView('timetable') / resize 再算一次）。 */
  ttFitRows(pRows);
  /* 行高统一之后再画当前时间线（线的位置是用行的实际 rect 算的，
     行高没定下来时算出来的 top 会偏）。面板不可见时 rect 全是 0 → 函数自己跳过，
     等 showView('timetable') / resize / 每分钟的定时器再补。 */
  ttUpdateNowLine();
  bindTimetable();
}

/** 同一时段有多门并行课时的格子内容：一条「N 门并行课 · 点开看是哪几门」的摘要 +
 *  默认收起的完整列表（点击展开/收起）。**课卡本身照旧在 DOM 里**（点击仍能开详情、
 *  兼容层与既有断言都还在），只是默认不给用户看「三张卡挤在一格里」。 */
function ttParallelInto(cell, ls, b) {
  cell.classList.add('tt-cell--multi');
  var names = ls.map(function (l) { return String(l.subject || '（未命名科目）'); });
  var sum = el('button', 'tt-par-sum');
  sum.type = 'button';
  sum.setAttribute('aria-expanded', 'false');
  sum.appendChild(el('b', null, ls.length + ' 门并行课'));
  sum.appendChild(el('span', 'rm',
    '该时段有 ' + ls.length + ' 门并行课：' + names.join(' / ') + ' —— 点开看是哪几门'));
  cell.appendChild(sum);

  var list = el('div', 'tt-par-list');
  list.hidden = true;
  ls.forEach(function (l) {
    var ti = ttFlat.length;
    ttFlat.push({ lesson: l, day: b.day, dayLabel: b.label });
    list.appendChild(ttLessonEl(l, ti, false));
  });
  cell.appendChild(list);

  sum.addEventListener('click', function () {
    var open = list.hidden;
    list.hidden = !open;
    sum.setAttribute('aria-expanded', open ? 'true' : 'false');
    sum.classList.toggle('open', open);
    /* 展开后这一格的自然高度变了 → 重量一次行高（"每个 p 统一为最长的"） */
    ttRefitSoon(0);
  });
}

/** 正课行高统一（客户端（Pinghe Launcher Lite）的 ttFitRows）：量出各正课行的自然高度取最大值，
 *  再把**每一个**正课行都固定成这个高度 —— 免得「三节课并行」的行很高、「只有一节」的行很矮。
 *
 *  两条必须守住的时机（否则整行高度会参差）：
 *  ① **量之前先还原** grid-template-rows（否则量到的是上一轮锁死的高度）；
 *  ② 面板不可见时 offsetHeight 全是 0（`applyLive()` 会在日程视图下就把课表渲染一遍）
 *     —— 这时**不能**把行高写成 0，直接跳过，等 `showView('timetable')` / resize 再算。 */
function ttFitRows(pRows) {
  var grid = $('tt-week');
  if (!grid || !pRows || !pRows.length || !grid.children.length) return;
  ttPRows = pRows.slice();
  grid.style.gridTemplateRows = '';          // 先还原，量的才是自然高度
  /* 跨行课卡（连堂）**不能**拿来定行高：它高两行，量出来的值会把每一行都撑成两行高。
     它跨过的那几行如实填好了内容（见 renderTimetable）→ 这几行用 auto，卡片自己撑开。 */
  var spanRows = {};
  var spanCards = grid.querySelectorAll('.tt-lesson[data-span]');
  for (var s = 0; s < spanCards.length; s++) {
    var stl = String((spanCards[s].style && spanCards[s].style.gridRow) || '');
    var mS = /(\d+)/.exec(stl);
    var mN = /span\s+(\d+)/.exec(stl);
    if (!mS) continue;
    var n0 = mN ? Number(mN[1]) : 1;
    for (var t = 0; t < n0; t++) spanRows[Number(mS[1]) + t] = true;
  }
  var byRow = {};
  var cells = grid.querySelectorAll('.tt-cell');
  for (var i = 0; i < cells.length; i++) {
    var row = parseInt(String((cells[i].style && cells[i].style.gridRow) || '').replace(/(\d+).*/, '$1'), 10);
    if (!row) continue;
    var h = cells[i].offsetHeight;
    /* 2026-09-16 用户要求「每个 p 的高度统一」：**所有格子都参与取最大值**，
       包括被跨行卡盖住的那几行 —— 它们的格子本来也画着（只是里面不放卡），
       量出来的高度同样代表"这一行需要多高"。 */
    if (h > (byRow[row] || 0)) byRow[row] = h;
  }
  /* 跨行卡（连堂）按「块高 ÷ 跨几行」折算成"这一行至少要多高"，一并参与取最大值 ——
     否则它盖住的那几行会被'块高/跨行数'压得比别的行矮（用户实测 p8 就这样）。 */
  var rowFix = {};
  for (var si = 0; si < spanCards.length; si++) {
    var el2 = spanCards[si];
    var st2 = String((el2.style && el2.style.gridRow) || '');
    var m2 = /(\d+)/.exec(st2);
    var n2 = /span\s+(\d+)/.exec(st2);
    if (!m2) continue;
    var start = Number(m2[1]);
    var cnt = n2 ? Number(n2[1]) : 1;
    if (cnt < 2) continue;
    var each = Math.ceil(el2.offsetHeight / cnt);
    for (var t2 = 0; t2 < cnt; t2++) {
      var rr2 = start + t2;
      if (each > (rowFix[rr2] || 0)) rowFix[rr2] = each;
    }
  }
  /* 全局最高：正课行、跨行卡盖住的行、以及任何画过格子的行，全部一起比。 */
  var maxH = 0;
  Object.keys(byRow).forEach(function (k) { if (byRow[k] > maxH) maxH = byRow[k]; });
  pRows.forEach(function (row) { if ((byRow[row] || 0) > maxH) maxH = byRow[row]; });
  Object.keys(rowFix).forEach(function (k) { if (rowFix[k] > maxH) maxH = rowFix[k]; });
  if (!maxH) return;                       // 页面不可见（量到 0）→ 不处理
  /* 最后一行 = DOM 里出现过的最大 grid-row（含「课外」行），照客户端从元素上量。
     注意 `el.style.gridRow` 是**规范化后**的值（"2"），不是 "grid-row:2"，只取数字。 */
  var lastRow = 1;
  var kids = grid.children;
  for (var k = 0; k < kids.length; k++) {
    var m = /(\d+)/.exec(String((kids[k].style && kids[k].style.gridRow) || ''));
    if (m && Number(m[1]) > lastRow) lastRow = Number(m[1]);
  }
  /* 每一个节次行**都用同一个高度**（含被跨行卡盖住的行）；
     表头行与 Lunch/晚自习横幅行仍是 auto（它们横跨整行、高度由内容定）。 */
  var tpl = [];
  for (var rr = 1; rr <= lastRow; rr++) {
    var isPeriod = pRows.indexOf(rr) !== -1;
    tpl.push((isPeriod || spanRows[rr]) ? (maxH + 'px') : 'auto');
  }
  grid.style.gridTemplateRows = tpl.join(' ');
}

/* 行高是在 DOM 里量出来的：字体/窗口大小一变自然高度就变，切视图时更要从「可见」状态下重量一遍。
   （渲染往往发生在日程视图（面板 hidden）里，那一次量到 0 是量不出行高的。） */
var ttPRows = [];
var ttFitTimer = 0;
function ttRefitSoon(delay) {
  clearTimeout(ttFitTimer);
  ttFitTimer = setTimeout(function () {
    if (currentView === 'timetable' && ttPRows.length) ttFitRows(ttPRows);
    /* 行高可能被改过（窗口变宽变窄 / 并行课展开）→ 时间线跟着重量一次 */
    ttUpdateNowLine();
  }, delay == null ? 180 : delay);
}
window.addEventListener('resize', function () { ttRefitSoon(180); });

/* ------------------------------------------------- 当前时间指示线（横贯整张课表）
 *  与桌面客户端（Pinghe Launcher Lite）的 #tt-nowline 同款：一根绿线横穿整张周课表，落在「现在」
 *  对应的节次行内；行内高度按**时间比例插值**（例：P3 是 09:35–10:15，现在 09:45
 *  → 过了 10/40 = 1/4 → 画在该行 1/4 处）。课间落在上一节课末尾与下一节开始之间。
 *
 *  三条时机上的讲究：
 *  ① **只在「显示的这一周包含今天」时才画**（翻到上一周/下一周不画任何线）；
 *  ② 位置用**所在行元素的实际 rect** 算，绝不写死行高 —— 行高是 ttFitRows 量出来
 *     统一过的，写死必然错位；
 *  ③ 每分钟自动更新一次，另外切到课表视图 / 刷新落地 / 窗口 resize 后立即重算。
 *  pointer-events:none（见 app.css）→ 不挡课卡点击。
 * -------------------------------------------------------------------------- */

/** 现在几点（分钟，带秒的小数）。抽出来是为了能测「假时刻」：
 *  `ttNowMins()` 正常取本地时钟；测试直接改 `ttNowMinsOverride` 构造「今天 10:00」。 */
var ttNowMinsOverride = null;
function ttNowMins() {
  if (ttNowMinsOverride != null) return Number(ttNowMinsOverride);
  var d = new Date();
  return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
}

/** 当前显示的这一周里有没有今天（有 → 才画时间线）。 */
function ttWeekHasToday() {
  var start = ttWeekStart || mondayOf(isoToday());
  var monday = mondayOf(isoOfDate(start));
  var today = mondayOf(isoToday());
  return isoOfDate(monday) === isoOfDate(today);
}

/**
 * 计算「现在」在网格里的落点。
 * 返回 `{ row: 行下标, f: 该行内的比例, gapTo: 课间时下一行的下标 / null, g: 课间进度 }`；
 * 不在任何节次 / 不在校时段内 → null。
 *
 * 时间比例**先按节次的起止时刻算**，再交给几何（行 rect）落到位：
 *   课中：`f = (t − start) / (end − start)`，例如 P3 09:35–10:15 的 10:00 → f = 25/40 = 0.625；
 *   课间：落在「上一节末尾 → 下一节开始」这一段里按比例取 g，最后在**像素**上
 *         从上一行末尾线性滑到下一行开头 —— 行高是统一过的，用像素插值才与视觉一致。
 */
function ttNowPoint(t) {
  var first = minutesOf(PERIODS[0].start);
  var last = minutesOf(PERIODS[PERIODS.length - 1].end);
  if (first == null || last == null || t < first || t > last) return null;
  for (var i = 0; i < PERIODS.length; i++) {
    var s = minutesOf(PERIODS[i].start), e = minutesOf(PERIODS[i].end);
    if (s == null || e == null) continue;
    if (t >= s && t < e) return { row: i, f: (t - s) / (e - s), gapTo: null, g: 0 };
    if (t >= e) {
      var nx = PERIODS[i + 1] ? minutesOf(PERIODS[i + 1].start) : null;
      if (nx == null) return { row: i, f: 1, gapTo: null, g: 0 };      // 最后一节已下课
      if (t < nx) return { row: i, f: 1, gapTo: i + 1, g: (t - e) / Math.max(1, nx - e) };
    }
  }
  return null;
}

/** 画（或抹掉）当前时间线。返回实测的 top（没有线时返回 null）——测试直接用它取证。 */
function ttUpdateNowLine() {
  var grid = $('tt-week');
  var oldLine = $('tt-nowline');
  var oldTag = $('tt-clock');
  if (oldLine && oldLine.parentNode) oldLine.parentNode.removeChild(oldLine);
  if (oldTag && oldTag.parentNode) oldTag.parentNode.removeChild(oldTag);
  if (!grid || currentView !== 'timetable') return null;
  /* 课表骨架还没画出来（骨架屏 / 空态卡）时不画线 */
  if (!grid.querySelector('.tt-time')) return null;
  if (!ttWeekHasToday()) return null;            // 翻到别的周：不画

  var t = ttNowMins();
  var pt = ttNowPoint(t);
  if (!pt) return null;

  /* 所在行的元素：`ttPaintWeek` 按 PERIODS 顺序 append `.tt-time`（「课外」那行是最后追加的），
     所以第 pt.row 个就是当前这一节的行。位置用 rect 算
     （视口可见时才有真实高度；隐藏视图量到 0 → 直接跳过，等切视图 / resize / 定时器重算）。 */
  var times = grid.querySelectorAll('.tt-time');
  var row = times[pt.row];
  if (!row) return null;
  var gridTop = grid.getBoundingClientRect().top;
  var rect = row.getBoundingClientRect();
  if (!rect.height) return null;
  var top = rect.top - gridTop + rect.height * pt.f;
  if (pt.gapTo != null) {
    /* 课间：从上一行末尾（≈ 下一行开头）按课间进度 g 在**像素**上插值 */
    var nextRow = times[pt.gapTo];
    if (nextRow) {
      var nrect = nextRow.getBoundingClientRect();
      if (nrect.height) {
        var endA = rect.top - gridTop + rect.height;
        var startB = nrect.top - gridTop;
        top = endA + (startB - endA) * pt.g;
      }
    }
  }

  var line = el('div', null);
  line.id = 'tt-nowline';
  line.setAttribute('style', 'top:' + Math.round(top * 10) / 10 + 'px');
  grid.appendChild(line);

  var tag = el('div', null, hhmm(t));
  tag.id = 'tt-clock';
  tag.setAttribute('style', 'top:' + Math.round((top - 8) * 10) / 10 + 'px');
  grid.appendChild(tag);
  return top;
}

/* 每分钟自动更新一次（切视图 / 数据落地 / resize 另外立即重算一次）。 */
var ttNowLineTimer = null;
function startTtNowLine() {
  if (ttNowLineTimer) return;
  ttNowLineTimer = setInterval(function () { ttUpdateNowLine(); }, 60000);
}

/** 点课卡 → 详情（与 客户端 弹卡同一组信息）。 */
function openLessonCard(l) {
  var parts = [];
  parts.push((l.start || '全天') + (l.end ? '–' + l.end : ''));
  parts.push('教室 ' + (l.room || '—'));
  parts.push('老师 ' + (l.teacher || '—'));
  parts.push('教学组 ' + (l.group || '—'));
  if (l.note) parts.push(cellText(l.note));
  toast((l.subject || '（未命名科目）') + ' · ' + parts.join(' · '), 5200);
}

function bindTimetable() {
  var host = $('tt-week');
  if (!host) return;
  var cards = host.querySelectorAll('.tt-lesson');
  for (var i = 0; i < cards.length; i++) {
    (function (card) {
      var open = function () {
        var entry = ttFlat[Number(card.getAttribute('data-ti'))];
        if (entry && entry.lesson) openLessonCard(entry.lesson);
      };
      card.addEventListener('click', open);
      card.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
      });
    })(cards[i]);
  }
}

/** 上一周 / 本周 / 下一周。 */
function ttShiftWeek(n) {
  // 用户自己翻周了 → 以后不再自动改他看的周（自动跳周只在"用户没表过态"时生效）
  ttUserNavigated = true;
  if (n === 0) { ttWeekStart = null; ttAutoJumped = false; }
  else {
    var base = ttWeekStart || mondayOf(isoToday());
    ttWeekStart = shiftDay(base, n * 7);
  }
  show($('tt-wait'), false);
  renderTimetable($('tt-week'));
}

/* =========================== ②b 导出课表（PNG / CSV） ===========================
 *
 *  用户原话：「加一个导出课表的功能，可以导出这个人目前的课表，但是是**周一周二这些是竖着，
 *  然后 p 几是横着排列的**。」
 *
 *  所以导出的表**方向与页面相反**：页面是「行=节次、列=星期」（照客户端 tt-grid），
 *  导出是「**行=星期（周一…周日竖着）+ 日期**、**列=节次（P1…P8 横着，表头带起止时间）**」。
 *
 *  数据 = **当前已加载的过滤后课表**（`st.ttLessons` → `ttWeekData.week`，含国家理科合并与
 *  全班必修，与页面所见完全一致）+ **当前显示的那一周**（`ttWeekStart`）。不重新抓取、不发请求。
 *
 *  两个格式都零外部依赖：CSV 就是字符串拼的（UTF-8 带 BOM）；PNG 用 `<canvas>` 自己画
 *  （没有 canvas 库、没有 CDN，中文用系统字体栈）。
 */

/** CSV / PNG 的「行 = 星期、列 = 节次」表：两个格式共用这一份数据与方向。 */

/** 导出表格里一个课格的**文字可用宽度**（PNG 的节次栏固定宽度减去内边距）。 */
var TT_EXPORT_CELL_W = 116;

/** 把「这一周的课表」转成导出的表格矩阵：行 = 星期，列 = 节次。
 *
 *  返回 {title, header, rows, cells, days, periods, widths, fits, widthsPx}：
 *    header  : ['周一', '日期', 'P1 08:00-08:40', …]（第 2 列是日期，第 3 列起是节次）
 *    rows    : [{label:'周一', date:'2026-09-14', cells:['', '数学 · A101 · 张老师', …]}]
 *              —— 不直接把表头和数据拼行，免得 CSV/PNG 两处各写一套方向，改一处忘一处。
 *    widths  : 每一列的**画布逻辑宽度**（PNG 布局用）
 *    fits    : 每个格子的文字排版结论（用了多大的字号、要不要换行、有没有溢出）
 *              —— 导出的图连断言都要能自证「文字没被裁掉」，不用肉眼猜。
 */
function ttExportMatrix() {
  var week = (ttWeekData && ttWeekData.week && ttWeekData.week.length)
    ? ttWeekData.week : ttBuildWeek();
  if (!week || !week.length) return null;
  var byDay = ttBuildByDay(week, null);          // 与页面同一套排格（含连堂合并、国家理科）

  /* 与页面一致：整周都没课的 Lunch / 晚自习只留一个休息标记，不单列一栏 */
  var columns = [];
  var periodIdx = [];
  PERIODS.forEach(function (p, pi) {
    var busy = byDay.some(function (b) { return b.cells[pi] && b.cells[pi].length; });
    if (p.rest && !busy) return;
    columns.push({ name: p.name, start: p.start, end: p.end, rest: !!p.rest, kind: 'P' });
    periodIdx.push(pi);
  });
  /* 「课外」：不在任何节次的课（临时调课等）→ 最后一栏，没有就不加 */
  var hasOther = byDay.some(function (b) { return b.other && b.other.length; });
  if (hasOther) columns.push({ name: '课外', kind: 'other' });

  /* PNG 的列宽：先给一套稳妥的默认值（画布量不到字宽时也能画出完整的表，
     而不是塌成 0 宽），再用真实字体量一次，取「默认」与「量出来的」里更宽的那个。 */
  var widths = [104, 104];
  columns.forEach(function (c) {
    widths.push(c.kind === 'P' ? 164 : 140);
  });
  var header = ['星期', '日期'];
  columns.forEach(function (c) {
    header.push(c.kind === 'P' ? (c.name + ' ' + c.start + '-' + c.end) : c.name);
  });
  var mctx = ttExportMeasureCtx();
  var mw = function (text, font) {
    var t = String(text == null ? '' : text);
    if (mctx) { mctx.font = font; return mctx.measureText(t).width; }
    /* 量不到字体时的保守估计：中文按一个字约等于一个字号宽（约 15px），别用 .length*9 ——
       那样会严重低估中文宽度，图片里的格子会挤到放不下。 */
    var w = 0;
    for (var i = 0; i < t.length; i++) w += t.charCodeAt(i) > 0x2e80 ? 15 : 8;
    return w;
  };
  var wl = 0, wr = 0;
  header.forEach(function (t, ci) {
    if (ci === 0) wl = Math.max(wl, mw(t, 'bold 13px ' + ttExportFontStack()) + 20);
    else if (ci === 1) wr = Math.max(wr, mw('日期', '11.5px ' + ttExportFontStack()) + 22);
    else {
      /* 节次表头分两行画（P1 / 08:00-08:40）→ 宽度按**较宽的那一行**算，不按整串 */
      var parts = String(t).split(' ');
      widths[ci] = Math.max(widths[ci],
        mw(parts[0] || '', 'bold 13px ' + ttExportFontStack()) + 18,
        mw(parts.slice(1).join(' '), '11.5px ' + ttExportFontStack()) + 18);
    }
  });
  byDay.forEach(function (b) {
    wl = Math.max(wl, mw(b.label, 'bold 13px ' + ttExportFontStack()) + 20);
    wr = Math.max(wr, mw(b.day.slice(5), '11.5px ' + ttExportFontStack()) + 22);
  });
  widths[0] = Math.max(widths[0], wl);
  widths[1] = Math.max(widths[1], wr);
  /* 课格的实际可用宽度 = 列宽 − 内边距（左边 9 + 右边 9）——排版测量与作画必须同一个数 */
  var cellW = [];
  widths.forEach(function (w, ci) {
    cellW.push(ci < 2 ? w - 14 : Math.max(40, w - 18));
  });

  /* 排格：先把每一格要写的文字定下来（连堂只在开始那一节写一次），
     再按**真实列宽**量一次排版结论（字号 / 换行 / 有无溢出）——
     PNG 与自检共用同一份口径，导出图的质量因此可以被断言，而不是靠肉眼。 */
  var fits = [];
  var rows = byDay.map(function (b) {
    var cells = [];
    columns.forEach(function (col, ci) {
      var got = [];
      if (col.kind === 'other') {
        got = (b.other || []).slice();
      } else {
        var pi = periodIdx[ci];
        /* 连堂（跨节 / 两卡合一）只在**开始那一节**显示一次，被跨过的节次留空 ——
           与页面上的跨行课卡同一个语义（填满会把同一节课抄成两遍）。 */
        var span = b.spanStart[pi] || null;
        var first = !!span && !b.consumed[pi - 1];
        if (span && b.merged[pi]) { if (first) got = [span.from]; }
        else if (!b.consumed[pi] && !(span && !first)) got = (b.cells[pi] || []).slice();
      }
      cells.push(got.length ? got.map(ttExportLessonText).join('\n') : '');
    });
    fits.push(cells.map(function (t, ci) {
      if (!t) return null;
      return ttExportTextFit(t, cellW[ci + 2], 12.5);
    }));
    return { label: b.label, date: b.day, cells: cells };
  });

  return {
    title: '我的课表 · ' + week[0].day + ' ~ ' + week[6].day,
    range: week[0].day + ' ~ ' + week[6].day,
    header: header, rows: rows, days: week.length, periods: columns.length,
    widths: widths, cellW: cellW, fits: fits, weekStart: week[0].day
  };
}

/** 导出图里「一节课」的文字：科目 + 教室 + 老师（有教学组也带上）。 */
function ttExportLessonText(l) {
  l = l || {};
  var parts = [String(l.subject || '（未命名科目）')];
  parts.push(String(l.room || '—'));
  parts.push(String(l.teacher || '—'));
  if (l.group) parts.push(String(l.group));
  return (l.cancelled ? '（已取消）' : '') + parts.join(' · ');
}

/** CSV 字段转义：含逗号 / 引号 / 换行时用双引号包起来，内部引号翻倍。 */
function csvField(v) {
  var s = (v == null ? '' : String(v));
  if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

/** 导出 CSV 文本：**UTF-8 带 BOM**（不带 BOM 时 Excel 会把中文显示成乱码）。
 *  第一行 = 表头（星期,日期,P1 08:00-08:40,…），之后每行 = 一天。 */
function ttExportCsvText(m) {
  var data = m || ttExportMatrix();
  if (!data) return '\ufeff';
  var out = [data.header.map(csvField).join(',')];
  data.rows.forEach(function (r) {
    var line = [r.label, r.date];
    r.cells.forEach(function (c) { line.push(c); });
    out.push(line.map(csvField).join(','));
  });
  return '\ufeff' + out.join('\r\n') + '\r\n';
}

/** 触发一次下载（Blob → Object URL → <a download>），用完立刻释放 URL。 */
function ttTriggerDownload(filename, blob) {
  if (typeof URL === 'undefined' || !URL.createObjectURL) return false;
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  a.style.display = 'none';
  (document.body || document.documentElement).appendChild(a);
  a.click();
  if (a.parentNode) a.parentNode.removeChild(a);
  /* 立刻撤销会让部分浏览器来不及取内容 → 下一轮事件循环再释放 */
  setTimeout(function () {
    try { URL.revokeObjectURL(url); } catch (e) { /* 已经释放过了 */ }
  }, 0);
  return true;
}

/** 导出开始前的统一检查：课表还没加载完（正在 pending / 还在读同步对象）→ 拒绝并说清楚。 */
function ttExportReady() {
  if (((st.ttLessons || []).length) > 0 || !!ttWeekData) {
    if (plPending('edupage') && !(st.ttLessons || []).length) return false;
    return true;
  }
  return !plPending('edupage') && !pendingSync();
}

/** 工具条上「⬇ 导出课表」按钮的可用状态：没加载完就禁用（点了给 toast，不静默）。 */
function updateTtExportState() {
  var btn = $('tt-export-btn');
  var ready = ttExportReady();
  if (btn) {
    btn.disabled = !ready;
    btn.title = ready
      ? '导出当前显示的这一周课表（行=星期、列=节次）'
      : '课表还没加载完';
  }
  if (!ready) ttCloseExportMenu();
  return ready;
}

function ttCloseExportMenu() {
  var menu = $('tt-export-menu'), btn = $('tt-export-btn');
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

/** 打开/收起小菜单；窄屏（菜单贴右边会顶出左边界）时按实际 rect 翻到左侧。 */
function ttToggleExportMenu() {
  var menu = $('tt-export-menu'), btn = $('tt-export-btn');
  if (!menu || !btn) return false;
  if (!menu.hidden) { ttCloseExportMenu(); return false; }
  menu.hidden = false;
  btn.setAttribute('aria-expanded', 'true');
  try {
    /* 先按 CSS 默认摆一次，量出真实位置；真的超出视口就贴左 —— 手机上不溢出。 */
    var r = menu.getBoundingClientRect();
    if (r.left < 8) { menu.style.left = '0'; menu.style.right = 'auto'; }
    else { menu.style.left = ''; menu.style.right = '0'; }
    r = menu.getBoundingClientRect();
    if (r.left < 8) menu.style.marginLeft = (8 - r.left) + 'px';
  } catch (e) { /* 布局量不到（隐藏视图）→ 用 CSS 默认位置 */ }
  return true;
}

/** 文件名：`课表-<那一周的周一>.png` / `.csv`。 */
function ttExportFilename(m, ext) {
  var day = (m && m.weekStart) || isoToday();
  return '课表-' + day + '.' + ext;
}

/* ---- PNG：整张表自己画（canvas 2D，零库） ---- */

/** 中文字体用系统栈（不引任何字体文件 / CDN）。 */
function ttExportFontStack() {
  return '"PingFang SC","Microsoft YaHei",system-ui,sans-serif';
}

/** 供测量用的 2D context（PNG 画布与测量共用同一份字体栈与字号口径）。
 *  **全局复用同一个**：每量一段文字就新建一个 canvas 的话，一张课表要建几十个，
 *  既慢又浪费内存。 */
var ttMeasureCtx = null;
function ttExportMeasureCtx() {
  if (ttMeasureCtx) return ttMeasureCtx;
  try {
    var c = document.createElement('canvas');
    c.width = 1; c.height = 1;
    ttMeasureCtx = c.getContext ? c.getContext('2d') : null;
  } catch (e) { ttMeasureCtx = null; }
  return ttMeasureCtx;
}

/** 一格里一段文字该用多大字号、要不要换行、会不会溢出。
 *  先缩字号（12px 起，最小 10px），再换行；换行也放不下就按「溢出」记下来
 *  —— 导出图的质量因此可以被断言，而不是靠肉眼。 */
function ttExportTextFit(text, maxW, startPx) {
  var t = String(text == null ? '' : text);
  var ctx = ttExportMeasureCtx();
  if (!ctx || !t) return { size: startPx || 12, lines: [], wrap: false, overflow: false };
  var sz = startPx || 12;
  ctx.font = sz + 'px ' + ttExportFontStack();
  if (ctx.measureText(t).width <= maxW) return { size: sz, lines: [t], wrap: false, overflow: false };
  while (sz > 10) {
    sz -= 0.5;
    ctx.font = sz + 'px ' + ttExportFontStack();
    if (ctx.measureText(t).width <= maxW) return { size: sz, lines: [t], wrap: false, overflow: false };
  }
  var lines = ttWrapText(t, maxW, sz + 'px ' + ttExportFontStack());
  var widest = 0;
  lines.forEach(function (ln) { widest = Math.max(widest, ctx.measureText(ln).width); });
  return { size: sz, lines: lines, wrap: true, overflow: widest > maxW + 0.5 };
}

/** 按宽度硬折行（中文没有空格，只能逐字量）。 */
function ttWrapText(text, maxW, font) {
  var ctx = ttExportMeasureCtx();
  var t = String(text == null ? '' : text);
  if (!ctx || !font) return [t];
  ctx.font = font;
  var out = [];
  t.split('\n').forEach(function (para) {
    var line = '';
    for (var i = 0; i < para.length; i++) {
      var ch = para.charAt(i);
      if (line && ctx.measureText(line + ch).width > maxW) { out.push(line); line = ch; }
      else line += ch;
    }
    if (line) out.push(line);
  });
  if (!out.length) out.push(t);
  return out;
}

/** 按 devicePixelRatio 放大画布后自己画一张「行=星期、列=节次」的课表图。
 *  返回 {w, h, cols, rows, fits, overflow} —— w/h 是**放大后的像素值**（验收要贴这两个数）。
 *
 *  **行高按文字实际占几行定**（不是写死一个数）：一格里科目名很长时会被折成好几行，
 *  行高不够就会把字裁掉 —— 所以先用与作画同一套字体测量定行高，再作画。 */
function drawExportPng(m, canvas, dpr) {
  var data = m || ttExportMatrix();
  if (!data) return null;
  var ratio = Number(dpr) > 0 ? Number(dpr) : 1;
  var F = ttExportFontStack();
  var HEAD = 52, WEEK = 34, PAD = 22;
  var widths = data.widths.slice();
  var tableW = widths.reduce(function (a, b) { return a + b; }, 0);
  var cxs = [PAD];
  widths.forEach(function (cw) { cxs.push(cxs[cxs.length - 1] + cw); });
  var right = PAD + tableW;

  /* ---- 第 1 遍：量版面（行高 / 每格文字怎么排）与画布尺寸 ---- */
  var fits = [];
  var rowH = data.rows.map(function (r) {
    var need = 50;
    data.header.forEach(function (_, ci) {
      if (ci < 2) return;
      var cell = (r.cells || [])[ci - 2];
      if (!cell) return;
      String(cell).split('\n').forEach(function (one) {
        var fit = ttExportTextFit(one, data.cellW[ci], 12.5);
        fits.push({ row: r.label, col: ci, size: fit.size, wrap: fit.wrap,
                    overflow: fit.overflow, text: one });
        var hh = fit.lines.length * (fit.size + 5.5) + 16;
        if (hh > need) need = hh;
      });
    });
    return Math.max(50, Math.min(260, Math.round(need)));
  });
  var headH = HEAD + WEEK;
  var bodyH = rowH.reduce(function (a, b) { return a + b; }, 0);
  var w = PAD * 2 + tableW;
  var h = PAD + headH + bodyH + PAD + 20;

  canvas.width = Math.round(w * ratio);
  canvas.height = Math.round(h * ratio);
  if (canvas.style) { canvas.style.width = w + 'px'; canvas.style.height = h + 'px'; }
  var ctx = canvas.getContext ? canvas.getContext('2d') : null;
  if (!ctx) return null;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';

  ctx.fillStyle = '#f1efe7';
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(PAD, PAD, tableW, headH + bodyH);

  /* 标题：与页面上那一行区间**同一份**文字（我的课表 · 2026-09-14 ~ 2026-09-20） */
  ctx.fillStyle = '#1d2b24';
  ctx.font = 'bold 22px ' + F;
  ctx.fillText(data.title, PAD, PAD + 15);

  function line(x, y1, x2, y2, color, lw) {
    ctx.strokeStyle = color || '#d8d3c4';
    ctx.lineWidth = lw || 1;
    ctx.beginPath();
    ctx.moveTo(x + 0.5, y1 + 0.5);
    ctx.lineTo(x2 + 0.5, y2 + 0.5);
    ctx.stroke();
  }

  /* ---- 表头：第 1 列「星期 / 日期」纵向跨两行；之后每列一个节次（名字 + 起止时间） ---- */
  var hy = PAD, hyMid = PAD + headH / 2;
  ctx.fillStyle = '#f6f4ec';
  ctx.fillRect(PAD, PAD, tableW, headH);
  data.header.forEach(function (label, ci) {
    var cx = cxs[ci], cw = widths[ci];
    var f1, f2;
    ctx.textAlign = 'center';
    if (ci === 0) {
      ctx.fillStyle = '#3d4a44';
      ctx.font = 'bold 13px ' + F;
      ctx.fillText('星期', cx + cw / 2, hyMid - 7);
      ctx.fillStyle = '#7b8480';
      ctx.font = '11.5px ' + F;
      ctx.fillText('日期', cx + cw / 2, hyMid + 8);
      f1 = ttExportTextFit('星期', cw - 10, 13);
      f2 = ttExportTextFit('日期', cw - 10, 11.5);
    } else {
      var parts = String(label).split(' ');
      var timeTxt = parts.slice(1).join(' ');      // 没有时段的栏（如「课外」）就只画名字
      ctx.fillStyle = '#2f3b35';
      ctx.font = 'bold 13px ' + F;
      f1 = ttExportTextFit(parts[0] || '', cw - 10, 13);
      ctx.fillText(parts[0] || '', cx + cw / 2, hy + 20);
      ctx.fillStyle = '#7b8480';
      ctx.font = '11.5px ' + F;
      f2 = ttExportTextFit(timeTxt, cw - 10, 11.5);
      ctx.fillText(timeTxt, cx + cw / 2, hy + 40);
    }
    fits.push({ row: '表头', col: ci, size: Math.min(f1.size, f2.size),
                wrap: !!(f1.wrap || f2.wrap), overflow: !!(f1.overflow || f2.overflow),
                text: String(label) });
  });
  line(PAD, hy + headH, right, hy + headH, '#c9c3b2', 1.5);

  /* ---- 数据行：一行 = 一天（周一…周日竖着排下来） ---- */
  var ry = PAD + headH;
  data.rows.forEach(function (r, ri) {
    var rh = rowH[ri];
    var isToday = (r.date === isoToday());
    if (ri % 2 === 1) { ctx.fillStyle = '#fbfaf5'; ctx.fillRect(PAD, ry, tableW, rh); }
    if (isToday) { ctx.fillStyle = '#fff8e2'; ctx.fillRect(PAD, ry, tableW, rh); }
    data.header.forEach(function (_, ci) {
      var cx = cxs[ci], cw = widths[ci];
      ctx.textAlign = 'left';
      if (ci === 0) {
        ctx.fillStyle = isToday ? '#8a6d00' : '#2f3b35';
        ctx.font = 'bold 13px ' + F;
        var fl = ttExportTextFit(r.label, cw - 14, 13);
        ctx.fillText(r.label, cx + 10, ry + rh / 2 - 9);
        ctx.fillStyle = '#7b8480';
        ctx.font = '11.5px ' + F;
        var fd = ttExportTextFit(r.date.slice(5), cw - 14, 11.5);
        ctx.fillText(r.date.slice(5), cx + 10, ry + rh / 2 + 8);
        fits.push({ row: r.label, col: 0, size: Math.min(fl.size, fd.size),
                    wrap: !!(fl.wrap || fd.wrap), overflow: !!(fl.overflow || fd.overflow),
                    text: r.label + ' ' + r.date });
        return;
      }
      if (ci === 1) {
        ctx.fillStyle = '#7b8480';
        ctx.font = '11.5px ' + F;
        ctx.fillText(r.date, cx + 10, ry + rh / 2);
        fits.push({ row: r.label, col: 1, size: 11.5, wrap: false, overflow: false,
                    text: r.date });
        return;
      }
      var txt = (r.cells || [])[ci - 2] || '';
      if (!txt) return;                       // 没课：白底、不写任何东西
      /* 有课：一层很淡的同色底 + 左侧色条（与页面课卡同一套调色板） */
      var pal = ttExportPalette(String(txt.split('\n')[0] || ''));
      ctx.fillStyle = pal.bg;
      ctx.fillRect(cx + 2, ry + 3, cw - 4, rh - 6);
      ctx.fillStyle = pal.fg;
      ctx.fillRect(cx + 2, ry + 3, 3, rh - 6);
      var arr = [];
      String(txt).split('\n').forEach(function (one) {
        var fit = ttExportTextFit(one, data.cellW[ci], 12.5);
        fit.lines.forEach(function (ln) { arr.push({ line: ln, size: fit.size }); });
      });
      var lh = 0;
      arr.forEach(function (o) { lh += o.size + 5.5; });
      var y0 = ry + rh / 2 - lh / 2 + (arr.length ? (arr[0].size + 5.5) / 2 : 0);
      arr.forEach(function (o, i) {
        ctx.fillStyle = pal.fg;
        ctx.font = o.size + 'px ' + F;
        ctx.fillText(o.line, cx + 9, y0 + i * (o.size + 5.5));
      });
    });
    line(PAD, ry, right, ry, '#e4dfd1', 1);
    ry += rh;
  });

  /* 竖线 + 外框 */
  for (var vi = 0; vi <= data.header.length; vi++) {
    line(cxs[vi], PAD, cxs[vi], PAD + headH + bodyH, '#e4dfd1', 1);
  }
  ctx.strokeStyle = '#c9c3b2';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(PAD + 0.5, PAD + 0.5, tableW - 1, headH + bodyH - 1);

  return {
    w: canvas.width, h: canvas.height, cols: data.periods, rows: data.days,
    fits: fits, overflow: fits.filter(function (f) { return f.overflow; }).length,
    logicalW: w, logicalH: h, dpr: ratio, rowH: rowH, colW: widths
  };
}

/** 导出图里课格的配色（照页面的 TT_PALETTE，取淡色底 + 深色字）。 */
function ttExportPalette(key) {
  var k = subjFamily(String(key || '')) + '|';
  var h = 0;
  for (var i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0;
  var pair = TT_PALETTE[h % TT_PALETTE.length];
  return { bg: pair[0], fg: pair[1] };
}

/** PNG 导出：建画布 → 画 → toBlob → <a download>。返回 done(res) 供调用方提示。 */
function ttExportPngFile(done) {
  var data = ttExportMatrix();
  if (!data) { if (done) done({ ok: false, reason: 'empty' }); return; }
  var dpr = 0;
  try { dpr = window.devicePixelRatio || 1; } catch (e) { dpr = 1; }
  if (!(dpr > 0)) dpr = 1;
  var canvas = document.createElement('canvas');
  var geom = drawExportPng(data, canvas, dpr);
  var name = ttExportFilename(data, 'png');
  if (!geom) { if (done) done({ ok: false, reason: 'no-canvas' }); return; }
  var finish = function (blob) {
    var ok = ttTriggerDownload(name, blob);
    if (done) done({ ok: !!ok, name: name, geom: geom, reason: ok ? '' : 'no-blob-url' });
  };
  if (canvas.toBlob) {
    canvas.toBlob(function (blob) {
      if (!blob) { if (done) done({ ok: false, reason: 'no-blob', geom: geom }); return; }
      finish(blob);
    }, 'image/png');
    return;
  }
  /* 极老的浏览器没有 toBlob：退回 dataURL（同样不引任何库） */
  try {
    var url = canvas.toDataURL('image/png');
    var m = /^data:([^;,]+);base64,(.*)$/.exec(url);
    var bin = (typeof atob === 'function') ? atob(m ? m[2] : '') : '';
    var arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    finish(new Blob([arr], { type: (m && m[1]) || 'image/png' }));
  } catch (e) { if (done) done({ ok: false, reason: 'no-blob' }); }
}

/** CSV 导出：UTF-8 带 BOM 的 .csv 文件，文件名 `课表-<周一>.csv`。 */
function ttExportCsvFile(done) {
  var data = ttExportMatrix();
  if (!data) { if (done) done({ ok: false, reason: 'empty' }); return; }
  var text = ttExportCsvText(data);
  var name = ttExportFilename(data, 'csv');
  var ok = false;
  try {
    ok = ttTriggerDownload(name, new Blob([text], { type: 'text/csv;charset=utf-8' }));
  } catch (e) { ok = false; }
  if (done) done({ ok: ok, name: name, bytes: text.length, reason: ok ? '' : 'no-blob-url' });
}

/** 菜单里点一项 → 生成 + 下载 + toast（含文件名）。 */
function ttExportSelect(kind) {
  var label = (kind === 'png') ? '图片 PNG' : '表格 CSV';
  if (!updateTtExportState()) {
    toast('课表还没加载完，稍等一下再导出（数据一到就能导出）', 4200);
    return false;
  }
  ttCloseExportMenu();
  var done = function (res) {
    if (res && res.ok) toast('已导出' + label + '：' + res.name, 4200);
    else toast('导出' + label + '失败：' + ((res && res.reason) || '浏览器不支持'), 4600);
  };
  if (typeof window === 'undefined' || !window.Blob) { done({ ok: false, reason: 'no-blob' }); return false; }
  if (kind === 'png') ttExportPngFile(done); else ttExportCsvFile(done);
  return true;
}

/* ------------------------- 选课（settings.lessons，同步对象） ------------------------- */

/** 从几个候选位置里取第一个「非空数组」。 */
function arrAt(doc, keys) {
  if (!isObj(doc)) return null;
  for (var i = 0; i < keys.length; i++) {
    var v = doc[keys[i]];
    if (Array.isArray(v) && v.length) return v;
  }
  return null;
}

/** lessons 文档 → 一组 {name, fields:[{k,v}]}。
 *  三种真实形状都吃：
 *    ① `{groups:["数学 A 组", …]}`（生产 settings.lessons 就是这么存的 → 每个组名一张卡）
 *    ② `[ {subject,group,…}, … ]`  ③ `{组名: {…}}` */
function lessonGroups(doc) {
  var out = [];
  if (isObj(doc) && Array.isArray(doc.groups) && doc.groups.length) {
    // 文档级的元信息对每个教学组都一样，逐卡重复显示没意义 → 不带进卡片
    var META = { updated_at: 1, updatedAt: 1, synced_at: 1, syncedAt: 1, fetched_at: 1,
                 version: 1, kind: 1, app: 1, lastId: 1 };
    var rest = [];
    Object.keys(doc).forEach(function (k) {
      if (k === 'groups' || META[k]) return;
      var v = doc[k];
      if (typeof v === 'string' || typeof v === 'number') rest.push({ k: k, v: cellText(v) });
    });
    var names = [];
    doc.groups.forEach(function (g) {
      if (isObj(g)) {
        var nm = g.group || g.name || g.title || g.subject;
        if (nm && names.indexOf(String(nm)) === -1) names.push(String(nm));
      } else if (g != null && String(g) !== '' && names.indexOf(String(g)) === -1) {
        names.push(String(g));
      }
    });
    return names.map(function (nm) { return { name: nm, fields: rest.slice() }; });
  }
  /* 真实的 settings.lessons 形态：`{lessons:[{subject,group,teacher}, …]}`
     —— 每条选课=「一门课 + 它被分到的教学组」，按组名聚合成一张卡。 */
  if (isObj(doc) && Array.isArray(doc.lessons) && doc.lessons.length) {
    var order = [];
    var byKey = {};
    doc.lessons.forEach(function (row) {
      if (!isObj(row)) return;
      var gname = cellText(row.group || row.groups || row.name || row.title) || '（未标组）';
      var key = groupKey(gname) || gname;
      if (!byKey[key]) { byKey[key] = { name: gname, fields: [] }; order.push(key); }
      var bits = [];
      var subj = cellText(row.subject || row.course || row.lesson);
      if (subj) bits.push({ k: '科目', v: subj });
      var teacher = cellText(row.teacher || row.teachers);
      if (teacher) bits.push({ k: '老师', v: teacher });
      var note = cellText(row.note || row.room);
      if (note) bits.push({ k: '备注', v: note });
      if (bits.length) byKey[key].fields.push(bits);
    });
    order.forEach(function (key) {
      var entry = byKey[key];
      out.push({
        name: entry.name,
        fields: entry.fields.map(function (bits) {
          return { k: bits[0].v, v: bits.slice(1).map(function (b) { return b.k + ' ' + b.v; }).join(' · ')
                     || bits[0].k };
        })
      });
    });
    return out;
  }
  var list = Array.isArray(doc) ? doc : arrAt(doc, ['items', 'selections', 'subjects']);
  if (list) {
    list.forEach(function (item, i) {
      var fields = [], name = '';
      if (isObj(item)) {
        name = item.group || item.name || item.title || item.subject || item.id || ('第 ' + (i + 1) + ' 项');
        Object.keys(item).forEach(function (k) {
          if (k === 'group' || k === 'name' || k === 'title') return;
          var v = item[k];
          if (isObj(v)) return;
          fields.push({ k: k, v: cellText(v) });
        });
        Object.keys(item).forEach(function (k) {
          var v = item[k];
          if (!isObj(v)) return;
          fields.push({ k: k, v: cellText(v) });
        });
      } else {
        name = cellText(item);
      }
      out.push({ name: String(name), fields: fields });
    });
    return out;
  }
  if (isObj(doc)) {
    Object.keys(doc).forEach(function (gname) {
      var g = doc[gname];
      var fields = [];
      if (isObj(g)) {
        Object.keys(g).forEach(function (k) { fields.push({ k: k, v: cellText(g[k]) }); });
      } else if (Array.isArray(g)) {
        fields.push({ k: '成员', v: cellText(g) });
      } else if (cellText(g) !== '') {
        fields.push({ k: gname, v: cellText(g) });
      }
      out.push({ name: gname, fields: fields });
    });
  }
  return out;
}

/** 旧的选课卡片渲染器已删除（照 Pinghe Launcher Lite 样式重做为按科目分组的选课界面）。
 *  这个函数保留为 no-op，避免调用方报错。 */
function renderLessons(panel) {
  // no-op: 旧的选课卡片已删除
}

/* ---------------------- 网页端选课（写自己的 settings.lessons）---------------------- */

/** 勾选面板里的候选教学组：接口回的 edupage.selected（该账号实测是全 20 个课程组）
 *  + 课表里实际出现的组，归一化去重后按名称排序。 */
function candidateGroups() {
  var out = uniqGroupNames(liveSelected().concat(lessonGroupNames(st.lessons && st.lessons.doc)));
  (st.ttLessons || []).forEach(function (l) {
    var g = lessonGroupText(l);
    if (g) out = uniqGroupNames(out.concat([g]));
  });
  return out.sort(function (a, b) { return String(a).localeCompare(String(b), 'zh'); });
}

var lessonDraft = null;        // 旧面板的草稿变量（**已不再用于渲染**：见 toggleGroupPicker）
var lessonPickHost = null;     // 旧变量，同上

/** 打开「选择我的教学组」面板（按科目分组，照 Pinghe Launcher Lite 样式）。
 *  **2026-09-16 起这个函数只做转发**：选课界面合成了一条共享渲染路径 + 一个模态
 *  （`renderSubjectPickerInto` / `openPickerModal`），
 *  以前那套「卡片内嵌面板用 `#lesson-list` / `toggleGroupPicker` 自建」的重复实现
 *  与模态的 `#picker-list` 是两份代码，正是「模态里什么都没有」这条 bug 的根源。
 *  这个函数名保留，是为了不惊动既有调用方（含测试）。 */
function toggleGroupPicker() {
  openPickerModal();
}

/** 旧接口：勾/取消一个教学组（只改草稿，点保存才写云端）。
 *  现在选课界面的勾选由浏览器原生 checkbox + `collectPickerSelection` 负责，
 *  这里保留名字给既有测试/脚本用，行为不变（`lessonDraft` 为空时是 no-op）。 */
function toggleDraftGroup(name) {
  if (!lessonDraft) return;
  var key = groupKey(name);
  var idx = -1;
  lessonDraft.forEach(function (g, i) { if (groupKey(g) === key) idx = i; });
  if (idx >= 0) lessonDraft.splice(idx, 1); else lessonDraft.push(name);
  var chips = document.querySelectorAll('#lesson-chips .chip');
  for (var i = 0; i < chips.length; i++) {
    var on = groupKey(chips[i].getAttribute('data-group')) === key ? idx < 0
                               : chips[i].classList.contains('on');
    chips[i].classList.toggle('on', on);
    chips[i].setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

/** 保存草稿到同步对象 settings.lessons（与日程写回同一条路径：base_revision + 409 重放一次）。
 *  保存格式：{lessons: [{subject, teacher, group}]} —— 与客户端认的同一形态。
 *  同时派生 groups 字母表（兼容旧逻辑）。
 *
 *  **2026-09-16 起走共享写回路径**（`savePickerSelection`）：以前这里自己复制了一份
 *  写回逻辑，与模态那份各写各的 —— 一个 bug 要在两处修。现在选课只有一个保存实现。 */
function saveLessonGroups() {
  if (!lessonDraft) return;
  savePickerSelection({ btnId: 'lessons-save', label: '保存选课' });
}

/* =========================== ③ 我的日程（周 / 月 / 年日历） ===========================
 * 版式照客户端 Pinghe Launcher Lite（`ui/index.html#view-schedule` + `ui/styles.css`
 * 的 `.sch-week / .sch-day / .cal / .cal-cell / .cal-head / .cal-year / .cal-mini /
 * .mini-grid`）：一周七列 · 日期格 · 格里的事件条 · 「还有 N 项…」· 年视图 12 个月缩略。
 *
 * 与客户端的差异（都记在这里，改的时候别当成 bug）：
 *  ① 默认视图是**周**（客户端是月）——用户要求「默认落在周视图」；
 *  ② 客户端的日程来自本地 SQLite 的 `schedule_range(from, to)`，网页端只有云端同步对象
 *     `schedule`（一次拿到全部事件），所以这里是**全量读入 + 本地分桶**：
 *     切换视图/翻页都是纯前端重渲染，不再发请求（新增/删除后重读一次）；
 *  ③ 客户端点某天只弹「当天安排」卡片、新增在卡片里；网页端卡片保留删除与「在这一天新增」
 *     （跳到底部的既有表单，写回契约不变：base_revision + 409 重放 + MineBy 权限口径）；
 *  ④ 域名换成网页端自己的 token（--green-* / --gold-* / --ivory-*），不引任何日历库。
 * ========================================================================== */

var WD_MON = ['一', '二', '三', '四', '五', '六', '日'];
/* 选中日期在切视图时保留（从月切到周要落在同一周），由 schFocus/ 日历键统一记录。 */
var schState = { view: 'week', anchor: null, focus: null, events: [] };

function scheduleEvents(doc) {
  if (isObj(doc) && Array.isArray(doc.events)) return doc.events;
  return [];
}

function schYmd(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }

function schToday() {
  var n = new Date();
  return new Date(n.getFullYear(), n.getMonth(), n.getDate());
}

/** 周一为一周的第一天（与课表口径一致）。 */
function schMonday(d) {
  var t = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  t.setDate(t.getDate() - ((t.getDay() + 6) % 7));
  return t;
}

function schAddDays(d, n) {
  var t = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  t.setDate(t.getDate() + n);
  return t;
}

/** 锁定到某个月 1 号，避免 `setMonth` 在 31 号上跳到下下个月。 */
function schAddMonths(d, n) { return new Date(d.getFullYear(), d.getMonth() + n, 1); }

/** ISO 周号（周一开头，含 1 月 4 日的那一周是第 1 周）。 */
function schIsoWeek(d) {
  var t = schMonday(d);
  t.setDate(t.getDate() + 3);                            // 到本周的周四
  var jan4 = new Date(t.getFullYear(), 0, 4);
  var w1 = schMonday(jan4);
  return Math.round((t - w1) / 604800000) + 1;
}

/** 'YYYY-MM-DD' → Date（解析不出来给 null，别静默当今天）。 */
function schParse(iso) {
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/** 当前区间：周 = 本周一到周日；月 = 当月 1 号到月末；年 = 当年 1/1~12/31。 */
function schRange(view, anchor) {
  var y = anchor.getFullYear(), m = anchor.getMonth();
  if (view === 'week') {
    var mon = schMonday(anchor);
    return [schYmd(mon), schYmd(schAddDays(mon, 6))];
  }
  if (view === 'month') {
    return [y + '-' + pad2(m + 1) + '-01', schYmd(new Date(y, m + 1, 0))];
  }
  return [y + '-01-01', y + '-12-31'];
}

/** 顶部当前区间文案：「2026 年第 38 周（09-14 ~ 09-20）」这种一眼能看懂的写法。 */
function schLabelText(view, anchor) {
  var from = schRange(view, anchor)[0];
  if (view === 'week') {
    var mon = schParse(from);
    return mon.getFullYear() + ' 年第 ' + schIsoWeek(mon) + ' 周（' +
      from.slice(5) + ' ~ ' + schYmd(schAddDays(mon, 6)).slice(5) + '）';
  }
  if (view === 'month') return anchor.getFullYear() + ' 年 ' + (anchor.getMonth() + 1) + ' 月';
  return anchor.getFullYear() + ' 年';
}

/** 按天分桶（一次遍历，周/月/年三个视图共用）。 */
function schByDay(events) {
  var map = {};
  (events || []).forEach(function (e) {
    var day = e && e.day ? String(e.day) : '';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;        // 日期不合法的条目不上日历（首页仍会显示）
    (map[day] = map[day] || []).push(e);
  });
  Object.keys(map).forEach(function (k) {
    map[k].sort(function (a, b) {
      return String((a && a.time) || '~').localeCompare(String((b && b.time) || '~'));
    });
  });
  return map;
}

function schEventsOf(byDay, iso) { return byDay[iso] || []; }

/** 日期格的状态类：今天 / 选中（切视图保留的聚焦日期）。 */
function schCellCls(iso, extra) {
  var cls = extra || '';
  if (iso === schYmd(schToday())) cls += ' today';
  if (iso === schState.focus) cls += ' is-sel';
  if (iso === schYmd(schState.anchor)) cls += ' is-anchor';
  return cls;
}

/** 事件条（周/月格里那一小行）。 */
function schEvChip(e) {
  /* 文案用一个 textContent 拼出来（不是两个子节点）：innerText 在两个子元素之间会插换行，
     「08:30 数学月考」这种一行文案不该在别处被读成两行。 */
  var chip = el('span', 'cal-ev',
    (e && e.time ? String(e.time) : '全天') + ' ' + ((e && e.title) ? String(e.title) : '（无标题）'));
  chip.title = (e && e.time ? String(e.time) + ' ' : '全天 ') +
    ((e && e.title) || '（无标题）') + ((e && e.note) ? ' · ' + String(e.note) : '');
  return chip;
}

/** 日期格（周视图的 .sch-day 与月视图的 .cal-cell 都是这一套内容）。 */
function schDayCard(iso, byDay, opts) {
  var o = opts || {};
  var day = schParse(iso);
  var evs = schEventsOf(byDay, iso);
  var card = el('div', (o.className || 'cal-cell') + schCellCls(iso));
  card.setAttribute('data-day', iso);
  if (o.weekday !== false) {
    card.appendChild(el('span', 'cal-num', String(day.getDate())));
    card.appendChild(el('span', 'cal-wd', (o.weekHead ? o.weekHead + ' ' : '周' + WD_MON[(day.getDay() + 6) % 7]) +
      ' · ' + iso.slice(5)));
  } else {
    card.appendChild(el('span', 'cal-num', String(day.getDate())));
  }
  if (!evs.length) {
    if (o.emptyNote) card.appendChild(el('span', 'cal-none', '无安排'));
  } else {
    var max = o.max || 2;
    evs.slice(0, max).forEach(function (e) { card.appendChild(schEvChip(e)); });
    if (evs.length > max) {
      card.appendChild(el('span', 'cal-more', '还有 ' + (evs.length - max) + ' 项…'));
    }
  }
  card.tabIndex = 0;
  card.title = prettyDay(iso) + ' · ' + (evs.length ? evs.length + ' 项安排' : '无安排') + '（点开看当天）';
  return card;
}

/** 兼容层：屏外按天分组的条目（语义与旧版列表一致，视觉上不占位）。 */
function schCompat(events) {
  var box = $('sched-compat');
  if (!box) return;
  clear(box);
  var byDay = schByDay(events);
  var days = Object.keys(byDay).sort();
  days.forEach(function (day) {
    var evs = byDay[day];
    box.appendChild(el('div', 'day-group__title', prettyDay(day) + ' · ' + evs.length + ' 条'));
    evs.forEach(function (e) {
      var row = el('div', 'evt');
      row.appendChild(el('span', 'evt__time', e && e.time ? String(e.time) : '全天'));
      var main = el('div', 'evt__main');
      main.appendChild(el('div', 'evt__title', (e && e.title) ? String(e.title) : '（无标题）'));
      if (e && e.note) main.appendChild(el('div', 'evt__note', String(e.note)));
      row.appendChild(main);
      box.appendChild(row);
    });
  });
}

/** 空态：日历照画（用户能直接点某天记一笔），顶部一句「还没有日程」保留可发现性。 */
function schHint(events, mode) {
  if (events.length) return null;
  var p = el('p', 'empty');
  p.appendChild(document.createTextNode('还没有日程。'));
  p.appendChild(el('strong', null, mode === 'error' ? '去设置看看同步状态。' : '点日历上任意一天记一笔。'));
  return p;
}

/* ---- 周视图：一周七列日期卡（照客户端 .sch-week / .sch-day） ---- */
function schRenderWeek(byDay) {
  var host = $('sch-week');
  if (!host) return;
  clear(host);
  var mon = schMonday(schState.anchor);
  for (var i = 0; i < 7; i++) {
    var iso = schYmd(schAddDays(mon, i));
    host.appendChild(schDayCard(iso, byDay,
      { className: 'sch-day', weekday: false, weekHead: '周' + WD_MON[i], emptyNote: true }));
  }
}

/* ---- 月视图：整月网格，周一为第一列，上下月补位（照客户端 `.cal`） ---- */
function schRenderMonth(byDay) {
  var host = $('sch-month');
  if (!host) return;
  clear(host);
  WD_MON.forEach(function (w) { host.appendChild(el('div', 'cal-head', '周' + w)); });
  var y = schState.anchor.getFullYear(), m = schState.anchor.getMonth();
  var first = new Date(y, m, 1);
  var lead = (first.getDay() + 6) % 7;                   // 周一开头
  var nDays = new Date(y, m + 1, 0).getDate();
  var i, iso, d;
  for (i = 0; i < lead; i++) {                           // 上个月补位
    d = new Date(y, m, 1 - (lead - i));
    iso = schYmd(d);
    host.appendChild(schDayCard(iso, byDay, { className: 'cal-cell is-out', weekday: false, max: 1 }));
  }
  for (i = 1; i <= nDays; i++) {
    iso = y + '-' + pad2(m + 1) + '-' + pad2(i);
    host.appendChild(schDayCard(iso, byDay, { className: 'cal-cell', weekday: false, max: 2 }));
  }
  var tail = (7 - ((lead + nDays) % 7)) % 7;             // 下个月补位，凑满整周
  for (i = 1; i <= tail; i++) {
    iso = schYmd(new Date(y, m + 1, i));
    host.appendChild(schDayCard(iso, byDay, { className: 'cal-cell is-out', weekday: false, max: 1 }));
  }
}

/* ---- 年视图：12 个月小月历缩略（照客户端 `.cal-year` / `.cal-mini` / `.mini-grid`） ---- */
function schRenderYear(byDay) {
  var host = $('sch-year');
  if (!host) return;
  clear(host);
  var y = schState.anchor.getFullYear();
  var todayIso = schYmd(schToday());
  for (var m = 0; m < 12; m++) {
    var mini = el('div', 'cal-mini');
    var head = el('button', 'mini-head', (m + 1) + ' 月');
    head.type = 'button';
    head.setAttribute('data-month', String(m + 1));
    head.title = '跳到 ' + y + ' 年 ' + (m + 1) + ' 月的月视图';
    mini.appendChild(head);
    var grid = el('div', 'mini-grid');
    WD_MON.forEach(function (w) { grid.appendChild(el('i', 'mini-wd', w)); });
    var first = new Date(y, m, 1);
    var lead = (first.getDay() + 6) % 7;
    var nDays = new Date(y, m + 1, 0).getDate();
    var i, iso, cell, has, dots;
    for (i = 0; i < lead; i++) grid.appendChild(el('i', 'is-empty'));
    for (i = 1; i <= nDays; i++) {
      iso = y + '-' + pad2(m + 1) + '-' + pad2(i);
      has = schEventsOf(byDay, iso);
      cell = el('i', 'mini-day' + (has.length ? ' has' : '') +
        (iso === todayIso ? ' today' : '') + (iso === schState.focus ? ' is-sel' : ''));
      cell.setAttribute('data-day', iso);
      cell.textContent = String(i);
      cell.title = prettyDay(iso) + ' · ' + (has.length ? has.length + ' 项安排' : '无安排');
      dots = el('u', 'mini-dots');
      var n = Math.min(has.length, 3);
      for (var k = 0; k < n; k++) dots.appendChild(el('s', 'mini-dot'));
      if (n) cell.appendChild(dots);
      grid.appendChild(cell);
    }
    mini.appendChild(grid);
    host.appendChild(mini);
  }
}

/** 顶部右侧那行状态：「共 N 条 · 云端第 R 版 · 当前视图」（拿不到云端版本就留空，不写假数字）。 */
function schMetaText() {
  if (!(st.schedule && st.schedule.parsed)) return '';
  var n = schState.events.length;
  return (n ? '共 ' + n + ' 条 · 云端第 ' + st.schedule.revision + ' 版'
            : '云端第 ' + st.schedule.revision + ' 版') +
    ' · ' + (schState.view === 'week' ? '周视图' : (schState.view === 'month' ? '月视图' : '年视图'));
}

/** 按当前视图渲染（三个视图的宿主节点同步显隐）。 */
function schRenderAll() {
  var byDay = schByDay(schState.events);
  ['week', 'month', 'year'].forEach(function (v) {
    var host = $('sch-' + v);
    if (host) show(host, v === schState.view);
  });
  if (schState.view === 'week') schRenderWeek(byDay);
  else if (schState.view === 'month') schRenderMonth(byDay);
  else schRenderYear(byDay);
  var label = $('sched-label');
  if (label) label.textContent = schLabelText(schState.view, schState.anchor);
  var meta = $('sched-meta');
  if (meta) meta.textContent = schMetaText();
  var wrap = $('sched-views');
  if (wrap) {
    Array.prototype.forEach.call(wrap.querySelectorAll('.seg__btn'), function (b) {
      var on = b.getAttribute('data-v') === schState.view;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.disabled = on;
    });
  }
  document.querySelectorAll('#view-schedule .is-sel').forEach(function (n) {
    n.setAttribute('aria-current', 'date');
  });
}

/** created 里是否出现当前用户名（删除权限判定，按契约：只删自己写的）。 */
function mineBy(created) {
  return !!st.me.username && String(created || '').indexOf(st.me.username) !== -1;
}

function schedRowBox() {
  var form = $('sched-form');
  if (!form || !form.parentNode) return null;
  var box = el('div', 'panel__body');
  form.parentNode.insertBefore(box, form);
  return box;
}

/** 状态行（读取中 / 非法 JSON / 加载失败）：日历照画，只把这句话摆到日历上方一行。 */
function schMsg(text, cls) {
  var hint = $('sched-hint');
  if (!hint) return;
  clear(hint);
  if (!text) { hint.hidden = true; return; }
  hint.hidden = false;
  hint.appendChild(el('p', cls || 'empty', text));
}

/** 渲染日程视图：**纯前端**（数据已经在 st.schedule 里），切视图/翻页不重新发请求。 */
function renderSchedule() {
  var box = $('sched-body');
  if (!box) return;
  var meta = $('sched-meta');
  if (meta) meta.textContent = '';

  if (!schState.anchor) {
    schState.anchor = schToday();
    schState.focus = schYmd(schState.anchor);
  }

  var ref = st.schedule;
  schState.events = [];
  if (!ref) {
    schMsg('正在读取日程…', 'muted');
  } else if (!ref.parsed) {
    schMsg('云端日程数据不是合法 JSON，已按只读处理（本次不写回）。', 'empty');
  } else {
    schState.events = scheduleEvents(ref.doc);
    if (meta) {
      meta.textContent = (schState.events.length
        ? '共 ' + schState.events.length + ' 条 · 云端第 ' + ref.revision + ' 版'
        : '云端第 ' + ref.revision + ' 版') + ' · ' + (schState.view === 'week' ? '周视图'
        : (schState.view === 'month' ? '月视图' : '年视图'));
    }
    schMsg('');
  }
  schCompat(schState.events);
  schRenderAll();
  /* 空态提示放在日历**上方**：既留住「还没有日程」这句可发现性文案，又让人能直接点某天记一笔。 */
  if (!schState.events.length && ref && ref.parsed) {
    var hint = schHint(schState.events, 'empty');
    var hintBox = $('sched-hint');
    if (hint && hintBox) { clear(hintBox); hintBox.hidden = false; hintBox.appendChild(hint); }
  }
  if (!$('sch-modal').hidden) schFillDay();
}

function loadSchedule() {
  return getObject(OBJ.schedule).then(function (ref) {
    st.schedule = ref;
    renderSchedule();
  })['catch'](function (err) {
    if (err && err.unauthorized) return;
    st.schedule = null;
    schMsg(err.message || '加载失败', 'empty');
    if ($('sched-meta')) $('sched-meta').textContent = '';
  });
}

/** 新增：事件对象先算好；写回时若冲突，change 基于最新 doc 重算 id 与 lastId。 */
function addScheduleEvent(item) {
  if (st.schedule && !st.schedule.parsed) { toast('云端数据无法解析，已停止写入以免覆盖'); return Promise.resolve(); }
  return putObject(OBJ.schedule, st.schedule, function (doc) {
    var base;
    if (isObj(doc)) {
      base = JSON.parse(JSON.stringify(doc));               // 保留未知字段原样写回
    } else {
      base = { version: 1, kind: 'pinghe-schedule', app: 'PHL Web' };
    }
    if (!Array.isArray(base.events)) base.events = [];
    var maxId = Number(base.lastId) || 0;
    base.events.forEach(function (e) {
      var n = Number(e && e.id);
      if (isFinite(n) && n > maxId) maxId = n;
    });
    var id = maxId + 1;
    base.lastId = id;                                        // 客户端高水位字段，id 不复用
    base.updated_at = nowIso();
    base.events.push({
      id: id,
      day: item.day,
      time: item.time,
      title: item.title,
      note: item.note,
      created: nowIso() + ' (' + st.me.username + ')'       // 带上当前用户名 = 删除权限凭据
    });
    return base;
  }).then(function () { renderSchedule(); });
}

function delScheduleEvent(id, btn) {
  if (st.schedule && !st.schedule.parsed) { toast('云端数据无法解析，已停止写入以免覆盖'); return; }
  if (btn) { btn.disabled = true; btn.textContent = '删除中…'; }
  putObject(OBJ.schedule, st.schedule, function (doc) {
    var base = isObj(doc) ? JSON.parse(JSON.stringify(doc)) : { version: 1, kind: 'pinghe-schedule', events: [] };
    if (!Array.isArray(base.events)) base.events = [];
    base.events = base.events.filter(function (e) { return String(e && e.id) !== String(id); });
    base.updated_at = nowIso();
    return base;
  }).then(function () {
    toast('已删除');
    renderSchedule();
  })['catch'](function (err) {
    if (err && err.unauthorized) return;
    toast(err.message || '删除失败');
    if (btn) { btn.disabled = false; btn.textContent = '删除'; }
    return loadSchedule();                                   // 失败后回到云端真值
  });
}

/* ---- 当天安排（点日历任意一天弹出）：看 / 删 / 在这一天新增 ---- */

function schDayModalOpen() { return !$('sch-modal').hidden; }

function schFillDay() {
  if (!schState.focus) return;
  var box = $('schm-list');
  if (!box) return;
  clear(box);
  var t = $('schm-title');
  if (t) t.textContent = prettyDay(schState.focus) + ' · 当天安排';
  var day = schParse(schState.focus);
  var evs = schState.events.filter(function (e) {
    return e && String(e.day || '') === schState.focus;
  }).sort(function (a, b) {
    return String(a.time || '~').localeCompare(String(b.time || '~'));
  });
  if (!evs.length) {
    box.appendChild(el('p', 'empty', prettyDay(schState.focus) + ' 还没有安排（点下面「在这一天新增日程」记一笔）。'));
    return;
  }
  if (day) {
    box.appendChild(el('p', 'muted small',
      '周' + WD_MON[(day.getDay() + 6) % 7] + ' · ' + evs.length + ' 项'));
  }
  evs.forEach(function (e) {
    var row = el('div', 'evt');
    row.appendChild(el('span', 'evt__time', e.time ? String(e.time) : '全天'));
    var main = el('div', 'evt__main');
    main.appendChild(el('div', 'evt__title', e.title ? String(e.title) : '（无标题）'));
    if (e.note) main.appendChild(el('div', 'evt__note', String(e.note)));
    if (e.created) main.appendChild(el('div', 'evt__by', '创建于 ' + prettyStamp(e.created)));
    row.appendChild(main);
    if (mineBy(e.created)) {
      var del = el('button', 'ghost evt__del', '删除');
      del.type = 'button';
      del.setAttribute('data-del', String(e.id));
      del.title = '删除「' + (e.title || e.id) + '」（云端 ' + (st.schedule ? st.schedule.revision : '') + ' 版）';
      row.appendChild(del);
    } else {
      var lock = el('span', 'evt__lock', '他人创建');
      lock.title = '只能删除自己创建的日程（created 不含当前用户名）';
      row.appendChild(lock);
    }
    box.appendChild(row);
  });
}

function schOpenDay(iso) {
  if (!schParse(iso)) return;
  schState.focus = iso;
  schRenderAll();                       // 先把选中态画到日历上，再弹卡片
  schFillDay();
  show($('sch-modal'), true);
}

function schCloseDay() { show($('sch-modal'), false); }

/** 翻页：周 ±7 天 / 月 ±1 月 / 年 ±1 年；聚焦日期跟着 anchor 走（切视图不跳区间）。 */
function schShift(delta) {
  if (schState.view === 'week') schState.anchor = schAddDays(schState.anchor, 7 * delta);
  else if (schState.view === 'month') schState.anchor = schAddMonths(schState.anchor, delta);
  else schState.anchor = new Date(schState.anchor.getFullYear() + delta, schState.anchor.getMonth(), 1);
  schState.focus = schYmd(schState.anchor);
  schRenderAll();
  if (schDayModalOpen()) schFillDay();
}

/** 切视图：**保留当前聚焦日期**（月切到周落在同一周）；纯前端重渲染，不发请求。 */
function schSetView(v) {
  if (['week', 'month', 'year'].indexOf(v) === -1) return;
  schState.view = v;
  schRenderAll();          // 状态行（共 N 条 · 云端第 R 版 · 当前视图）由 schRenderAll 一起更新
}

/** 年视图点某月 / 月视图点补位日：跳到那个区间（聚焦日期一起带过去）。 */
function schGotoMonth(m1) {
  var y = schState.anchor.getFullYear();
  var day = schParse(schState.focus);
  var dd = (day && day.getMonth() === m1 - 1) ? day.getDate() : 1;
  schState.focus = y + '-' + pad2(m1) + '-' + pad2(dd);
  schState.anchor = new Date(y, m1 - 1, dd);
  schSetView('month');
}

function schGotoDay(iso) {
  var d = schParse(iso);
  if (!d) return;
  schState.focus = iso;
  schState.anchor = d;
  schRenderAll();
}

function bindScheduleCalendar() {
  var prev = $('sched-prev'), next = $('sched-next'),
      today = $('sched-today'), views = $('sched-views');
  if (prev) prev.addEventListener('click', function () { schShift(-1); });
  if (next) next.addEventListener('click', function () { schShift(1); });
  if (today) {
    today.addEventListener('click', function () {
      schState.anchor = schToday();
      schState.focus = schYmd(schState.anchor);
      schRenderAll();
      if (schDayModalOpen()) schFillDay();
    });
  }
  if (views) {
    views.addEventListener('click', function (ev) {
      var b = ev.target && ev.target.closest ? ev.target.closest('.seg__btn') : null;
      if (b) schSetView(b.getAttribute('data-v'));
    });
  }
  var host = $('sched-body');
  if (host) {
    host.addEventListener('click', function (ev) {
      var t = ev.target;
      var head = t && t.closest ? t.closest('.mini-head') : null;
      if (head) { schGotoMonth(Number(head.getAttribute('data-month'))); return; }
      var cell = t && t.closest ? t.closest('[data-day]') : null;
      if (!cell) return;
      var iso = cell.getAttribute('data-day');
      if (cell.classList.contains('is-out')) { schGotoDay(iso); return; }   // 月视图补位日 → 跳到那个月
      schOpenDay(iso);
    });
  }
  var close = $('schm-close');
  if (close) close.addEventListener('click', schCloseDay);
  var modal = $('sch-modal');
  if (modal) {
    modal.addEventListener('click', function (ev) { if (ev.target === modal) schCloseDay(); });
  }
  /* 当天卡片里的「删除」走**事件委托**（卡片每一帧都是重画的，逐行绑会随重画丢监听）。
     权限口径不变：只有 mineBy(created) 为真的条目才画得出这个按钮。 */
  var dayList = $('schm-list');
  if (dayList) {
    dayList.addEventListener('click', function (ev) {
      var b = ev.target && ev.target.closest ? ev.target.closest('[data-del]') : null;
      if (!b) return;
      ev.stopPropagation();
      delScheduleEvent(b.getAttribute('data-del'), b);
    });
  }
  var add = $('schm-add');
  if (add) {
    add.addEventListener('click', function () {
      schCloseDay();
      var opener = $('sched-open') || $('sched-open2');
      if (opener) opener.click();                       // 复用既有「新建日程」表单（含权限与写回契约）
      var dayIn = $('f-day');
      if (dayIn) dayIn.value = schState.focus || todayStr();
      var titleIn = $('f-title');
      if (titleIn) titleIn.focus();
    });
  }
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && schDayModalOpen()) schCloseDay();
  });
}

/* 说明：「我的成绩」视图已按用户要求**整个移除**（导航项 / 视图 DOM / 渲染函数 / 专属 CSS）。
   `managebac.courses` 的 `grade` 数据源没有删——课程页（「我的课程」）仍在课程列表里显示各科总评。 */

/* =========================== ④ 我的课程 =========================== */
/* 版式 1:1 复制 客户端（Pinghe Launcher Lite）的「我的课程」：顶部 CAS/EE 两张 IB Core 卡（.card.core-card）+
   「作业 / 考试（含已过期）」与「课程列表」两张卡；课程行 = .item.course-row
   （课程名 + 「总评 … · 点击看课程详情」+ .badge），作业行 = .item.ddl-item
   （截止时间 + 标题 · 课程 + .badge 状态，已过期灰底 .past-due）。
   我们比 客户端多两条工具（搜索 / 排序下拉）——那是可用性要求，行为与排序语义保持一致。 */

/** 总评徽标：有分数用绿色，没出分用灰金（客户端的 badge() 语义）。 */
function mbBadge(text, cls) {
  return el('span', 'badge' + (cls ? ' ' + cls : ''), text);
}

/** ▲▼ 调顺序按钮（客户端的 .move-btns / .move-btn 组件）。 */
function moveBtns(onMove) {
  var box = el('span', 'move-btns');
  [['up', '▲', -1], ['down', '▼', 1]].forEach(function (spec) {
    var b = el('button', 'move-btn', spec[1]);
    b.type = 'button';
    b.setAttribute('data-move', spec[0]);
    b.title = spec[0] === 'up' ? '上移' : '下移';
    b.addEventListener('click', function (ev) {
      ev.stopPropagation();
      onMove(spec[2]);
    });
    box.appendChild(b);
  });
  return box;
}

/* 顺序调整只影响**本页显示**（与 客户端的本地排序不同，这里不写回云端：
   网页端对选课/课程顺序没有写权限，写回会被服务端按只读拒绝）。 */
var coOrder = [];       // 课程名顺序（用户用 ▲▼ 调整后的）
var taskOrder = [];     // 作业 key 顺序
var mbSortLast = '';    // 上一次的排序方式（换排序就放弃手动顺序）

function coOrderApply(courses) {
  var names = courses.map(function (c) { return String(c.name); });
  coOrder = coOrder.filter(function (n) { return names.indexOf(n) !== -1; });
  names.forEach(function (n) { if (coOrder.indexOf(n) === -1) coOrder.push(n); });
  return courses.slice().sort(function (a, b) {
    return coOrder.indexOf(String(a.name)) - coOrder.indexOf(String(b.name));
  });
}

function coMove(name, dir) {
  var i = coOrder.indexOf(String(name)), j = i + dir;
  if (i < 0 || j < 0 || j >= coOrder.length) return;
  var t = coOrder[i]; coOrder[i] = coOrder[j]; coOrder[j] = t;
  renderPanelNow('mb-courses');
}

function taskKey(r) { return String(r.title) + '|' + String(r.dueRaw || r.due || ''); }

/** 在当前可见列表里上下移动一条作业（客户端的 ▲▼ 语义：动的是可见顺序）。 */
function moveTaskRow(key, dir) {
  var i = taskOrder.indexOf(key), j = i + dir;
  if (i < 0 || j < 0 || j >= taskOrder.length) return;
  var t = taskOrder[i]; taskOrder[i] = taskOrder[j]; taskOrder[j] = t;
  var rows = st.coTasks || [];
  var byKey = {};
  rows.forEach(function (r) { byKey[taskKey(r)] = r; });
  var moved = taskOrder.map(function (k) { return byKey[k]; }).filter(Boolean);
  /* 不在可见顺序里的（被搜索过滤掉的）按原相对顺序追加在后面 */
  rows.forEach(function (r) { if (taskOrder.indexOf(taskKey(r)) === -1) moved.push(r); });
  st.coTasks = moved;
  renderCourseTasks(st.taskPanelBox);
}

function renderCourseList(panel) {
  var courses = st.coCourses || [];
  if ($('mb-courses-n')) $('mb-courses-n').textContent = String(courses.length);
  if (!courses.length) {
    if (pendingSync()) { panel.appendChild(el('p', 'muted', '正在读取同步对象…')); return; }
    var whyEmpty = emptyText('课程列表', 'managebac', 'ManageBac');
    var boxE = el('div', 'empty');
    boxE.appendChild(document.createTextNode(whyEmpty));
    retryInto(boxE, whyEmpty);
    panel.appendChild(boxE);
    return;
  }
  var conn = accountLine('managebac', payload().meta);
  panel.appendChild(el('p', 'note', '共 ' + courses.length + ' 门课程 · ' +
    (conn ? conn + ' · ' : '') + (st.mbLabel || '服务器实时抓取') + ' · 只读'));
  /* 课程行（客户端的 .item.course-row 结构：▲▼ 调顺序 + .co-name + 「总评 … · 点击看课程详情」+ .badge） */
  var list = el('div', 'list');
  coOrderApply(courses).forEach(function (c) {
    var row = el('div', 'item course-row');
    row.setAttribute('data-cid', String(c.name));
    row.appendChild(moveBtns(function (dir) { coMove(c.name, dir); }));
    var grow = el('span', 'grow');
    var nm = el('span', 'co-name', c.name);
    nm.appendChild(el('small', null,
      '总评 ' + (c.grade && c.grade !== '—' ? c.grade : '未出分') +
      ' · 单元 ' + (c.units || '—') + ' · 点击看课程详情'));
    grow.appendChild(nm);
    row.appendChild(grow);
    row.appendChild(mbBadge(c.grade && c.grade !== '—' ? c.grade : '未出分',
                            c.grade && c.grade !== '—' ? 'green' : ''));
    row.tabIndex = 0;
    row.title = '查看这门课程的详情';
    var open = function () {
      openCourseDetail(c);
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
    list.appendChild(row);
  });
  panel.appendChild(list);
  /* 兼容层：给既有测试/脚本留一份 <table> 语义视图（内容与上面的列表一致，视觉上收成 0 尺寸）。
     客户端（Pinghe Launcher Lite）的课程列表不是表格；这里保留表格只为「课程名称 / 总评 / 单元数」的机器可读契约。 */
  var wrap = buildTable(el('div', 'sr-only-table'), ['课程名称', '总评', '单元数'],
    courses.map(function (c) {
      return { cells: [c.name, c.grade, c.units], attrs: { 'data-cid': String(c.name) } };
    }));
  panel.appendChild(wrap.parentNode);
}

/* ------------------------------------------------- 课程活动流（通知 / 消息 / 讨论） */

/* ManageBac 侧的状态是**能力感知**的：ok / partial 有数据；unavailable /
   unrecognized / forbidden / rate_limited / timeout / authentication_required
   是「读不出来」，绝不能画成「没有数据」。这里给每种状态一句中文，并且
   `available === false` 且无数据时按状态说清楚，而不是显示空列表。 */
var CO_STATUS_TEXT = {
  ok: '',
  partial: '只取到一部分（分页上限 / 请求预算 / 部分来源失败）',
  unavailable: 'ManageBac 学生页面没有找到这个入口',
  unrecognized: '页面结构没认出来，无法确认有没有数据',
  authentication_required: 'ManageBac 会话失效了，请到「个人中心 → 密码管理」更新账号密码',
  forbidden: '当前账号无权读取这一块',
  rate_limited: 'ManageBac 限流了，稍后再试',
  timeout: '连 ManageBac 超时了',
  not_loaded: '这一轮预算不够，还没读到',
  upstream_error: 'ManageBac 读取失败'
};

/** 一次课程活动流请求（通知 / 消息）。返回 promise，失败时 resolve(null) 不抛。 */
function fetchCourseActivity(path) {
  return req('GET', path, undefined, { timeoutMs: DATA_TIMEOUT_MS })
    ['catch'](function (err) {
      if (err && err.unauthorized) return null;
      return null;
    });
}

/** 并行拉通知 + 消息（私信/作业/讨论聚合），任何一边失败都不影响另一边。 */
function loadCourseActivity(force) {
  var q = force ? '?force=1' : '';
  var p1 = fetchCourseActivity('/app/courses/notifications/' + q).then(function (res) {
    st.coNotifs = (res && res.status === 200 && isObj(res.body)) ? res.body : { ok: false };
    renderPanelNow('mb-notifications');
  });
  var p2 = fetchCourseActivity('/app/courses/messages/' + q).then(function (res) {
    st.coMsgs = (res && res.status === 200 && isObj(res.body)) ? res.body : { ok: false };
    renderPanelNow('mb-messages');
    renderPanelNow('mb-discussions');
  });
  return Promise.all([p1, p2]).then(function () {
    st.coActivityAt = nowIso();
  });
}

/** 一块活动卡片的空态：按 status 如实说明，不假装「没有数据」。
 *
 *  `what` 必须是 `'通知'` —— 只有它会去读 `data.notifications`，**其它任何值**
 *  都会去读 `data.messages`（见下面那行三元）。2026-09-17 实测踩到过：
 *  `renderNotifications` 传的是 `'待办'`，于是 rows 永远取 `data.messages`
 *  （通知接口里是 undefined）→ 明明后端回来了 1 条待办，卡片却写「暂时没有待办。」
 *  传参写错时不再猜，直接抛出来（渲染器有 try/catch，会显示成可读的提示条）。 */
function coPlaceholder(data, what) {
  if (what !== '通知' && what !== '消息' && what !== '讨论') {
    throw new Error('coPlaceholder 的 what 只认 通知/消息/讨论，收到 ' + what);
  }
  if (data === null) return '正在加载…';
  if (!data || data.ok === false) {
    var err = data && data.error && data.error.message;
    return err || ('暂时拿不到' + what + '，可以点右上角「刷新」重试。');
  }
  var rows = (what === '通知' ? data.notifications : data.messages) || [];
  if (rows.length) return '';
  var status = data.status || (data.available === false ? 'unavailable' : 'ok');
  if (status !== 'ok' && status !== 'partial') {
    return CO_STATUS_TEXT[status] || ('暂时读不到' + what + '。');
  }
  return '暂时没有' + what + '。';
}

function coActivityItem(row, cls) {
  var box = el('div', cls);
  box.appendChild(el('div', cls + '__title', row.title || '（无标题）'));
  var bits = [];
  if (row.course) bits.push(row.course);
  if (row.author) bits.push(row.author);
  if (row.date) bits.push(prettyStamp(row.date) || row.date);
  if (row.status) bits.push(row.status);
  if (row.score) bits.push(row.score);
  box.appendChild(el('div', cls + '__meta', bits.join(' · ') || '—'));
  if (row.link) {
    var a = el('a', 'ghost', '在 ManageBac 打开 ↗');
    a.href = row.link;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.style.fontSize = '11.5px';
    box.appendChild(a);
  }
  return box;
}

function renderNotifications(panel) {
  var data = st.coNotifs;
  var foot = $('mb-notif-foot');
  /* 必须是 '通知'（而不是 '待办'）：coPlaceholder 靠这个字面量决定读
     `data.notifications` 还是 `data.messages` —— 写错就会把「有 1 条待办」
     渲染成「暂时没有待办。」（2026-09-17 修的就是这个）。 */
  var msg = coPlaceholder(data, '通知');
  if (msg) {
    panel.appendChild(el('div', 'empty', msg));
    if (foot) foot.textContent = '来自 ManageBac';
    return;
  }
  var rows = (data.notifications || []).slice(0, 20);
  if (rows.length) {
    rows.forEach(function (r) { panel.appendChild(coActivityItem(r, 'mb-notif-item')); });
  } else {
    panel.appendChild(el('div', 'empty', '近期没有待办或即将截止的作业。'));
  }
  var status = data.status || 'ok';
  if (status === 'partial' || data.partial) {
    panel.appendChild(el('div', 'empty', CO_STATUS_TEXT.partial));
  }
  /* 页脚如实说明通知那一块：ManageBac 的通知由**独立服务**下发（页面只给
     data-count），我们能拿到的是权威的未读数；正文在 ManageBac 自己的通知中心里。 */
  if (foot) {
    clear(foot);
    var n = data.unread_count;
    foot.appendChild(document.createTextNode(
      n == null ? 'ManageBac 通知：未读数读不到' : ('ManageBac 通知：' + n + ' 条未读')));
    if (data.notifications_url) {
      foot.appendChild(document.createTextNode(' · '));
      var a = el('a', null, '去通知中心 ↗');
      a.href = data.notifications_url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      foot.appendChild(a);
    }
  }
}

function renderMessages(panel) {
  var data = st.coMsgs;
  var msg = coPlaceholder(data, '消息');
  if (msg) { panel.appendChild(el('div', 'empty', msg)); return; }
  /* 私信卡片：优先讨论/公告类（真正「消息」语义），再补作业/考试 */
  var all = data.messages || [];
  var msgs = all.filter(function (r) {
    return r.type === 'discussion' || r.type === 'announcement' || r.type === 'other';
  });
  if (!msgs.length) msgs = all;
  msgs.slice(0, 20).forEach(function (r) { panel.appendChild(coActivityItem(r, 'mb-msg-item')); });
  if (data.partial || (data.status && data.status !== 'ok')) {
    panel.appendChild(el('div', 'empty', CO_STATUS_TEXT[data.status] || CO_STATUS_TEXT.partial));
  }
}

function renderDiscussions(panel) {
  var data = st.coMsgs;
  var msg = coPlaceholder(data, '讨论');
  if (msg) { panel.appendChild(el('div', 'empty', msg)); return; }
  var rows = (data.messages || []).filter(function (r) { return r.type === 'discussion'; });
  if (!rows.length) { panel.appendChild(el('div', 'empty', '暂时没有讨论。')); return; }
  rows.slice(0, 20).forEach(function (r) { panel.appendChild(coActivityItem(r, 'mb-disc-item')); });
}

/* ---------------------------------------------------------------- 课程详情 */

var currentCourse = null;

function openCourseDetail(course) {
  currentCourse = course;
  var listBox = $('mb-list');
  var detailBox = $('mb-detail');
  var backBtn = $('mb-back');
  if (listBox) listBox.hidden = true;
  if (detailBox) detailBox.hidden = false;
  if (backBtn) backBtn.hidden = false;
  if ($('mb-detail-title')) $('mb-detail-title').textContent = course.name;
  /* 没有课程 id 就**不要**拿课程名去凑（服务端只认数字 id，会回 400）。
     这里直接说清楚原因，并把「作业 / 考试」那一块指给用户 —— 那些数据本来就在
     课程页的主列表里，不点进来也看得到。 */
  if (!course.classId) {
    var box = $('mb-detail-info');
    if (box) {
      clear(box);
      box.appendChild(el('p', 'muted',
        '这门课没有带回课程编号（多半是「上次同步的快照」里的旧数据，快照里不含编号）。'
        + '点右上角「刷新」抓一次实时数据，编号就有了。'));
    }
    ['mb-detail-tasks', 'mb-detail-resources', 'mb-detail-grades'].forEach(function (id) {
      var node = $(id);
      if (!node) return;
      clear(node);
      node.appendChild(el('p', 'muted', '需要课程编号才能读取，先刷新一次。'));
    });
    return;
  }
  loadCourseDetail(course.classId);
}

function closeCourseDetail() {
  currentCourse = null;
  var listBox = $('mb-list');
  var detailBox = $('mb-detail');
  var backBtn = $('mb-back');
  if (listBox) listBox.hidden = false;
  if (detailBox) detailBox.hidden = true;
  if (backBtn) backBtn.hidden = true;
}

function loadCourseDetail(classId) {
  var infoBox = $('mb-detail-info');
  var tasksBox = $('mb-detail-tasks');
  var resourcesBox = $('mb-detail-resources');
  var gradesBox = $('mb-detail-grades');
  if (infoBox) clear(infoBox);
  if (tasksBox) clear(tasksBox);
  if (resourcesBox) clear(resourcesBox);
  if (gradesBox) clear(gradesBox);
  
  if (infoBox) infoBox.appendChild(el('p', 'muted', '正在加载课程详情…'));
  if (tasksBox) tasksBox.appendChild(el('p', 'muted', '正在加载…'));
  if (resourcesBox) resourcesBox.appendChild(el('p', 'muted', '正在加载…'));
  if (gradesBox) gradesBox.appendChild(el('p', 'muted', '正在加载…'));

  fetch('/app/courses/' + encodeURIComponent(classId) + '/details/', { credentials: 'same-origin' })
    .then(function (r) {
      if (r.status === 401) { tokenGone(); throw new Error('unauthorized'); }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      if (!data.ok || !data.details) throw new Error('Invalid response');
      renderCourseDetail(data.details);
    })
    ['catch'](function (err) {
      if (err.message === 'unauthorized') return;
      var msg = '加载失败：' + (err.message || '未知错误');
      if (infoBox) { clear(infoBox); infoBox.appendChild(el('p', 'muted', msg)); }
      if (tasksBox) { clear(tasksBox); tasksBox.appendChild(el('p', 'muted', msg)); }
      if (resourcesBox) { clear(resourcesBox); resourcesBox.appendChild(el('p', 'muted', msg)); }
      if (gradesBox) { clear(gradesBox); gradesBox.appendChild(el('p', 'muted', msg)); }
    });
}

function renderCourseDetail(details) {
  var infoBox = $('mb-detail-info');
  var tasksBox = $('mb-detail-tasks');
  var resourcesBox = $('mb-detail-resources');
  var gradesBox = $('mb-detail-grades');
  
  // 基本信息
  if (infoBox) {
    clear(infoBox);
    var info = el('div', 'course-info');
    if (details.teacher) info.appendChild(el('p', null, '教师：' + details.teacher));
    if (details.teaching_group) info.appendChild(el('p', null, '教学组：' + details.teaching_group));
    if (details.students && details.students.length > 0) {
      var studentsP = el('p', null, '同学：' + details.students.length + ' 人');
      info.appendChild(studentsP);
    }
    if (details.stats) {
      var stats = details.stats;
      if (stats.completion_rate != null) {
        info.appendChild(el('p', null, '完成率：' + Math.round(stats.completion_rate) + '%'));
      }
      if (stats.average_score != null) {
        info.appendChild(el('p', null, '平均分：' + stats.average_score + '%'));
      }
    }
    infoBox.appendChild(info);
  }
  
  // 作业与考试
  if (tasksBox) {
    clear(tasksBox);
    var messages = details.messages || [];
    if (messages.length === 0) {
      tasksBox.appendChild(el('p', 'muted', '暂无作业或考试'));
    } else {
      var list = el('div', 'list');
      messages.forEach(function (m) {
        var row = el('div', 'item');
        row.appendChild(el('span', 'dim', (m.due || m.date || '—').slice(0, 16)));
        var grow = el('span', 'grow');
        grow.appendChild(document.createTextNode(m.title || '无标题'));
        if (m.status) grow.appendChild(el('span', 'dim', ' · ' + m.status));
        row.appendChild(grow);
        if (m.score) row.appendChild(mbBadge(m.score, 'green'));
        list.appendChild(row);
      });
      tasksBox.appendChild(list);
    }
  }
  
  // 资源
  if (resourcesBox) {
    clear(resourcesBox);
    var resources = details.resources || [];
    if (resources.length === 0) {
      resourcesBox.appendChild(el('p', 'muted', '暂无资源'));
    } else {
      var list = el('div', 'list');
      resources.forEach(function (r) {
        var row = el('div', 'item');
        var link = el('a', 'grow');
        link.href = r.link || '#';
        link.target = '_blank';
        link.textContent = r.title || '无标题';
        row.appendChild(link);
        list.appendChild(row);
      });
      resourcesBox.appendChild(list);
    }
  }
  
  // 成绩
  if (gradesBox) {
    clear(gradesBox);
    var grades = details.grades || [];
    if (grades.length === 0) {
      gradesBox.appendChild(el('p', 'muted', '暂无成绩'));
    } else {
      var list = el('div', 'list');
      grades.forEach(function (g) {
        var row = el('div', 'item');
        row.appendChild(el('span', 'dim', (g.date || '—').slice(0, 16)));
        var grow = el('span', 'grow');
        grow.appendChild(document.createTextNode(g.title || '无标题'));
        row.appendChild(grow);
        if (g.score) row.appendChild(mbBadge(g.score, 'green'));
        list.appendChild(row);
      });
      gradesBox.appendChild(list);
    }
  }
}

/** 作业/考试一行（客户端的 .item.ddl-item 结构：▲▼ + 时间 + 标题 · 课程 + 状态徽标）。 */
function mbTaskRow(r) {
  /* 已过期 → past-due（灰底）；未过期且 2 天内到期 → urgent（加粗）。
     注意**外层**括号不能省：`a + (b && c ? x : '')` 里 `&&` 的优先级低于 `?:`，
     少一层括号就会变成 `(a + b) && c` → urgent 永远加不上（这个坑踩过一次）。
     内层的 `(!r.past && …)` 是多余的，去掉后语义完全一样。 */
  var cls = 'item ddl-item'
    + (r.past ? ' past-due' : '')
    + (!r.past && isUrgentDue(r.dueRaw || r.due) ? ' urgent' : '');
  var row = el('div', cls);
  row.setAttribute('data-due', r.dueRaw || '');
  if (r.past) row.setAttribute('data-past', '1');
  if (r.dueInferred) row.setAttribute('data-due-inferred', '1');
  row.appendChild(moveBtns(function (dir) { moveTaskRow(taskKey(r), dir); }));
  var dueCell = el('span', 'dim', String(r.due || '—').slice(5, 16) || '—');
  /* 日期是按「周几」推算的 → 标明，别让用户以为这是页面上的精确日期 */
  if (r.dueInferred) {
    dueCell.appendChild(el('span', 'inferred', '（推算）'));
    row.title = 'ManageBac 只给了「' + (r.dueText || '周几') + '」，这里按本周推算到最近的那个星期几';
  }
  row.appendChild(dueCell);
  var grow = el('span', 'grow');
  grow.appendChild(document.createTextNode(r.title));
  grow.appendChild(el('span', 'dim', ' · ' + r.course));
  row.appendChild(grow);
  var status = r.status && r.status !== '—' ? r.status : '?';
  row.appendChild(mbBadge(r.past ? (r.status && r.status !== '—' ? r.status : '已过期') : status,
                          r.past ? 'past' : (status === 'Pending' || status === '未提交' ? 'red' : 'green')));
  return row;
}

function renderCourseTasks(panel) {
  var box = panel || st.taskPanelBox;
  if (!box) return;
  if (panel) clear(box);              // 工具条（搜索/排序）触发的重渲染要自己清空
  st.taskPanelBox = box;
  var rows = st.coTasks || [];
  if (!rows.length) {
    if ($('mb-count')) $('mb-count').textContent = '';
    if (pendingSync()) { box.appendChild(el('p', 'muted', '正在读取同步对象…')); return; }
    var e = plErr('managebac');
    var why = e ? ('暂时拿不到作业列表（' + e + '）')
      : (st.mode === 'live' ? '这次抓取没有拿到作业条目。可以点右上角「刷新」重试。'
                            : liveFailText('作业列表'));
    var boxE = el('div', 'empty');
    boxE.appendChild(document.createTextNode(why));
    retryInto(boxE, why);
    box.appendChild(boxE);
    return;
  }
  var q = String(($('mb-q') && $('mb-q').value) || '').trim().toLowerCase();
  var sort = String(($('mb-sort') && $('mb-sort').value) || 'due-asc');
  if (sort !== mbSortLast) { mbSortLast = sort; taskOrder = []; }   // 换排序 → 放弃手动顺序
  var all = rows.map(function (r) {
    var copy = {};
    Object.keys(r).forEach(function (k) { copy[k] = r[k]; });
    copy.past = isPast(r.dueRaw || r.due);
    return copy;
  });
  var list = all.filter(function (r) {
    if (!q) return true;
    return (r.course + ' ' + r.title + ' ' + r.status + ' ' + r.score + ' ' + r.due)
      .toLowerCase().indexOf(q) !== -1;
  });
  list = list.slice().sort(function (a, b) {
    /* 用户用 ▲▼ 调过顺序 → 以那个顺序为准（客户端的手动排序语义） */
    var ia = taskOrder.indexOf(taskKey(a)), ib = taskOrder.indexOf(taskKey(b));
    if (ia !== -1 || ib !== -1) {
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    }
    if (sort === 'course') {
      var c = String(a.course).localeCompare(String(b.course), 'zh');
      return c !== 0 ? c : String(a.title).localeCompare(String(b.title), 'zh');
    }
    var ka = a.dueRaw || '~', kb = b.dueRaw || '~';
    if (ka === kb) return String(a.title).localeCompare(String(b.title), 'zh');
    return (sort === 'due-desc' ? (ka < kb ? 1 : -1) : (ka < kb ? -1 : 1));
  });
  taskOrder = list.map(taskKey);      // ▲▼ 在当前这两段可见列表里换位

  if ($('mb-count')) $('mb-count').textContent = '显示 ' + list.length + ' / ' + all.length + ' 条';
  if (!list.length) {
    box.appendChild(el('div', 'empty', '没有匹配「' + q + '」的作业。'));
    return;
  }
  box.appendChild(el('p', 'note', '共 ' + all.length + ' 条 · ' + (st.mbLabel || '服务器实时抓取') + ' · 只读'));
  /* 列表主体（客户端版式）：未过期在前，过期的一组放在下面并加分隔标题 */
  var listBox = el('div', 'list');
  var upcoming = list.filter(function (r) { return !r.past; });
  var past = list.filter(function (r) { return r.past; });
  if (!upcoming.length) {
    listBox.appendChild(el('div', 'empty', '没有未截止的作业'));
  }
  upcoming.forEach(function (r) {
    var row = mbTaskRow(r);
    row.tabIndex = 0;
    row.title = '查看这门作业的详情';
    var open = function () {
      toast(r.course + ' · ' + r.title + ' · 截止 ' + r.due +
            (r.status !== '—' ? ' · ' + r.status : '') +
            (r.score !== '—' ? ' · 分数 ' + r.score : ''));
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
    listBox.appendChild(row);
  });
  if (past.length) {
    var sep = el('div', 'list-sep');
    sep.appendChild(el('span', null, '已过期 · ' + past.length + ' 项'));
    listBox.appendChild(sep);
    past.forEach(function (r) {
      var row = mbTaskRow(r);
      row.tabIndex = 0;
      var open = function () { toast(r.course + ' · ' + r.title + ' · 已于 ' + r.due + ' 截止'); };      row.addEventListener('click', open);
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
      });
      listBox.appendChild(row);
    });
    box.appendChild(el('p', 'note', '已过期的作业 / 考试排在列表下方（灰底一栏）。'));
  }
  box.appendChild(listBox);
  /* 兼容层：等内容的 <table> 语义视图（机器可读契约：课程/标题/截止时间/状态/分数 + data-due） */
  var holder = el('div', 'sr-only-table');
  buildTable(holder, ['课程', '标题', '截止时间', '状态', '分数'], list.map(function (r) {
    return { cells: [r.course, r.title,
                     r.due + (r.dueInferred ? '（推算）' : ''), r.status, r.score],
             attrs: { 'data-due': r.dueRaw,
                      'data-due-inferred': r.dueInferred ? '1' : '0' } };
  }));
  box.appendChild(holder);
}

/** 课程视图的工具条：搜索 / 排序改变后立刻重渲染作业表（不重新抓取）。 */
function bindCourseTools() {
  var q = $('mb-q'), s = $('mb-sort');
  if (q) q.addEventListener('input', function () { renderCourseTasks(st.taskPanelBox); });
  if (s) s.addEventListener('change', function () { renderCourseTasks(st.taskPanelBox); });
  ['mb-core-cas', 'mb-core-ee'].forEach(function (id) {
    var card = $(id);
    if (!card) return;
    var open = function () {
      var what = card.getAttribute('data-core') === 'cas' ? 'CAS 创意 · 行动 · 服务' : 'EE 拓展论文';
      toast(what + '：IB Core 项目，不属于任何课程；详细记录请在客户端的「我的课程」里维护。', 5200);
    };
    card.addEventListener('click', open);
    card.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });
}

/* =========================== ⑥ 平和邮箱 =========================== */

/** 打码：固定 8 个圆点，不泄露真实长度。 */
function maskPw() { return '••••••••'; }

/** 邮件头列表：mail.recent（实时优先，失败降级到同步快照），点一行 → 抓正文。 */
function renderMailList(panel) {
  if (panel) clear(panel);
  var heads = st.mailRows || [];
  if (!heads.length) {
    if (pendingSync()) { panel.appendChild(el('p', 'muted', '正在读取同步对象…')); return; }
    var e = plErr('mail');
    var why = e
      ? ('暂时拿不到邮件（' + e + '）')
      : (st.mode === 'live'
          ? '这次抓取没有拿到邮件，同步对象里也没有快照。可以点右上角「刷新」重试；'
            + '若一直为空，请到客户端确认邮箱账号是否还能登录。'
          : liveFailText('邮件'));
    var boxE = el('div', 'empty');
    boxE.appendChild(document.createTextNode(why));
    retryInto(boxE, why);
    panel.appendChild(boxE);
    return;
  }

  var stats = el('div', 'stats');
  stats.appendChild(el('span', 'chip', '未读 ' + (st.mailUnread == null ? '未知' : st.mailUnread) + ' 封'));
  stats.appendChild(el('span', 'chip', '最近 ' + heads.length + ' 封'));
  if (st.mailSnap) stats.appendChild(el('span', 'chip', '上次同步的快照'));
  if (st.mailConnected) stats.appendChild(el('span', 'chip', st.mailConnected));
  panel.appendChild(stats);
  /* 这里**不再**写"列表是服务器实时抓取的邮件头…"那段技术说明（用户 2026-09-17 要求删掉）。 */

  /* 邮件头列表（客户端的 .mail-item 结构：.subj + .meta，**未读才**加粗 + 蓝点） */
  var list = el('div', 'list mail-items');
  heads.forEach(function (h) {
    /* 逐封的真实已读状态：unread === true 才画蓝点。
       缺字段（null）一律不画 —— 以前是「只要有未读数就给每封都画点」，
       结果 unread=0 时整列都是蓝点，把用户看懵了。 */
    var unread = h.unread === true;
    var isRead = h.unread === false;
    var item = el('div', 'mail-item' + (unread ? ' unread' : '') + (isRead ? ' read' : ''));
    item.setAttribute('data-uid', h.uid || '');
    if (h.unread === true || h.unread === false) {
      item.setAttribute('data-unread', unread ? '1' : '0');
    }
    var subj = el('div', 'subj');
    if (unread) subj.appendChild(document.createTextNode('🔵 '));
    subj.appendChild(document.createTextNode(h.subject));
    /* 回形针按**本地正文缓存**里的附件清单画（正文取回来了才知道有没有附件） */
    var cachedAtts = (mailBodyCache[String(h.uid)] || {}).attachments;
    if (Array.isArray(cachedAtts) && cachedAtts.length) {
      subj.appendChild(el('span', 'att-clip',
                          '📎' + (cachedAtts.length > 1 ? cachedAtts.length : '')));
    }
    item.appendChild(subj);
    item.appendChild(el('div', 'meta', h.from + ' · ' + h.date));
    item.tabIndex = 0;
    item.title = h.uid ? '点开读正文' : '这封邮件没有 uid，读不了正文';
    var open = function () { if (h.uid) openMail(h.uid); };
    item.addEventListener('click', open);
    item.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
    /* 选中态：当前打开的那封高亮 */
    if (st.mailOpen && String(st.mailOpen) === String(h.uid)) item.classList.add('on');
    list.appendChild(item);
  });
  panel.appendChild(list);

  /* 兼容层：等内容的 <table> 语义视图（发件人 / 标题 / 日期 + data-uid + 可点行），视觉收成 0 尺寸 */
  var holder = el('div', 'sr-only-table');
  var tbody = buildTable(holder, ['发件人', '标题', '日期'], heads.map(function (h) {
    return { cells: [h.from, h.subject, h.date], attrs: { 'data-uid': h.uid } };
  }));
  var trs = tbody.querySelectorAll('tr');
  for (var i = 0; i < trs.length; i++) {
    (function (tr, head) {
      if (!head.uid) return;
      tr.classList.add('row--clickable');
      tr.tabIndex = 0;
      tr.title = '点开读正文';
      tr.addEventListener('click', function () { openMail(head.uid); });
      tr.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openMail(head.uid); }
      });
    })(trs[i], heads[i]);
  }
  panel.appendChild(holder);

  /* 列表画完 → 把最新 N 封的正文排进后台预取队列（串行、一次一封）。
     这样用户点开最新几封时正文已经到了，看到的就是「点开即显」。
     预取失败不影响任何事，点开时会按需再取一次。 */
  scheduleMailPreload(heads);
}

/** 邮件正文顶部的操作条：**回复 / 转发放在最上面**。
 *
 *  用户原话：「查看邮件页面上面有个回复按钮，点击就进入写回复邮件」——
 *  所以它们在主题**上方**，不用滚到正文末尾去找（之前放在底部，用户根本没看见）。
 *  转发语义与 CipherCore E-Mail Suite 一致：收件人留空、主题加 `Fwd: `、
 *  原文逐行 `> ` 引用，**并把原附件一并带上**。
 */
function mailActionBar(uid) {
  var bar = el('div', 'mail-actions');
  var reply = el('button', 'primary', '↩ 回复');
  reply.type = 'button';
  reply.setAttribute('data-mail-reply', '1');       // 给脚本/CDP 用的稳定钩子
  reply.title = '给发件人回信（自动填好收件人和「Re: 主题」，并引用原文）';
  reply.addEventListener('click', function () { replyToMail(uid); });
  bar.appendChild(reply);

  var forward = el('button', 'ghost', '↪ 转发');
  forward.type = 'button';
  forward.setAttribute('data-mail-forward', '1');
  forward.title = '转发给其他人（主题加「Fwd: 」、引用原文，原附件一并带上）';
  forward.addEventListener('click', function () { forwardMail(uid); });
  bar.appendChild(forward);
  return bar;
}

/** 抓某一封的正文：优先本机直连 POST /mail/<uid> → 服务器 GET /app/mail/<uid>/ → 纯文本渲染。 */
function fetchMailBody(uid) {
  var useBridge = bridgeReady();
  if (!useBridge) return req('GET', MAIL_PATH + encodeURIComponent(String(uid)) + '/', undefined,
                             { timeoutMs: DATA_TIMEOUT_MS });
  return bridgePost(BRIDGE_MAIL_PATH + encodeURIComponent(String(uid)),
                    { accounts: bridgeAccounts }, DATA_TIMEOUT_MS)
    ['catch'](function () { return null; })
    .then(function (local) {
      if (local && local.status === 200 && local.body && isObj(local.body.mail)) {
        st.mailSrc = BRIDGE_SRC_LOCAL;
        return local;
      }
      if (local && local.status === 404) { st.mailSrc = BRIDGE_SRC_LOCAL; return local; }
      bridgeOk = false;                         // 本机这条路不通 → 本次会话不再走它
      st.mailSrc = BRIDGE_SRC_SERVER;
      return req('GET', MAIL_PATH + encodeURIComponent(String(uid)) + '/', undefined,
                 { timeoutMs: DATA_TIMEOUT_MS });
    });
}

/* ---- 已读同步（③）：打开一封邮件 → 显式调一个只做这件事的接口 ---- */

/** 邮件列表项（本地视图模型）。 */
function mailRowOf(uid) {
  var want = String(uid);
  for (var i = 0; i < (st.mailRows || []).length; i++) {
    if (String(st.mailRows[i].uid) === want) return st.mailRows[i];
  }
  return null;
}

/** 未读数增减：只在拿到**可信原值**时动；没有原值（未知）绝不瞎猜一个新数字。
 *
 *  **首页那一格要跟着动**：用户点开一封未读邮件后，`st.mailUnread` 立即 -1、
 *  邮件列表与计数行也会重画，但首页的数字属于另一块渲染（renderHomeNow）——
 *  不在这里补一次重画，首页就会停在旧数字上（用户报的「首页未读数量显示不对」里
 *  最容易看见的一种：数字与列表明显不一致）。 */
function mailUnreadAdd(delta) {
  if (st.mailUnread == null) return;
  st.mailUnread = Math.max(0, st.mailUnread + delta);
  renderHomeUnread(homeUnreadSource());
}

/** 打开邮件时**先乐观**把它算成已读：去掉加粗与蓝点、未读数 -1（只动本地）。 */
function mailOptimisticRead(uid) {
  var row = mailRowOf(uid);
  if (!row || row.unread !== true) return false;      // 本来就是已读 / 状态未知 → 不动
  row.unread = false;
  mailUnreadAdd(-1);
  /* 不在这里调 renderMailList：调用方（openMail / 模态按钮）会在同帧内处理 UI 更新。
     旧版在这里调 renderMailList 会导致列表整体重建，而调用方之后还会再重建一次，
     白做一次 DOM 操作。 */
  return { uid: uid, countBefore: st.mailUnread != null ? st.mailUnread + 1 : null };
}

/** 接口失败 → **回滚**乐观改动，并如实告诉用户「没标成已读」。 */
function mailRollbackRead(snap) {
  if (!snap) return;
  mailOptimisticReads.delete(String(snap.uid));
  var row = mailRowOf(snap.uid);
  if (row) row.unread = true;
  if (snap.countBefore != null) st.mailUnread = snap.countBefore;
  renderMailList($('mail-heads'));
  renderHomeUnread(homeUnreadSource());      // 首页那一格跟着回滚，绝不留下假数字
  toast('没能把它标记为已读（邮箱那边没改成），刷新后仍是未读', 5200);
}

/** 标记一封为已读：本机直连优先，失败如实回落到服务器；两条路都是「只标这一封」。 */
function markMailSeen(uid) {
  var path = MAIL_PATH + encodeURIComponent(String(uid)) + '/read/';
  if (!bridgeReady()) {
    return req('POST', path, {}, { timeoutMs: DATA_TIMEOUT_MS });
  }
  return bridgePost(BRIDGE_MAIL_PATH + encodeURIComponent(String(uid)) + '/read/',
                    { accounts: bridgeAccounts }, DATA_TIMEOUT_MS)
    ['catch'](function () { return null; })
    .then(function (local) {
      if (local && local.status === 200 && local.body && isObj(local.body.mail)) return local;
      if (local && local.status === 404) return local;      // 这封不在了 → 交给调用方回滚
      bridgeOk = false;                                     // 本机这条路不通 → 本次会话不再走它
      return req('POST', path, {}, { timeoutMs: DATA_TIMEOUT_MS });
    });
}

function openMail(uid) {
  var box = $('mail-detail');
  if (!box) return;
  clear(box);
  st.mailOpen = String(uid);
  /* 用户手动点开 = 这一封优先：清掉后台预取队列与待起跑的定时器，
     别让预取跟他的请求抢带宽。（已经在途的那一封无法撤回，但它只影响它自己。） */
  mailPreloadQueue = [];
  if (mailPreloadTimer) { clearTimeout(mailPreloadTimer); mailPreloadTimer = null; }

  /* 先更新数据模型（如果 st.mailRows 已就绪） */
  var readSnap = null;
  var row = mailRowOf(uid);
  if (row && row.unread === true) {
    row.unread = false;
    mailOptimisticReads.add(String(uid));
    var prevCount = st.mailUnread;
    mailUnreadAdd(-1);
    readSnap = { uid: uid, countBefore: prevCount };
  }

  /* 渲染列表：有数据时从模型重建（模型已含乐观已读状态），无数据时跳过。 */
  var hadData = !!(st.mailRows && st.mailRows.length);
  if (hadData) {
    renderMailList($('mail-heads'));
  }

  /* 正文是否已经在手：**只看本地正文缓存**（后台预取填的）。
     服务端不再把正文塞进邮件列表 —— 那会让列表本身超时（真机实测）。
     缓存里有 → 直接渲染，用户看到的就是「点开即显」。 */
  var cached = mailBodyCache[String(uid)];
  var hasBody = !!(cached && (cached.body_text || cached.body_html));
  
  /* DOM 兜底：st.mailRows 为空时（异步加载未完成），直接操作当前 DOM 上的 .mail-item。
     有数据时 renderMailList 已经从模型重建了 DOM（乐观已读态），不需要兜底。 */
  if (!readSnap && !hadData) {
    var item = document.querySelector('.mail-item[data-uid="' + String(uid) + '"]');
    if (item && item.getAttribute('data-unread') === '1') {
      item.classList.remove('unread');
      item.setAttribute('data-unread', '0');
      var subj = item.querySelector('.subj');
      if (subj && subj.firstChild && subj.firstChild.nodeType === 3) {
        var t = subj.firstChild.textContent;
        if (t.indexOf('\uD83D\uDD35') === 0) subj.firstChild.textContent = t.slice(1);
      }
      mailOptimisticReads.add(String(uid));
      mailUnreadAdd(-1);
      readSnap = { uid: uid, countBefore: st.mailUnread != null ? st.mailUnread + 1 : null };
    }
  }
  var view = $('view-mail');
  if (view && window.innerWidth <= 900) view.classList.add('show-detail');
  show($('mail-close'), true);

  /* 乐观更新已发给服务器（readSnap 可能是上面设的，也可能是空 —— 已读时不重复标记） */
  if (readSnap) {
    markMailSeen(uid).then(function (res) {
      var ok = res && res.status === 200 && isObj(res.body) && isObj(res.body.mail)
               && res.body.mail.unread === false;
      if (ok) return;
      mailRollbackRead(readSnap);
      panelMsg('mail-error', '标记已读失败：' + errText(res), true);
    })['catch'](function (err) {
      if (err && err.unauthorized) return;
      mailRollbackRead(readSnap);
      panelMsg('mail-error', '标记已读失败：' +
        (err && err.isTimeout ? '请求超时' : ((err && err.message) || '网络错误')), true);
    });
  }

  /* 前10条已预加载正文：直接渲染，不显示加载状态 */
  if (hasBody) {
    var m = {
      uid: uid,
      subject: row.subject || '（无标题）',
      from: row.from || '（未知）',
      date: row.date || '',
      to: '',
      body_text: cached.body_text || '',
      body_html: cached.body_html || '',
      attachments: Array.isArray(cached.attachments) ? cached.attachments : []
    };
    var card = el('div', 'mail-view');
    card.appendChild(mailActionBar(uid));
    card.appendChild(el('h3', 'mail-view__subject', cellText(m.subject)));
    card.appendChild(el('div', 'muted small mail-view__meta',
                        cellText(m.from) + (m.date ? ' · ' + prettyStamp(m.date) : '')));
    var attBar0 = mailAttachmentsBar(uid, m.attachments);
    if (attBar0) card.appendChild(attBar0);
    card.appendChild(mailBodyCard(m));
    card.appendChild(el('div', 'mail-view__foot',
                        '正文已就绪（' + (st.mailSrc || bridgeSourceLabel()) + '）。'));
    box.appendChild(card);
    if (typeof box.scrollIntoView === 'function') box.scrollIntoView({ block: 'nearest' });
    return;
  }

  /* 还没有正文 → 只显示「正在加载…」（不显示编号、不显示技术细节） */
  var card = el('div', 'mail-view');
  card.appendChild(el('div', 'mail-view__row', '正在加载…'));
  box.appendChild(card);

  fetchMailBody(uid)
    .then(function (res) {
      clear(box);
      if (res.status !== 200 || !isObj(res.body.mail)) {
        box.appendChild(mailErrorCard('读不到这封邮件：' + errText(res), uid));
        return;
      }
      var m = res.body.mail;
      /* 存进本地正文缓存：同一会话里再点开同一封就是零等待。 */
      mailBodyCache[String(uid)] = {
        body_text: m.body_text || '',
        body_html: m.body_html || '',
        attachments: Array.isArray(m.attachments) ? m.attachments : []
      };
      renderMailList($('mail-heads'));      // 列表补上回形针
      card = el('div', 'mail-view');
      /* 与客户端的正文区同构：h3 主题 → muted small 发件人·日期 → 收件人 → 附件 → 正文。
         多出来的 .mail-view__* 只是给既有测试/脚本留的钩子（同名同文案）。
         **回复按钮在最上面**（用户原话：查看邮件页面上面有个回复按钮）。 */
      card.appendChild(mailActionBar(uid));
      card.appendChild(el('h3', 'mail-view__subject', cellText(m.subject || '（无标题）')));
      card.appendChild(el('div', 'muted small mail-view__meta',
                          cellText(m.from || '（未知）') +
                          (m.date ? ' · ' + prettyStamp(m.date) : '') +
                          (m.to ? ' · 收件人：' + cellText(m.to) : '')));
      /* 收件人 / 抄送（客户端的 .rc-bar > .rc-item） */
      var tos = String(m.to || '').split(/[,;]\s*/).filter(Boolean);
      var ccs = String(m.cc || '').split(/[,;]\s*/).filter(Boolean);
      if (tos.length || ccs.length) {
        var bar = el('div', 'rc-bar');
        tos.forEach(function (x) { bar.appendChild(el('span', 'rc-item', 'To: ' + x)); });
        ccs.forEach(function (x) { bar.appendChild(el('span', 'rc-item', 'Cc: ' + x)); });
        card.appendChild(bar);
      }
      /* 附件（客户端的 .att-bar）：每一项都是**真下载链接**，
         走 GET /app/mail/<uid>/attachments/<index>/（服务端回原始字节 + attachment 头）。
         名字与大小都如实写出来；超大的附件服务端会回 404，这里也标出来。 */
      var attBar = mailAttachmentsBar(uid, m.attachments);
      if (attBar) card.appendChild(attBar);
      card.appendChild(mailBodyCard(m));
      card.appendChild(el('div', 'mail-view__foot',
                          '正文来自你的邮箱实时读取（' + (st.mailSrc || bridgeSourceLabel()) +
                          '），只在本次会话中使用。打开邮件已把它标记为已读。'));
      box.appendChild(card);
      if (typeof box.scrollIntoView === 'function') box.scrollIntoView({ block: 'nearest' });
    })['catch'](function (err) {
      if (err && err.unauthorized) return;
      clear(box);
      box.appendChild(mailErrorCard('读不到这封邮件：' +
        (err && err.isTimeout ? '读取超过 ' + Math.round(DATA_TIMEOUT_MS / 1000) + ' 秒还没回来'
                              : ((err && err.message) || '网络错误')), uid));
    });
}

/* ---- 正文区：有 body_html 就原样塞进**沙箱 iframe**（不消毒），没有才回退纯文本 ---- */

/** 沙箱 iframe **唯一**的 sandbox 值：只有 `allow-same-origin`。
 *  为什么要它：父页面得读 `contentDocument` 才能量出正文真实高度（不带它时
 *  contentDocument 为 null，高度就没法自适应）。
 *  **为什么绝对不加 `allow-scripts`**：邮件正文按用户要求「不消毒、原样渲染」，
 *  唯一的结构性防线就是这颗沙箱 —— 没有 allow-scripts，邮件里的 `<script>`
 *  与 `<img onerror=…>` 永远不会执行。**这一条不能删。** */
var MAIL_FRAME_SANDBOX = 'allow-same-origin';
/** 沙箱 iframe 的高度上限（px）：超过就内部滚动，别让一封超长邮件把页面撑到几万像素 */
var MAIL_FRAME_MAX_H = 3600;

/** 附件栏：把 `attachments` 渲染成一排**真下载链接**（没有附件返回 null）。
 *
 *  下载走 `GET /app/mail/<uid>/attachments/<index>/`：服务端重新 FETCH 那封邮件、
 *  取出同一个序号的那一部分、带 `Content-Disposition: attachment` 原样回字节。
 *  这里用 `<a href>` 而不是 fetch+Blob —— 浏览器的原生下载能正确处理大文件和中文名，
 *  也不会把附件内容读进 JS 内存。
 */
function mailAttachmentsBar(uid, atts) {
  if (!Array.isArray(atts) || !atts.length) return null;
  var bar = el('div', 'att-bar');
  bar.appendChild(el('b', null, '📎 附件 (' + atts.length + ')'));
  atts.forEach(function (a) {
    var obj = isObj(a) ? a : {};
    var idx = (obj.index == null) ? -1 : Number(obj.index);
    var label = cellText(obj.filename || obj.name || '附件');
    var size = Number(obj.size || 0);
    var text = '📄 ' + label + (size ? '（' + mailBytes(size) + '）' : '');
    if (idx < 0 || !uid) {
      bar.appendChild(el('span', 'ghost att-dl', text));
      return;
    }
    var link = el('a', 'ghost att-dl', '');
    link.href = '/app/mail/' + encodeURIComponent(String(uid)) +
                '/attachments/' + idx + '/';
    link.setAttribute('download', label);
    link.textContent = text;
    link.title = '下载 ' + label;
    bar.appendChild(link);
  });
  return bar;
}

/** 字节数 → 人看的大小（B / KB / MB）。 */
function mailBytes(n) {
  var v = Number(n) || 0;
  if (v < 1024) return v + ' B';
  if (v < 1024 * 1024) return (v / 1024).toFixed(1) + ' KB';
  return (v / 1024 / 1024).toFixed(1) + ' MB';
}

/** 量不到内层高度时的兜底高度（px） */
var MAIL_FRAME_FALLBACK_H = 420;

/** 量内层文档的真实高度；读不到（异常）就返回 0 交给兜底高度。 */
function mailFrameHeight(frame) {
  var inner = null;
  try { inner = frame.contentDocument; } catch (e) { inner = null; }
  if (!inner) return 0;
  var de = inner.documentElement, bd = inner.body, h = 0;
  try {
    h = Math.max(de ? de.scrollHeight : 0, de ? de.offsetHeight : 0,
                 bd ? bd.scrollHeight : 0, bd ? bd.offsetHeight : 0);
  } catch (e) { return 0; }
  if (!h || !isFinite(h)) return 0;
  return Math.round(h);
}

/** 高度自适应：把内层文档的真实高度写回 iframe（上限 MAIL_FRAME_MAX_H，超出内部滚动）。
 *  邮件里的脚本不执行，所以只能在父页面这边量 —— 这正是需要 allow-same-origin 的原因。 */
function fitMailFrame(frame) {
  if (!frame || !frame.parentNode) return;      // 卡片已经被换掉 → 什么都不做
  var h = mailFrameHeight(frame);
  if (!h) return;                               // 量不到：保留 CSS + 兜底高度
  frame.style.height = Math.min(MAIL_FRAME_MAX_H, Math.max(60, h + 2)) + 'px';
}

/** 正文卡：有 body_html 就**原样**塞进沙箱 iframe（不消毒、不重写一个字符）；
 *  没有 HTML 部分就只渲染纯文本（`white-space:pre-wrap` 分支）。
 *  手机版走同一条路（app.css 里对窄屏另有尺寸规则）。 */
function mailBodyCard(m) {
  var html = (typeof m.body_html === 'string') ? m.body_html : '';
  var text = (typeof m.body_text === 'string') ? m.body_text : '';
  var wrap = el('div', 'mail-body-wrap');

  if (!html) {
    wrap.appendChild(el('div', 'mail-body mail-view__body', text || '（这封邮件没有正文）'));
    return wrap;
  }

  var frame = el('iframe', 'mail-frame');
  frame.setAttribute('sandbox', MAIL_FRAME_SANDBOX);      // ← 唯一防线：不带 allow-scripts
  frame.setAttribute('referrerpolicy', 'no-referrer');    // 邮件里的图不会把地址带出去
  frame.setAttribute('title', '邮件正文（沙箱渲染，脚本不执行）');
  frame.style.height = MAIL_FRAME_FALLBACK_H + 'px';
  frame.setAttribute('srcdoc', html);                     // 原样：服务端返回什么就是什么
  frame.addEventListener('load', function () {
    fitMailFrame(frame);
    // 图片是异步加载的：onload 之后再补量两次，别让一屏图把高度算小
    setTimeout(function () { fitMailFrame(frame); }, 300);
    setTimeout(function () { fitMailFrame(frame); }, 1200);
  });
  wrap.appendChild(frame);
  return wrap;
}

function mailErrorCard(text, uid) {
  var card = el('div', 'mail-view');
  card.appendChild(el('div', 'mail-view__row is-error', text));
  var retry = el('button', 'ghost', '重试');
  retry.type = 'button';
  retry.addEventListener('click', function () { openMail(uid); });
  card.appendChild(retry);
  return card;
}

/** 「选择一封邮件查看」占位 + 关于正文的说明。
 *
 *  这份说明在**两个地方**出现：`app/index.html` 里的初始占位、以及关掉一封邮件后
 *  这里重建的占位。用户 2026-09-17 要求删掉「列表是服务器…实时抓取的邮件头」那段
 *  （技术细节太多），**两处都要删**，否则点开再关掉那行字又回来了。
 *  改的时候请两边一起改（test_app_web.py 有一条断言盯着"两处都不含这句话"）。
 */
function mailEmptyNotice() {
  var wrap = el('div');
  wrap.appendChild(el('div', 'empty', '选择一封邮件查看'));
  var notice = el('div', 'notice');
  notice.appendChild(el('p', 'notice__title', '关于正文'));
  var b1 = el('p', 'notice__body');
  b1.appendChild(document.createTextNode('另外：'));
  b1.appendChild(el('strong', null, '打开一封邮件会把它标记为已读'));
  b1.appendChild(document.createTextNode('（只标记你点开的那一封，绝不会批量标记），这会同步到你的邮箱。'));
  notice.appendChild(b1);
  var b2 = el('p', 'notice__body');
  b2.appendChild(document.createTextNode('带 '));
  b2.appendChild(el('strong', null, '📎'));
  b2.appendChild(document.createTextNode(' 的邮件有附件，点开正文即可下载；右上角 '));
  b2.appendChild(el('strong', null, '👥 通讯录'));
  b2.appendChild(document.createTextNode(' 是从你往来邮件里整理出的联系人，点一下就能写邮件给他。'));
  notice.appendChild(b2);
  wrap.appendChild(notice);
  return wrap;
}

function closeMail() {
  var box = $('mail-detail');
  st.mailOpen = null;
  var view = $('view-mail');
  if (view) view.classList.remove('show-detail');
  show($('mail-close'), false);
  renderMailList($('mail-heads'));            // 列表选中态清掉
  if (!box) return;
  clear(box);
  var placeholder = mailEmptyNotice();
  while (placeholder.firstChild) box.appendChild(placeholder.firstChild);
}

/** settings.accounts：只显示账号名，密码打码（同步对象里的凭据，只读展示）。 */
function renderMailAccounts(ref) {
  var box = $('mail-body');
  if (!box) return;
  clear(box);
  if (!ref) { box.appendChild(el('p', 'muted', '正在读取…')); return; }
  if (!ref.parsed || !isObj(ref.doc) || !isObj(ref.doc.accounts)) {
    box.appendChild(el('p', 'empty', ref.parsed ? '云端还没有账号数据（settings.accounts）。'
                                                : '云端账号数据不是合法 JSON，无法展示。'));
    return;
  }
  var accounts = ref.doc.accounts;
  var mailKeys = Object.keys(accounts).filter(function (k) { return k.indexOf('mail') === 0; });
  var otherKeys = Object.keys(accounts).filter(function (k) { return k.indexOf('mail') !== 0; });
  var keys = mailKeys.length ? mailKeys : otherKeys;
  var label = { edupage: 'Edupage', managebac: 'ManageBac', mail: '邮箱' };

  if (!keys.length) {
    box.appendChild(el('p', 'empty', '暂无邮箱账号。请在客户端添加邮箱凭据并同步后再来查看。'));
    return;
  }
  if (!mailKeys.length) {
    box.appendChild(el('p', 'note', '（还没有邮箱账号，下面是客户端同步过来的其它平台账号，同样只显示用户名。）'));
  }

  keys.sort();
  keys.forEach(function (name) {
    var sec = isObj(accounts[name]) ? accounts[name] : {};
    var user = sec.username || sec.email || sec.user ||
               (name.indexOf('mail:') === 0 ? name.slice(5) : '') || '（未填用户名）';

    var card = el('div', 'mail-card');
    var main = el('div', 'mail-card__main');
    main.appendChild(el('div', 'mail-card__user', String(user)));
    main.appendChild(el('div', 'mail-card__sub',
                        (label[name] ? label[name] + ' · ' : '') + '账号标识 ' + name));
    card.appendChild(main);

    var raw = sec.password || sec.passwd || sec.authcode || sec.token || '';
    card.appendChild(el('span', 'secret', raw ? maskPw() : '（未设置密码）'));
    box.appendChild(card);
  });
}

/* =========================== ⑦ Agent 助手 =========================== */

var AI_PATH = '/app/ai/chat/';
var AI_TIMEOUT_MS = 60000;
var AI_MAX_HISTORY = 12;

var ai = {
  history: [],      // 已确认的消息（user / assistant 交替）
  seq: 0,           // 请求序号：只渲染最新一次请求的结果
  ctl: null,        // 当前 AbortController（用于「取消」）
  timer: null,
  busy: false,
  needConfig: false,   // 后端报过「未配置」→ 提示常驻，直到用户点「我已配置好」
  lastQuestion: ''
};

/** 上下文一行：用到了哪些数据 / 多少字符 / 是否截断。 */
function ctxText(c) {
  if (!isObj(c)) return '这次回答没有返回上下文信息。';
  var objs = Array.isArray(c.objects) ? c.objects : [];
  var nameMap = {
    schedule: '日程', timetable: '课表', edupage: '课表',
    school: '校园数据（课表/ManageBac/邮件头）',
    managebac: 'ManageBac', mail: '邮件标题',
    'settings.lessons': '选课（教学组）', 'settings.accounts': '账号（不含密码）'
  };
  var names = objs.map(function (o) { return nameMap[o] || String(o); });
  var chars = (typeof c.chars === 'number') ? c.chars : null;
  var parts = [];
  parts.push('数据：' + (names.length ? names.join('、') : '（没有可用的数据）'));
  parts.push('字符：' + (chars == null ? '未知' : chars + ' 字符'));
  parts.push(c.truncated ? '已截断（数据太多，只送了前一部分）' : '未截断');
  return parts.join(' · ');
}

function aiBubble(role, text, metaText, isError) {
  var wrap = el('div', 'msg' + (role === 'user' ? ' msg--user' : ''));
  var head = el('div', 'msg__head');
  head.appendChild(el('span', 'msg__who', role === 'user' ? ('我（' + (st.me.username || '当前用户') + '）') : 'AI'));
  wrap.appendChild(head);
  var body = el('div', 'msg__body', text);      // 一律 textContent：纯文本安全渲染
  if (isError) body.classList.add('is-error');
  wrap.appendChild(body);
  if (metaText) wrap.appendChild(el('div', 'msg__meta', metaText));
  return wrap;
}

function renderAi() {
  var log = $('ai-log');
  if (!log) return;
  clear(log);
  if (!ai.history.length) {
    var empty = el('p', 'empty', '还没有对话。在下面输入问题，比如「这周有哪些课」「最近有哪些作业快到截止时间」。');
    empty.id = 'ai-empty';
    log.appendChild(empty);
  } else {
    ai.history.forEach(function (m) {
      log.appendChild(aiBubble(m.role, m.content, m.meta, m.error));
    });
  }
  $('ai-count').textContent = '历史 ' + ai.history.length + ' / ' + AI_MAX_HISTORY + ' 条';
}

function aiSetBusy(on) {
  ai.busy = on;
  $('ai-send').disabled = on;
  $('ai-q').disabled = on;
  $('ai-wait').hidden = !on;
  // 「未配置」提示只在后端确实报过未配置、且用户没手动收起时保留，不随 loading 一起消失
  if (!on && !ai.needConfig) { $('ai-unconf').hidden = true; }
}

function pushHistory(role, content, meta, isError) {
  ai.history.push({ role: role, content: content, meta: meta || '', error: !!isError });
  while (ai.history.length > AI_MAX_HISTORY) ai.history.shift();
  renderAi();
}

function aiError(msg) {
  var text = String(msg || '请求失败');
  if (text.length > 400) text = text.slice(0, 400) + '…';
  pushHistory('assistant', '出错：' + text, '', true);
  return text;
}

function askAi(question) {
  var seq = ++ai.seq;
  ai.lastQuestion = question;
  pushHistory('user', question);
  aiSetBusy(true);
  $('ai-wait-text').textContent = 'AI 正在根据你的数据思考…';

  var ctl = (typeof AbortController === 'function') ? new AbortController() : null;
  ai.ctl = ctl;
  var timedOut = false;
  ai.timer = setTimeout(function () {
    timedOut = true;
    if (ctl) ctl.abort();
  }, AI_TIMEOUT_MS);

  var prior = ai.history.slice(0, -1).filter(function (m) { return !m.error; })
    .map(function (m) { return { role: m.role, content: m.content }; })
    .slice(-AI_MAX_HISTORY);

  req('POST', AI_PATH, { question: question, history: prior },
      { signal: ctl ? ctl.signal : undefined })
    .then(function (res) {
      if (seq !== ai.seq) return;                       // 已有更新的提问，丢弃这次结果
      if (res.status === 200 && res.body && typeof res.body.answer === 'string') {
        var model = res.body.model ? ('模型 ' + res.body.model) : '';
        var meta = [model, ctxText(res.body.context)].filter(function (s) { return !!s; }).join(' · ');
        pushHistory('assistant', res.body.answer, meta);
        $('ai-ctx').textContent = ctxText(res.body.context);
        if (ai.needConfig) { ai.needConfig = false; }
        $('ai-unconf').hidden = true;
        return;
      }
      var message = errText(res);
      aiError(message);
      var code = (res.body && res.body.error && res.body.error.code) || '';
      if (res.status === 409 || code === 'ai_not_configured' || code === 'not_configured') {
        ai.needConfig = true;                           // 未配置：把后端提示 + 配置入口摆出来并留住
        $('ai-unconf').hidden = false;
      }
    })['catch'](function (err) {
      if (seq !== ai.seq || (err && err.unauthorized)) return;
      if (timedOut) { aiError('等待超过 ' + Math.round(AI_TIMEOUT_MS / 1000) + ' 秒还没有回复，已取消。可以稍后重试。'); return; }
      if (err && (err.isAbort || err.name === 'AbortError')) { aiError('已取消这次提问。'); return; }
      aiError((err && err.message) || '网络错误，请稍后重试');
    })
    .then(function () {
      if (seq !== ai.seq) return;
      clearTimeout(ai.timer); ai.timer = null; ai.ctl = null;
      aiSetBusy(false);
    });
}

function bindAi() {
  $('ai-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    if (ai.busy) { toast('上一个问题还在回答中'); return; }
    var q = String($('ai-q').value || '').trim();
    if (!q) { toast('请先输入问题'); $('ai-q').focus(); return; }
    $('ai-q').value = '';
    askAi(q);
  });

  $('ai-cancel').addEventListener('click', function () {
    if (ai.ctl) {
      clearTimeout(ai.timer); ai.timer = null;
      ai.ctl.abort();
    }
  });

  $('ai-clear').addEventListener('click', function () {
    if (ai.busy && ai.ctl) { clearTimeout(ai.timer); ai.timer = null; ai.ctl.abort(); }
    ai.seq++;                                   // 让在途结果失效
    ai.history = [];
    ai.needConfig = false;
    aiSetBusy(false);
    $('ai-unconf').hidden = true;
    $('ai-ctx').textContent = '还没有提问。提问后会显示这次回答用到了哪些数据、多少字符、是否被截断。';
    renderAi();
  });

  $('ai-q').addEventListener('keydown', function (ev) {   // Ctrl/⌘ + Enter 直接发送
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
      ev.preventDefault();
      $('ai-form').dispatchEvent(new Event('submit', { cancelable: true }));
    }
  });

  var fixed = $('ai-fixed');
  if (fixed) {
    fixed.addEventListener('click', function () {          // 用户说「配好了」→ 收起提示
      ai.needConfig = false;
      $('ai-unconf').hidden = true;
      $('ai-q').focus();
    });
  }
}

/* =========================== ⑧ 设置 =========================== */

/** 逐平台一行：账号名（只显示账号名）+ 该平台这次抓取的状态。 */
function renderSettings() {
  var box = $('set-platforms');
  if (box) {
    clear(box);
    var m = payload().meta;
    m = isObj(m) ? m : {};
    var acc = metaAccounts(m);
    var names = Object.keys(acc);
    var label = { edupage: 'EduPage · 课表', managebac: 'ManageBac · 课程与成绩', mail: '平和邮箱' };
    if (!names.length) {
      var all = (st.me.username && st.accounts && isObj(st.accounts.doc) &&
                 isObj(st.accounts.doc.accounts)) ? Object.keys(st.accounts.doc.accounts) : [];
      box.appendChild(el('p', 'empty', all.length
        ? '这次抓取没拿到账号名（同步对象里有 ' + all.length + ' 个平台的凭据，去同步状态看原因）。'
        : '还没有连接的平台账号：请到「个人中心 → 密码管理」补齐 EduPage / ManageBac / 邮箱，再看这里。'));
    } else {
      var kv = el('div', 'kv');
      names.forEach(function (k) {
        var row = el('div', 'kv__row');
        row.appendChild(el('div', 'kv__k', label[k] || k));
        row.appendChild(el('div', 'kv__v', String(acc[k]) ) );
        kv.appendChild(row);
      });
      box.appendChild(kv);
    }

    var errs = metaErrors(m);
    var ekeys = Object.keys(errs).filter(function (k) { return typeof errs[k] === 'string' && errs[k]; });
    if (ekeys.length) {
      var ebox = el('div', 'inline-msg inline-msg--error');
      ebox.textContent = ekeys.map(function (k) {
        return (label[k] || k) + '：' + String(errs[k]);
      }).join('　|　');
      box.appendChild(ebox);
    }
  }

  var abox = $('set-account');
  if (abox) {
    clear(abox);
    var kv2 = el('div', 'kv');
    var r1 = el('div', 'kv__row');
    r1.appendChild(el('div', 'kv__k', '登录账号'));
    r1.appendChild(el('div', 'kv__v', st.me.username || '—'));
    kv2.appendChild(r1);
    var r2 = el('div', 'kv__row');
    r2.appendChild(el('div', 'kv__k', '账号来源'));
    r2.appendChild(el('div', 'kv__v', '账号由 PHIX 提供，与桌面客户端共用'));
    kv2.appendChild(r2);
    abox.appendChild(kv2);
  }

  /* 账号标识这类技术字段（mail:* / edupage / managebac 的对象名）收进诊断区，
     常规视图不留 —— 普通用户要的就是「登录的是谁 + 去哪改密码 + 退出」。 */
  var idbox = $('set-account-ids');
  if (idbox) {
    clear(idbox);
    var doc2 = (st.accounts && isObj(st.accounts.doc) && isObj(st.accounts.doc.accounts))
      ? st.accounts.doc.accounts : null;
    if (!doc2) {
      idbox.appendChild(el('p', 'muted small', st.accounts ? '云端没有 settings.accounts' : '正在读取 settings.accounts…'));
    } else {
      var keys2 = Object.keys(doc2).sort();
      if (!keys2.length) {
        idbox.appendChild(el('p', 'muted small', 'settings.accounts 里还没有任何平台凭据。'));
      } else {
        var kvId = el('div', 'kv');
        keys2.forEach(function (k) {
          var sec = isObj(doc2[k]) ? doc2[k] : {};
          var user = sec.username || sec.email || sec.user || '（未填用户名）';
          var rowId = el('div', 'kv__row');
          rowId.appendChild(el('div', 'kv__k', '账号标识 ' + k));
          rowId.appendChild(el('div', 'kv__v', String(user) + ' · 密码 ' + maskPw()));
          kvId.appendChild(rowId);
        });
        idbox.appendChild(kvId);
      }
    }
    idbox.appendChild(el('p', 'footnote',
      '这些只是同步对象里凭据的对象名与账号名，口令一律打码、绝不出现在页面上。'));
  }

  var sbox = $('set-sync');
  if (sbox) {
    clear(sbox);
    var kv3 = el('div', 'kv');
    var m2 = isObj(payload().meta) ? payload().meta : {};
    var rows = [
      ['数据来源', bridgeSourceLabel() + (st.dataSrc ? '' : '（本次会话尚未抓取）')],
      ['实时抓取时间', typeof m2.fetched_at === 'string' && m2.fetched_at ? prettyStamp(m2.fetched_at) : '—'],
      ['缓存', CACHE_TAG[m2.cache] || (m2.cache ? String(m2.cache) : '—')],
      ['日程（schedule）', st.schedule ? ('云端第 ' + st.schedule.revision + ' 版' +
          (scheduleEvents(st.schedule.doc).length ? ' · ' + scheduleEvents(st.schedule.doc).length + ' 条' : '')) : '未读取'],
      ['选课（' + OBJ.lessons + '）', st.lessons ? ('云端第 ' + st.lessons.revision + ' 版') : '未读取'],
      ['账号（' + OBJ.accounts + '）', st.accounts ? ('云端第 ' + st.accounts.revision + ' 版 · 只显示账号名') : '未读取'],
      ['降级快照（' + SCHOOL_OBJ + '）', st.sync ? ('云端第 ' + st.sync.revision + ' 版' +
          (syncStamp() ? ' · 时间 ' + syncStamp() : '')) : '未读取']
    ];
    rows.forEach(function (pair) {
      var row = el('div', 'kv__row');
      row.appendChild(el('div', 'kv__k', pair[0]));
      row.appendChild(el('div', 'kv__v', pair[1]));
      kv3.appendChild(row);
    });
    if (st.liveErr) {
      var rowErr = el('div', 'kv__row');
      rowErr.appendChild(el('div', 'kv__k', '这次抓取'));
      rowErr.appendChild(el('div', 'kv__v', st.liveErr));
      kv3.appendChild(rowErr);
    }
    sbox.appendChild(kv3);
    sbox.appendChild(el('p', 'footnote',
      '平台密码只存在你端到端加密的同步对象里，服务器只用于代抓，不会回传到浏览器。'));
  }

  renderDataSourceCard();
  updateLiveStamp('set');
}

/** 设置页「数据来源」卡：现在是谁在抓、为什么、怎么改成另一条路（一键启动说明）。 */
function renderDataSourceCard() {
  var box = $('set-source');
  if (!box) return;
  clear(box);
  var live = bridgeReady();
  var row = el('div', 'kv__row');
  row.appendChild(el('div', 'kv__k', '当前来源'));
  var v = el('div', 'kv__v');
  v.appendChild(el('span', 'tag tag--' + (live ? 'on' : 'off'), bridgeSourceLabel()));
  v.appendChild(document.createTextNode('　'));
  v.appendChild(el('span', 'muted small',
    live ? '本机直连：数据不经过服务器' : (bridgeOk ? '本机服务已就绪，但还没有平台账号' : '本机直连服务未探测到（可选）')));
  row.appendChild(v);
  box.appendChild(row);
  box.appendChild(el('p', 'notice__body', bridgeSourceNote()));
  var steps = el('ol', 'src-steps');
  var items = [
    '装一次依赖：python -m pip install requests beautifulsoup4 edupage-api',
    '双击 local-bridge\\start-bridge.cmd（或 python local-bridge\\bridge.py），它会只监听 127.0.0.1:38123',
    '刷新本页：本机服务在跑 → 显示「本机直连」；关掉窗口 → 自动回到「服务器抓取」'
  ];
  items.forEach(function (t) { steps.appendChild(el('li', null, t)); });
  box.appendChild(el('p', 'notice__body', '一键启动（可选加速 / 可用性增强）：'));
  box.appendChild(steps);
  box.appendChild(el('p', 'footnote',
    '本机服务只在内存里使用你的平台账号（不落盘、不写日志），只绑回环地址，'
    + '所以凭据不出本机；它跟服务器抓取返回的数据结构完全一致，随时可以关掉。'));
  var bar = el('div', 'retry-bar');
  var btn = el('button', 'ghost', '↻ 重新检测本机服务');
  btn.type = 'button';
  btn.addEventListener('click', function () {
    btn.disabled = true;
    btn.textContent = '正在检测…';
    bridgeProbe = null;
    bridgeOk = false;
    bridgeTried = false;
    bridgeAccounts = null;
    var ref = st.accounts;
    if (ref) bridgeLoadAccounts();
    bridgeProbeOnce().then(function () {
      renderDataSourceCard();
      renderSettings();
      toast(bridgeReady() ? '探测到本机直连服务' : '没有探测到本机服务（仍用服务器抓取）');
    });
  });
  bar.appendChild(btn);
  box.appendChild(bar);
}

/* -------------------------------------------------------------- 视图切换 */

var VIEWS = [
  { id: 'home', title: '首页' },
  { id: 'timetable', title: '我的课表' },
  { id: 'schedule', title: '我的日程' },
  { id: 'courses', title: '我的课程' },
  { id: 'mail', title: '平和邮箱' },
  { id: 'ai', title: 'Agent 助手' },
  { id: 'settings', title: '设置' }
];
var VIEW_IDS = VIEWS.map(function (v) { return v.id; });
var loaded = {};
var currentView = 'schedule';

function showView(name) {
  if (VIEW_IDS.indexOf(name) === -1) name = 'schedule';
  currentView = name;
  VIEWS.forEach(function (v) {
    var panel = $('view-' + v.id);
    var tab = document.querySelector('#tabs .tab[data-view="' + v.id + '"]');
    var on = (v.id === name);
    if (panel) panel.hidden = !on;
    if (panel) panel.classList.toggle('active', on);
    if (tab) {
      tab.classList.toggle('is-active', on);
      tab.setAttribute('aria-current', on ? 'true' : 'false');
    }
    if (on) $('view-title').textContent = v.title;
  });

  // 每个视图首次进入才发请求（实时抓取可能几秒，先把 loading 摆出来）
  if (name === 'home' && !loaded.home) { loaded.home = true; loadHome(); }
  if (name === 'timetable' && !loaded.timetable) { loaded.timetable = true; loadTimetable(); }
  if (name === 'schedule' && !loaded.schedule) { loaded.schedule = true; loadSchedule(); }
  if (name === 'courses' && !loaded.courses) { loaded.courses = true; loadCourses(); }
  if (name === 'mail' && !loaded.mail) { loaded.mail = true; loadMail(); }
  if (name === 'settings' && !loaded.settings) { loaded.settings = true; loadSettings(); }

  /* 课表行高是在 DOM 里量出来的：面板刚被 un-hide 时量一次（此刻才有真实高度）。
     渲染常常发生在别的视图（面板 hidden、量到 0），不在这里补一次，行高就会参差。 */
  if (name === 'timetable') { ttRefitSoon(0); ttUpdateNowLine(); }
}

/** 「刷新」：绕过缓存重新抓一次实时数据，并把当前视图的提示位写清楚。 */
/* 刷新时哪颗按钮要转圈（由 bindRefresh / retryInto 传进来）；
   抓完（无论成功失败）由 loadAppData 的收尾统一摘掉 loading。 */
function refreshLive(panelIds, btnIds) {
  st.live = null;
  st.liveErr = '';
  st.ttLabel = '';
  st.mbLabel = '';
  /* 刷新时清除乐观已读状态：用户主动刷新 = 要求最新数据，
     之前乐观标记的已读应被服务端真实状态覆盖。 */
  mailOptimisticReads.clear();
  beginRefreshButtons(btnIds);          // 点了就立刻有反馈（转圈 + 置灰）
  (panelIds || []).forEach(function (id) {
    var node = $(id);
    if (node && id !== 'tt-week') {
      clear(node);
      node.appendChild(el('p', 'muted', '正在重新抓取…'));
    }
  });
  if ((panelIds || []).indexOf('tt-week') !== -1) show($('tt-wait'), true);
  if ((panelIds || []).indexOf('mail-heads') !== -1) {
    closeMail();
    /* 刷新 = 要最新数据：本地正文缓存与预取队列一起清掉，列表回来后会重新预取 */
    mailBodyCache = {};
    mailPreloadQueue = [];
  }
  /* 课程视图：活动流（通知 / 消息 / 讨论）走另一条链路，刷新时一起重抓 */
  if ((panelIds || []).indexOf('mb-courses') !== -1) {
    st.coNotifs = null;
    st.coMsgs = null;
    loadCourseActivity(true)['catch'](function () {});
  }
  loadAppData(true);
}

function bindRefresh() {
  var top = $('btn-refresh');
  if (top) {
    top.addEventListener('click', function () {
      var panels = {
        home: ['home-lessons', 'home-events', 'home-ddl'],
        timetable: ['tt-week'],
        courses: ['mb-courses', 'mb-tasks', 'mb-notifications', 'mb-messages', 'mb-discussions'],
        mail: ['mail-heads'],
        settings: null
      };
      refreshLive(panels[currentView] || null, ['btn-refresh']);
    });
  }
}

function bindTabs() {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('#tabs .tab'));
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      showView(tab.getAttribute('data-view'));
      if (window.innerWidth <= 900) window.scrollTo({ top: 0 });
    });
  });

  // 首页上的大卡片可直接跳视图（go-card）
  Array.prototype.forEach.call(document.querySelectorAll('.go-card'), function (card) {
    card.addEventListener('click', function () { showView(card.getAttribute('data-go')); });
  });

  // 左上角的 logo + 名字：点了回首页（用户 2026-09-20 要求）
  var brand = $('logo');
  if (brand) {
    brand.addEventListener('click', function () {
      showView('home');
      if (window.innerWidth <= 900) window.scrollTo({ top: 0 });
    });
  }

  var toggle = $('side-toggle');
  var side = $('sidebar');
  if (toggle && side) {
    toggle.addEventListener('click', function () {
      var collapsed = side.classList.toggle('is-collapsed');
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    });
  }
}

/** 窄屏标签条兜底：若某个标签被压得太窄（一行真放不下时才会），
 *  让标签条改成横向可滚动 —— 绝不换行断字、也绝不把标签挤没。 */
function guardTabs() {
  var wrap = $('tabs');
  if (!wrap || window.innerWidth > 900) return;
  var tabs = wrap.querySelectorAll('.tab');
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].offsetWidth < 24) { wrap.style.overflowX = 'auto'; return; }
  }
  wrap.style.overflowX = '';
}

/* ------------------------------------------------------------ 数据加载 */

/** 课表工具条：上一周 / 本周 / 下一周（客户端同款）。 */
function bindTimetableTools() {
  var prev = $('tt-prev'), next = $('tt-next'), now = $('tt-this');
  if (prev) prev.addEventListener('click', function () { ttShiftWeek(-1); });
  if (next) next.addEventListener('click', function () { ttShiftWeek(1); });
  if (now) now.addEventListener('click', function () { ttShiftWeek(0); });
  /* 工具条上的「⬇ 导出课表」：点开一个站内小下拉（PNG / CSV），不用 window.confirm/prompt。
     没加载完时按钮是禁用的（见 updateTtExportState）；**禁用态的浏览器不派发 click**，
     所以那一下「点了没反应」由外层 .tt-export 接住并按 disabled 状态给一句为什么 —— 
     用户要的是「按钮禁用并提示」，不是「按下去毫无动静」。 */
  var expBtn = $('tt-export-btn');
  var expBox = $('tt-export');
  var clickedExport = function () {
    if (!updateTtExportState()) {
      toast('课表还没加载完，稍等一下再导出（数据一到就能导出）', 4200);
      return;
    }
    ttToggleExportMenu();
  };
  if (expBtn) expBtn.addEventListener('click', function (ev) {
    ev.preventDefault();
    clickedExport();
  });
  if (expBox) expBox.addEventListener('click', function (ev) {
    /* 只有「disabled 的按钮被点到」这一种情况需要这里兜底（其余由上面那个监听器处理） */
    if (ev.target === expBtn && expBtn.disabled) {
      ev.preventDefault();
      clickedExport();
    }
  });
  ['tt-export-png', 'tt-export-csv'].forEach(function (id) {
    var b = $(id);
    if (b) b.addEventListener('click', function (ev) {
      ev.preventDefault();
      ttExportSelect(id === 'tt-export-png' ? 'png' : 'csv');
    });
  });
  /* 点别处 / 按 Esc 收起菜单（菜单不挡课表点击） */
  document.addEventListener('click', function (ev) {
    var box = $('tt-export');
    if (box && !box.contains(ev.target)) ttCloseExportMenu();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') ttCloseExportMenu();
  });
  updateTtExportState();
  /* 工具栏常驻「选择我的教学组」按钮：任何状态都显示，点击打开模态选课面板 */
  var pickBtn = $('tt-pick-btn');
  if (pickBtn) pickBtn.addEventListener('click', openPickerModal);
  /* 没选课时「选择我的教学组」按钮在课表空态卡里（随课表整体重建），
     用事件委托挂在 document 上，重建后依然有效，不必每次重新绑。
     **与工具栏那颗按钮同一个模态**（同一份数据、同一套勾选、同一份保存逻辑）。 */
  document.addEventListener('click', function (ev) {
    if (ev.target.closest('#tt-pick-open')) {
      ev.preventDefault();
      openPickerModal();
    }
    // 空态卡里的选课按钮
    if (ev.target.closest('#nopick-save')) {
      ev.preventDefault();
      saveNopickPicker();
    }
    if (ev.target.closest('#nopick-cancel')) {
      ev.preventDefault();
      // 收起选课面板（不保存）
      var picker = $('nopick-picker');
      if (picker) picker.hidden = true;
    }
  });
  // 空态卡里的搜索框
  document.addEventListener('input', function (ev) {
    if (ev.target.id === 'nopick-filter') {
      renderNopickPicker();
    }
  });
  /* 空态卡里的勾选 → 同步进共享草稿（打开模态时看到的是同一套勾选状态） */
  document.addEventListener('change', function (ev) {
    if (ev.target && ev.target.type === 'checkbox' && ev.target.closest('#nopick-list')) {
      syncPickerDraftFrom($('nopick-list'));
    }
  });
}

var schedulePromise = null, lessonsPromise = null, accountsPromise = null;

function loadScheduleOnce() {
  if (st.schedule) return Promise.resolve(st.schedule);
  if (!schedulePromise) {
    schedulePromise = loadSchedule()['catch'](function (e) { schedulePromise = null; throw e; });
  }
  return schedulePromise;
}

/** 选课（教学组）仍来自同步对象 settings.lessons —— 它是用户的「选择」，不是平台抓来的。 */
function loadLessonsOnce() {
  if (st.lessons) return Promise.resolve(st.lessons);
  if (!lessonsPromise) {
    lessonsPromise = getObject(OBJ.lessons).then(function (ref) {
      st.lessons = ref;
      applyLive();
      return ref;
    })['catch'](function (e) { lessonsPromise = null; throw e; });
  }
  return lessonsPromise;
}

function loadAccountsOnce() {
  if (st.accounts) return Promise.resolve(st.accounts);
  if (!accountsPromise) {
    accountsPromise = getObject(OBJ.accounts).then(function (ref) {
      st.accounts = ref;
      bridgeLoadAccounts();          // 顺带把明文账号交给本机直连（只在内存里）
      renderMailAccounts(ref);
      applyLive();
      return ref;
    })['catch'](function (e) { accountsPromise = null; throw e; });
  }
  return accountsPromise;
}

/** 首页：时钟 + 今日课表 / 日程 + 未读 + ±14 天 DDL。 */
function loadHome() {
  startClock();
  loadScheduleOnce()['catch'](function () {}).then(function () { applyLive(); });
  return loadAppData();
}

/** 课表：实时抓取（本机直连优先 / 服务器兜底，抓 EduPage）+ 选课（settings.lessons）。 */
function loadTimetable() {
  loadLessonsOnce()['catch'](function (err) {
    if (err && err.unauthorized) return;
    panelMsg('tt-info', '选课数据加载失败：' + ((err && err.message) || '请稍后刷新'), true);
  });
  return loadAppData();
}

/** 邮箱：实时抓取邮件头 + settings.accounts（账号名 + 本机直连用的明文账号）。 */
function loadMail() {
  loadAccountsOnce()['catch'](function () {});
  ensureSync()['catch'](function () {});
  return loadAppData();
}

/** 课程视图：这次抓取可能要走本机直连，先把明文账号备好（没有也不影响服务器抓取）。 */
function loadCourses() {
  loadAccountsOnce()['catch'](function () {});
  ensureSync()['catch'](function () {});
  /* 课程活动流（通知 / 消息 / 讨论）与平台数据并行拉：两条链路互不阻塞，
     任一条失败都有各自的空态文案，绝不互相清空。 */
  loadCourseActivity(false)['catch'](function () {});
  return loadAppData();
}

/** 设置：账号名（实时）+ 同步状态（各对象修订号）+ AI 配置。 */
function loadSettings() {
  loadAccountsOnce()['catch'](function () {});
  loadLessonsOnce()['catch'](function () {});
  loadScheduleOnce()['catch'](function () {});
  loadAiConfig()['catch'](function () {});
  ensureSync()['catch'](function () {});          // 就位后 ensureSync 自己会补渲染一次
  return loadAppData();
}

/* ------------------------------------------------------------- 启动 */

function bindScheduleForm() {
  var form = $('sched-form');
  var closed = $('sched-foot-closed');
  var foot = $('sched-foot');
  var day = $('f-day');

  function clearForm() {
    $('f-time').value = '';
    $('f-title').value = '';
    $('f-note').value = '';
    day.value = '';
  }

  function open() {
    closed.hidden = true;
    foot.hidden = false;
    /* 日期默认「今天」；但如果用户在日历上点了某一天（有聚焦日期），就默认那一天 —— 
       「点某天 → 在那天记一笔」不该再让用户手动改日期。 */
    if (!day.value) day.value = (schState.focus && schParse(schState.focus)) ? schState.focus : todayStr();
    day.focus();
  }
  function close() {
    clearForm();                       // 逐个清空（不依赖 form.reset 的浏览器实现）
    foot.hidden = true;
    closed.hidden = false;
  }

  $('sched-open').addEventListener('click', open);
  if ($('sched-open2')) $('sched-open2').addEventListener('click', open);
  $('sched-cancel').addEventListener('click', close);

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var item = {
      day: day.value,
      time: $('f-time').value,
      title: $('f-title').value.trim(),
      note: $('f-note').value.trim()
    };
    if (!item.day) { toast('请选择日期'); day.focus(); return; }
    if (!item.title) { toast('请填写标题'); $('f-title').focus(); return; }
    if (!st.me.username) { toast('还没拿到用户名，请刷新页面'); return; }

    var btn = $('sched-add');
    btn.disabled = true;
    btn.textContent = '添加中…';
    addScheduleEvent(item).then(function () {
      toast('已添加');
      close();
    })['catch'](function (err) {
      if (err && err.unauthorized) return;
      toast(err.message || '添加失败');
    }).then(function () {
      btn.disabled = false;
      btn.textContent = '添加';
    });
  });
}

function bindCourses() {
  var backBtn = $('mb-back');
  if (backBtn) backBtn.addEventListener('click', closeCourseDetail);
}

/* ---------------------------------------------------------------- 通讯录
 *
 * 数据来自 `GET /app/mail/contacts/`：服务端从**收件箱 + 已发送**的邮件头里
 * 收割地址，(地址 → 姓名, 往来次数) 聚合并按频率排序。
 * 和桌面客户端 `MailService.contacts()` 是同一套做法（网易企业邮的个人账号
 * 没有 CardDAV/通讯录 API，那是管理员端能力）。
 */
var contactsCache = null;      // 本次会话内已取到的联系人
var contactsLoading = false;

function openContacts() {
  show($('contacts-modal'), true);
  if (!contactsCache && !contactsLoading) {
    loadContacts(false);
  } else {
    renderContacts();
  }
}

function closeContacts() { show($('contacts-modal'), false); }

function loadContacts(force) {
  var box = $('contacts-list');
  contactsLoading = true;
  if (box) {
    clear(box);
    box.appendChild(el('div', 'empty',
      '正在整理联系人…（要扫一遍收件箱与已发送，第一次大约几秒）'));
  }
  return req('GET', '/app/mail/contacts/' + (force ? '?force=1' : ''), undefined,
             { timeoutMs: DATA_TIMEOUT_MS })
    .then(function (res) {
      contactsLoading = false;
      if (res && res.status === 200 && isObj(res.body) && Array.isArray(res.body.contacts)) {
        contactsCache = res.body.contacts;
        renderContacts();
        return;
      }
      renderContactsError(errText(res));
    })['catch'](function (err) {
      contactsLoading = false;
      if (err && err.unauthorized) return;
      renderContactsError(err && err.isTimeout
        ? '整理超时了（超过 ' + Math.round(DATA_TIMEOUT_MS / 1000) + ' 秒），稍后再试'
        : ((err && err.message) || '网络错误'));
    });
}

function renderContactsError(message) {
  var box = $('contacts-list');
  if (!box) return;
  clear(box);
  box.appendChild(el('div', 'empty', '读不到通讯录：' + message));
  var again = el('button', 'ghost', '↻ 再试一次');
  again.type = 'button';
  again.style.marginTop = '8px';
  again.addEventListener('click', function () { loadContacts(true); });
  box.appendChild(again);
}

function renderContacts() {
  var box = $('contacts-list');
  if (!box) return;
  var all = contactsCache || [];
  var q = ($('contacts-q') && $('contacts-q').value || '').trim().toLowerCase();
  var rows = all.filter(function (c) {
    if (!q) return true;
    return ((c.name || '') + ' ' + (c.email || '')).toLowerCase().indexOf(q) !== -1;
  });
  if ($('contacts-count')) {
    $('contacts-count').textContent = q
      ? (rows.length + ' / ' + all.length + ' 位')
      : (all.length + ' 位联系人');
  }
  clear(box);
  if (!rows.length) {
    box.appendChild(el('div', 'empty', all.length
      ? '没有匹配的联系人。'
      : '还没有整理出联系人 —— 收件箱和已发送里都没有可识别的地址。'));
    return;
  }
  var list = el('div', 'list');
  rows.slice(0, 200).forEach(function (c) {
    var row = el('div', 'item contacts-row');
    row.tabIndex = 0;
    row.title = '点一下给 ' + (c.name || c.email) + ' 写邮件';
    var grow = el('span', 'grow');
    grow.appendChild(el('div', null, c.name || '（未署名）'));
    grow.appendChild(el('div', 'dim', c.email || ''));
    row.appendChild(grow);
    if (c.count) row.appendChild(el('span', 'dim', c.count + ' 封'));
    var write = function () {
      closeContacts();
      openCompose({ to: c.email || '', subject: '', body_text: '' });
    };
    row.addEventListener('click', write);
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); write(); }
    });
    list.appendChild(row);
  });
  box.appendChild(list);
}

function bindContacts() {
  var open = $('mail-contacts-btn');
  if (open) open.addEventListener('click', openContacts);
  var close = $('contacts-close');
  if (close) close.addEventListener('click', closeContacts);
  var q = $('contacts-q');
  if (q) q.addEventListener('input', renderContacts);
  var overlay = $('contacts-modal');
  if (overlay) {
    overlay.addEventListener('click', function (ev) {
      if (ev.target === overlay) closeContacts();
    });
  }
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') closeContacts();
  });
}

function bindMail() {
  var closeBtn = $('mail-close');
  if (closeBtn) closeBtn.addEventListener('click', closeMail);

  /* 写邮件按钮 */
  var composeBtn = $('mail-compose-btn');
  if (composeBtn) composeBtn.addEventListener('click', function () { openCompose(); });

  /* 撰写面板：取消 */
  var cancelBtn = $('compose-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', closeCompose);

  /* 撰写面板：发送 */
  var sendBtn = $('compose-send');
  if (sendBtn) sendBtn.addEventListener('click', sendCompose);

  /* 撰写面板：CC 折叠/展开 */
  var ccToggle = $('compose-toggle-cc');
  if (ccToggle) ccToggle.addEventListener('click', function () {
    var row = $('compose-cc-row');
    if (row) {
      var hidden = row.hidden;
      row.hidden = !hidden;
      ccToggle.textContent = hidden ? '− 抄送' : '+ 抄送';
    }
  });

  /* 撰写面板：添加附件（原生 file 输入；用按钮触发，样式统一） */
  var attachBtn = $('compose-attach-btn');
  var attachInput = $('compose-attach-input');
  if (attachBtn && attachInput) {
    attachBtn.addEventListener('click', function () { attachInput.click(); });
    attachInput.addEventListener('change', function () {
      composeAddFiles(attachInput.files);
      /* 清空 value：同一个文件连选两次也要能再次触发 change */
      attachInput.value = '';
    });
  }

  /* 撰写面板：点击遮罩关闭 */
  var overlay = $('compose-overlay');
  if (overlay) {
    overlay.addEventListener('click', function (ev) {
      if (ev.target === overlay) closeCompose();
    });
  }

  /* 撰写面板：输入校验 → 启用/禁用发送按钮 */
  ['compose-to', 'compose-subject', 'compose-body'].forEach(function (id) {
    var inp = $(id);
    if (inp) inp.addEventListener('input', validateCompose);
  });

  /* ESC 关闭撰写面板 */
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') {
      var co = $('compose-overlay');
      if (co && !co.hidden) closeCompose();
    }
  });
}

/* ===================== ⑤ 撰写邮件 ===================== */

/** 发送邮件：本机直连优先，失败回落服务器。
 *
 *  `isForm` 为真时 `data` 是一份 `FormData`（带附件那条路）：
 *  本机直连桥只认 JSON，**带附件时不走桥**，直接打服务器 —— 服务器的
 *  `/app/mail/send/` 两种 Content-Type 都收（见 server.py `_parse_mail_send_body`）。
 */
function sendMail(data, isForm) {
  if (isForm) {
    return req('POST', MAIL_SEND_PATH, data, { timeoutMs: DATA_TIMEOUT_MS });
  }
  if (!bridgeReady()) {
    return req('POST', MAIL_SEND_PATH, data, { timeoutMs: DATA_TIMEOUT_MS });
  }
  var body = { accounts: bridgeAccounts, to: data.to, subject: data.subject,
               body_text: data.body_text };
  if (data.cc) body.cc = data.cc;
  if (data.in_reply_to) body.in_reply_to = data.in_reply_to;
  return bridgePost(BRIDGE_MAIL_SEND, body, DATA_TIMEOUT_MS)
    ['catch'](function () { return null; })
    .then(function (local) {
      if (local && local.status === 200) return local;
      if (local && local.status === 409) return local;   // 没配邮箱账号
      bridgeOk = false;
      return req('POST', MAIL_SEND_PATH, data, { timeoutMs: DATA_TIMEOUT_MS });
    });
}

/** 打开撰写面板（新邮件 or 回复）。opts: {to, subject, body_text, in_reply_to, title} */
/** 撰写窗口里待发送的附件（File 对象）。限额与桌面客户端、服务端三边保持一致
 *  （20 个 / 单个 20 MB / 合计 20 MB），免得同一份文件在别处能发、这里被拒。 */
var composeAttachments = [];
var COMPOSE_MAX_ATTACHMENTS = 20;
var COMPOSE_MAX_FILE_BYTES = 20 * 1024 * 1024;
var COMPOSE_MAX_TOTAL_BYTES = 20 * 1024 * 1024;

/** 加文件到待发列表。返回真正加进去的个数。超限只提示、不抛。 */
function composeAddFiles(files, opts) {
  var arr = Array.prototype.slice.call(files || []);
  var msg = $('compose-msg');
  var added = 0;
  arr.forEach(function (f) {
    if (!f || typeof f.name !== 'string') return;
    if (composeAttachments.length >= COMPOSE_MAX_ATTACHMENTS) {
      if (msg && !(opts && opts.silent)) {
        msg.textContent = '附件最多 ' + COMPOSE_MAX_ATTACHMENTS + ' 个。';
      }
      return;
    }
    if (f.size > COMPOSE_MAX_FILE_BYTES) {
      if (msg && !(opts && opts.silent)) {
        msg.textContent = '「' + f.name + '」太大（单个最多 ' +
          (COMPOSE_MAX_FILE_BYTES / 1024 / 1024) + ' MB）。';
      }
      return;
    }
    var total = composeAttachments.reduce(function (n, x) { return n + x.size; }, 0) + f.size;
    if (total > COMPOSE_MAX_TOTAL_BYTES) {
      if (msg && !(opts && opts.silent)) {
        msg.textContent = '附件合计超过 ' + (COMPOSE_MAX_TOTAL_BYTES / 1024 / 1024) + ' MB。';
      }
      return;
    }
    if (composeAttachments.some(function (x) {
      return x.name === f.name && x.size === f.size;
    })) return;                       // 同名同大小视为同一份，不重复加
    composeAttachments.push(f);
    added += 1;
  });
  renderComposeAttachments();
  validateCompose();
  return added;
}

function composeRemoveFile(index) {
  composeAttachments.splice(index, 1);
  renderComposeAttachments();
  validateCompose();
}

/** 画待发附件列表（名字 + 大小 + 移除按钮）。
 *
 *  限额说明就挂在这一行里（`未选择附件` / `2 个，合计 3.1 MB / 上限 20 MB`）——
 *  原来是撰写窗底部单独一行技术说明「单个附件最大 20 MB，合计最大 20 MB，最多 20 个
 *  （与本机客户端一致）。」，用户 2026-09-17 要求删掉（"与本机客户端一致"这类话
 *  不该露给用户）。删掉不等于藏起来：真要超的时候下面照样有中文原因。
 */
function renderComposeAttachments() {
  var box = $('compose-att-list');
  var summary = $('compose-att-summary');
  if (summary) {
    var n = composeAttachments.length;
    var cap = mailBytes(COMPOSE_MAX_TOTAL_BYTES);
    summary.textContent = n
      ? (n + ' 个，合计 ' +
         mailBytes(composeAttachments.reduce(function (s, x) { return s + x.size; }, 0)) +
         ' / 上限 ' + cap)
      : '未选择附件（最多 ' + COMPOSE_MAX_ATTACHMENTS + ' 个，合计 ' + cap + '）';
  }
  if (!box) return;
  clear(box);
  composeAttachments.forEach(function (f, i) {
    var row = el('div', 'compose-att__item');
    row.appendChild(el('span', 'compose-att__name', '📄 ' + f.name));
    row.appendChild(el('span', 'muted small', mailBytes(f.size)));
    var del = el('button', 'ghost compose-att__del', '✕');
    del.type = 'button';
    del.title = '移除「' + f.name + '」';
    del.addEventListener('click', function () { composeRemoveFile(i); });
    row.appendChild(del);
    box.appendChild(row);
  });
}

function openCompose(opts) {
  var overlay = $('compose-overlay');
  if (!overlay) return;
  overlay.hidden = false;
  var to = $('compose-to');
  var cc = $('compose-cc');
  var subj = $('compose-subject');
  var body = $('compose-body');
  var replyTo = $('compose-reply-to');
  var title = $('compose-title');
  var msg = $('compose-msg');
  var ccRow = $('compose-cc-row');
  var ccToggle = $('compose-toggle-cc');

  if (to) to.value = (opts && opts.to) || '';
  /* 注意这里是 `opts.cc` 而不是 `cc` —— `cc` 是本函数里**刚取到的那个 DOM 元素**
     （见上面 `var cc = $('compose-cc')`）。写成 `(opts && cc)` 会把这个元素本身
     赋给输入框，String(element) 的结果就是字面量 "[object HTMLInputElement]"；
     于是从「回复 / 转发 / 通讯录点人」进撰写窗（都会传 opts）之后，
     哪怕用户根本没碰抄送，发信时也会带上一个叫 "[object HTMLInputElement]" 的抄送地址，
     服务端只能回「抄送地址格式非法：…」。**这个坑踩过一次，别再把 opts. 去掉。** */
  if (cc) cc.value = (opts && opts.cc) || '';
  if (subj) subj.value = (opts && opts.subject) || '';
  if (body) body.value = (opts && opts.body_text) || '';
  if (replyTo) replyTo.value = (opts && opts.in_reply_to) || '';
  if (title) title.textContent = (opts && opts.title) || '写邮件';
  if (msg) msg.textContent = '';
  /* 每次打开都是一份**干净**的草稿：清掉上一次的附件（转发时会重新带原附件） */
  composeAttachments = [];
  renderComposeAttachments();
  /* 回复时默认折叠 CC（原邮件可能没有 CC） */
  if (ccRow) ccRow.hidden = true;
  if (ccToggle) ccToggle.textContent = '+ 抄送';
  /* 启用发送按钮（有内容时） */
  validateCompose();
  var sendBtn = $('compose-send');
  if (sendBtn && (!to || !to.value.trim())) { sendBtn.disabled = true; sendBtn.textContent = '发送'; }
  if (to) to.focus();
}

function closeCompose() {
  var overlay = $('compose-overlay');
  if (overlay) overlay.hidden = true;
  composeAttachments = [];
  st.composeFor = '';
  st.composeMode = '';
}

/** 回复邮件：自动填写收件人/主题/正文引用。 */
function replyToMail(uid) { composeFromMail(uid, 'reply'); }

/** 转发邮件：收件人留空、主题 Fwd:、带上原文引用**与原附件**。 */
function forwardMail(uid) { composeFromMail(uid, 'forward'); }

/** 从一封已读邮件起一份撰写草稿。
 *
 *  语义照抄 CipherCore E-Mail Suite（`D:\phix\_lab\ciphercore\cces.py` 的
 *  `_open_compose_email_window(mode='reply'|'forward')`，2287–2540 行）：
 *    * **回复**：收件人 = `Reply-To` 优先，否则 `From`；主题加 `Re: `（已有就不重复加）；
 *    * **转发**：收件人**留空**（由用户填）；主题加 `Fwd: `；
 *    * 两者都把原文**用 `> ` 逐行引用**、放在正文里，光标语义上落在引用上方。
 *  比参考多做的一点：**转发把原邮件的附件一并带上**（CipherCore 那里留了个 TODO，
 *  但通行做法就是带附件，否则「转发」等于把附件丢了）。回复**不带**原附件。
 */
function composeFromMail(uid, mode) {
  var row = mailRowOf(uid);
  if (!row) { toast('找不到这封邮件'); return; }

  var fromRaw = row.from || '';
  var addrMatch = /<([^>]+)>/.exec(fromRaw);
  var fromAddr = addrMatch ? addrMatch[1].trim() : '';
  if (!fromAddr && fromRaw.indexOf('@') !== -1) fromAddr = fromRaw.trim();

  var origSubject = String(row.subject || '');
  var prefix = (mode === 'forward') ? 'Fwd: ' : 'Re: ';
  /* 已经带了同样的前缀就不重复加（`re:` / `fwd:` 忽略大小写） */
  var already = (mode === 'forward')
    ? /^\s*(fwd|fw)\s*:/i.test(origSubject)
    : /^\s*re\s*:/i.test(origSubject);
  var subject = already ? origSubject : (prefix + origSubject);

  /* 引用块：与参考实现同构，文案用中文 */
  var quote = [];
  quote.push('---------- 原始邮件 ----------');
  quote.push((row.date ? prettyStamp(row.date) : '时间未知') + '，' + (fromRaw || '（未知发件人）') + ' 写道：');
  quote.push('');
  var bodyBox = document.querySelector('#mail-detail .mail-body, #mail-detail .mail-view__body');
  var cached = mailBodyCache[String(uid)];
  var origText = '';
  if (bodyBox) origText = bodyBox.textContent || '';
  else if (cached && cached.body_text) origText = cached.body_text;
  if (origText) {
    origText.split('\n').forEach(function (line) { quote.push('> ' + line); });
  } else {
    quote.push('> （原文正文还没加载，可以在引用上方直接写你要说的话）');
    /* 正文没在手就先把它取回来，取到后补进已经打开的撰写窗口 */
    fetchMailBody(uid).then(function (res) {
      if (!(res && res.status === 200 && isObj(res.body) && isObj(res.body.mail))) return;
      var m = res.body.mail;
      mailBodyCache[String(uid)] = {
        body_text: m.body_text || '',
        body_html: m.body_html || '',
        attachments: Array.isArray(m.attachments) ? m.attachments : []
      };
      var box = $('compose-body');
      var to = $('compose-to');
      if (!box || box.value.indexOf('> （原文正文还没加载') === -1) return;
      /* 只在这条草稿还"没被动过"时补引用，绝不覆盖用户已经打的字 */
      if (to && String(st.composeFor || '') !== String(uid)) return;
      var lines = [];
      lines.push('---------- 原始邮件 ----------');
      lines.push((row.date ? prettyStamp(row.date) : '时间未知') + '，' + (fromRaw || '（未知发件人）') + ' 写道：');
      lines.push('');
      (m.body_text || '').split('\n').forEach(function (line) { lines.push('> ' + line); });
      box.value = lines.join('\n');
      validateCompose();
      /* 转发：附件补上 */
      if (mode === 'forward') composeAddOriginalAttachments(uid, m.attachments);
    })['catch'](function () {});
  }

  st.composeFor = String(uid);
  st.composeMode = mode;

  var opts = {
    to: (mode === 'reply') ? fromAddr : '',
    subject: subject,
    body_text: quote.join('\n'),
    in_reply_to: (mode === 'reply') ? uid : '',
    title: (mode === 'reply' ? '回复 · ' : '转发 · ') + (origSubject || '无主题')
  };
  if (mode === 'reply' && !fromAddr) {
    toast('这封邮件没有可用的发件人地址，请手动填写收件人');
  }

  /* 转发：如果正文已经缓存（含附件清单），立刻带上原附件 */
  if (mode === 'forward' && cached) composeAddOriginalAttachments(uid, cached.attachments);

  openCompose(opts);
  /* 转发时焦点给收件人（要用户填）；回复时焦点给正文顶部（用户直接写） */
  var toInput = $('compose-to');
  var bodyInput = $('compose-body');
  if (mode === 'forward' && toInput) toInput.focus();
  else if (bodyInput) { bodyInput.focus(); bodyInput.setSelectionRange(0, 0); }
}

/** 把原邮件的附件拉进当前撰写草稿（转发用）。
 *
 *  附件内容是**在浏览器里从下载接口取回来的**：服务端只提供单附件字节流，
 *  我们在这里转成 File 塞进待发列表，用户能在发送前逐个删掉。
 *  超限或取不到只提示、不阻断写信。
 */
function composeAddOriginalAttachments(uid, attachments) {
  var list = Array.isArray(attachments) ? attachments : [];
  list.forEach(function (att) {
    if (!isObj(att) || att.index == null) return;
    var name = cellText(att.filename || ('附件 ' + att.index));
    if (composeAttachments.some(function (f) { return f.name === name; })) return;
    var url = '/app/mail/' + encodeURIComponent(String(uid)) +
              '/attachments/' + Number(att.index) + '/';
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.blob();
      })
      .then(function (blob) {
        var file = new File([blob], name, { type: blob.type || 'application/octet-stream' });
        var added = composeAddFiles([file], { silent: true });
        if (added) {
          var msg = $('compose-msg');
          if (msg) msg.textContent = '已把原邮件的 ' + added + ' 个附件一并带上（可逐个移除）。';
        }
      })['catch'](function () {
        var msg = $('compose-msg');
        if (msg) msg.textContent = '原邮件的附件「' + name + '」没能取回来，可以手动添加。';
      });
  });
}

/** 校验撰写面板：收件人/主题/正文都非空时才启用发送按钮。 */
function validateCompose() {
  var to = $('compose-to');
  var subj = $('compose-subject');
  var body = $('compose-body');
  var sendBtn = $('compose-send');
  if (!sendBtn) return;
  var toVal = to ? to.value.trim() : '';
  var subjVal = subj ? subj.value.trim() : '';
  var bodyVal = body ? body.value.trim() : '';
  sendBtn.disabled = !(toVal && subjVal && bodyVal);
}

/** 发送撰写面板的邮件。 */
function sendCompose() {
  var to = $('compose-to');
  var cc = $('compose-cc');
  var subj = $('compose-subject');
  var body = $('compose-body');
  var replyTo = $('compose-reply-to');
  var msg = $('compose-msg');
  var sendBtn = $('compose-send');

  var toVal = to ? to.value.trim() : '';
  var ccVal = cc ? cc.value.trim() : '';
  var subjVal = subj ? subj.value.trim() : '';
  var bodyVal = body ? body.value : '';
  var replyVal = replyTo ? replyTo.value.trim() : '';

  /* 校验 */
  if (!toVal) { if (msg) msg.textContent = '请填写收件人'; if (to) to.focus(); return; }
  if (!subjVal) { if (msg) msg.textContent = '请填写主题'; if (subj) subj.focus(); return; }
  if (!bodyVal.trim()) { if (msg) msg.textContent = '请填写正文'; if (body) body.focus(); return; }

  /* 地址合法性检查：至少一个地址包含 @ */
  var addrs = toVal.split(/[,;]\s*/).filter(Boolean);
  var badAddr = addrs.some(function (a) { return a.indexOf('@') === -1; });
  if (badAddr) { if (msg) msg.textContent = '收件人地址格式不对（需要包含 @）'; return; }

  /* 检查收件人数量 ≤ 20 */
  if (addrs.length > 20) { if (msg) msg.textContent = '收件人不能超过 20 个'; return; }

  /* 抄送同样在本地先校验一遍：抄送填错时**当场**说清楚，
     而不是把请求打出去、等服务器回一句「抄送地址格式非法」。
     （顺带把「抄送框里混进了非地址内容」这类问题挡在前端——历史上真的出现过
     抄送框被塞进 "[object HTMLInputElement]" 的情况，见 openCompose 里的注释。） */
  var ccAddrs = ccVal ? ccVal.split(/[,;]\s*/).filter(Boolean) : [];
  var badCc = ccAddrs.some(function (a) { return a.indexOf('@') === -1; });
  if (badCc) {
    if (msg) msg.textContent = '抄送地址格式不对（需要包含 @）：' + ccAddrs.filter(function (a) {
      return a.indexOf('@') === -1;
    })[0];
    var ccBox = $('compose-cc');
    if (ccBox) ccBox.focus();
    return;
  }
  if (ccAddrs.length > 20) { if (msg) msg.textContent = '抄送不能超过 20 个'; return; }

  /* 发送中状态 */
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = '发送中…'; }
  if (msg) msg.textContent = '';

  var data = { to: toVal, subject: subjVal, body_text: bodyVal };
  if (ccVal) data.cc = ccVal;
  if (replyVal) data.in_reply_to = replyVal;

  /* 有附件 → 走 multipart/form-data（浏览器原生 FormData，文件不读进 JS 内存）；
     没附件 → 保持原来的 JSON 路径（本机直连桥也认这一种）。 */
  var payload;
  var hasFiles = composeAttachments.length > 0;
  if (hasFiles) {
    payload = new FormData();
    payload.append('to', toVal);
    payload.append('subject', subjVal);
    payload.append('body_text', bodyVal);
    if (ccVal) payload.append('cc', ccVal);
    if (replyVal) payload.append('in_reply_to', replyVal);
    composeAttachments.forEach(function (f) { payload.append('attachments', f, f.name); });
  } else {
    payload = data;
  }

  sendMail(payload, hasFiles).then(function (res) {
    if (res && res.status === 200 && res.body && res.body.ok) {
      toast('已发送' + (hasFiles ? '（含 ' + composeAttachments.length + ' 个附件）' : ''));
      closeCompose();
      return;
    }
    /* 失败：展示 error.message，不清空用户内容 */
    var e = errText(res);
    var code = (res && res.body && res.body.error && res.body.error.code) || '';
    if (res && res.status === 409) {
      e = '还没有配置邮箱账号：请到「个人中心 → 密码管理」添加邮箱凭据后重试。';
    } else if (res && res.status === 413) {
      e = e || '附件太大：单个最大 20 MB、合计最大 20 MB。';
    } else if (res && res.status === 429) {
      e = '发送太频繁了，请稍等一分钟再试。';
    } else if (code === 'smtp_auth_failed') {
      e = '邮箱授权码不对：请到「个人中心 → 密码管理」更新邮箱凭据。';
    } else if (code === 'smtp_connection_failed') {
      e = '连不上邮件服务器：请检查网络或稍后重试。';
    } else if (code === 'recipient_rejected') {
      e = '收件人地址被邮件服务器拒绝：请检查收件人地址是否正确。';
    }
    if (msg) msg.textContent = e;
    if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = '发送'; }
  })['catch'](function (err) {
    if (err && err.unauthorized) return;
    if (msg) msg.textContent = (err && err.message) || '网络错误，请稍后重试';
    if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = '发送'; }
  });
}

/* ===================== 选课模态（工具栏按钮常驻入口） =====================
 *
 *  工具条的「✏️ 选择我的教学组」和空态卡里的「选择我的教学组」都走**这一个**模态，
 *  渲染与保存用的是上面那条共享路径（`renderSubjectPickerInto` / `savePickerSelection`）
 *  —— 两个入口同一份数据、同一份勾选状态、同一份保存逻辑。 */

/** 打开选课模态：预勾选当前选课，按科目分组渲染列表。 */
function openPickerModal() {
  var overlay = $('picker-overlay');
  if (!overlay) return;
  overlay.hidden = false;

  /* 重开时清掉上一次的搜索词（渲染前先清，`renderPickerList` 读的就是这个框的值） */
  var filterBox = $('picker-filter');
  if (filterBox) filterBox.value = '';

  /* 草稿以**已保存的选课**为准 → 有选课时打开模态是预勾选当前选课（既有要求，别退化） */
  pickerTouched = false;
  renderPickerList();      // ← 必须画进模态自己的 #picker-list（这次漏的就是这一步）
  syncPickerHosts();       // 同一时刻只留一个选课界面：卡片里那份收起
  var btn = $('tt-pick-btn');
  if (btn) btn.setAttribute('aria-expanded', 'true');
}

function closePickerModal() {
  var overlay = $('picker-overlay');
  if (overlay) overlay.hidden = true;
  var btn = $('tt-pick-btn');
  if (btn) btn.setAttribute('aria-expanded', 'false');
  /* 关掉模态后把课表空态卡里的选课界面恢复出来（没选课时那是「一步可达」的入口），
     草稿是共享的，所以刚在模态里勾的、没保存的勾选在这里照样在。 */
  syncPickerHosts();
}

/** 保存模态里的选课（写 settings.lessons → 共享的那条写回路径）。 */
function savePickerModal() {
  savePickerSelection({ overlay: true, btnId: 'picker-save', label: '保存选课' });
}

function bindPickerModal() {
  var overlay = $('picker-overlay');
  if (!overlay) return;
  overlay.addEventListener('click', function (ev) {
    if (ev.target === overlay) closePickerModal();   // 点遮罩关闭
  });
  var save = $('picker-save');
  if (save) save.addEventListener('click', savePickerModal);
  var cancel = $('picker-cancel');
  if (cancel) cancel.addEventListener('click', closePickerModal);
  var filter = $('picker-filter');
  if (filter) filter.addEventListener('input', function () { renderPickerList(); });
  /* 模态里的勾选 → 同步进共享草稿（关模态后卡片里那份渲染出来的是同一套勾选） */
  overlay.addEventListener('change', function (ev) {
    if (ev.target && ev.target.type === 'checkbox') syncPickerDraftFrom($('picker-list'));
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !overlay.hidden) closePickerModal();
  });
}

/* ===================== 设置页：修改密码 ===================== */

function bindChangePassword() {
  var btn = $('set-change-pw-btn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    var cur = String(($('set-cur-pw') && $('set-cur-pw').value) || '');
    var nw = String(($('set-new-pw') && $('set-new-pw').value) || '');
    var cf = String(($('set-confirm-pw') && $('set-confirm-pw').value) || '');
    var msgBox = $('set-pw-msg');
    if (!cur) { if (msgBox) msgBox.textContent = '请填写当前密码'; return; }
    if (!nw) { if (msgBox) msgBox.textContent = '请填写新密码'; return; }
    if (nw.length < 6) { if (msgBox) msgBox.textContent = '新密码至少 6 位'; return; }
    if (nw !== cf) { if (msgBox) msgBox.textContent = '两次新密码不一致'; return; }
    btn.disabled = true;
    btn.textContent = '修改中…';
    if (msgBox) msgBox.textContent = '';
    req('POST', '/auth/password/', {
      old_password: cur,
      new_password: nw,
      confirm_password: cf
    }).then(function (res) {
      if (res.status === 200) {
        if (msgBox) msgBox.textContent = '密码已修改，其它设备需重新登录';
        toast('密码已修改，其它设备需重新登录');
        $('set-cur-pw').value = '';
        $('set-new-pw').value = '';
        $('set-confirm-pw').value = '';
      } else {
        var e = errText(res);
        /* 后端可能因为缺少 DEK 材料而报 400 —— 如实提示用户走个人中心 */
        if (res.body && res.body.error && res.body.error.code === 'bad_request') {
          e += '。网页端暂不支持完整密钥重包裹，请到「个人中心」修改密码。';
        }
        if (msgBox) msgBox.textContent = e;
      }
    })['catch'](function (err) {
      if (msgBox) msgBox.textContent = (err && err.message) || '网络错误';
    }).then(function () {
      btn.disabled = false;
      btn.textContent = '修改密码';
    });
  });
}

/* ===================== 设置页：AI 服务商管理 ===================== */

var OBJ_AI = 'settings.ai';

/** AI 服务商的预设模板。 */
var AI_PRESETS = {
  deepseek: { name: 'DeepSeek', protocol: 'openai', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  kimi:     { name: 'Kimi', protocol: 'openai', base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  glm:      { name: '智谱 GLM', protocol: 'openai', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  qwen:     { name: '通义千问', protocol: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-turbo' },
  custom:   { name: '自定义', protocol: 'openai', base_url: '', model: '' }
};

var aiConfig = { providers: [], default_index: 0, revision: 0, doc: null };

function loadAiConfig() {
  return getObject(OBJ_AI).then(function (ref) {
    aiConfig.revision = ref.revision;
    aiConfig.doc = ref.doc;
    var doc = ref.doc;
    if (isObj(doc) && Array.isArray(doc.providers)) {
      aiConfig.providers = doc.providers;
      aiConfig.default_index = typeof doc.default_index === 'number' ? doc.default_index : 0;
    } else {
      aiConfig.providers = [];
      aiConfig.default_index = 0;
    }
    renderAiProviders();
  })['catch'](function () {
    aiConfig.providers = [];
    aiConfig.default_index = 0;
    aiConfig.doc = null;
    renderAiProviders();
  });
}

/** 遮掩密钥：只显示前 4 位 + 后 4 位，中间用圆点替代。 */
function maskApiKey(key) {
  if (!key) return '（未设置）';
  var s = String(key);
  if (s.length <= 8) return '••••••••';
  return s.slice(0, 4) + '••••' + s.slice(-4);
}

function renderAiProviders() {
  var box = $('set-ai-providers');
  if (!box) return;
  clear(box);

  /* 下拉列表：当前使用哪个服务商 */
  var defaultSel = $('set-ai-default');
  if (defaultSel) {
    clear(defaultSel);
    aiConfig.providers.forEach(function (p, i) {
      var opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = (p.name || ('服务商 ' + (i + 1))) + (i === aiConfig.default_index ? ' ★' : '');
      if (i === aiConfig.default_index) opt.selected = true;
      defaultSel.appendChild(opt);
    });
  }

  if (!aiConfig.providers.length) {
    box.appendChild(el('p', 'empty', '还没有配置任何 AI 服务商。点下方「＋ 添加服务商」开始。'));
    return;
  }

  aiConfig.providers.forEach(function (p, idx) {
    var card = el('div', 'kv');
    card.style.marginBottom = '10px';

    /* 行：名称 */
    var r1 = el('div', 'kv__row');
    r1.appendChild(el('div', 'kv__k', '名称'));
    var v1 = el('div', 'kv__v');
    var nameInput = el('input', null);
    nameInput.type = 'text';
    nameInput.value = p.name || '';
    nameInput.placeholder = '服务商名称';
    nameInput.style.maxWidth = '200px';
    nameInput.setAttribute('data-ai-idx', String(idx));
    nameInput.setAttribute('data-ai-field', 'name');
    v1.appendChild(nameInput);
    if (idx === aiConfig.default_index) v1.appendChild(el('span', 'tag', '默认'));
    r1.appendChild(v1);
    card.appendChild(r1);

    /* 行：协议 + Base URL */
    var r2 = el('div', 'kv__row');
    r2.appendChild(el('div', 'kv__k', '协议 / Base URL'));
    var v2 = el('div', 'kv__v');
    var protoSelect = el('select', null);
    ['openai', 'ollama'].forEach(function (pr) {
      var opt = document.createElement('option');
      opt.value = pr;
      opt.textContent = pr;
      if (p.protocol === pr) opt.selected = true;
      protoSelect.appendChild(opt);
    });
    protoSelect.setAttribute('data-ai-idx', String(idx));
    protoSelect.setAttribute('data-ai-field', 'protocol');
    protoSelect.style.maxWidth = '100px';
    v2.appendChild(protoSelect);
    v2.appendChild(document.createTextNode(' '));
    var urlInput = el('input', null);
    urlInput.type = 'text';
    urlInput.value = p.base_url || '';
    urlInput.placeholder = 'https://api.example.com/v1';
    urlInput.style.flex = '1';
    urlInput.setAttribute('data-ai-idx', String(idx));
    urlInput.setAttribute('data-ai-field', 'base_url');
    v2.appendChild(urlInput);
    r2.appendChild(v2);
    card.appendChild(r2);

    /* 行：模型 + API Key */
    var r3 = el('div', 'kv__row');
    r3.appendChild(el('div', 'kv__k', '模型 / 密钥'));
    var v3 = el('div', 'kv__v');
    var modelInput = el('input', null);
    modelInput.type = 'text';
    modelInput.value = p.model || '';
    modelInput.placeholder = '模型名（如 deepseek-chat）';
    modelInput.style.maxWidth = '200px';
    modelInput.setAttribute('data-ai-idx', String(idx));
    modelInput.setAttribute('data-ai-field', 'model');
    v3.appendChild(modelInput);
    v3.appendChild(document.createTextNode(' '));
    var keyInput = el('input', null);
    keyInput.type = 'password';
    keyInput.value = p.api_key || '';
    keyInput.placeholder = 'API Key';
    keyInput.style.flex = '1';
    keyInput.setAttribute('data-ai-idx', String(idx));
    keyInput.setAttribute('data-ai-field', 'api_key');
    v3.appendChild(keyInput);
    var eyeBtn = el('button', 'ghost', '👁');
    eyeBtn.type = 'button';
    eyeBtn.title = '显示/隐藏密钥';
    eyeBtn.style.padding = '4px 8px';
    eyeBtn.addEventListener('click', function () {
      keyInput.type = (keyInput.type === 'password') ? 'text' : 'password';
    });
    v3.appendChild(eyeBtn);
    r3.appendChild(v3);
    card.appendChild(r3);

    /* 行：删除按钮 */
    var r4 = el('div', 'kv__row');
    r4.appendChild(el('div', 'kv__k', ''));
    var v4 = el('div', 'kv__v');
    var delBtn = el('button', 'danger', '删除此服务商');
    delBtn.type = 'button';
    delBtn.style.fontSize = '12px';
    delBtn.addEventListener('click', function () { deleteAiProvider(idx); });
    v4.appendChild(delBtn);
    r4.appendChild(v4);
    card.appendChild(r4);

    box.appendChild(card);
  });
}

function collectAiProviders() {
  var providers = [];
  var inputs = document.querySelectorAll('#set-ai-providers [data-ai-idx]');
  var byIdx = {};
  inputs.forEach(function (inp) {
    var idx = Number(inp.getAttribute('data-ai-idx'));
    var field = inp.getAttribute('data-ai-field');
    if (!byIdx[idx]) byIdx[idx] = {};
    byIdx[idx][field] = inp.type === 'password' ? inp.value : inp.value;
  });
  Object.keys(byIdx).sort(function (a, b) { return Number(a) - Number(b); }).forEach(function (i) {
    var p = byIdx[Number(i)];
    providers.push({
      name: p.name || '',
      protocol: p.protocol || 'openai',
      base_url: p.base_url || '',
      model: p.model || '',
      api_key: p.api_key || ''
    });
  });
  return providers;
}

function addAiProvider(presetKey) {
  var preset = AI_PRESETS[presetKey] || AI_PRESETS.custom;
  aiConfig.providers.push({
    name: preset.name, protocol: preset.protocol,
    base_url: preset.base_url, model: preset.model, api_key: ''
  });
  renderAiProviders();
}

function deleteAiProvider(idx) {
  aiConfig.providers.splice(idx, 1);
  if (aiConfig.default_index >= aiConfig.providers.length) {
    aiConfig.default_index = Math.max(0, aiConfig.providers.length - 1);
  }
  renderAiProviders();
}

function saveAiConfig() {
  var providers = collectAiProviders();
  var defaultSel = $('set-ai-default');
  var defIdx = defaultSel ? Number(defaultSel.value) || 0 : 0;
  if (defIdx >= providers.length) defIdx = 0;

  var doc = aiConfig.doc && isObj(aiConfig.doc) ? JSON.parse(JSON.stringify(aiConfig.doc)) : {};
  if (!isObj(doc)) doc = {};
  doc.providers = providers;
  doc.default_index = defIdx;
  doc.updated_at = nowIso();
  doc.updated_by = 'web';

  var ref = { doc: aiConfig.doc, revision: aiConfig.revision };
  var btn = $('set-ai-save');
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  var msgBox = $('set-ai-msg');

  putObject(OBJ_AI, ref, function () { return doc; })
    .then(function (rev) {
      aiConfig.doc = doc;
      aiConfig.revision = rev;
      aiConfig.providers = providers;
      aiConfig.default_index = defIdx;
      renderAiProviders();
      toast('AI 服务商配置已保存');
      if (msgBox) msgBox.textContent = '已保存';
    })['catch'](function (err) {
      if (err && err.unauthorized) return;
      toast(err.message || '保存失败');
      if (msgBox) msgBox.textContent = err.message || '保存失败';
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = '保存 AI 服务商'; }
    });
}

function bindAiSettings() {
  var addBtn = $('set-ai-add');
  if (addBtn) {
    addBtn.addEventListener('click', function () {
      var preset = $('set-ai-add-preset');
      addAiProvider(preset ? preset.value : 'custom');
    });
  }
  var saveBtn = $('set-ai-save');
  if (saveBtn) saveBtn.addEventListener('click', saveAiConfig);
}

function bindTopbar() {
  var logout = $('logout');
  if (logout) {
    logout.addEventListener('click', function () {
      logout.disabled = true;
      req('POST', '/auth/logout/', {}).then(function () {
        location.href = '/';
      })['catch'](function () {
        location.href = '/';          // 登出失败也回首页（服务端已尽力清理）
      });
    });
  }
  var setOut = $('set-logout');
  if (setOut) {
    setOut.addEventListener('click', function () {
      setOut.disabled = true;
      req('POST', '/auth/logout/', {}).then(function () {
        location.href = '/';
      })['catch'](function () {
        location.href = '/';
      });
    });
  }
}

function bindLogin() {
  var form = $('login-form');
  if (!form) return;
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var msg = $('login-msg');
    var user = String($('login-user').value || '').trim();
    var pass = String($('login-pass').value || '');
    msg.textContent = '';
    if (!user || !pass) { msg.textContent = '请填写用户名和密码'; return; }
    var btn = $('login-go');
    btn.disabled = true;
    msg.textContent = '正在登录…';
    reqRaw('POST', '/auth/login/', { username: user, password: pass }).then(function (res) {
      if (res.status === 200 && res.body && res.body.ok !== false) {
        $('login-pass').value = '';
        msg.textContent = '';
        location.reload();
        return;
      }
      msg.textContent = errText(res);
    })['catch'](function (err) {
      msg.textContent = (err && err.message) || '网络错误，请稍后重试';
    }).then(function () {
      btn.disabled = false;
    });
  });
}

function boot() {
  bindLogin();

  // ---- 启动屏（2026-09-20 用户要求，三端统一）：只有一根进度条、没有任何文字。
  //      三组预载各自报进度，这里取平均值填那一根条。 ----
  var bootPct = [0, 0, 0];
  function bootBar(i, pct) {
    bootPct[i] = Math.min(100, Math.max(0, pct));
    var fill = $('boot-fill');
    if (fill) {
      var avg = (bootPct[0] + bootPct[1] + bootPct[2]) / 3;
      fill.style.width = avg + '%';
    }
  }

  reqRaw('GET', '/me/').then(function (res) {
    if (res.status !== 200 || !res.body.username) { showLogin(''); return; }
    st.me.username = String(res.body.username);
    st.me.is_staff = !!res.body.is_staff;
    $('who').textContent = st.me.username;
    $('who').title = '当前登录用户：' + st.me.username;

    bootBar(0, 40);   // 账号验证完成

    show($('loginwrap'), false);
    show($('app'), true);
    show($('topbar'), true);
    show($('layout'), true);
    show($('side-foot'), true);

    bindTabs();
    bindScheduleForm();
    bindScheduleCalendar();
    bindCourseTools();
    bindRefresh();
    bindMail();
    bindCourses();
    bindContacts();
    bindTopbar();
    bindAi();
    bindTimetableTools();
    startTtNowLine();      // 课表当前时间线：每分钟自动重算一次
    bindPickerModal();
    bindChangePassword();
    bindAiSettings();
    bridgeProbeOnce()['catch'](function () {});

    // 各面板先登记（进入各自视图时由 showView 触发抓取）
    renderPanel('tt-week', renderTimetable);
    renderPanel('mb-courses', renderCourseList);
    renderPanel('mb-tasks', renderCourseTasks);
    renderPanel('mb-notifications', renderNotifications);
    renderPanel('mb-messages', renderMessages);
    renderPanel('mb-discussions', renderDiscussions);
    renderPanel('mail-heads', renderMailList);
    renderPanel('home-lessons', renderHomeLessons);
    renderPanel('home-events', renderHomeEvents);
    renderPanel('home-ddl', renderHomeDdl);
    renderSchedule();
    renderMailAccounts(null);
    renderSettings();
    renderAi();
    renderHomeNow();

    // ---- 并行预载：三组数据同时启动，每组完成后推进对应进度条 ----
    var p1 = loadScheduleOnce().then(function () {
      bootBar(2, 80);   // 日程加载完成
      return loadLessonsOnce();
    }).then(function () {
      bootBar(2, 100);  // 选课加载完成
    }).catch(function () { bootBar(2, 100); });

    var p2 = loadAccountsOnce().then(function () {
      bootBar(1, 60);
      return loadAppData(false);
    }).then(function () {
      bootBar(1, 100);
    }).catch(function () { bootBar(1, 100); });

    // 等预载全部完成（或单项失败但超时）后：进度条到 100% → 停半秒 → 渐渐淡出。
    // 用户 2026-09-21：「进度条走完以后等个半秒，然后再渐渐消失，不要一下子没掉，
    // 让用户能看到进度条走完」。所以这里两个 500/450 的常数别乱改。
    Promise.all([p1, p2]).then(function () {
      bootBar(0, 100);
      var bootEl = $('boot');
      if (!bootEl) return;
      setTimeout(function () {
        bootEl.style.transition = 'opacity 0.45s ease';
        bootEl.style.opacity = '0';
        setTimeout(function () { show(bootEl, false); }, 500);
      }, 500);
    })['catch'](function () {
      var bootEl = $('boot');
      if (bootEl) show(bootEl, false);
    });

    showView('schedule');
  })['catch'](function (err) {
    if (err && err.unauthorized) return;
    // 开机画面里不写字：连不上服务器就照旧给登录页，让登录卡去说清楚。
    var bootEl = $('boot');
    if (bootEl) show(bootEl, false);
    showLogin('');
  });
}

/* ============ 陈旧标签页自愈（2026-09-21）============
 *
 * 背景：`/static/**` 带 `immutable` 长缓存。用户把标签页开着不关，页面里跑的还是
 * **当时那一版 app.js**；如果之后服务端改了接口/结构，那个旧标签页就会表现成
 * 「课表、课程都空了」之类，而且**不刷新永远不会好**（用户实测就是这样报的）。
 *
 * 做法：拿自己这个 `<script src=...app.js?v=xxx>` 的版本串，去和服务端**当前** HTML
 * 里的版本串比；不一致就 reload 一次（用 sessionStorage 限流，最多 10 秒一次，
 * 绝不会变成刷新死循环）。在页面可见性变化时也查一次 —— 用户切回标签页就自动修好。
 */
function guardStaleShell() {
  try {
    var tag = document.querySelector('script[src*="app.js"]');
    var mine = tag ? String(tag.getAttribute('src') || '') : '';
    var mineVersion = (/\bv=([0-9a-z]+)/i.exec(mine) || [])[1] || '';
    if (!mineVersion) return;
    var last = Number(sessionStorage.getItem('phl-stale-check') || 0);
    if (Date.now() - last < 10000) return;
    sessionStorage.setItem('phl-stale-check', String(Date.now()));
    fetch('/app/', { cache: 'no-store' }).then(function (response) {
      return response.ok ? response.text() : '';
    }).then(function (html) {
      var fresh = (/app\.js\?v=([0-9a-z]+)/i.exec(html) || [])[1] || '';
      if (fresh && fresh !== mineVersion) {
        // 这一版已经过期了：刷一次拿新的（只刷一次，新页面里两边就一致了）
        location.reload();
      }
    })['catch'](function () { /* 断网/离线：什么都不做，下次再看 */ });
  } catch (e) { /* 隐私模式下 sessionStorage 可能不可用，忽略 */ }
}
window.addEventListener('visibilitychange', function () {
  if (!document.hidden) guardStaleShell();
});
setInterval(guardStaleShell, 60000);

boot();
