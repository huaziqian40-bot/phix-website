"""下载页改造专项测试（≥10 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_download_page.py [端口]

覆盖：
- 下载页三个分类可折叠（心履 / PHL，PHL 内两个子标题 PHL / PHLL）
- 8 张卡片、8 个下载链接都指向真实存在的文件名
- 每张卡片的 logo 路径与平台标志路径正确
- <details> 默认展开
- 每个静态资源 HTTP 200（含 /assets/product-*.png、/assets/platform-*.png，Content-Type image/png）
- 每页 title 含「上海民办平和学校」
- 首页/页脚含「维修社」，页脚联系方式仍在
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = f"http://127.0.0.1:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
DL_DIR = os.path.join(HERE, "media", "downloads")
PASSED, FAILED = [], []

# 8 个真实安装包（文件名与结构要求一一对应）
# PLL Windows 自 2026-09-21 起改用 WiX 构建的 MSI（只出安装版，不再提供便携版 exe）
EXPECTED_FILES = [
    "xinlv-windows-setup.exe", "xinlv-windows.zip", "xinlv-macos.dmg", "xinlv-android.apk",
    "phl-windows-setup.exe", "phl-macos.dmg",
    "phllite-windows-setup.msi", "phllite-macos.dmg",
]


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


def _visible(html: str) -> str:
    """剥掉 script/style/注释/标签 + code/pre 后的可见文本（品牌名口径用）。"""
    t = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    t = re.sub(r"<script\b.*?</script>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<style\b.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<(code|pre)\b[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", t)


def main():
    print("=" * 70)
    print(f"下载页改造测试 → {BASE}")
    print("=" * 70)

    # ---- 1. 页面与结构 ----
    print("\n[1] 下载页结构与卡片")
    st, hdrs, html = get("/download/")
    html_str = html.decode("utf-8", "replace")
    check("1. GET /download/ → 200", st == 200, f"{st}")

    # 三个分类（心履 + Pinghe Launcher 顶层；Pinghe Launcher 内层两个子标题
    # Pinghe Launcher / Pinghe Launcher Lite）
    check("2. 三个分类小标题存在（心履 / Pinghe Launcher / Pinghe Launcher Lite）",
          "心履" in html_str and "Pinghe Launcher Lite" in html_str
          and html_str.count("Pinghe Launcher") >= 2, "")
    dl_cat = len(re.findall(r'<details class="dl-cat"', html_str))
    check("3. 顶层分类 2 个（心履 / Pinghe Launcher）", dl_cat == 2, f"dl-cat={dl_cat}")
    dl_sub = len(re.findall(r'<details class="dl-sub"', html_str))
    check("4. Pinghe Launcher 内两个可折叠子标题", dl_sub == 2, f"dl-sub={dl_sub}")
    # 2026-09-13 品牌全称改造：可见文本里不许再出现简称
    check("4b. 可见文案不再出现简称（PHL / PHL Lite / PHLL）",
          "PHL Lite" not in _visible(html_str)
          and not re.search(r"PHL", _visible(html_str).replace("Pinghe Launcher", "")),
          "")

    # 8 张卡片
    cards = re.findall(r'<article class="dl-card">', html_str)
    check("5. 共 8 张卡片", len(cards) == 8, f"cards={len(cards)}")

    # ---- 2. 下载链接指向真实文件名 ----
    print("\n[2] 下载链接与文件")
    hrefs = re.findall(r'href="(/media/downloads/[^"]+)"', html_str)
    names = sorted(set(os.path.basename(h) for h in hrefs))
    check("6. 8 个下载链接", len(hrefs) == 8, f"links={len(hrefs)}")
    check("7. 链接文件名与 8 个真实安装包一一对应",
          names == sorted(EXPECTED_FILES), f"{names}")
    missing = [n for n in EXPECTED_FILES if not os.path.isfile(os.path.join(DL_DIR, n))]
    check("8. 8 个安装包在 media/downloads 里都存在", not missing, f"缺失:{missing}")

    # ---- 3. logo 与平台标志路径 ----
    print("\n[3] 卡片 logo 与平台标志")
    logos = re.findall(r'class="dl-card__logo" src="(/assets/[^"]+)"', html_str)
    badges = re.findall(r'class="dl-card__badge" src="(/assets/[^"]+)"', html_str)
    check("9. 每张卡片 logo 路径正确（4 心履 / 2 PHL / 2 PHLL）",
          logos.count("/assets/product-xinlv.png") == 4
          and logos.count("/assets/product-phl.png") == 2
          and logos.count("/assets/product-phll.png") == 2
          and len(logos) == 8, f"{logos}")
    check("10. 每张卡片平台标志路径正确（4 Windows / 3 Apple / 1 Android）",
          badges.count("/assets/platform-windows.png") == 4
          and badges.count("/assets/platform-apple.png") == 3
          and badges.count("/assets/platform-android.png") == 1
          and len(badges) == 8, f"{badges}")

    # 逐卡校验：每张卡片恰好 1 个 logo + 1 个 badge + 1 个下载按钮
    card_blocks = re.split(r'<article class="dl-card">', html_str)[1:]
    ok_per_card = all(
        block.count('class="dl-card__logo"') == 1
        and block.count('class="dl-card__badge"') == 1
        and block.count('dl-card__btn') == 1
        for block in card_blocks
    )
    check("11. 每张卡片 = 1 logo + 1 平台标志 + 1 下载按钮", ok_per_card and len(card_blocks) == 8, "")

    # ---- 4. details 默认展开 ----
    print("\n[4] <details> 默认展开")
    open_details = len(re.findall(r'<details[^>]*\bopen\b', html_str))
    check("12. 4 个 <details> 均带 open（2 分类 + 2 子标题）", open_details == 4, f"open={open_details}")
    check("13. summary 含展开指示箭头（chevron）", "dl-cat__chevron" in html_str and "dl-sub__chevron" in html_str, "")

    # ---- 5. 静态资源 HTTP 200 + Content-Type ----
    print("\n[5] 静态资源")
    assets = [
        "/static/site.css", "/static/site.js",
        "/assets/product-xinlv.png", "/assets/product-phl.png", "/assets/product-phll.png",
        "/assets/platform-windows.png", "/assets/platform-apple.png", "/assets/platform-android.png",
    ]
    all_ok = True
    for a in assets:
        s, h, b = get(a)
        ok = s == 200 and len(b) > 0
        all_ok &= ok
        if not ok:
            print(f"       资源 {a} → {s}")
    check("14. 每个静态资源 HTTP 200", all_ok, "")

    png_ok = True
    for a in ["/assets/product-xinlv.png", "/assets/product-phl.png", "/assets/product-phll.png",
              "/assets/platform-windows.png", "/assets/platform-apple.png", "/assets/platform-android.png"]:
        s, h, b = get(a)
        if h.get("Content-Type") != "image/png":
            png_ok = False
            print(f"       {a} Content-Type={h.get('Content-Type')}")
    check("15. 6 个素材 Content-Type 均为 image/png", png_ok, "")

    # ---- 6. 全站 title 含校名 ----
    print("\n[6] 全站 title 与页脚")
    pages = ["/", "/about/", "/docs/", "/support/", "/download/", "/login/", "/register/",
             "/account/", "/products/xinlv/", "/products/phl/", "/products/hardware/"]
    bad_titles = []
    for p in pages:
        s, h, b = get(p)
        t = re.search(r'<title>(.*?)</title>', b.decode("utf-8", "replace"))
        title = t.group(1) if t else ""
        if "上海民办平和学校" not in title:
            bad_titles.append((p, title))
    check("16. 每页 title 含「上海民办平和学校」", not bad_titles, f"{bad_titles}")

    # 首页/页脚含「维修社」，联系方式仍在
    s, h, home = get("/")
    home_str = home.decode("utf-8", "replace")
    check("17. 首页 hero/页脚含「维修社」",
          "维修社" in home_str and "上海民办平和学校 · 维修社" in home_str, "")
    check("18. 页脚联系方式仍在（邮箱/电话/微信/B站）",
          "norine.liu@icloud.com" in home_str and "18901712280" in home_str
          and "Norine2010" in home_str and "space.bilibili.com/1121702307" in home_str, "")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
