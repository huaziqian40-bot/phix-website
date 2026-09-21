/* phix 手机版基础设施 —— UA 自动切换（冻结接口，三批页面共用）
   写在 <head> 里同步执行（不能加 defer/async，否则会闪一下桌面版）。

   1) 手机 UA(/iPhone|iPod|Android.*Mobile|Windows Phone|iPad|Android/i)
      → <html class="ua-mobile" data-ua-page="home|about|support|…">
        + 注入 <link rel="stylesheet" href="/static/mobile.css">
   2) 页面里若写了 <meta name="mobile-css" content="/static/xxx.css">（可多个，逗号分隔）也一并注入
   3) ?mobile=1 / ?desktop=1 覆盖 UA 判定，并写入 localStorage['phix-mobile-override']
      （覆盖只对以后加载的页面生效，本页不重新判定 —— 重新判定会闪屏）
   4) 注入移动端顶栏抽屉的 DOM 与开关；样式一律只在 mobile.css 里
   无依赖、不阻塞；压缩后 <3KB。 */
(function () {
  var d = document, h = d.documentElement, RE = /iPhone|iPod|Android.*Mobile|Windows Phone|iPad|Android/i;
  function css(href) {
    if (!href || d.querySelector('link[href="' + href + '"]')) return;
    var l = d.createElement('link');
    l.rel = 'stylesheet'; l.href = href; d.head.appendChild(l);
  }
  function page() {
    var p = location.pathname.replace(/index\.html$/, '');
    if (p === '/' || p === '') return 'home';
    var m = p.match(/(?:^|\/)([a-z0-9_-]+)\/?$/i);
    return m ? m[1].toLowerCase() : '';
  }
  function boot() {
    h.classList.add('ua-mobile');
    var pid = page();
    if (pid) h.setAttribute('data-ua-page', pid);
    css('/static/mobile.css');
  }
  function draw() {
    if (d.getElementById('phix-drawer')) return;
    var box = d.createElement('div');
    box.innerHTML = '<button type="button" id="phix-menu" class="phix-menu" aria-label="打开导航菜单"'
      + ' aria-expanded="false" aria-controls="phix-drawer"><span class="phix-menu__bars" aria-hidden="true"></span></button>'
      + '<div class="phix-drawer" id="phix-drawer" aria-label="移动端导航" aria-hidden="true">'
      + '<div class="phix-drawer__top"><img class="phix-drawer__logo" src="/assets/logo-transparent.png" alt="phix">'
      + '<button type="button" class="phix-drawer__close" aria-label="关闭导航菜单">✕</button></div>'
      + '<nav class="phix-drawer__nav"><a href="/about/">关于我们</a><p class="phix-drawer__group">产品</p>'
      + '<a class="phix-drawer__sub" href="/products/xinlv/">心履</a>'
      + '<a class="phix-drawer__sub" href="/products/phl/">Pinghe Launcher</a>'
      + '<a href="/docs/">文档</a><a href="/log/">PHIX 日志</a><a href="/support/">服务与支持</a>'
      + '<a href="/download/">下载</a><a class="phix-drawer__cta" href="/login/">登录 / 注册</a></nav></div>';
    var menu = box.firstChild, dr = menu.nextSibling, i,
        list = dr.querySelectorAll('a');
    d.body.insertBefore(menu, d.body.firstChild);
    d.body.appendChild(dr);
    function set(on) {
      dr.classList.toggle('is-open', on); h.classList.toggle('phix-nav-open', on);
      menu.setAttribute('aria-expanded', on ? 'true' : 'false');
      dr.setAttribute('aria-hidden', on ? 'false' : 'true');
    }
    menu.addEventListener('click', function () { set(!dr.classList.contains('is-open')); });
    dr.querySelector('.phix-drawer__close').addEventListener('click', function () { set(false); });
    for (i = 0; i < list.length; i++) list[i].addEventListener('click', function () { set(false); });
    d.addEventListener('keydown', function (e) { if (e.key === 'Escape') set(false); });
    d.addEventListener('click', function (e) {
      if (dr.classList.contains('is-open') && !dr.contains(e.target) && !menu.contains(e.target)) set(false);
    });
    /* 抽屉放不下时底部渐隐，让人一眼看出还能滚（能放下就不显示） */
    function fade() {
      dr.classList.toggle('is-scrollable', dr.scrollHeight - dr.clientHeight > 4 &&
                                            dr.scrollTop + dr.clientHeight < dr.scrollHeight - 4);
    }
    dr.addEventListener('scroll', fade);
    if (window.ResizeObserver) new ResizeObserver(fade).observe(dr);
    fade();
  }
  var q = (location.search.match(/[?&](mobile|desktop)=1(?!\d)/) || [])[1], mode = q;
  if (q) { try { localStorage.setItem('phix-mobile-override', q); } catch (e) {} }
  if (!mode) { try { mode = localStorage.getItem('phix-mobile-override'); } catch (e) {} }
  var on;
  if (mode === 'mobile') on = true;
  else if (mode === 'desktop') on = false;
  else if (d.querySelector('link[rel="stylesheet"][href^="/static/mobile.css"]')) on = null;  /* 站点已按 UA 发手机版 */
  else on = RE.test(navigator.userAgent || '');
  if (on === true) boot();                                    /* head 同步阶段就加 class */
  if (on !== false) {
    var meta = d.querySelector('meta[name="mobile-css"]'), x;
    if (meta) { x = (meta.getAttribute('content') || '').split(','); for (var k = 0; k < x.length; k++) css(x[k].trim()); }
    if (d.body) draw(); else d.addEventListener('DOMContentLoaded', draw);
  }
})();
