/* phix 官网共享 JS（零外部依赖） */

// ---- 滚动渐显（DESIGN.md §6.2）----
// 2026-09-13 修 BUG：原来固定用 threshold:0.15 + rootMargin 底部 -8%，
// 当某个 .reveal 元素**比视口高很多**时（产品页 main 高 5000–9000px），
// 它的可见比例永远到不了 0.15 → is-visible 永远不加 → `.reveal{opacity:0}`
// 让整页正文在浏览器里一片空白。现在对"高个子"元素改用能触发的参数，
// 并加一层兜底，保证任何情况下内容都不会永久不可见。
const revealEls = document.querySelectorAll('.reveal');
function revealNow(el) { el.classList.add('is-visible'); }

function observeReveal(el) {
  const tall = el.offsetHeight > window.innerHeight * 0.5;
  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      revealNow(entry.target);
      io.unobserve(entry.target);
    }
  }, tall
    ? { threshold: 0.01 }                                  // 高个子：碰到一点就显示
    : { threshold: 0.15, rootMargin: '0px 0px -8% 0px' });  // 常规块：保留原来的渐显手感
  io.observe(el);
}

revealEls.forEach((el, i) => el.style.setProperty('--reveal-delay', `${(i % 3) * 80}ms`));
revealEls.forEach(observeReveal);

// 兜底：比视口还高的元素（观察器可能因阈值/布局变化一直不触发）在加载后强制显示；
// 只针对这类元素，不影响普通区块的渐显动画。
window.addEventListener('load', () => {
  window.setTimeout(() => {
    revealEls.forEach((el) => {
      if (el.offsetHeight > window.innerHeight && !el.classList.contains('is-visible')) revealNow(el);
    });
  }, 900);
});

// ---- API 辅助 ----
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  return { status: resp.status, data };
}

// ---- 登录状态检测 ----
async function checkAuth() {
  try {
    const { status, data } = await api('GET', '/me/');
    if (status === 200 && data.username) {
      return data;
    }
  } catch (e) { /* ignore */ }
  return null;
}

// ---- 头像压缩（canvas 256×256）----
function compressAvatar(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const ctx = canvas.getContext('2d');
      // 居中裁切
      const size = Math.min(img.width, img.height);
      const sx = (img.width - size) / 2;
      const sy = (img.height - size) / 2;
      ctx.drawImage(img, sx, sy, size, size, 0, 0, 256, 256);
      resolve(canvas.toDataURL('image/png'));
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

// ---- Tab 切换（个人中心）----
function initTabs(containerSel, tabSel, panelSel) {
  const container = document.querySelector(containerSel);
  if (!container) return;
  const tabs = container.querySelectorAll(tabSel);
  const panels = container.querySelectorAll(panelSel);

  function activate(name) {
    tabs.forEach(t => t.classList.toggle('is-active', t.dataset.tab === name));
    panels.forEach(p => p.classList.toggle('hidden', p.dataset.panel !== name));
  }

  tabs.forEach(t => {
    t.addEventListener('click', () => activate(t.dataset.tab));
  });

  // 默认激活第一个
  const first = tabs[0];
  if (first) activate(first.dataset.tab);
}

// ---- 密码显示切换 ----
function togglePassword(inputId, btnEl) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const isHidden = input.type === 'password';
  input.type = isHidden ? 'text' : 'password';
  if (btnEl) btnEl.textContent = isHidden ? '🙈' : '👁';
}

// ---- 顶栏登录态（cookie 即时判断 + /me/ 后台兜底）----
//
// phix_hint_user cookie 里存着登录用户名（非 httpOnly，JS 可读），
// 有了它就不需要等 /me/ 返回就能把「登录 / 注册」换成用户名。
// 这样用户在任何页面（首页、产品页、个人中心）都能一眼看到登录态。
(function initStaffUI() {
  // 先读 cookie（即时判断，不等网络）
  var hintUser = '';
  document.cookie.split(';').forEach(function(c) {
    var p = c.trim().split('=');
    if (p[0] === 'phix_hint_user' && p[1]) hintUser = decodeURIComponent(p[1]);
  });
  if (hintUser) {
    var authBtn = document.getElementById('auth-btn');
    if (authBtn) {
      authBtn.textContent = hintUser;
      authBtn.href = '/account/';
    }
  }
  // 后台 /me/ 仍然跑一次：确定 is_staff 并补上管理后台按钮
  checkAuth().then(function(u) {
    if (!u) return;
    var authBtn2 = document.getElementById('auth-btn');
    if (authBtn2) {
      authBtn2.textContent = u.username || hintUser || '个人中心';
      authBtn2.href = '/account/';
    }
    if (!u.is_staff) return;
    var actions = document.querySelector('.site-header__actions');
    if (!actions || !authBtn2) return;
    if (document.getElementById('admin-btn')) return;
    var admin = document.createElement('a');
    admin.id = 'admin-btn';
    admin.className = 'nav__link';
    admin.href = '/admin/';
    admin.textContent = '管理后台';
    actions.insertBefore(admin, authBtn2);
  }).catch(function() {});
})();

// ---- 数据迁移通告 banner ----
// 2026-09-12 用户决定：**撤下通告** —— 迁移对用户应当是无感的，
// 不需要横幅提醒。这里直接移除元素（HTML 里的容器保留，将来要再开
// 只需把下面这行 return 去掉；文案仍在 content.json 的 banner.notice 里）。
(function initBanner() {
  const el = document.getElementById('migration-banner');
  if (!el) return;
  el.remove();
  return;
  /* eslint-disable no-unreachable */
  const STORAGE_KEY = 'phix_banner_dismissed';
  try {
    if (localStorage.getItem(STORAGE_KEY) === '1') { el.remove(); return; }
  } catch(e) { /* localStorage 不可用时仍显示 */ }
  el.style.display = 'block';
  const closeBtn = el.querySelector('.banner__close');
  if (closeBtn) {
    closeBtn.addEventListener('click', function() {
      el.remove();
      try { localStorage.setItem(STORAGE_KEY, '1'); } catch(e) {}
    });
  }
})();
