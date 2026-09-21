"""三个产品页「文案校准」专项测试（≥14 项断言，实际 ~40 项）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_products_copy.py [base|端口]

    base 可传完整 URL（如 http://192.168.5.41:8940），也可只传端口（默认 8940）。

依据：`D:\\phix\\_recon\\COPY-FACTS.md`（事实清单，唯一依据）。覆盖：
  · 三页都有「我该选哪个」功能对比表（产品 / 主要用途 / 适合谁 / 需要的设备 / 是否需要账号 / 数据存在哪）
  · PHL「数据与隐私」引用了 /assets/encryption-chain.svg（HTTP 200、Content-Type 含 svg、figure 包裹、窄屏不溢出）
  · PHL 隐私节把加密链路讲透：scrypt→KEK→DEK→HKDF→对象密钥→AES-256-GCM→密文信封、AAD、服务端只存密文、
    换密码不用重传数据、传输层 X25519 信封 + 公钥固定
  · PHL 隐私节写出事实清单 §2 的 5 条边界（本机明文 / AI 出本机 / 邮件不交给 AI / 密码+恢复码 / 数据在哪可关 + 备份建议）
  · 口径统一：无「三端同步」，无绝对保证词，无性能承诺，正文收掉技术栈名词（指向技术文档）
  · 心履页写明「AI 树洞不是真人」的边界与「跨设备同步」（四端）
  · 邮箱口径：桌面端 IMAP 最近 100 封 / 纯文本 / 不加载外部图片 / 客户端授权码，网页版不做真实收发
  · 三页正文无真实姓名 / 内网 IP / 盘符路径 / 密钥文件名（页脚联系方式按契约保留，故只看 <main>）
"""
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

_arg = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = _arg if str(_arg).startswith("http") else f"http://127.0.0.1:{_arg}"

# 公网（https://phix.ing 经 Cloudflare）会对 python-urllib 的默认 UA 直接 403，
# 这里统一带一个浏览器 UA，好让同一份断言既能打本机也能打公网。
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

PASSED, FAILED = [], []

PAGES = [("心履", "/products/xinlv/", "xinlv"),
         ("Pinghe Launcher", "/products/phl/", "phl"),
         ("硬件", "/products/hardware/", "hardware")]

SVG = "/assets/encryption-chain.svg"

# 事实清单 §4：大段技术栈名词移到「技术文档」页，产品页正文不再出现
TECH_WORDS = ["Electron", "JavaFX", "Room", "Gradle", "jpackage", "Python",
              "Django", "SQLite", "Flutter", "JVM", "Node.js"]
# 事实清单 §2.5：不要写绝对保证
ABSOLUTE_WORDS = ["绝不", "永不", "永远拿不到", "永远不", "绝对", "100%", "百分之百",
                  "万无一失", "保证不会", "一定不会", "零风险"]
# 事实清单 §4：删除没有实测依据的性能承诺
PERF_WORDS = ["秒开", "低配", "极速", "飞快", "瞬间打开", "零卡顿", "丝滑",
              "性能提升", "无卡顿", "秒级启动"]
# 事实清单 §6：不得出现的敏感串（页脚联系方式除外，只看正文）
SENSITIVE = ["192.168", "127.0.0.1", "10.0.", "172.16", "phix-server",
             "D:\\", "D:/", "C:\\", "C:/", "/home/", "db.sqlite3", "secret_key",
             "service_key", "huaziqian", "Norine", "norine", "hzq", "yourenatwo", "BVV_d"]

COMPARE_COLS = ["主要用途", "适合谁", "需要的设备", "是否需要账号", "数据存在哪"]


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def split_main(html: str) -> str:
    m = re.search(r"<main\b.*?</main>", html, re.S)
    return m.group(0) if m else html


def strip_tags(html: str) -> str:
    """去标签后只剩可见文字：措辞类断言必须看正文，而不是 style="max-width:100%" 这类属性。"""
    return re.sub(r"<[^>]+>", " ", html)


