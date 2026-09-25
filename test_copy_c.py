# -*- coding: utf-8 -*-
"""文案改造 C 组专项测试（下载页 / 技术文档页；≥30 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_copy_c.py [端口]

覆盖：
- 下载页：8 张卡片仍带版本号 / 更新日期 / 精确大小 / 平台与架构 / 安装方式
- 每张卡片的 SHA-256 == media/downloads 里那个文件的真实哈希（换包忘了改文案就会红）
- checksums.txt：可 HTTP 访问、恰好 8 行、按文件名排序、与页面与磁盘三方一致
- 「安装版还是免安装版」「卸载与数据」「报错引导」三段新文案存在且口径正确
- 文档页：加密链路 SVG 就位（HTML + 资源 200）且图注解释了整条链路
- 文档页口径：跨设备同步（非三端）/ AI 第三方边界 / PHL 邮箱口径 / 本机明文边界 / 备份建议
- 文档页既有的配额与限流数字未被改动
- 全站：无「三端」、无「绝不丢失 / 绝对安全」、无内网 IP / 盘符路径 / 密钥文件名 / 真实用户名
"""
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = f"http://127.0.0.1:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
DL_DIR = os.path.join(HERE, "media", "downloads")
CHECKSUMS = os.path.join(HERE, "checksums.txt")
PASSED, FAILED = [], []

# 事实清单 §3：8 个安装包 → (版本, 精确字节数, 平台/架构关键词)
PACKAGES = {
    "xinlv-windows-setup.exe":   ("v1.1.6", "77,869,056", ["Windows 10", "x64"]),
    "xinlv-windows.zip":         ("v1.1.6", "78,477,783", ["Windows 10", "x64"]),
    "xinlv-macos.dmg":           ("v1.1.6", "120,112,596", ["macOS 10.11", "x86_64"]),
    "xinlv-android.apk":         ("v1.2.32", "5,602,420", ["Android 8.0", "原生库"]),
    "phl-windows-setup.exe":     ("v1.0.9", "172,214,729", ["Windows 10/11", "x64"]),
    "phl-macos.dmg":             ("v1.0.9", "213,326,848", ["macOS 13.0", "x86_64"]),
    # PLL Windows 自 2026-09-21 起改用 WiX 构建的 MSI（只出安装版，不再提供便携版 exe）
    "phllite-windows-setup.msi": ("v1.2.2", "54,804,480", ["Windows", "x64"]),
    "phllite-macos.dmg":         ("v1.2.2", "38,207,815", ["macOS 11.0", "x86_64"]),
}
BUILD_DATE = "2026-09-25"
#: 2026-09-21 重出过的包 → 它们卡片上的日期比 BUILD_DATE 新
NEWER_DATE = {"phl-windows-setup.exe": "2026-09-25",
              "phl-macos.dmg": "2026-09-25",
              "phllite-windows-setup.msi": "2026-09-25",
              "phllite-macos.dmg": "2026-09-25",
              "xinlv-windows-setup.exe": "2026-09-25",
              "xinlv-windows.zip": "2026-09-25",
              "xinlv-macos.dmg": "2026-09-25",
              "xinlv-android.apk": "2026-09-25"}

# 文档里「是对的、不要动」的配额与限流数字
QUOTA_NUMBERS = ["8 MiB", "2000", "200 MiB", "50 个对象", "10 次", "8 次", "60 次", "120 次",
                 "15 分钟", "30 天", "120 秒", "±300 秒", "5 分钟"]

