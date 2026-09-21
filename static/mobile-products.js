/* phix 手机版 · 页面层的小脚本（不引任何依赖，<3KB）
   只做一件事：让「加密链路示意图」这类整宽插图在手机上可点开放大。
   用 <details> + <summary> 包一层（原生折叠，不用 JS 管状态、不用新 DOM 库）；
   页面结构保持不变 —— 只是在外面多包一层可折叠容器，桌面版不加载本文件。
   （mobile-products.css 里给 .deco-figure__img 设了 cursor: zoom-in。） */
(function () {
  var d = document;
  function wrap() {
    if (!d.documentElement.classList.contains('ua-mobile')) return;
    var imgs = d.querySelectorAll('.deco-figure__img');
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i], fig = img.parentNode;
      if (!fig || fig.parentNode && fig.parentNode.classList.contains('phix-zoom')) continue;
      var box = d.createElement('details');
      box.className = 'phix-zoom';
      var sum = d.createElement('summary');
      sum.className = 'phix-zoom__hint';
      sum.textContent = '点这里放大看这张示意图';
      fig.parentNode.insertBefore(box, fig);
      box.appendChild(sum);
      box.appendChild(fig);
    }
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', wrap);
  else wrap();
})();
