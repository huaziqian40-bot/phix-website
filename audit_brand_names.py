# -*- coding: utf-8 -*-
"""官网「产品全称」盘点脚本（可重复运行）。

    C:\\Python314\\python.exe -X utf8 D:\\phix\\website\\audit_brand_names.py

口径：
0. **只看 .html 页面**（人能看到的产品名都在页面里）。*_lab/audit_brand_names.py
   会把 test_*.py 等也扫进来，路径不同 → 顶部数字与本文不同，以本脚本为准。
1. 先剥掉 <script>/<style>/<!-- -->，再剥掉所有 HTML 标签，得到「可见文本」；
   另外单独统计 `<code>` / `<pre>` 内的文本（那是 API 名、同步对象名、文件名，
   不是产品名，单独报告）。admin/ 页面也扫（它没有产品名，扫了更保险）。
2. 可见文本里的 `PHL` / `PHL Lite` / `PHLL` 都应归零；
   出现在文件名（phl-windows-setup.exe、product-phl.png 等小写/路径）里的不算。
3. `Pinghe Launcher` 出现次数应 ≥ 50（实现上 0 处简称 + 全称合计，见 [4]）。

退出码：发现「可见文本里的简称」或全称 < 50 → 1，否则 0。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# 站点页面（HTML；products/ 与 app/ 也算，admin/ 也扫）
SKIP_DIRS = {"__pycache__", "media", "feedback"}

TAG = re.compile(r"<[^>]+>")
CODE_BLOCK = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>", re.S | re.I)
# 文件名 / 路径形态的 phl（小写）允许存在
PATHISH = re.compile(r"[A-Za-z0-9_./@:%\-]*phl[A-Za-z0-9_./@:%\-]*")


def visible_text(html: str) -> tuple[str, str]:
    """返回 (可见文本, code/pre 内的文本)。"""
    body = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    body = re.sub(r"<script\b.*?</script>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<style\b.*?</style>", " ", body, flags=re.S | re.I)
    code = "\n".join(m.group(0) for m in CODE_BLOCK.finditer(body))
    code_text = TAG.sub(" ", code)
    rest = CODE_BLOCK.sub(" ", body)
    return TAG.sub(" ", rest), code_text


def pages() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".html"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


# SPA 外壳页会引用 /static/app/app.js 与 app.css：里面的「用户可见文案」也要一起管，
# 而 JS 变量名 / 事件名 / CSS 类名 / localStorage 键名 / 同步对象里的标识不算文案。
UI_SINK = re.compile(r"textContent|innerHTML|insertAdjacentHTML|alert|confirm|prompt"
                     r"|placeholder|\.title\s*=|\bsetAttribute\(\s*['\"]title")
STRING_LIT = re.compile(r"""(['"`])((?:(?!\1)[^\\]|\\.)*?)\1""", re.S)


def js_ui_strings() -> list[tuple[str, int, str]]:
    """返回 app.js 里「出现在用户可见出口那一行」的字符串字面量。"""
    out = []
    for path in (os.path.join(ROOT, "static", "app", "app.js"),):
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        lines = src.split("\n")
        for m in STRING_LIT.finditer(src):
            ln = src[:m.start()].count("\n") + 1
            if UI_SINK.search(lines[ln - 1]):
                out.append((os.path.relpath(path, ROOT).replace("\\", "/"), ln, m.group(2)))
    return out


def count(text: str, pat: str) -> int:
    return len(re.findall(pat, text))


def main() -> int:
    print("=" * 78)
    print("phix 官网 · 产品全称盘点（可见文本口径）")
    print("=" * 78)

    total = {"PHLL": 0, "PHL Lite": 0, "PHL": 0, "Pinghe Launcher": 0,
             "Pinghe Launcher Lite": 0}
    bad_lines: list[tuple[str, str, str]] = []
    leftover: list[tuple[str, str]] = []

    print(f"\n{'页面':34} {'PHLL':>5} {'PHLLite':>8} {'PHL':>5} {'全称':>5} {'全称Lite':>9}")
    print("-" * 78)
    for path in pages():
        html = open(path, encoding="utf-8", errors="replace").read()
        vis, code_text = visible_text(html)
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        n_phll = count(vis, r"PHLL")
        n_phllite = count(vis, r"PHL Lite")
        n_phl = count(vis, r"PHL")
        n_full = count(vis, r"Pinghe Launcher")
        n_full_lite = count(vis, r"Pinghe Launcher Lite")
        total["PHLL"] += n_phll
        total["PHL Lite"] += n_phllite
        total["PHL"] += n_phl
        total["Pinghe Launcher"] += n_full
        total["Pinghe Launcher Lite"] += n_full_lite
        flag = "  ← 简称" if (n_phll or n_phllite or n_phl) else ""
        print(f"{rel:34} {n_phll:>5} {n_phllite:>8} {n_phl:>5} {n_full:>5} {n_full_lite:>9}{flag}")

        for m in re.finditer(r"(PHLL|PHL Lite|PHL)", vis):
            ctx = vis[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            ctx = re.sub(r"\s+", " ", ctx).strip()
            bad_lines.append((rel, m.group(1), ctx))
        # code/pre 里剩下的命中（应为 API 名/对象名/文件名）
        for m in re.finditer(r"(PHLL|PHL Lite|PHL)([A-Za-z0-9_.\-]*)", code_text):
            ctx = code_text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
            leftover.append((rel, re.sub(r"\s+", " ", ctx).strip()))

    print("-" * 78)
    print(f"{'合计':34} {total['PHLL']:>5} {total['PHL Lite']:>8} {total['PHL']:>5} "
          f"{total['Pinghe Launcher']:>5} {total['Pinghe Launcher Lite']:>9}")

    print("\n[1] 可见文本里的简称命中（应为 0 行）：")
    if not bad_lines:
        print("    （无）")
    for rel, kind, ctx in bad_lines[:200]:
        print(f"    {rel}  [{kind}]  …{ctx}…")
    if len(bad_lines) > 200:
        print(f"    ……（另有 {len(bad_lines) - 200} 行，已省略）")
    vis_bad = len(bad_lines)

    print("\n[2] <code>/<pre> 内的命中（API 名 / 同步对象名 / 文件名，按设计保留）：")
    if not leftover:
        print("    （无）")
    for rel, ctx in leftover:
        print(f"    {rel}  …{ctx}…")

    print("\n[3] 全站小写文件名 / 路径里的 phl（保持原样，不算命中）：")
    seen: set[str] = set()
    for path in pages():
        html = open(path, encoding="utf-8", errors="replace").read()
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        for m in PATHISH.finditer(html):
            tok = m.group(0)
            if not re.search(r"[A-Z]", tok) and "phl" in tok and len(tok) > 3:
                key = f"{rel} :: {tok}"
                if key not in seen:
                    seen.add(key)
    for s in sorted(seen):
        print("    " + s)

    print("\n[3b] SPA 外壳引用的 static/app/app.js：用户可见出口上的字符串（textContent/innerHTML/…）：")
    js_bad = []
    for f, ln, s in js_ui_strings():
        flag = "!! 含简称" if re.search(r"PHL", s) else " ·"
        print(f"    {flag} {f}:{ln}  {s[:70]!r}")
        if re.search(r"PHL", s):
            js_bad.append((f, ln, s))
    if not js_bad:
        print("    （用户可见出口上的 JS 字符串共上面这些，均无简称）")

    ok = vis_bad == 0 and not js_bad and total["Pinghe Launcher"] >= 50
    print("\n[4] 判定：")
    print(f"    可见文本简称 = {vis_bad} （要求 0）")
    print(f"    app.js 用户可见文案里的简称 = {len(js_bad)} （要求 0）")
    print(f"    Pinghe Launcher = {total['Pinghe Launcher']} （要求 ≥ 50）")
    print("    → " + ("通过 ✅" if ok else "未通过 ❌"))
    print(f"    说明：JS 变量名 / 事件名 / CSS 类名 / localStorage 键名 / 同步对象标识"
          f"（如 device:'PHL Web'）不算文案，本脚本不检查也不得改动。")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
