"""文档页 /docs/ 横屏应用外壳改造专项测试（≥50 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_docs_page.py [端口]

覆盖：
- 横屏应用外壳 .docs-shell 占满视口（无 max-width 居中收缩）；顶部条 logo + 「PHIX 技术文档」
- 左侧分组导航 7 个 <details> 分组标题正确，默认全部收起（0 个 open）
- 侧栏锚点 ↔ 正文 h2 双向往返一一对应
- >10 个正文章节；表格数量与内容；代码块数量；三种 callout 都存在
- scroll-spy JS 已就位（scroll 监听）且侧栏锚点可点击
- 页脚联系方式仍在；title 含「上海民办平和学校」
- 页面不含 192.168 / 盘符路径 / phix-server / 内网 IP 等敏感串
- 源文件不手写 ?v=（版本号由 server.py 自动加）
"""
import os
import re
import sys
import urllib.error
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = f"http://127.0.0.1:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
PASSED, FAILED = [], []

# 7 个分组标题（顺序与侧栏一致）
GROUP_TITLES = ["快速开始", "账号与登录", "安全设计", "令牌与会话", "云同步", "平台", "附录"]


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def get(path):
    r = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    print("=" * 70)
    print(f"文档页分块文档测试 → {BASE}")
    print("=" * 70)

    # ---- 1. 页面与结构 ----
    print("\n[1] 页面与两栏结构")
    st, html = get("/docs/")
    html_str = html.decode("utf-8", "replace")
    check("1. GET /docs/ → 200", st == 200, f"{st}")

    check("2. 左侧导航存在（.docs-nav）", 'class="docs-nav"' in html_str, "")
    check("3. 应用外壳 .docs-shell 存在（占满视口）", 'class="docs-shell"' in html_str, "")
    check("4. 正文容器 .docs-content 存在", 'class="docs-content"' in html_str, "")

    # ---- 2. 分组标题 ----
    print("\n[2] 分组标题")
    nav_m = re.search(r'<nav class="docs-nav".*?</nav>', html_str, re.S)
    nav_block = nav_m.group(0) if nav_m else ""
    check("5. 侧栏 nav 能解析出来", bool(nav_m), "")
    group_ok = nav_block and all(re.search(r'class="docs-nav__group-title">\s*' + re.escape(t), nav_block)
                                  for t in GROUP_TITLES)
    check("6. 7 个分组标题正确（快速开始/账号与登录/安全设计/令牌与会话/云同步/平台/附录）",
          group_ok, "分组缺失或不正确")

    # ---- 3. 侧栏锚点 ↔ 正文 h2 双向一一对应 ----
    print("\n[3] 侧栏锚点 ↔ 正文标题")
    side_ids = re.findall(r'href="#([A-Za-z0-9-]+)"', nav_block)
    side_ids_uniq = sorted(set(side_ids))
    h2_ids = re.findall(r'<h2 id="([A-Za-z0-9-]+)"', html_str)
    h3_ids = re.findall(r'<h3 id="([A-Za-z0-9-]+)"', html_str)
    head_ids = sorted(set(h2_ids + h3_ids))
    check("7. 侧栏锚点数量 ≥ 20", len(side_ids_uniq) >= 20, f"{len(side_ids_uniq)}")
    check("8. 每个侧栏锚点在正文都能找到对应 id（正向）",
          all(i in head_ids for i in side_ids_uniq),
          f"缺失:{[i for i in side_ids_uniq if i not in head_ids]}")
    check("9. 每个 h2/h3 都有侧栏入口（反向）",
          head_ids and all(i in side_ids_uniq for i in head_ids),
          f"缺失:{[i for i in head_ids if i not in side_ids_uniq]}")
    # 确保正文没有漏 id 的 h2
    all_h2 = len(re.findall(r'<h2\b', html_str))
    check("10. 每个 h2 都带 id（无漏网之鱼）", all_h2 == len(h2_ids), f"h2={all_h2},带id={len(h2_ids)}")

    # ---- 4. 章节数 ----
    print("\n[4] 章节与内容分块")
    check("11. 正文章节数 > 10（h2）", len(h2_ids) > 10, f"{len(h2_ids)}")

    # ---- 5. 表格数量与内容 ----
    print("\n[5] 表格数量与内容")
    tables = re.findall(r'<table class="docs-table"', html_str)
    check("12. 表格数量 ≥ 4", len(tables) >= 4, f"tables={len(tables)}")
    check("13. 错误码表含 bad_credentials + 含义列",
          "bad_credentials" in html_str and ">含义<" in html_str, "")
    # 2026-09-12 修：真实同步对象是 10 个具名对象（+ agent:<id> 命名空间），早期断言只认 4 个，
    # 还把 timetable / school / settings.lessons 当成"不该出现"——那是错的，客户端确实在用。
    OBJECT_NAMES = ["settings.accounts", "settings.lessons", "settings.ui", "settings.ai",
                    "schedule", "timetable", "school", "profile", "mood", "agent:"]
    missing_obj = [n for n in OBJECT_NAMES if n not in html_str]
    check("14. 同步对象表含全部 10 个具名对象（+ agent: 命名空间）",
          not missing_obj, f"缺失:{missing_obj}")

    # ---- 6. 代码块数量 ----
    print("\n[6] 代码块")
    pres = re.findall(r'<pre><code', html_str)
    check("15. 代码块（pre>code）≥ 4", len(pres) >= 4, f"pre={len(pres)}")

    # ---- 7. 三种 callout ----
    print("\n[7] 提示框（callout）")
    check("16. 信息 callout 存在（callout--info）", "callout--info" in html_str, "")
    check("17. 注意 callout 存在（callout--note）", "callout--note" in html_str, "")
    check("18. 安全 callout 存在（callout--safe）", "callout--safe" in html_str, "")
    # 2026-09-13 文案改版 A 组：「服务端永远拿不到明文口令」按 §4 改成「服务端只保存密文，口令与密钥不上传」
    check("19. 安全 callout 文案含改口径后的「服务端只保存密文」", "服务端只保存密文" in html_str, "")

    # ---- 8. scroll-spy JS ----
    print("\n[8] scroll-spy")
    check("20. JS 含 scroll 监听或 IntersectionObserver",
          ("addEventListener('scroll'" in html_str or "IntersectionObserver" in html_str), "")
    check("21. JS 含 is-active 高亮切换", "is-active" in html_str, "")
    check("22. 侧栏锚点是可点击的 <a href=\"#...\">",
          len(side_ids_uniq) > 0 and all(("#" + i) in nav_block for i in side_ids_uniq), "")

    # ---- 9. 页脚与 title ----
    print("\n[9] 页脚与 title")
    check("23. 页脚联系方式仍在（邮箱/电话/微信/B站）",
          "norine.liu@icloud.com" in html_str and "18901712280" in html_str
          and "Norine2010" in html_str and "space.bilibili.com/1121702307" in html_str, "")
    t = re.search(r'<title>(.*?)</title>', html_str)
    title = t.group(1) if t else ""
    check("24. title 含「上海民办平和学校」", "上海民办平和学校" in title, title)

    # ---- 10. 敏感串 ----
    print("\n[10] 敏感串审计（必须全部不含）")
    sensitive = ["192.168", "127.0.0.1", "10.0.", "172.16", "phix-server",
                 "D:\\", "D:/", "C:\\", "C:/", "/home/", "db.sqlite3",
                 "secret_key", "service_key"]
    bad = [s for s in sensitive if s in html_str]
    check("25. 不含内网 IP / 盘符路径 / phix-server / 服务密钥文件名等敏感串", not bad, f"命中:{bad}")

    # ---- 11. 源文件不手写 ?v= ----
    print("\n[11] 静态资源版本号由 server.py 自动加（源文件不手写 ?v=）")
    src = open(os.path.join(HERE, "docs.html"), encoding="utf-8").read()
    check("26. 源 docs.html 不手写 site.css?v=", "site.css?v=" not in src, "")
    check("27. 源 docs.html 不手写 site.js?v=", "site.js?v=" not in src, "")

    # ---- 12. 右侧本页目录（TOC）+ 上一节/下一节翻页 ----
    print("\n[12] 右侧本页目录（TOC）+ 上一节/下一节翻页")
    toc_m = re.search(r'<nav class="docs-toc".*?</nav>', html_str, re.S)
    toc_block = toc_m.group(0) if toc_m else ""
    toc_ids = re.findall(r'href="#([A-Za-z0-9-]+)"', toc_block)
    toc_ids_uniq = sorted(set(toc_ids))
    check("28. 右侧 TOC 存在（.docs-toc）", bool(toc_m), "")
    check("29. TOC 项数与正文 h2 数一致", len(toc_ids_uniq) == len(head_ids),
          f"toc={len(toc_ids_uniq)} h2={len(head_ids)}")
    check("30. TOC 每个锚点都能命中正文 h2", all(i in head_ids for i in toc_ids_uniq),
          f"缺失:{[i for i in toc_ids_uniq if i not in head_ids]}")
    check("31. 左导航与 TOC 锚点集合一致", side_ids_uniq == toc_ids_uniq, "")
    check("32. 翻页链接存在（上一节/下一节）",
          'id="pagination-prev"' in html_str and 'id="pagination-next"' in html_str, "")
    check("33. 翻页链接指向有效锚点",
          bool(re.search(r'id="pagination-prev"[^>]*href="#', html_str))
          and bool(re.search(r'id="pagination-next"[^>]*href="#', html_str)), "")

    # ---- 13. 事实清单核对（与代码默认一致，不编造）----
    print("\n[13] 事实清单核对（限流/配额/派生/AAD/无域名细节）")
    check("34. 口令派生含 scrypt-n15-r8-p1 与 scrypt-hkdf-v2",
          "scrypt-n15-r8-p1" in html_str and "scrypt-hkdf-v2" in html_str, "")
    check("35. AAD 含 phix/v1/object 与 phix/v1/identity",
          "phix/v1/object" in html_str and "phix/v1/identity" in html_str, "")
    check("36. 传输层含 phix/v1/seal 与 phix/v1/req",
          "phix/v1/seal" in html_str and "phix/v1/req" in html_str, "")
    check("37. 配额/限流默认值齐全（8 MiB/2000/200 MiB/50/10/8/60/120）",
          all(s in html_str for s in ["8 MiB", "2000", "200 MiB", "50 个对象",
                                      "10 次", "8 次", "60 次", "120 次"]), "")
    check("38. 不含域名/CDN/版本号细节（Cloudflare/phix.ing/www./CDN/Django 5.1）",
          not any(s in html_str for s in ["Cloudflare", "phix.ing", "www.", "CDN", "Django 5.1"]), "")
    # 39：内部信息不外泄（内网 IP / Windows 路径 / 服务端文件名 / 真实用户名）
    leaks = [s for s in ("192.168.", "D:\\", "phix-server", "db.sqlite3", "service_key",
                         "Re1", "hzq", "yourenatwo") if s in html_str]
    check("39. 无内网 IP / 路径 / 密钥文件名 / 真实用户名", not leaks, f"命中:{leaks}")

    # ---- 14. 三栏媒体查询 + 右目录 id 唯一 + 翻页相邻 + 进度（本轮复核要求）----
    print("\n[14] 三栏媒体查询 + 右目录 id 唯一 + 翻页相邻 + 进度")
    check("40. 右目录链接指向的 id 唯一（无重复）",
          len(toc_ids) == len(toc_ids_uniq), f"toc={len(toc_ids)} unique={len(toc_ids_uniq)}")
    css = open(os.path.join(HERE, "static", "site.css"), encoding="utf-8").read()
    check("41. CSS 含 ≥1280px 三栏外壳媒体查询（260px + 1fr + 200px）",
          "min-width: 1280px" in css and "260px minmax(0, 1fr) 200px" in css, "")
    check("42. CSS 翻页类名 docs-pager 存在", ".docs-pager" in css, "")
    prev_href = re.search(r'id="pagination-prev" href="#([A-Za-z0-9-]+)"', html_str)
    next_href = re.search(r'id="pagination-next" href="#([A-Za-z0-9-]+)"', html_str)
    adjacent = False
    if prev_href and next_href:
        p, n = prev_href.group(1), next_href.group(1)
        adjacent = p in side_ids and n in side_ids and abs(side_ids.index(n) - side_ids.index(p)) == 1
    check("43. 翻页 prev/next 指向相邻锚点", adjacent, "")
    check("44. 翻页含进度提示（第 n / 24 节）",
          'id="pagination-progress"' in html_str and "/ 24" in html_str, "")

    # ---- 15. DeepSeek 式横屏应用外壳 + 折叠分组（默认收起，本轮改造）----
    print("\n[15] 横屏应用外壳 + 折叠分组（默认收起）")
    check("45. 顶部条 .docs-topbar 存在", 'class="docs-topbar"' in html_str, "")
    check("46. 顶部条标题 = 「PHIX 技术文档」且为 h1",
          'class="docs-topbar__title">PHIX 技术文档</h1>' in html_str, "")
    check("47. 顶部条含 logo <img>（logo-transparent.png）",
          'docs-topbar__brand' in html_str and '/assets/logo-transparent.png' in html_str, "")
    detail_total = len(re.findall(r'<details class="docs-nav__group"', html_str))
    detail_open = len(re.findall(r'<details class="docs-nav__group"[^>]*\bopen\b', html_str))
    check("48. 7 个折叠分组（<details class=\"docs-nav__group\">）",
          detail_total == 7, f"details={detail_total}")
    check("49. 分组默认全部收起（0 个带 open）", detail_open == 0, f"open={detail_open}")
    check("50. 分组标题字号 ≥ 子项字号（CSS 源 1.0625rem ≥ .9375rem）",
          ".docs-nav__group-title" in css and "font-size: 1.0625rem" in css
          and ".docs-nav__link" in css and "font-size: .9375rem" in css, "")
    check("51. CSS 含分组标题高亮规则 .docs-nav__group-title.is-active（收起时可见）",
          ".docs-nav__group-title.is-active" in css, "")
    check("52. JS 首屏不自动展开（setActive(currentSection(), false) 且含 location.hash 深链处理）",
          "setActive(currentSection(), false)" in html_str and "location.hash" in html_str, "")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
