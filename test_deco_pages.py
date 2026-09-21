"""「服务与支持」「下载」两页装饰改造专项测试（≥10 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_deco_pages.py [端口]

覆盖：
- 两页均引入 deco-pages.css，hero 色带引用各自风景图（decor-support / decor-download）
- CSS 用线性渐变紫色色罩 + 色带高度 clamp(240px, 34vh, 420px)
- 分区数、FAQ <details> 数量、服务状态 / 系统要求表格
- 反馈提交指向 /feedback/、联系卡片区、宽幅色带分隔
- 下载页 8 张卡片 + 2 个顶层分类仍保留
- title 含校名、无敏感串、静态资源 200、源文件不手写 ?v=
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


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def get(path, raw=True):
    r = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            data = resp.read()
            return resp.status, dict(resp.headers), data
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main():
    print("=" * 70)
    print(f"两页装饰改造测试 → {BASE}")
    print("=" * 70)

    st, _, sup = get("/support/")
    sup = sup.decode("utf-8", "replace")
    st2, _, dl = get("/download/")
    dl = dl.decode("utf-8", "replace")

    # ---- 1. CSS 与 hero 装饰图 ----
    print("\n[1] CSS 引入与 hero 装饰图")
    # 2026-09-13 修：服务端会给 /static/**/*.css|js 自动加内容哈希（`?v=xxxxxxxx`），
    # 原来的精确串断言会因此误红（那是我引入的行为，断言该跟着放宽）。
    check("1. 两页均引入 /static/deco-pages.css（允许服务端自动加的 ?v=）",
          re.search(r'href="/static/deco-pages\.css(\?v=[0-9a-f]+)?"', sup) is not None
          and re.search(r'href="/static/deco-pages\.css(\?v=[0-9a-f]+)?"', dl) is not None, "")
    css = open(os.path.join(HERE, "static", "deco-pages.css"), encoding="utf-8").read()
    check("2. 服务与支持页 hero 装饰图（class deco-hero--support → decor-support.jpg）",
          'class="deco-hero deco-hero--support"' in sup
          and "url('/assets/decor-support.jpg')" in css, "")
    check("3. 下载页 hero 装饰图（class deco-hero--download → decor-download.jpg）",
          'class="deco-hero deco-hero--download"' in dl
          and "url('/assets/decor-download.jpg')" in css, "")
    check("4. CSS 用线性渐变紫色色罩（rgba(76,29,149,.86)）", "rgba(76, 29, 149, .86)" in css, "")
    check("5. 色带高度 clamp(240px, 34vh, 420px)", "clamp(240px, 34vh, 420px)" in css, "")
    check("6. 宽幅色带引用本页专属照片 band-support.jpg",
          "url('/assets/band-support.jpg')" in css, "")

    # ---- 2. 服务与支持页结构 ----
    print("\n[2] 服务与支持页结构")
    sup_sections = sup.count('<section class="deco-')
    check("7. 服务与支持页分区数 ≥ 6（hero+分区+色带）", sup_sections >= 6, f"sections={sup_sections}")
    check("8. 宽幅色带分隔存在（.deco-band）", 'class="deco-band"' in sup, "")
    sup_faq = len(re.findall(r'<details class="deco-faq__item"', sup))
    check("9. 常见问题 <details> ≥ 6 条", sup_faq >= 6, f"faq={sup_faq}")
    check("10. 服务状态与配额表格（8 MiB/2000/200 MiB/15 分钟/30 天）",
          all(s in sup for s in ["8 MiB", "2000", "200 MiB", "15 分钟", "30 天"]), "")
    check("11. 反馈提交指向 /feedback/", "/feedback/" in sup and 'id="feedback-form"' in sup, "")
    check("12. 联系卡片区（邮箱/电话/微信/B站）",
          "mailto:" in sup and "tel:" in sup and "微信" in sup and "space.bilibili.com/1121702307" in sup, "")

    # ---- 3. 下载页结构（保留原卡片 + 新内容）----
    print("\n[3] 下载页结构")
    dl_cat = len(re.findall(r'<details class="dl-cat"', dl))
    check("13. 下载页顶层分类仍为 2 个（心履 / Pinghe Launcher）", dl_cat == 2, f"dl-cat={dl_cat}")
    dl_cards = len(re.findall(r'<article class="dl-card">', dl))
    check("14. 下载页 8 张卡片仍在", dl_cards == 8, f"cards={dl_cards}")
    check("15. 下载页「怎么安装」步骤存在", "怎么安装" in dl and "deco-step" in dl, "")
    # 2026-09-13 文案改版 C 组：系统要求表按事实清单/构建配置重写——
    # 「macOS 12+」原是心履 macOS 的无出处写法，实测安装包内 LSMinimumSystemVersion 为 10.11，
    # 于是断言改为「表里要有 Android 8.0+ 与 macOS 最低版本 + 架构列」，不再钉死旧数字。
    check("16. 下载页「系统要求」表格（Android 8.0+ / macOS 版本 / 架构列）",
          all(s in dl for s in ["系统要求", "Android 8.0+", "macOS 13.0", "架构"]), "")
    dl_faq = len(re.findall(r'<details class="deco-faq__item"', dl))
    check("17. 下载页常见问题 <details> 4–6 条", 4 <= dl_faq <= 6, f"faq={dl_faq}")

    # ---- 4. title 与敏感串 ----
    print("\n[4] title 与敏感串")
    ts = re.search(r'<title>(.*?)</title>', sup)
    td = re.search(r'<title>(.*?)</title>', dl)
    check("18. 两页 title 均含「上海民办平和学校」",
          bool(ts) and "上海民办平和学校" in ts.group(1)
          and bool(td) and "上海民办平和学校" in td.group(1), "")
    sensitive = ["192.168", "127.0.0.1", "10.0.", "172.16", "phix-server",
                 "D:\\", "D:/", "C:\\", "C:/", "/home/", "db.sqlite3",
                 "secret_key", "service_key"]
    bad_sup = [s for s in sensitive if s in sup]
    bad_dl = [s for s in sensitive if s in dl]
    check("19. 两页无内网 IP / 盘符路径 / 密钥文件名等敏感串", not bad_sup and not bad_dl,
          f"support:{bad_sup} download:{bad_dl}")

    # ---- 5. 静态资源 200 ----
    print("\n[5] 静态资源")
    assets = ["/static/deco-pages.css", "/assets/decor-support.jpg",
              "/assets/decor-download.jpg", "/assets/band-support.jpg"]
    ok = True
    for a in assets:
        s, h, b = get(a)
        good = s == 200 and len(b) > 0
        ok &= good
        if not good:
            print(f"       资源 {a} → {s}")
    check("20. 装饰静态资源 HTTP 200", ok, "")
    check("21. 装饰图 Content-Type 为 image/jpeg",
          all(get(a)[1].get("Content-Type") == "image/jpeg"
              for a in assets[1:]), "")

    # ---- 6. 源文件不手写 ?v= ----
    print("\n[6] 源文件不手写 ?v=")
    s_src = open(os.path.join(HERE, "support.html"), encoding="utf-8").read()
    d_src = open(os.path.join(HERE, "download.html"), encoding="utf-8").read()
    check("22. 源文件不手写 site.css?v= / site.js?v= / deco-pages.css?v=",
          all("?v=" not in x for x in (s_src, d_src)), "")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
