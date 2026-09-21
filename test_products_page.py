"""三个产品页（心履 / PHL / 硬件）「风景图 + 紫色色罩」装饰与内容详化专项测试（≥12 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_products_page.py [base|端口]

    base 可传完整 URL（如 http://192.168.5.41:8940），也可只传端口（默认 8940）。
    覆盖：
    - 每个页面各自 hero 装饰图（decor-xxx.jpg）在 deco-products.css 中被引用、页面引用对应 hero 变体类
    - 分区数（<main> 内 <section>）≥5；正文汉字数（去标签后）≥800
    - 平台表格 ≥1；<details> 折叠问答 ≥4；title 含「上海民办平和学校」
    - 下载 / 文档 / 支持链接存在；CTA 指向 /download/ /docs/ /support/
    - 无敏感串（内网 IP / 盘符路径 / 密钥文件名 / 真实用户名 / 域名与 CDN 细节）
    - 源文件不手写 ?v=（版本号由 server.py 自动加）
    - 静态资源 deco-products.css 与 5 张装饰图 HTTP 200 且 Content-Type 正确
"""
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

_arg = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = _arg if str(_arg).startswith("http") else f"http://127.0.0.1:{_arg}"

PASSED, FAILED = [], []

# 页面 → (路由, hero 变体类, 装饰图)
PAGES = [
    ("心履", "/products/xinlv/", "deco-hero--xinlv", "decor-xinlv.jpg"),
    ("Pinghe Launcher", "/products/phl/", "deco-hero--phl", "decor-phl.jpg"),
    ("硬件", "/products/hardware/", "deco-hero--hardware", "decor-hardware.jpg"),
]

# 2026-09-13：色带改成「每页各用一张专属照片」（用户要求图片不重复），
# 原来三页共用的 decor-band / decor-band-alt 已删除。
DECOR_ASSETS = ["decor-xinlv.jpg", "decor-phl.jpg", "decor-hardware.jpg",
                "band-xinlv-a.jpg", "band-xinlv-b.jpg",
                "band-phl-a.jpg", "band-phl-b.jpg",
                "band-hardware-a.jpg", "band-hardware-b.jpg"]

# 全页都必须不含的硬性敏感串（内网 IP / 盘符路径 / 密钥文件名 / 域名与 CDN 细节）
# 注：xin-lv.com 是心履网页版的正式地址（用户明确要求在 CTA 里放「打开网页版」跳它），
# 因此不再列为敏感串；内网 IP / 盘符 / 密钥文件名等仍然禁止。
SENSITIVE_FULL = ["192.168", "127.0.0.1", "10.0.", "172.16", "phix-server",
                  "D:\\", "D:/", "C:\\", "C:/", "/home/", "db.sqlite3",
                  "secret_key", "service_key", "Cloudflare", "DeepSeek",
                  "Django 5.1", "Kimi K3", "CDN"]
# 正文（<main>）里不得出现的真实用户名（页脚联系方式除外，故只看正文）
SENSITIVE_BODY = ["hzq", "yourenatwo", "BVV_d", "huaziqian", "Norine2010", "norine"]


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def get(path, headers_only=False):
    req = urllib.request.Request(BASE + path, method="HEAD" if headers_only else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if headers_only:
                return resp.status, dict(resp.headers), b""
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def han_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def main():
    print("=" * 72)
    print(f"产品页装饰与内容专项测试 → {BASE}")
    print("=" * 72)

    # ---- 0. 静态资源：deco-products.css 与装饰图 ----
    print("\n[0] 静态资源 HTTP 200 与 Content-Type")
    st, hdrs, body = get("/static/deco-products.css")
    css = body.decode("utf-8", "replace")
    ct = (hdrs.get("Content-Type") or "").lower()
    check("0.1 GET /static/deco-products.css → 200", st == 200, f"{st}")
    check("0.2 deco-products.css Content-Type 含 text/css", "text/css" in ct, ct)
    for i, name in enumerate(DECOR_ASSETS, 3):
        st, hdrs, _ = get(f"/assets/{name}")
        ct = (hdrs.get("Content-Type") or "").lower()
        check(f"0.{i} GET /assets/{name} → 200 且 image/jpeg",
              st == 200 and "image/jpeg" in ct, f"{st} {ct}")

    # ---- 1. 每个页面：hero 装饰图引用 + 结构 + 内容 ----
    for label, route, hero_cls, decor in PAGES:
        print(f"\n[{label}] {route}")
        st, hdrs, body = get(route)
        html = body.decode("utf-8", "replace")
        check(f"{label} · GET {route} → 200", st == 200, f"{st}")

        # hero 装饰图：CSS 里引用对应 decor-xxx.jpg，页面引用 hero 变体类
        css_has = f"url('/assets/{decor}')" in css or f"url(\"/assets/{decor}\")" in css
        check(f"{label} · deco-products.css 引用 {decor}（hero 装饰图）", css_has, "")
        check(f"{label} · 页面引用 hero 变体类 {hero_cls}",
              hero_cls in html, "")
        check(f"{label} · 页面引入 /static/deco-products.css",
              "/static/deco-products.css" in html, "")

        # 分区数 ≥5（<main> 内的 <section>）
        m = re.search(r"<main\b.*?</main>", html, re.S)
        main_html = m.group(0) if m else html
        sections = len(re.findall(r"<section\b", main_html))
        check(f"{label} · 分区数 ≥5（<section>={sections}）", sections >= 5, f"{sections}")

        # 正文汉字数 ≥800（去标签后）
        plain = strip_tags(main_html)
        n_han = han_count(plain)
        check(f"{label} · 正文汉字数 ≥800（实际 {n_han}）", n_han >= 800, f"{n_han}")

        # 表格 ≥1、<details> ≥4
        tables = len(re.findall(r"<table\b", main_html))
        check(f"{label} · 表格 ≥1（<table>={tables}）", tables >= 1, f"{tables}")
        details = len(re.findall(r"<details\b", main_html))
        check(f"{label} · <details> ≥4（实际 {details}）", details >= 4, f"{details}")

        # title 含「上海民办平和学校」
        t = re.search(r"<title>(.*?)</title>", html)
        title = t.group(1) if t else ""
        check(f"{label} · title 含「上海民办平和学校」", "上海民办平和学校" in title, title)

        # 下载 / 文档 / 支持链接存在（CTA）
        check(f"{label} · 含下载链接 /download/", 'href="/download/"' in html, "")
        check(f"{label} · 含文档链接 /docs/", 'href="/docs/"' in html, "")
        check(f"{label} · 含支持链接 /support/", 'href="/support/"' in html, "")

        # 敏感串审计
        bad_full = [s for s in SENSITIVE_FULL if s in html]
        check(f"{label} · 全页不含内网 IP / 路径 / 密钥文件名 / 域名细节", not bad_full,
              f"命中:{bad_full}")
        bad_body = [s for s in SENSITIVE_BODY if s in main_html]
        check(f"{label} · 正文不含真实用户名", not bad_body, f"命中:{bad_body}")

        # 源文件不手写 ?v=
        fname = route.strip("/").split("/")[-1] + ".html"
        src_path = os.path.join(HERE, "products", fname)
        src = open(src_path, encoding="utf-8").read()
        check(f"{label} · 源文件不手写 ?v=", "?v=" not in src, "")

    # ---- 2. 汇总 ----
    print("\n" + "=" * 72)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