def main():
    print("=" * 72)
    print(f"产品页文案校准专项测试 → {BASE}")
    print("=" * 72)

    # ---- 0. 页面与素材可达 ----
    print("\n[0] 页面与加密链路示意图可达")
    htmls = {}
    for label, route, _slug in PAGES:
        st, _, body = get(route)
        htmls[label] = body.decode("utf-8", "replace")
        check(f"0. GET {route} → 200（{label}）", st == 200, f"{st}")

    st, hdrs, _ = get(SVG)
    ct = (hdrs.get("Content-Type") or "").lower()
    check(f"1. GET {SVG} → 200 且 Content-Type 含 svg",
          st == 200 and "svg" in ct, f"{st} {ct}")

    # ---- 1. 加密链路图：引用 + figure 包裹 + 窄屏不溢出 ----
    print("\n[1] PHL 页的加密链路示意图")
    phl = htmls["Pinghe Launcher"]
    img_m = re.search(r'<img[^>]*src="' + re.escape(SVG) + r'"[^>]*>', phl)
    check("2. PHL 页引用了 /assets/encryption-chain.svg", bool(img_m),
          img_m.group(0)[:100] if img_m else "未找到 <img>")
    if img_m:
        tag = img_m.group(0)
        check("3. 图片 alt = phix 端到端加密链路示意",
              'alt="phix 端到端加密链路示意"' in tag, tag[:140])
        check("4. 图片自带 max-width:100% 与 height:auto（窄屏不溢出）",
              "max-width:100%" in tag.replace(" ", "") and "height:auto" in tag.replace(" ", ""),
              tag[:140])
    fig_m = re.search(r"<figure\b.*?</figure>", phl, re.S)
    check("5. 图片被 <figure> 包裹",
          bool(fig_m) and SVG in (fig_m.group(0) if fig_m else ""), "")
    st_css, _, css_body = get("/static/deco-products.css")
    css = css_body.decode("utf-8", "replace")
    check("6. deco-products.css 里 .deco-figure__img 有 max-width:100% 与 height:auto",
          re.search(r"\.deco-figure__img\s*\{[^}]*max-width:\s*100%", css, re.S) is not None
          and re.search(r"\.deco-figure__img\s*\{[^}]*height:\s*auto", css, re.S) is not None, "")
    # 2026-09-13：产品页正文 5000–9000px 高，site.js 原来的 IntersectionObserver(threshold 0.15)
    # 永远等不到触发，`.reveal{opacity:0}` 不会移除 → 整页除顶栏/页脚外全白。
    # 主代理已在 site.js **根治**（高个子元素 threshold:0.01 + 加载后兜底），
    # 产品页样式表里的兜底规则已撤掉，因此这里改为断言「根治机制存在」。
    js = get("/static/site.js")[2].decode("utf-8", "replace")
    check("6b. site.js 对超高元素用可触发的阈值 + 加载后兜底（长页面不再整页空白）",
          "threshold: 0.01" in js and "offsetHeight > window.innerHeight" in js
          and "load" in js, "")

    # ---- 2. 三页的「我该选哪个」功能对比表 ----
    print("\n[2] 功能对比表（我该选哪个）")
    for label, route, _slug in PAGES:
        html = htmls[label]
        has_tbl = "deco-table--compare" in html
        check(f"7.{PAGES.index((label, route, _slug)) + 1} {label} 页有功能对比表（deco-table--compare）",
              has_tbl, "未找到对比表")
    for label, _, _s in PAGES:
        html = htmls[label]
        missing = [c for c in COMPARE_COLS if c not in html]
        check(f"8. {label} 页对比表五个列名齐全", not missing, f"缺:{missing}")
    for label, _, _s in PAGES:
        html = htmls[label]
        check(f"9. {label} 页对比表覆盖三个产品（心履 / Pinghe Launcher / Pinghe Launcher Lite）",
              all(w in html for w in ["心履", "Pinghe Launcher", "Pinghe Launcher Lite"]), "")

    # ---- 3. 技术栈收口 ----
    print("\n[3] 技术名词收进技术文档")
    texts = {label: strip_tags(split_main(htmls[label])) for label, _, _s in PAGES}
    for label, _, _s in PAGES:
        hits = [w for w in TECH_WORDS if w in texts[label]]
        check(f"10. {label} 页正文不再出现技术栈名词", not hits, f"命中:{hits}")
    for label, _, _s in PAGES:
        html = htmls[label]
        check(f"11. {label} 页有一句「完整技术栈见技术文档」并链到 /docs/",
              "完整技术栈见" in html and re.search(r'href="/docs/"[^>]*>\s*技术文档', html) is not None, "")

    # ---- 4. 全站口径 ----
    print("\n[4] 口径统一（无三端同步 / 无绝对保证 / 无性能承诺）")
    for label, _, _s in PAGES:
        check(f"12. {label} 页不再出现「三端同步」", "三端同步" not in texts[label], "")
    for label, _, _s in PAGES:
        hits = [w for w in ABSOLUTE_WORDS if w in texts[label]]
        check(f"13. {label} 页无绝对保证措辞", not hits, f"命中:{hits}")
    for label, _, _s in PAGES:
        hits = [w for w in PERF_WORDS if w in texts[label]]
        check(f"14. {label} 页无性能承诺", not hits, f"命中:{hits}")
    check("15. 三页都出现过「跨设备同步」（心履是四端）",
          all("跨设备同步" in htmls[l] for l, _, _ in PAGES), "")
    check("16. 心履页写清四端（网页版 / Windows / macOS / Android）",
          all(w in htmls["心履"] for w in ["网页版", "Windows", "macOS", "Android"]), "")
    check("17. 三个平台表都只剩「形态」列（不再有「技术栈」列）",
          all("技术栈</th>" not in htmls[l] for l, _, _ in PAGES), "")

    # ---- 5. 心履页的 AI 边界 ----
    print("\n[5] 心履页：AI 树洞不是真人 + 数据存哪")
    xl = htmls["心履"]
    check("18. 心履页写明 AI 回复由第三方服务商生成、内容会离开本机",
          "第三方" in xl and "离开本机" in xl and "AI" in xl, "")
    check("19. 心履页写明紧急/严重情况找可信任的成年人、老师",
          "成年人" in xl and "老师" in xl and ("紧急" in xl or "严重" in xl), "")
    check("20. 心履页写明心情记录 = 本地 + 云端密文，同步对象是 mood",
          "本地" in xl and "云端密文" in xl and "<code>mood</code>" in xl, "")

    # ---- 6. PHL「数据与隐私」扩写 ----
    print("\n[6] PHL「数据与隐私」：加密链路 + 5 条边界")
    start = phl.find('id="privacy"')
    end = phl.find("常见问题", start) if start >= 0 else -1
    privacy = phl[start:end] if start >= 0 and end > start else ""
    check("21. 能定位到 PHL 隐私节（id=privacy）", len(privacy) > 1500, f"len={len(privacy)}")
    chain_words = ["scrypt", "KEK", "DEK", "HKDF", "AES-256-GCM", "PHIX1."]
    missing = [w for w in chain_words if w not in privacy]
    check("22. 隐私节写出完整密钥链路（scrypt→KEK→DEK→HKDF→对象密钥→AES-256-GCM→密文信封）",
          not missing, f"缺:{missing}")
    check("23. 隐私节写明「服务端只存密文」", "服务端只存密文" in privacy, "")
    check("24. 隐私节写明 AAD 绑定用户与对象名",
          "AAD" in privacy and "phix/v1/object" in privacy and "对象名" in privacy, "")
    check("25. 隐私节写明换密码不用重传数据", "换密码不用重传数据" in privacy, "")
    check("26. 隐私节写明传输层 X25519 信封 + 公钥固定（TOFU）",
          "X25519" in privacy and ("公钥" in privacy) and ("固定" in privacy or "TOFU" in privacy), "")
    check("27. 边界①：本机共享账号文件当前是明文保存", "明文保存" in privacy and "本机" in privacy, "")
    check("28. 边界②：AI 有本地/API 两种模式，API 模式内容会离开本机到第三方",
          "本地 / API 两种模式" in privacy and "第三方" in privacy and "离开本机" in privacy, "")
    check("29. 边界③：邮件内容不交给 AI", "邮件" in privacy and "不交给 AI" in privacy, "")
    check("30. 边界④：密码 + 恢复码都丢失则云端数据解不开",
          "恢复码" in privacy and "解不开" in privacy, "")
    check("31. 边界⑤：写出数据存在哪 / 同步哪些对象 / 可以关",
          "本地" in privacy and "云端" in privacy
          and all(o in privacy for o in ["schedule", "settings.accounts", "agent:"])
          and ("可以关" in privacy or "关掉" in privacy), "")
    check("32. 隐私节给出实用指引：重要内容建议定期备份", "重要内容建议定期备份" in privacy, "")

    # ---- 7. 邮箱口径 ----
    print("\n[7] 邮箱口径（桌面端 IMAP / 网页版只入口）")
    check("33. PHL 页写明桌面端邮箱细节（最近 100 封 / 纯文本 / 不加载外部图片 / 客户端授权码）",
          all(w in phl for w in ["最近 100 封", "纯文本", "不加载外部图片", "客户端授权码"]), "")
    check("34. PHL 页写明网页版只有邮箱入口、不做真实收发",
          "邮箱入口" in phl and "不做真实收发" in phl, "")

    # ---- 8. 敏感串与页脚回归 ----
    print("\n[8] 正文敏感串与页脚回归")
    for label, _, _s in PAGES:
        m = split_main(htmls[label])
        hits = [w for w in SENSITIVE if w in m]
        check(f"35. {label} 页正文无真实姓名 / 内网 IP / 路径 / 密钥文件名", not hits, f"命中:{hits}")
    for label, _, _s in PAGES:
        html = htmls[label]
        # 公网经 Cloudflare 时邮箱会被 __cf_email__ 混淆（data-cfemail），两种形态都算通过
        mail_ok = "norine.liu@icloud.com" in html or "__cf_email__" in html
        check(f"36. {label} 页页脚联系方式仍保留（未被文案改动波及）",
              mail_ok and "18901712280" in html
              and "space.bilibili.com/1121702307" in html and "© 2026 phix" in html, "")

    print("\n" + "=" * 72)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