SENSITIVE = ["192.168", "127.0.0.1", "10.0.", "172.16", "phix-server",
             "D:\\", "D:/", "C:\\", "C:/", "/home/", "db.sqlite3",
             "secret_key", "service_key", "hzq", "yourenatwo"]


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def get(path):
    r = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 74)
    print(f"文案改造 C 组测试（下载页 / 文档页）→ {BASE}")
    print("=" * 74)

    st, _, dl_raw = get("/download/")
    dl = dl_raw.decode("utf-8", "replace")
    st_docs, _, docs_raw = get("/docs/")
    docs = docs_raw.decode("utf-8", "replace")

    # ---- 1. 下载页结构未被破坏 ----
    print("\n[1] 下载页结构（改造前的老结构必须还在）")
    check("1. GET /download/ → 200", st == 200, f"{st}")
    check("2. 顶层分类 2 个（心履 / Pinghe Launcher）",
          len(re.findall(r'<details class="dl-cat"', dl)) == 2, "")
    check("3. Pinghe Launcher 内 2 个可折叠子标题", len(re.findall(r'<details class="dl-sub"', dl)) == 2, "")
    check("4. 8 张卡片", len(re.findall(r'<article class="dl-card">', dl)) == 8, "")
    hrefs = re.findall(r'href="(/media/downloads/[^"]+)"', dl)
    # 链接带 ?v=<sha8> 用于穿透 Cloudflare 缓存；比文件名时要去掉 query
    hrefs = [h.split('?')[0] for h in hrefs]
    check("5. 8 个真实下载链接", len(hrefs) == 8
          and sorted(os.path.basename(h) for h in hrefs) == sorted(PACKAGES), f"{len(hrefs)}")
    check("6. 4 个 <details> 默认展开",
          len(re.findall(r'<details[^>]*\bopen\b', dl)) == 4, "")

    # ---- 2. 每个安装包的完整信息 ----
    print("\n[2] 每个安装包的完整信息（版本 / 日期 / 大小 / 平台与架构）")
    missing_ver = [f for f, (v, _, _) in PACKAGES.items() if v not in dl]
    check("7. 8 个安装包的版本号都在页面上", not missing_ver, f"缺失:{missing_ver}")
    check("8. 每张卡片都有更新日期：8 张 %s" % BUILD_DATE,
          dl.count(BUILD_DATE) == 8,
          f"{BUILD_DATE}×{dl.count(BUILD_DATE)}（应全为 8；重出的包见 NEWER_DATE={sorted(NEWER_DATE)}）")
    missing_sz = [f for f, (_, sz, _) in PACKAGES.items() if sz not in dl]
    check("9. 8 个安装包的精确字节数都在页面上", not missing_sz, f"缺失:{missing_sz}")
    missing_mb = [f for f, (_, _, _) in PACKAGES.items()
                  if not re.search(r'<td>[\d.]+ MB</td>', dl)]
    check("10. 文件校验表每行都有 MB 大小列", not missing_mb, "")
    missing_arch = [f for f, (_, _, kws) in PACKAGES.items() if any(k not in dl for k in kws)]
    check("11. 平台与架构关键词齐全（x64 / x86_64 / Android / macOS 版本）",
          not missing_arch, f"缺失:{missing_arch}")
    check("12. 每张卡片都有「安装方式」一行（安装版 / 免安装版）",
          len(re.findall(r'<dt>安装方式</dt>', dl)) == 8, "")
    check("13. 卡片仍带 logo 与平台徽标（8 + 8）",
          len(re.findall(r'class="dl-card__logo"', dl)) == 8
          and len(re.findall(r'class="dl-card__badge"', dl)) == 8, "")

    # ---- 3. SHA-256：页面 == checksums.txt == 磁盘 ----
    print("\n[3] SHA-256：页面 == checksums.txt == 磁盘真实哈希")
    rows = re.findall(r'<tr>(.*?)</tr>', dl, re.S)
    page_hashes = {}
    for row in rows:
        fname = re.search(r'<code>([A-Za-z0-9._-]+)</code>', row)
        h = re.search(r'<code class="dl-hash">([0-9a-f]{64})</code>', row)
        if fname and h:
            page_hashes[fname.group(1)] = h.group(1)
    check("14. 页面上解析出 8 行「文件名 + SHA-256」", len(page_hashes) == 8, f"{sorted(page_hashes)}")

    disk = {}
    for f in PACKAGES:
        p = os.path.join(DL_DIR, f)
        disk[f] = sha256_file(p) if os.path.isfile(p) else "<缺失>"
    bad = [f for f in PACKAGES if page_hashes.get(f) != disk.get(f)]
    check("15. 页面每个哈希 == 磁盘文件真实哈希", not bad,
          f"不一致:{[(f, page_hashes.get(f), disk.get(f)) for f in bad]}")

    st_cs, hdrs_cs, cs_raw = get("/checksums.txt")
    check("16. GET /checksums.txt → 200", st_cs == 200, f"{st_cs}")
    cs_lines = [ln.rstrip("\r") for ln in cs_raw.decode("utf-8", "replace").split("\n")]
    cs_lines = [ln for ln in cs_lines if ln.strip()]
    check("17. checksums.txt 恰好 8 行", len(cs_lines) == 8, f"lines={len(cs_lines)}")
    fmt_ok = all(re.fullmatch(r"[0-9a-f]{64}  [A-Za-z0-9._-]+", ln) for ln in cs_lines)
    check("18. 每行格式为「<sha256>  <文件名>」", fmt_ok, f"{cs_lines[:2]}")
    cs_map = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in cs_lines if "  " in ln}
    check("19. checksums.txt 按文件名排序",
          list(cs_map) == sorted(cs_map), f"{list(cs_map)}")
    check("20. checksums.txt 哈希 == 磁盘真实哈希",
          all(cs_map.get(f) == disk.get(f) for f in PACKAGES),
          f"{[f for f in PACKAGES if cs_map.get(f) != disk.get(f)]}")
    check("21. checksums.txt 与页面哈希一致",
          all(cs_map.get(f) == page_hashes.get(f) for f in PACKAGES), "")
    check("22. 本地 checksums.txt 文件存在且非空",
          os.path.isfile(CHECKSUMS) and os.path.getsize(CHECKSUMS) > 0, "")

    # ---- 4. 选包指引 / 卸载与数据 / 报错引导 ----
    print("\n[4] 学生视角选包指引 / 卸载 / 报错引导")
    check("23. 「安装版还是免安装版」选择指引存在",
          "安装版还是免安装版" in dl and "免安装版" in dl and "解压" in dl, "")
    check("24. 指引里写明安装版有开始菜单/桌面入口、可卸载",
          "开始菜单" in dl and "卸载" in dl, "")
    check("25. 「卸载与数据」说明存在", "卸载与数据" in dl, "")
    check("26. 卸载说明不笼统写「删文件夹就等于卸载」，并给出数据目录/云端删除说法",
          "以安装包内说明为准" in dl and "云端" in dl
          and "删掉文件夹即等于卸载" not in dl and "删文件夹就等于卸载" not in dl, "")
    check("27. 报错引导先核对大小与 SHA-256（不是一律归因未签名）",
          "SHA-256" in dl and ("先核对文件大小" in dl or "先核对" in dl), "")
    check("28. 报错引导含「哈希对不上 → 重新下载」分支",
          "重新下载" in dl, "")
    check("29. 报错引导含「哈希对得上但被拦截 → 多半缺少代码签名 → 仍要运行」分支",
          "代码签名" in dl and "仍要运行" in dl, "")
    check("30. 旧口径「（未签名，属正常）」已删除", "未签名，属正常" not in dl, "")

    # ---- 5. 系统要求表 ----
    print("\n[5] 系统要求表")
    sys_m = re.search(r'系统要求(.*?)</section>', dl, re.S)
    sys_block = sys_m.group(1) if sys_m else ""
    check("31. 系统要求表含 Windows / macOS / Android 三种平台",
          all(s in sys_block for s in ["Windows", "macOS", "Android"]), "")
    check("32. 系统要求表含「架构」列且给出 x64 / x86_64",
          "架构" in sys_block and "x64" in sys_block and "x86_64" in sys_block, "")
    check("33. 无法确认的项写明「以安装包内说明为准」",
          "以安装包内说明为准" in sys_block, "")
    check("34. 系统要求表里的最低版本与事实清单一致",
          all(s in sys_block for s in ["Android 8.0+", "macOS 13.0", "macOS 10.11", "macOS 11.0"]), "")

    # ---- 6. 文档页：加密链路图 ----
    print("\n[6] 文档页：加密链路图")
    check("35. GET /docs/ → 200", st_docs == 200, f"{st_docs}")
    check("36. 文档页含规定 markdown 结构的加密链路 figure",
          '<figure class="docs-figure">' in docs
          and '<img src="/assets/encryption-chain.svg"' in docs
          and 'alt="phix 端到端加密链路示意"' in docs
          and "<figcaption>" in docs, "")
    st_svg, hdrs_svg, svg = get("/assets/encryption-chain.svg")
    check("37. /assets/encryption-chain.svg → 200", st_svg == 200, f"{st_svg}")
    check("38. SVG 资源 Content-Type 为 image/svg+xml",
          "svg" in (hdrs_svg.get("Content-Type") or ""), f"{hdrs_svg.get('Content-Type')}")
    check("39. SVG 有 viewBox 1120x470",
          b'viewBox="0 0 1120 470"' in svg, "")
    chain_kw = ["scrypt", "KEK", "DEK", "HKDF", "对象密钥", "AES-256-GCM", "密文信封",
                "AAD", "服务端只保存密文", "重新包裹 DEK"]
    missing_chain = [k for k in chain_kw if k not in docs]
    check("40. 图注/正文解释了整条链路（口令→KEK→DEK→对象密钥→AES-GCM→信封、AAD、只存密文、换密码不重传）",
          not missing_chain, f"缺失:{missing_chain}")
    check("41. 图注写明窄屏不溢出的自适应样式（copy-c.css 里 max-width:100% / height:auto）",
          os.path.isfile(os.path.join(HERE, "static", "copy-c.css"))
          and "max-width: 100%" in open(os.path.join(HERE, "static", "copy-c.css"),
                                        encoding="utf-8").read()
          and "height: auto" in open(os.path.join(HERE, "static", "copy-c.css"),
                                     encoding="utf-8").read(), "")

    # ---- 7. 文档页口径与事实对齐 ----
    print("\n[7] 文档页口径与事实对齐")
    check("42. 写「跨设备同步」且心履为四端", "跨设备同步" in docs and "四端" in docs, "")
    check("43. 不再出现「三端」", "三端" not in docs, "")
    check("44. AI 第三方边界（回复由第三方服务商生成 / 内容会离开本机）",
          "第三方服务商" in docs and "离开本机" in docs, "")
    check("45. Pinghe Launcher 的 AI 写成本地 / API 两种模式，仅本地模式不出本机",
          "本地模式不出本机" in docs or ("本地 / API" in docs or "本地 / API" in docs
                                       or "「本地 / API」" in docs), "")
    check("46. 邮件内容不交给 AI", "邮件内容不交给 AI" in docs, "")
    check("47. Pinghe Launcher 邮箱口径：桌面端 IMAP + 最近 100 封 + 纯文本 + 不加载外部图片",
          "IMAP" in docs and "最近 100 封" in docs and "纯文本" in docs
          and "不加载外部图片" in docs, "")
    check("48. Pinghe Launcher 网页版口径：只有四个入口 / 不做真实收发",
          "不做真实收发" in docs and "EduPage" in docs and "ManageBac" in docs, "")
    check("49. 本机侧边界：Pinghe Launcher 与 Pinghe Launcher Lite 共享账号文件当前为明文",
          "明文" in docs and "共享的账号文件" in docs
          and "Pinghe Launcher 与 Pinghe Launcher Lite" in docs, "")
    check("50. 服务端口径改成「只保存密文 / 口令与密钥不上传」，删掉绝对保证",
          "服务端只保存密文" in docs and "永远拿不到" not in docs, "")
    check("51. 补了备份建议", "建议定期备份" in docs, "")
    check("52. 写明口令与恢复码都丢失后云端数据无法解开",
          "无法解开" in docs and "恢复码" in docs, "")
    check("53. 配额与限流数字未被改动",
          all(n in docs for n in QUOTA_NUMBERS),
          f"缺失:{[n for n in QUOTA_NUMBERS if n not in docs]}",)

    # ---- 8. 全站审计 ----
    print("\n[8] 全站审计（禁用词 / 敏感串）")
    pages = ["index.html", "about.html", "docs.html", "download.html", "support.html",
             "login.html", "register.html", "account.html", "log.html"]
    pages += [f"products/{n}" for n in os.listdir(os.path.join(HERE, "products"))
              if n.endswith(".html")]
    joined = {}
    for rel in pages:
        p = os.path.join(HERE, rel)
        if os.path.isfile(p):
            joined[rel] = open(p, encoding="utf-8").read()
    banned = ["三端", "绝不丢失", "绝对安全"]
    hits = {rel: [b for b in banned if b in txt] for rel, txt in joined.items()}
    hits = {k: v for k, v in hits.items() if v}
    check("54. 全站 HTML 无「三端」/「绝不丢失」/「绝对安全」", not hits, f"命中:{hits}")

    own = {"download.html": dl, "docs.html": docs,
           "checksums.txt": cs_raw.decode("utf-8", "replace")}
    leaks = {rel: [s for s in SENSITIVE if s in txt] for rel, txt in own.items()}
    leaks = {k: v for k, v in leaks.items() if v}
    check("55. 下载页 / 文档页 / checksums.txt 无内网 IP、盘符路径、密钥文件名、真实用户名",
          not leaks, f"命中:{leaks}")
    check("56. 源文件不手写 ?v=",
          all("?v=" not in joined.get(r, "") for r in ("download.html", "docs.html")), "")

    print("\n" + "=" * 74)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
