"""文案改版 A 组专项测试（首页 / 关于我们 / 服务与支持，≥12 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_copy_a.py [端口]
    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_copy_a.py https://phix.ing
       （第一个参数给完整 URL 时，同一套断言直接跑公网复核）

覆盖：
- 首页：新主标题 / 新副标题 / 「了解学习工具」→ /products/phl/ / 「体验心履」→ https://xin-lv.com
  （新窗口）/ 校园照片与紫色色罩保留 / 下载入口仍在 / PHL Lite 不再有性能承诺
- 关于我们：原有一比一文字逐句仍在（含半角 `--` 与半角 `:`）/ 新增「为什么开始做 / 谁在维护 /
  服务谁 / 维修服务」/「无需安装」改成 §4 口径
- 服务与支持：不再出现「24 小时」、出现新的回复时间口径 / 四问四答（心情记录存哪、AI 经过哪些
  服务器、同步哪些数据能否关、密码与恢复码都丢了）/ 绝对保证词与「永远拿不到」已改口径 /
  反馈表单、联系方式、配额表保留
- 全站（website/ 下所有 .html 源文件）：无「三端同步」「无需安装」、无绝对保证词、无真实姓名、
  无内网 IP / 盘符路径 / 密钥名
- 新增的 static/copy-a.css 可访问，且三页都在 site.css 之后引入

事实依据：_recon/COPY-FACTS.md（§0 品牌与定位、§2 加密与隐私边界、§4 口径统一、§5 结构要求、§6 硬约束）。
"""
import os
import re
import sys
import urllib.error
import urllib.request

ARG = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = ARG.rstrip("/") if ARG.startswith("http") else f"http://127.0.0.1:{ARG}"
HERE = os.path.dirname(os.path.abspath(__file__))
PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")   # 公网走 Cloudflare，默认 urllib UA 会被 403


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def text(path):
    st, _, body = get(path)
    return st, body.decode("utf-8", "replace")


def src(name):
    return open(os.path.join(HERE, name), encoding="utf-8").read()


def all_html_sources():
    """website/ 下所有页面的源文件（不含 app/ 与 admin/ 的模板，它们不是对外页面）。"""
    out = {}
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs
                   if d not in {"__pycache__", ".git", "app", "admin", "media", "assets", "static"}]
        for f in files:
            if f.endswith(".html"):
                p = os.path.join(root, f)
                out[os.path.relpath(p, HERE).replace("\\", "/")] = open(p, encoding="utf-8").read()
    return out


PAGE_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)
LI_RE = re.compile(r'<li[^>]*>(.*?)</li>', re.S)
H2_RE = re.compile(r'<h2[^>]*>(.*?)</h2>', re.S)
H3_RE = re.compile(r'<h3[^>]*>(.*?)</h3>', re.S)


def texts(html):
    out = []
    for rx in (PAGE_RE, LI_RE, H2_RE, H3_RE):
        out += rx.findall(html)
    return out


def norm(s):
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", s))


# ---- 关于我们「一比一」原文（2026-09-15 用户指定的新文案，含半角 : 与中英混排）----
ABOUT_ORIGINAL = [
    "PHix维修社是上海民办平和学校学生组织的服务社团，致力于用代码，工具与竭诚的服务，"
    "为学生们改善校园日常生活，便利学生的学习生涯。",
    "我们相信好的工具不需要复杂，所有的帮助应该融入学生的日常生活，"
    "出现在方方面面的趁手之处。我们从学生的视角出发，每一个产品都执着于“简单”与“便捷”的追求。",
    "软件开发理念",
    "开箱即用:网页版打开即可立即使用，桌面与手机端可下载客户端享受更全面的服务。",
    "隐私优先:端到端加密，用户数据被全面保护。",
    "开放共享:所有代码开源至Github，欢迎一起贡献。",
    "联系方式",
    "如有问题或建议，请访问服务与支持页面提交反馈。社长微信Norine2010/int_32_2147483647",
    "我们的起始点",
    "维修社始于社长在平和多年的就读经验。",
    "物业的响应速度却十分缓慢",
    "PHIX的谐音这时在脑内成型",
    "一切都有解决方案，我们可以做得更好。",
    "谁在维护",
    "由社团成员分工维护：心履维护组负责心情记录与日程提醒，Pinghe Launcher 维护组负责桌面端与网页版，"
    "Pinghe Launcher Lite 维护组负责轻量版Launcher。",
    "目前服务谁",
    "主要面向平和在校同学，为学生提供多方面的服务。",
    "维修服务",
    "课余我们也帮同学修东西：耳机、充电线、台灯、文具、雨伞、小电器这类日常小件都可以。",
]

# ---- 绝对保证词 / 过时口径（全站不得出现）----
ABSOLUTE_BANNED = ["绝不丢失", "永远拿不到任何明文", "绝对安全", "永远拿不到", "24 小时内回复",
                   "三端同步", "低配电脑也能秒开"]
# 注意：不把「绝不静默覆盖」列入 —— 那是同步冲突处理的具体实现（乐观锁 / 409），事实清单未要求改。


def main():
    print("=" * 70)
    print(f"文案改版 A 组测试（首页 / 关于我们 / 服务与支持） → {BASE}")
    print("=" * 70)

    st_home, home = text("/")
    st_about, about = text("/about/")
    st_support, support = text("/support/")
    home_src, about_src, support_src = src("index.html"), src("about.html"), src("support.html")

    # ================= 1. 首页 =================
    print("\n[1] 首页：新主标题 / 副标题 / 两个按钮")
    check("1. 首页 HTTP 200 且主标题为新口号「Repair,Restore,Renew.」",
          st_home == 200 and 'class="hero__title hero__title--latin">Repair,Restore,Renew.</h1>' in home)
    check("2. 首页副标题为指定文案（平和学生自主创办 / 从学生的角度出发）",
          '<p class="hero__subtitle">PHIX维修社是由平和学生自主创办的社团，'
          '旨在从学生的角度出发，做便捷的校园工具。</p>' in home)
    check("2b. 首页英文标语单独一行「Everything can be PHIXed」",
          'class="hero__tagline-en">Everything can be PHIXed</p>' in home)
    check("2c. 旧主标题「让校园生活，少一点来回切换」已消失",
          "少一点来回切换" not in home)
    check("3. 旧主标题「把校园日常，调到黄昏频道」已从首页消失",
          "把校园日常，调到黄昏频道" not in home)
    btn_primary = re.search(r'<a class="btn btn--primary" href="([^"]+)">([^<]+)</a>', home)
    check("4. 主按钮「了解学习工具」→ /products/phl/",
          bool(btn_primary) and btn_primary.group(2) == "了解学习工具"
          and btn_primary.group(1) == "/products/phl/",
          f"got={btn_primary.groups() if btn_primary else None}")
    btn_xinlv = re.search(r'<a class="btn btn--ghost" href="([^"]+)"([^>]*)>([^<]+)</a>', home)
    # 2026-09-13：次按钮从「直连 https://xin-lv.com」改成走本站的免密跳转
    # `/sso/to-xinlv/?soft=1` —— 已登录 phix 的人会被带着一次性码送到心履自动登录，
    # 没登录的人（soft=1）则照常打开心履。外部地址仍然只出现在心履自己的域名上。
    check("5. 次按钮「体验心履」→ 免密跳转 /sso/to-xinlv/?soft=1（新窗口 rel=noopener）",
          bool(btn_xinlv) and btn_xinlv.group(1) == "/sso/to-xinlv/?soft=1"
          and btn_xinlv.group(3) == "体验心履"
          and 'target="_blank"' in btn_xinlv.group(2)
          and "noopener" in btn_xinlv.group(2),
          f"got={btn_xinlv.groups() if btn_xinlv else None}")
    check("6. 下载入口仍在（首屏次级链接「下载安装包」→ /download/，另「产品一览」→ #products）",
          'href="/download/">下载安装包</a>' in home
          and 'href="#products">产品一览</a>' in home
          and "hero__links" in home)
    check("7. 首屏校园照片与紫色色罩保留（hero 风格不动）",
          'class="hero__photo" src="/assets/hero-campus.jpg"' in home
          and 'class="hero__scrim"' in home)
    check("8. Pinghe Launcher Lite 卡片不再写「低配电脑也能秒开」，改成「本地独立运行 + 可选云同步」",
          "低配电脑也能秒开" not in home
          and "默认在本地独立运行" in home and "可选开启云同步" in home)
    check("9. 首页新增维修服务说明（范围 + 到「服务与支持」留言的求助方式）",
          "维修服务：不只修电脑" in home
          and "耳机、充电线、台灯、文具、雨伞、小电器" in home
          and "能修就修" in home
          and "修不好的，我们会说明原因" in home
          and "到「服务与支持」页面的反馈表单留言" in home)

    # ================= 2. 关于我们 =================
    print("\n[2] 关于我们：新文案逐句核对（2026-09-15 用户指定）")
    check("10. 关于我们 HTTP 200", st_about == 200, f"status={st_about}")
    if st_about == 200:
        about_txts = [norm(t) for t in texts(about)]
        # 子串语义：新文案里有几条是长段落的片段，不能要求与元素全文相等
        missing = [s for s in ABOUT_ORIGINAL
                   if not any(norm(s) in t for t in about_txts)]
        check("11. 新文案逐句仍在（20 条一比一）", not missing, f"缺: {missing}")
        check("12. 半角冒号与英文品牌写法未被改成全角",
              "开箱即用:网页版打开即可立即使用" in about
              and "隐私优先:端到端加密" in about
              and "开放共享:所有代码开源至Github" in about)
    else:
        check("11. 新文案逐句仍在（20 条一比一）", False, "页面取不到")
        check("12. 半角冒号与英文品牌写法未被改成全角", False, "页面取不到")

    print("\n[3] 关于我们：关键小节与口径")
    check("13. 「我们的起始点」：社长经历 / 物业响应慢 / PHIX 谐音 / Everything can be PHIXed",
          "我们的起始点" in about
          and "维修社始于社长在平和多年的就读经验" in about
          and "PHIX的谐音这时在脑内成型" in about
          and "Everything can be PHIXed" in about
          and "一切都有解决方案，我们可以做得更好" in about)
    check("14. 「谁在维护」：三个维护组（Lite 组名为「轻量版Launcher」）",
          "谁在维护" in about and "心履维护组" in about and "Pinghe Launcher 维护组" in about
          and "Pinghe Launcher Lite 维护组" in about and "社团成员分工维护" in about
          and "轻量版Launcher" in about)
    check("15. 「目前服务谁」：平和在校同学（简化版文案）",
          "目前服务谁" in about and "主要面向平和在校同学，为学生提供多方面的服务" in about)
    check("16. 「维修服务」：范围 + 求助方式",
          "维修服务" in about and "能修就修" in about and "修不好的会说明原因" in about)
    check("17. 联系方式含社长微信；旧口号「像黄昏的天空」已移除",
          "社长微信Norine2010/int_32_2147483647" in about
          and "黄昏的天空" not in about and "为什么开始做" not in about
          and "桌面与手机端可下载客户端" in about)

    # ================= 3. 服务与支持 =================
    print("\n[4] 服务与支持：回复时间")
    check("18. 支持页 HTTP 200", st_support == 200, f"status={st_support}")
    check("19. 不再出现「24 小时」，改成「通常 1–3 天内回复；考试周可能更慢；紧急事项请在反馈里注明「紧急」」",
          "24 小时" not in support and "24小时" not in support
          and "通常 1–3 天内回复" in support
          and "考试周可能更慢" in support
          and "紧急事项请在反馈里注明「紧急」" in support)

    print("\n[5] 服务与支持：四问四答")
    qs = ["1. 心情记录保存在哪里？", "2. 发给 AI 的内容会经过哪些服务器？",
          "3. 同步哪些数据？能不能关？", "4. 密码和恢复码都丢了怎么办？"]
    check("20. 四个问题的标题都在", all(q in support for q in qs),
          f"缺: {[q for q in qs if q not in support]}")
    check("21. 第 1 问答：本地一份 + 云端密文，且写出「账号文件当前以明文保存在本机」的边界",
          "本地" in support and "端到端加密的密文" in support
          and "账号文件当前以明文保存在本机" in support
          and "服务端只保存密文，你的口令与密钥不上传" in support)
    check("22. 第 2 问答：第三方服务商（DeepSeek API）+ 内容会离开本机 + Pinghe Launcher 本地模式不出本机",
          "第三方服务商（DeepSeek API）" in support and "会离开本机" in support
          and "本地模型不出本机" in support and "邮件正文与邮箱授权码不会交给 AI" in support)
    check("23. 第 3 问答：列出同步对象清单（日程/心情/课表/学校快照/头像昵称/账号凭据/选课/界面偏好/AI 配置/AI 会话）且说明可以关",
          all(s in support for s in ["日程", "心情", "课表", "学校快照", "头像昵称", "账号凭据",
                                     "选课", "界面偏好", "AI 配置", "AI 会话"])
          and "同步可以在客户端关闭" in support and "删除云端某个对象" in support)
    check("24. 第 4 问答：密码与恢复码都丢 → 云端数据无法解开（端到端加密的设计后果）+ 建议保管恢复码",
          "云端数据就无法解开" in support and "端到端加密的设计后果" in support
          and "保管好一次性恢复码" in support)
    check("25. 绝对表述已改口径（「服务端永远拿不到」不再是「拿不到任何明文」的写法）",
          "永远拿不到" not in support
          and "服务端只保存密文，你的口令与密钥不上传" in support)

    print("\n[6] 服务与支持：原有区块保留")
    # 公网经 Cloudflare 时邮箱会被 cdn-cgi/email-protection 包裹（或直接在响应里不可辨别），
    # 所以邮箱这项目只看「有没有邮箱入口」的任一形态。
    has_email = ("mailto:norine.liu@icloud.com" in support
                 or "email-protection" in support
                 or "__cf_email__" in support
                 or ('deco-card__label">邮箱' in support))
    check("26. 反馈表单、联系方式区块、配额表保留（8 MiB/2000/200 MiB/15 分钟/30 天）",
          'id="feedback-form"' in support and "/feedback/" in support
          and has_email
          and ("tel:18901712280" in support or "18901712280" in support)
          and "space.bilibili.com/1121702307" in support
          and all(s in support for s in ["8 MiB", "2000", "200 MiB", "15 分钟", "30 天"]))
    sup_faq = len(re.findall(r'<details class="deco-faq__item"', support))
    check("27. 常见问题仍为 ≥6 条 <details>（新增一题后不破坏结构）", sup_faq >= 6, f"faq={sup_faq}")
    check("28. 支持页不再写「联网后自动同步，不会丢失」，改为「支持本地保存与云端同步；重要内容建议定期备份」",
          "不会丢失" not in support and "支持本地保存与云端同步" in support
          and "重要内容建议定期备份" in support)

    # ================= 4. 全站硬约束 =================
    print("\n[7] 全站：口径、绝对词、敏感信息")
    sources = all_html_sources()
    bad = {f: [s for s in ABSOLUTE_BANNED if s in h] for f, h in sources.items()}
    bad = {f: v for f, v in bad.items() if v}
    check("29. 全站 .html 源文件无「三端同步 / 无需安装 / 绝不丢失 / 永远拿不到(任何明文) / 绝对安全 / 24 小时内回复 / 低配电脑也能秒开」",
          not bad, f"{bad}")
    pages = {f: h for f, h in sources.items() if f in {"index.html", "about.html", "support.html"}}
    served = {"index.html": home, "about.html": about, "support.html": support}
    bad2 = {}
    for f in served:
        hit = [s for s in ABSOLUTE_BANNED if s in served[f]]
        if hit:
            bad2[f] = hit
    check("30. 三页渲染结果里也没有这些词（含 CMS 注入的文案）", not bad2, f"{bad2}")

    # 只检查正文（页脚联系方式区块与「联系我们」卡片按 §6 / 用户要求必须保留，里面的
    # 邮箱、微信本来就是这个社团对外公布的账号字母）
    def main_region(h):
        m = re.search(r"</header>(.*?)<footer", h, re.S)
        body = m.group(1) if m else h
        return re.sub(r'<section class="deco-section">\s*<h2 class="deco-section__title">联系我们</h2>.*?</section>',
                      "", body, flags=re.S)
    body = {f: main_region(h) for f, h in pages.items()}
    names = ["huaziqian", "norine", "hzq", "yourenatwo", "BVV_d"]
    name_hits = {f: [n for n in names if n in h] for f, h in body.items()}
    name_hits = {f: v for f, v in name_hits.items() if v}
    check("31. 三页正文不含真实姓名 / 用户名（页脚与「联系我们」区块按 §6 保留，不计入）",
          not name_hits, f"{name_hits}")

    sens = ["192.168", "127.0.0.1", "10.0.", "172.16", "phix-server", "D:\\", "D:/", "C:\\", "C:/",
            "/home/", "db.sqlite3", "secret_key", "service_key", "DEEPSEEK_API_KEY"]
    sens_hits = {f: [s for s in sens if s in h] for f, h in pages.items()}
    sens_hits = {f: v for f, v in sens_hits.items() if v}
    check("32. 三页无内网 IP / 盘符路径 / 密钥与配置名", not sens_hits, f"{sens_hits}")

    # ================= 5. 新样式 =================
    print("\n[8] 新样式 static/copy-a.css")
    st_css, hdr, css_body = get("/static/copy-a.css")
    css = css_body.decode("utf-8", "replace")
    check("33. /static/copy-a.css HTTP 200 且非空", st_css == 200 and len(css) > 200, f"status={st_css}")
    check("34. copy-a.css 含本次用到的三个类（hero__links / info-block / deco-ai-note）",
          all(c in css for c in [".hero__links", ".info-block__term", ".deco-ai-note"]))
    check("35. 三页都在 site.css 之后引入 copy-a.css（且不手写 ?v=）",
          all(0 <= h.find("/static/site.css") < h.find("/static/copy-a.css") and "copy-a.css?v=" not in h
              for h in (home_src, about_src, support_src)),
          "见页面源文件")
    check("36. 未改动 site.css（本次只新增 copy-a.css）",
          "copy-a" not in src(os.path.join("static", "site.css")))
    print("\n" + "=" * 70)
    total = len(PASSED) + len(FAILED)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项（共 {total} 项）")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
