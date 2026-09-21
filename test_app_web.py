"""Pinghe Launcher 网页端（/app/）专项测试（PHL Lite 版式：七视图，无「心履」也无「我的成绩」）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_app_web.py [端口]

覆盖：
  1) 七个视图 / 七个标签（首页 / 我的课表 / 我的日程 / 我的课程 / 平和邮箱 /
     Agent 助手 / 设置）且**没有心履视图、也没有「我的成绩」**，标签顺序与 app.js 的 VIEWS 一致
  2) 外壳 id 保留（boot / layout / topbar / who / logout / tabs / toast），登录逻辑不变
  3) 与官网视觉彻底解耦：不引 site.css、无 site-header/site-footer、无官网淡紫变量
  4) 侧栏是 PHL Lite 的深绿渐变 + 米白文字（token 逐个核对）
  5) 邮箱凭据是「同步对象只读、只显示账号名、密码一律打码」，账号名来自 meta.accounts
  6) 日程写回契约（POST /proxy/sync/objects/schedule/ + base_revision + device + 409 重放）
  7) 各视图渲染：课表（edupage.lessons 按天，没选教学组时给空态卡 + 选课入口）、课程
     （managebac.tasks 搜索 + 按截止时间排序）、邮箱（unread + recent + 正文 + 点开标已读）、
     首页（今天/下一节/未读/DDL）、设置（常规视图只留账号；技术细节收在折叠诊断区）
  8) 降级：实时失败 → 「以下为上次同步的数据（时间：…）」；两边都没有才空态；不白屏
  9) AI 页：能力边界文案、POST /app/ai/chat/、context（对象/字符/截断）、textContent 渲染、
     409 未配置处理、12 条历史上限
 10) 未登录访问 /app/ 行为、静态资源 200、AI 接口未登录 401
 11) 零外部依赖（无 CDN / 外链）
 12) 未读圆点只按每封邮件真实的 `unread` 画（字段缺失不画点）、
     首页 DDL 是 ±14 天窗口 + 最近 2 天加粗、课程页已过期排在下方
 13) HTML 邮件：服务端**不消毒**（body_html = MIME 原文，原样搬运）+ 前端把正文放进
     `<iframe sandbox="allow-same-origin">`（**不带 allow-scripts** → 邮件里的脚本不执行）
 14) 没选教学组时，课表**不铺全年级候选课**，改画空态卡 + 一步可达的选课入口
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = PORT if PORT.startswith("http") else f"http://127.0.0.1:{PORT}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "     # 公网走 Cloudflare：
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")              # 默认 urllib UA 会被 403
HERE = os.path.dirname(os.path.abspath(__file__))
APP_HTML = os.path.join(HERE, "app", "index.html")
APP_JS = os.path.join(HERE, "static", "app", "app.js")
APP_CSS = os.path.join(HERE, "static", "app", "app.css")
VIEWS = ["home", "timetable", "schedule", "courses", "mail", "ai", "settings"]
LABELS = ["首页", "我的课表", "我的日程", "我的课程", "平和邮箱", "Agent 助手", "设置"]
PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def get(path):
    r = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def post_json(path, body, redirect=True):
    """不带 cookie 的 POST；redirect=False 时手动处理 302/303 看原始状态。"""
    data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json", "User-Agent": UA},
                               method="POST")
    opener = urllib.request.build_opener() if redirect else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post_multipart(path, fields, files):
    """发一个 multipart/form-data 请求（附件上传走的就是它）。

    `fields` 是 `{名: 文本}`，`files` 是 `[(字段名, 文件名, 内容 bytes, MIME)]`。
    **这个助手是为了固化解附件解析踩到的坑**：服务端一开始用
    `email.message_from_bytes` 的默认 compat32 策略，它返回的老式 `Message`
    **没有 `iter_parts()`** → 抛异常 → 服务器直接断连接（客户端只看到
    RemoteDisconnected，没有任何可读错误）。这条测试就盯住"必须回一个 JSON"。
    """
    boundary = "----phixTest" + os.urandom(6).hex()
    out = []
    for k, v in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                    % (boundary, k, v)).encode("utf-8"))
    for name, filename, content, ctype in files:
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    'Content-Type: %s\r\n\r\n' % (boundary, name, filename, ctype)).encode("utf-8"))
        out.append(content)
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode("utf-8"))
    data = b"".join(out)
    r = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:  # noqa: BLE001  连接被断（服务器崩在处理器里）
        return 0, ("CONNECTION_DROPPED: %s" % type(exc).__name__).encode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def visible_text(html: str) -> str:
    """剥掉注释/script/style/标签后的可见文本。"""
    t = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    t = re.sub(r"<script\b.*?</script>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<style\b.*?</style>", " ", t, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", t)


def strip_comments(js: str) -> str:
    """剥掉 JS 里的块注释与行注释：断言「**代码里**没有某个 token」时不能把注释算进去
    （注释里常常正是那句「不要写 allow-scripts」的说明）。"""
    t = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    t = re.sub(r"(?m)^\s*//.*$", " ", t)
    return t


def main():
    print("=" * 72)
    print(f"Pinghe Launcher 网页端（/app/，PHL Lite 版式）专项测试 → {BASE}")
    print("=" * 72)

    html = open(APP_HTML, encoding="utf-8").read()
    js = open(APP_JS, encoding="utf-8").read()
    css = open(APP_CSS, encoding="utf-8").read()
    srv = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    py = open(os.path.join(HERE, "webapp_data.py"), encoding="utf-8").read()
    vis = visible_text(html)
    low_html = html.lower()
    low_css = css.lower()
    #: 剥掉注释的 app.js：判定「某句话不再写进页面」时必须用它 ——
    #: 代码里刻意留着原话的引用（说明这次改了什么），直接 in js 会被注释误判为「还在」。
    js_nocode = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js_nocode = re.sub(r"^\s*//.*$", "", js_nocode, flags=re.M)

    # ---------- [1] 七个视图 / 七个标签 ----------
    print("\n[1] 七个视图与七个标签（无「心履」、无「我的成绩」）")
    view_ids = re.findall(r'<section class="view[^>]*id="view-([a-z0-9]+)"', html)
    tab_views = re.findall(r'data-view="([a-z0-9]+)"', html)
    check("1. 恰好 7 个视图（section.view#view-*）", view_ids == VIEWS, f"{view_ids}")
    check("2. 恰好 7 个导航项（data-view=*，顺序一致）", tab_views == VIEWS, f"{tab_views}")
    check("3. 七个中文名齐全（含 emoji 前缀也算）",
          all(lb in html for lb in LABELS),
          json.dumps([lb for lb in LABELS if lb not in html], ensure_ascii=False))
    check("4. app.js 的 VIEWS 与七个视图一一对应",
          all(f"{{ id: '{v}'," in js for v in VIEWS) and "var VIEW_IDS = VIEWS.map" in js, "")
    check("5. 没有心履视图 / 没有心履导航 / 没有心履文案",
          "view-xinlv" not in html and "xinlv" not in low_html
          and "心履" not in vis and "心履" not in js
          and "xin-lv" not in low_html and "badge_100" not in html, "")
    check("6. 每个视图都是一段 section.view（当前视图为日程）",
          len(re.findall(r'<section class="view[^"]*" id="view-', html)) == 7
          and html.count('id="view-schedule" hidden') == 0, "")
    check("7. 除当前视图外，其余六个视图都带 hidden（切视图不闪全量内容）",
          len(re.findall(r'<section class="view" id="view-[a-z]+" hidden>', html)) == 6,
          f"{len(re.findall(r'<section class=.view. id=.view-[a-z]+. hidden>', html))}")
    check("8. 每个导航项都指向存在的视图",
          all(f'id="view-{v}"' in html for v in tab_views), "")

    # ---------- [2] 外壳结构（登录逻辑不变） ----------
    print("\n[2] 外壳结构（登录 / 登出逻辑不变）")
    for i, tid in enumerate(["boot", "loginwrap", "layout", "topbar", "who", "logout", "tabs", "toast"], 9):
        check(f"{i}. 保留 id={tid!r}", f'id="{tid}"' in html, "")
    check("17. 未登录时正文区隐藏（#app hidden）+ 登录卡自带",
          re.search(r'id="app"[^>]*hidden', html) is not None
          and 'id="login-form"' in html and 'id="login-user"' in html and 'id="login-pass"' in html, "")
    check("18. 登录仍走 POST /auth/login/（用户名 + 密码），并说明账号来源",
          "'/auth/login/'" in js and "账号由 PHIX 提供" in vis, "")
    check("19. 登出仍走 POST /auth/logout/ 且失败也回首页",
          "'/auth/logout/'" in js and "location.href = '/'" in js and 'id="set-logout"' in html, "")
    check("20. 会话失效不跳站外页，而是就地回到登录卡（toLogin → showLogin）",
          "function showLogin" in js and "登录状态已过期，请重新登录" in js
          and "location.href = '/login/" not in js, "")
    check("21. 顶栏有当前视图名 + 刷新按钮（view-title / btn-refresh）",
          'id="view-title"' in html and 'id="btn-refresh"' in html and "$('view-title').textContent" in js, "")

    # ---------- [3] 与官网视觉解耦 ----------
    print("\n[3] 与官网视觉彻底解耦")
    check("22. 不引 site.css（HTML 里没有 /static/site 引用）",
          "/static/site" not in low_html, "")
    check("23. 没有 site-header / site-footer / 站点顶栏结构",
          "site-header" not in low_html and "site-footer" not in low_html, "")
    check("24. app.css 里没有官网淡紫变量（--phix-violet / mist / dusk / lilac / rose）",
          "--phix-violet" not in low_css and "--phix-mist" not in low_css
          and "--phix-dusk" not in low_css and "--phix-lilac" not in low_css
          and "--phix-rose" not in low_css, "")
    check("25. 淡紫十六进制值也清零（#9A2BE2 / #F5F3FF / #E9D5FF / #6D28D9）",
          "#9a2be2" not in low_css and "#f5f3ff" not in low_css
          and "#e9d5ff" not in low_css and "#6d28d9" not in low_css, "")
    check("26. 页面里没有 phix 社团 logo / 页脚联系方式",
          "logo-transparent" not in low_html and "icloud.com" not in low_html
          and "微信" not in vis and "bilibili" not in low_html, "")
    check("27. 品牌是 Pinghe Launcher Web（2026-09-20 用户改名；UI 文案里不出现 PHL 简称）",
          "Pinghe Launcher Web" in vis and not re.search(r"\bPHL\b", vis)
          and js.count("PHL") == 3
          and "device: 'PHL Web'" in js and "app: 'PHL Web'" in js, f"{js.count('PHL')}")
    check("28. 页面只引本站 CSS/JS（无 CDN / 外链脚本）",
          re.findall(r'(?:src|href)="(https?://[^"]+)"', html) == []
          and "/static/app/app.css" in html and "/static/app/app.js" in html, "")

    # ---------- [4] PHL Lite 配色 token ----------
    print("\n[4] 侧栏与 token 是 PHL Lite 那套（深绿 + 米白）")
    for i, tok in enumerate([
        "--green-950:#102d25", "--green-900:#173f33", "--green-800:#1f5a46",
        "--green-700:#2a725a", "--green-100:#dfeae4", "--green-50:#edf4f0",
        "--wine-700:#8b3445", "--gold-600:#b58d45", "--gold-500:#c5a05a", "--gold-100:#f2e8d2",
        "--ivory-50:#fbfaf6", "--ivory-100:#f5f2e9", "--ivory-200:#ebe6d8",
        "--ink:#18231e", "--ink-2:#4d5d55", "--ink-3:#7c8982",
        "--border:rgba(24,35,30,.1)", "--radius-sm:10px", "--radius:16px", "--radius-lg:24px",
        "--side-w:224px",
    ], 29):
        check(f"{i}. token {tok}", tok in css.replace(" ", ""), "")
    check("50. 侧栏是深绿竖向渐变 + 米白文字",
          "linear-gradient(180deg,var(--green-900),var(--green-950))" in css
          and re.search(r"\.sidebar\{[^}]*color:var\(--ivory-50\)", css) is not None, "")
    check("51. 字体栈与客户端一致（Segoe UI Variable Text / PingFang SC / Microsoft YaHei UI）",
          '"Segoe UI Variable Text","Segoe UI","PingFang SC","Microsoft YaHei UI",sans-serif' in css, "")
    check("52. 控件字号 13.5px、输入框白底细边、聚焦绿边",
          "font-size:13.5px" in css and "background:var(--white)" in css
          and re.search(r"input:focus,select:focus,textarea:focus\{[^}]*border-color:var\(--green-700\)", css) is not None, "")
    check("53. 阴影是柔和绿色调（rgba(26,50,40,…)）",
          "rgba(26,50,40,.07)" in css and "rgba(26,50,40,.1)" in css, "")
    check("54. 宽屏侧栏常驻（.icon-btn 只在窄屏出现）",
          re.search(r"@media\s*\(min-width:901px\)\s*\{\s*\.icon-btn\s*\{\s*display:none", css) is not None, "")
    check("55. 窄屏（≤900px）侧栏收起、导航变横向标签条",
          "@media (max-width:900px)" in css and "#tabs{flex-direction:row;flex-wrap:wrap" in css.replace(" ", "")
          and ".sidebar.is-collapsed #tabs" in css, "")
    check("56. 表格窄屏容器内横向滚动（table-wrap overflow-x）",
          ".table-wrap{overflow-x:auto" in css.replace(" ", ""), "")
    check("57. 邮箱视图窄屏互斥显示列表/正文（.show-detail）",
          ".show-detail" in css and "show-detail" in js, "")
    check("58. 页面无「去客户端同步」这类过时口径 / 无横向固定宽度",
          "先在客户端抓取并同步" not in js and "width:1000px" not in css, "")

    # ---------- [5] 实时抓取数据层（契约） ----------
    print("\n[5] 实时数据层 GET /app/data/（契约不变）")
    check("59. app.js 调 GET /app/data/",
          "DATA_PATH = '/app/data/'" in js and "req('GET', DATA_PATH" in js, "")
    check("60. app.js 调 GET /app/mail/<uid>/ 读正文",
          "MAIL_PATH = '/app/mail/'" in js and "req('GET', MAIL_PATH + encodeURIComponent" in js, "")
    check("61. 课表渲染 edupage.lessons（平铺 + date 字段）",
          "function liveLessons" in js and "arrOf(pSeg('edupage'), 'lessons')" in js
          and "r.date || r.day" in js, "")
    check("62. 各视图显示 meta.accounts 的账号名（已连接：…）",
          "function accountLine" in js and "'已连接：'" in js, "")
    check("63. 逐平台错误：原样展示 meta.errors.<平台>",
          "function plErr" in js and "plErr('edupage')" in js and "plErr('managebac')" in js
          and "plErr('mail')" in js, "")
    check("64. 某平台失败时该面板显示原因而不是白屏",
          "emptyText('课表', 'edupage', 'EduPage')" in js
          and "emptyText('课程列表', 'managebac', 'ManageBac')" in js
          and "暂时拿不到邮件（" in js and "暂时拿不到作业列表（" in js, "")
    check("65. 每个视图有 loading 态（loading 文案 + 刷新按钮）",
          "正在从平台实时抓取…" in html or "正在从平台实时抓取…" in js, "")
    check("66. /app/data/ 失败时给出可读错误 + 重试提示",
          "实时抓取失败：" in js and "请点「刷新」重试" in js and "function refreshLive" in js, "")
    check("67. 显示抓取时间与 cache 命中/实时抓取标记",
          "fetched_at" in js and "缓存命中" in js and "实时抓取" in js and 'id="tt-cache"' in html, "")
    check("68. 「刷新」按钮加 ?force=1 绕过服务端缓存",
          "DATA_PATH + (force ? '?force=1' : '')" in js, "")

    # ---------- [6] 降级路径（实时失败 → 上次同步的数据） ----------
    print("\n[6] 降级路径（同步快照兜底）")
    check("69. 读同步对象 school 作兜底（ensureSync / syncFallbackRaw / edupageFromSync）",
          "function ensureSync" in js and "function syncFallbackRaw" in js
          and "function edupageFromSync" in js and "SCHOOL_OBJ = 'school'" in js, "")
    check("70. 降级时显示「以下为上次同步的数据（时间：…）」",
          "以下为上次同步的数据" in js and "同步对象里没有时间戳" in js, "")
    check("71. 降级逐平台判定（课表/课程/邮件各自回退，不互相清空）",
          "syncNotice('tt-info'" in js and "syncNotice('mb-info'" in js
          and "syncNotice('mail-error'" in js, "")
    check("72. 两边都没有才显示空态（文案含「同步对象里也没有快照」）",
          js.count("同步对象里也没有快照") >= 2, f"{js.count('同步对象里也没有快照')}")
    check("73. 客户端真实形态 school.edupage.days 会被归一化（flattenDays）",
          "function flattenDays" in js and "flattenDays(sec.days)" in js
          and "copy.date = day" in js, "")
    check("74. days 形态与平铺 lessons 都兼容（lessons 非空优先）",
          "Array.isArray(sec.lessons) && sec.lessons.length ? sec.lessons : null" in js, "")
    check("75. 课表按 date 字段分组（normLesson 读 date/day）",
          "r.date || r.day" in js and "prettyDay(day)" in js, "")
    check("76. 快照渲染时注明来源（「上次同步的快照」）",
          "上次同步的快照" in js, "")
    check("77. 渲染异常也有兜底文案（applyLive 兜底 + 面板级 try/catch）",
          "function renderPanelNow" in js and "这一块渲染失败了：" in js
          and "页面渲染出错：" in js, "")

    # ---------- [7] 日程可写回 ----------
    print("\n[7] 日程：查看 + 新增/删除写回")
    check("78. 日程仍走 schedule 同步对象（可写）",
          "schedule: 'schedule'" in js and "putObject(OBJ.schedule" in js, "")
    check("79. 写回带 base_revision", "base_revision: ref.revision" in js, "")
    check("80. 写回带 device 标识", "device: 'PHL Web'" in js, "")
    check("81. 409 冲突：提示 + 自动重试一次",
          "res.status === 409 && attempt === 0" in js and "云端有更新，已刷新，正在重试…" in js, "")
    check("82. 写失败给出可读提示（toast err.message）",
          re.search(r"\['catch'\]\(function \(err\) \{[\s\S]{0,200}?toast\(err\.message", js) is not None, "")
    check("83. 事件字段沿用既有格式（id/day/time/title/note/created）",
          all(k in js for k in ["id: id", "day: item.day", "time: item.time",
                                "title: item.title", "note: item.note", "created: nowIso()"]), "")
    check("84. 选课仍读 settings.lessons（用户的选课不是平台抓的）",
          "lessons: 'settings.lessons'" in js, "")
    check("85. settings.accounts 只读账号名（密码打码，无明文出口）",
          "accounts: 'settings.accounts'" in js and "function maskPw" in js and "••••••••" in js
          and 'type="password"' in html
          # 2026-09-15：新增「修改密码」（3 个）与「AI 密钥」（1 个）输入框后不再是 1 个；
          # 这里只要求密码框存在且页面无明文口令回显出口（maskPw/•••••••• 已断言）
          and html.count('type="password"') >= 1, "")
    check("86. 删除只对自己创建的条目开放（mineBy 判定）",
          "function mineBy" in js and "只能删除自己创建的日程" in js, "")

    # ---------- [8] 各视图渲染分支 ----------
    print("\n[8] 各视图渲染分支")
    check("87. 首页：今天/正在上的课/下一节 + 今日日程 + 未读 + ±14 天 DDL",
          "function renderHomeLessons" in js and "function renderHomeEvents" in js
          and "function renderHomeDdl" in js and "function renderHomeNow" in js
          and "±14 天" in vis and "正在上的课" in vis and "下一节课" in vis, "")
    check("88. 成绩视图已整个移除（导航/视图/渲染函数/gr-* 元素全部不存在）",
          "view-grades" not in html and 'data-view="grades"' not in html
          and "我的成绩" not in vis and "function renderGrades" not in js
          and "gr-body" not in html and "gr-body" not in js
          and "function renderGradeUnits" not in js and "gr-units" not in html,
          "成绩数据源 managebac.courses 保留，课程页仍显示总评")
    check("89. 课程视图：课程列 + 作业列（课程/标题/截止时间/状态/分数）",
          "function renderCourseList" in js and "function renderCourseTasks" in js
          and "'课程', '标题', '截止时间', '状态', '分数'" in js
          and "'课程名称', '总评', '单元数'" in js, "")
    check("90. 可按截止时间排序（近→远 / 远→近）+ 按课程，且可搜索",
          'value="due-asc"' in html and 'value="due-desc"' in html and 'value="course"' in html
          and "q.addEventListener('input', function () { renderCourseTasks(st.taskPanelBox); })" in js
          and "s.addEventListener('change', function () { renderCourseTasks(st.taskPanelBox); })" in js, "")
    check("91. 邮箱：未读（mail.unread）+ 最近邮件（mail.recent）+ 点行读正文",
          "function renderMailList" in js and "function openMail" in js
          and "row--clickable" in js and "data-uid" in js, "")
    check("92. 邮件正文：纯文本走 .mail-view__body + pre-wrap；HTML 走 .mail-frame 沙箱 iframe",
          "mail-view__body" in js and "white-space:pre-wrap" in css.replace(" ", "")
          and "mail-frame" in js and ".mail-frame" in css.replace(" ", "")
          and "class === 'iframe'" not in js, "")
    # 只认**真正写进 DOM 的 sandbox 值**（注释里写「不带 allow-scripts」是讲解，不是配置）
    _sv = re.search(r"var MAIL_FRAME_SANDBOX = '([^']*)';", js)
    sandbox_val = _sv.group(1) if _sv else None
    _sets = re.findall(r"setAttribute\('sandbox',\s*([^)]*)\)", js)
    check("92b. HTML 邮件原样进 srcdoc 沙箱 iframe：sandbox 值恰好是 allow-same-origin（**绝不含 allow-scripts**）",
          sandbox_val == "allow-same-origin"
          and "allow-scripts" not in (sandbox_val or "")
          and _sets == ["MAIL_FRAME_SANDBOX"]
          and "frame.setAttribute('srcdoc', html)" in js
          and "function fitMailFrame(" in js and "MAIL_FRAME_MAX_H = 3600" in js,
          f"sandbox_val={sandbox_val!r} setAttribute={_sets}")
    check("92c. 消毒彻底没了：前后端都没有 sanitizer，HTML 也不再内联进主文档（app.js 里没有 innerHTML）",
          "function mailBodyCard" in js
          and "'HTML 版式'" not in js and "'纯文本'" not in js
          and "'显示图片'" not in js
          and "sanitizeMailHTML" not in js and "MAIL_DROP_TAGS" not in js
          and "mailUnsafeURL" not in js and "DOMParser" not in js
          and "innerHTML" not in js
          and "sanitize" not in py, "")
    check("92d. 正文接口有 body_html（MIME 原文原样搬运），服务端不再做任何 HTML 改写",
          "body_html" in js and "原样" in py
          and "HTML_ALLOWED_TAGS" not in py and "_DROP_RE" not in py, "")
    check("92e. 点开邮件乐观标已读：调 POST /app/mail/<uid>/read/，失败回滚并提示",
          "'/read/'" in js and "function markMailSeen" in js and "function mailOptimisticRead" in js
          and "function mailRollbackRead" in js and "没能把它标记为已读" in js, "")
    check("92f. 页面上如实说明「打开邮件会把它标记为已读」",
          "打开一封邮件会把它标记为已读" in vis and "只标记你点开的那一封" in vis, "")
    check("92g. 没选教学组时课表给空态卡 + 一步可达的选课入口，不铺全年级候选课",
          "function noPickCard" in js and "还没选择你的教学组" in js
          and "nopick__pick" in js and "tt-nopick" in js
          and "function noLessonPick" in js
          and "下面是全年级候选课表" not in js, "")
    check("92h. 课表上**不再**写「只显示你勾选的教学组…（隐藏了其它组 N 条）」这类细节",
          "function ttRenderGroupNote" not in js
          and "只显示你勾选的教学组：" not in js
          and "隐藏了其它组" not in js
          and "已隐藏" not in js_nocode
          and "本周选课" not in js_nocode, "")
    check("92i. 设置页技术细节（数据来源/同步状态/关于/账号标识）收进默认收起的 <details>",
          '<details class="card diag" id="set-diag">' in html
          and 'id="set-source"' in html and 'id="set-sync"' in html
          and 'id="set-account-ids"' in html
          and "details.card.diag" in css.replace(" ", "")
          and '<details class="card diag" id="set-diag" open' not in html, "")
    check("93. 正文读取失败给可读提示 + 重试",
          "读不到这封邮件：" in js and "function mailErrorCard" in js and "'重试'" in js, "")
    check("94. 邮件头表头为 发件人 / 标题 / 日期",
          "['发件人', '标题', '日期']" in js, "")
    check("95. 设置视图常规区只留：登录账号 + 去个人中心 + 退出登录",
          "function renderSettings" in js and 'id="set-platforms"' in html
          and 'href="/account/"' in html
          and "去个人中心管理凭据 / AI" in vis and 'id="set-logout"' in html, "")
    check("96. 设置页写明「只显示账号名，不含任何密码」",
          "只显示账号名" in vis and "密码一律打码" in vis, "")

    # ---------- [9] AI 页 ----------
    print("\n[9] Agent 助手（AI 只读查询）")
    check("97. 能力边界文案：只读 / 不能修改任何数据 / 没有工作区文件功能",
          "不能修改任何数据、没有工作区/文件功能" in vis, "")
    check("98. 边界文案点明数据范围（课表、选课、ManageBac、邮件标题、日程）",
          all(k in vis for k in ["课表", "选课", "ManageBac", "邮件标题", "日程"]), "")
    check("99. 调用 POST /app/ai/chat/",
          "AI_PATH = '/app/ai/chat/'" in js and "req('POST', AI_PATH" in js, "")
    check("100. 请求体含 question 与 history", "question: question, history: prior" in js, "")
    check("101. 显示 context（对象 / 字符数 / 是否截断）",
          "ctxText" in js and "c.objects" in js and "c.chars" in js and "c.truncated" in js, "")
    # 邮件正文改走 srcdoc 沙箱 iframe 之后，app.js 里**一处 innerHTML 都不该再有**：
    # 正文原样渲染靠的是 iframe 的 sandbox，而不是「插进主文档前先洗干净」。
    _mb = js.find("/* ---- 正文区：有 body_html")
    _mc = js.find("function mailErrorCard(")
    _mail_body = js[_mb:_mc] if (_mb != -1 and _mc != -1 and _mc > _mb) else ""
    check("102. AI 回答仍用 textContent；app.js 全篇没有 innerHTML/insertAdjacentHTML（正文走 srcdoc iframe）",
          "el('div', 'msg__body', text)" in js
          and 'insertAdjacentHTML' not in js and 'outerHTML' not in js
          and js.count("innerHTML") == 0
          and "setAttribute('srcdoc', html)" in _mail_body
          and "setAttribute('sandbox', MAIL_FRAME_SANDBOX)" in _mail_body, "")
    check("103. 未配置（409）原样展示后端提示 + 配置入口",
          "res.status === 409" in js and 'href="/account/"' in html
          and 'href="/download/"' in html and "设置 → AI" in vis, "")
    check("104. 历史最多 12 条", "AI_MAX_HISTORY = 12" in js and "ai.history.length > AI_MAX_HISTORY" in js, "")
    check("105. 有 loading 与取消/超时处理",
          'id="ai-wait"' in html and "ai-cancel" in html and "AI_TIMEOUT_MS" in js
          and "AbortController" in js and "isTimeout" in js, "")
    check("106. 错误按 error.message 展示",
          "return (e && e.message) || ('请求失败（HTTP ' + res.status + '）')" in js, "")
    check("107. 416/429/502 等错误码不改写（原样展示后端 message）",
          "var message = errText(res);" in js and "aiError(message)" in js, "")

    # ---------- [10] HTTP 行为 ----------
    print("\n[10] HTTP 行为与静态资源")
    st, hdrs, body = get("/app/")
    page = body.decode("utf-8", "replace")
    check("108. 未登录 GET /app/ → 200", st == 200, f"{st}")
    check("109. 未登录返回的仍是同一套外壳（boot + 登录卡 + 七视图）",
          'id="boot"' in page and 'id="loginwrap"' in page
          and all(f'id="view-{v}"' in page for v in VIEWS), "")
    check("110. 未登录时正文区默认隐藏（#app 带 hidden，boot 不带）",
          re.search(r'id="app"[^>]*hidden', page) is not None
          and re.search(r'id="boot"', page) is not None, "")
    check("111. 未登录不重定向（无 Location 头）", "Location" not in hdrs, f"{hdrs.get('Location')}")
    check("112. 返回的 HTML 里 app.css / app.js 都带 ?v=（服务端自动加）",
          re.search(r'/static/app/app\.css\?v=[0-9a-f]{8}', page) is not None
          and re.search(r'/static/app/app\.js\?v=[0-9a-f]{8}', page) is not None, "")

    res_ok = True
    for a in ["/static/app/app.css", "/static/app/app.js", "/app/"]:
        s, h, b = get(a)
        if s != 200 or not b:
            res_ok = False
            print(f"       {a} → {s}")
    check("113. 三个资源都 200 且非空", res_ok, "")

    s, h, b = get("/static/app/app.css")
    check("114. app.css Content-Type 正确", "text/css" in (h.get("Content-Type") or ""), f"{h.get('Content-Type')}")
    s, h, b = get("/static/app/app.js")
    check("115. app.js Content-Type 正确", "javascript" in (h.get("Content-Type") or ""), f"{h.get('Content-Type')}")

    st, body = post_json("/app/ai/chat/", {"question": "hi", "history": []})
    parsed = {}
    try:
        parsed = json.loads(body or b"{}")
    except ValueError:
        parsed = {}
    check("116. 未登录 POST /app/ai/chat/ 不放行（401/400/409/429/502，绝不 200）",
          st in (400, 401, 409, 429, 502), f"HTTP {st} {str(parsed)[:120]}")
    check("117. 未登录请求不泄露任何回答（无 answer 字段）",
          "answer" not in parsed, f"{str(parsed)[:120]}")
    st, body = post_json("/app/ai/chat/", {"question": "hi", "history": []}, redirect=False)
    check("118. AI 接口未登录不重定向到登录页而是回 JSON", st != 302, f"HTTP {st}")

    st, body = post_json("/auth/login/", {"username": "", "password": ""})
    check("119. 登录接口空账号仍被拒（登录卡不会把空表单当成功）", st in (400, 401),
          f"HTTP {st} {str(body)[:80]}")

    # ---------- [11] 本轮新增：未读圆点只按真实状态画 / DDL 窗口 ----------
    print("\n[11] 未读圆点与 DDL 窗口（只看真实字段，不假设未读）")
    check("120. 未读圆点只在 unread === true 时画（字段缺失一律不画点）",
          "var unread = h.unread === true;" in js
          and "item.setAttribute('data-unread', unread ? '1' : '0')" in js
          and "if (unread) subj.appendChild(document.createTextNode('🔵 '));" in js
          and "st.mailUnread != null) ? true : false" not in js, "")
    check("121. 未读计数用服务端 mail.unread 原值（没有就显示未知，不猜）",
          "'未读 ' + (st.mailUnread == null ? '未知' : st.mailUnread) + ' 封'" in js
          and "if (box) box.textContent = (n == null) ? '–' : String(n);" in js
          and re.search(r"if \(n == null\) \{\s*\n\s*/\* 这一轮没拿到数", js) is not None, "")
    check("121b. 未读数**绝不显示 0 骗人**：缺失时显示 –，刷新失败时保住上一次的已知值",
          "(n == null) ? '–' : String(n)" in js
          and "if (st.mailUnreadLast != null) {" in js
          and "st.mailUnreadLast = n;" in js
          and "function renderHomeUnread(stamp)" in js, "")
    check("121c. 首页未读卡如实写明数据时间（meta.fetched_at → 「最新数据 …」，没有就写 —）",
          # 用户 2026-09-16 明确要求把「服务器抓取」改成「最新数据」，
          # 首页未读卡的口径跟着一起改（151b/151c 那一批），这里断言的就是新文案。
          'id="home-unread-stamp"' in html
          and "'最新数据 ' + (n == null ? '—' : (stamp || '—'))" in js
          and "function homeUnreadSource()" in js
          and "prettyStamp(m.fetched_at)" in js, "")
    check("121d. 刷新有即时反馈：按钮 loading（置灰 + 转圈 + 「正在抓取…」），抓完（成功或失败）都摘掉",
          "function beginRefreshButtons(" in js and "function finishRefreshButtons(" in js
          and "正在抓取" in js
          and "beginRefreshButtons(btnIds);" in js
          and "finishRefreshButtons();" in js
          # 顶栏那颗刷新按钮真的传进了 beginRefreshButtons（否则转圈永远不出现）
          and "refreshLive(panels[currentView] || null, ['btn-refresh'])" in js, "")
    check("121e. 数据落地后**当前可见视图**（含首页）一定重绘：applyLive → renderAllPanels + renderHomeNow",
          "function applyLive()" in js and "renderAllPanels();" in js
          and "renderHomeNow();" in js
          and "function renderAllPanels()" in js
          and "panelOrder.forEach(function (id) { renderPanelNow(id); });" in js, "")
    check("122. 首页 DDL = ±14 天窗口（dateStrOffset(-14) ~ dateStrOffset(14)）+ 最近 2 天加粗 urgent",
          "DDL_WINDOW_DAYS = 14" in js and "DDL_URGENT_DAYS = 2" in js
          and "day >= dateStrOffset(-DDL_WINDOW_DAYS) && day <= dateStrOffset(DDL_WINDOW_DAYS)" in js
          and "day >= dateStrOffset(-DDL_URGENT_DAYS)" in js
          and "'item ddl-item' + (urgent ? ' urgent' : '')" in js
          and ".item.ddl-item.urgent{font-weight:750}" in css.replace(" ", ""), "")
    check("123. 课程页：含已过期且排在下方（past-due 灰底 / 上方加粗 urgent）",
          "past-due" in js and "已过期 · " in js and "已过期的作业 / 考试排在列表下方" in js
          and "!r.past && isUrgentDue(r.dueRaw || r.due) ? ' urgent' : ''" in js
          and ".item.ddl-item.past-due" in css.replace(" ", ""), "")
    check("124. 时间解析不出来的 DDL 不丢：排在末尾并标「时间未知」",
          "function dueDay(" in js and "时间未知 · " in js
          and "if (dueDay(t.dueRaw)) known.push(t); else unknown.push(t);" in js, "")
    check("125. 网页端不做写操作：DDL 卡说明「不支持移除 DDL」而不假装能左滑删",
          "不支持像桌面客户端那样左滑移除" in vis and "DDL" in vis
          and "ddl_dismiss" not in js, "")

    # ---------- [12] 课表按教学组过滤成「我自己的课表」 ----------
    print("\n[12] 课表过滤（我自己的课表）与真实日期形态")
    check("126. 课表按用户勾选的教学组过滤（只留我的组；无组信息的一律保留）",
          "function filterLessonsByGroups" in js and "function personalGroups" in js
          and "if (!g) { kept.push(l); unknown++; return; }" in js
          and "if (keySet[groupKey(g)]) kept.push(l); else dropped++;" in js, "")
    check("127. 组名比较归一化：去空白（含全角/不换行空格）+ 忽略大小写",
          "function groupKey(" in js
          and "s.replace(/[\\s\\u00a0\\u3000]/g, '')" in js
          and "s.toLowerCase()" in js, "")
    check("128. 教学组来源优先级：settings.lessons → school.edupage.selected（都空则不过滤）",
          "function lessonGroupNames(" in js
          and "var fromLessons = uniqGroupNames(lessonGroupNames(st.lessons && st.lessons.doc));" in js
          and "var fromSync = uniqGroupNames(edupageFromSync().selected);" in js
          and "emptyGroups: true" in js, "")
    check("128b. 不用接口 edupage.selected 做过滤（实测它是全量 20 个课程组，拿它过滤等于没筛）",
          "return uniqGroupNames(liveSelected());" not in js
          and re.search(r"function personalGroups\(\)[^}]*liveSelected", js) is None, "")
    check("128c. GroupFolding：组名去重按归一化键（uniqGroupNames）",
          "function uniqGroupNames(" in js and "if (!key || seen[key]) return;" in js, "")
    check("129. 没勾教学组时**不给全年级候选课表**：课表位置只画空态卡 + 一步可达的选课入口"
          "（国家必修那部分照常显示，见 151c）",
          "function noLessonPick(" in js and "if (noLessonPick())" in js
          and "还没选择你的选修课：在下面勾选你上的选修课教学组，之后这里只显示你自己的课表。" in js
          and "在下面勾选你上的课，之后这里只显示你自己的课表。" not in js
          and "也可以在客户端「设置 → 选课」里勾，两边同步" in js
          and "未选择教学组，显示全年级候选课表（共 " not in js
          and "未选择教学组：下面是全年级候选课表（共 " not in js, "")
    # 判定「课表页不再写这些细节」时用剥掉注释的 js（见 main() 开头）
    check("130. 过滤细节（只显示你选的课 N/M 条、本周选课 N 个教学组、隐藏了 N 条）不再写进课表页",
          "只显示你选的课" not in js_nocode
          and "本周选课" not in js_nocode
          and "已隐藏" not in js_nocode
          and "'已连接：'" in js and "function accountLine" in js, "")
    check("131. 课表工具条上是绿色「已连接」徽标（只有四个字 + 一个绿点，用站点绿色 token）",
          'id="tt-conn"' in html and "tt-conn__dot" in html
          and "function ttRenderConn" in js and "function ttConnected" in js
          and "ttRenderConn();" in js
          and re.search(r"\.tt-conn\{[^}]*color:var\(--green-700\)", css.replace(" ", "")) is not None
          and re.search(r"\.tt-conn__dot\{[^}]*background:var\(--green-700\)",
                        css.replace(" ", "")) is not None
          and ("选课保存在同步对象" in js or "只改你自己的选课设置" in js), "")
    check("131b. 旧的 #tt-note / #tt-groups 与 ttRenderGroupNote 已整个删掉（课表上不再有长说明行）",
          'id="tt-note"' not in html and 'id="tt-groups"' not in html
          and "$('tt-note')" not in js and "$('tt-groups')" not in js
          and "ttRenderGroupNote" not in js, "")
    check("131c. 「已连接」徽标连不上/出错时不显示绿色（那一态仍走既有错误条）",
          "if (!box) return;" in js
          and "box.hidden = !on;" in js
          and re.search(r"function ttConnected\(\)\s*\{\s*\n\s*return !!\(st\.live \|\| st\.syncReady\)"
                        r"[^\n]*!plErr\('edupage'\)", js) is not None, "")
    check("132. settings.lessons 的 {lessons:[{subject,group}]} 形态能在「选课（教学组）」卡片里展示",
          "Array.isArray(doc.lessons) && doc.lessons.length" in js
          and "var gname = cellText(row.group || row.groups" in js, "")
    check("133. ManageBac 的相对日期（Thursday at 12:00 PM）能被解析成真实日期",
          "function relativeDueKey(" in js and "var rel = relativeDueKey(s);" in js
          and "WEEKDAY_KEY" in js, "")
    check("134. ISO 的 T 分隔符只在紧挨数字时替换（不能吃掉 Thursday 的首字母）",
          "String(v == null ? '' : v).replace(/(\\d)[Tt](\\d)/g, '$1 $2')" in js
          and re.search(r"var s = [^;]*\.replace\(/\[T\]/", js) is None, "")

    # ---------- [13] 网页端选课（写自己的 settings.lessons） ----------
    print("\n[13] 网页端选课入口（按科目分组 + 写回 settings.lessons）")
    check("135. 课表页空态卡内嵌按科目分组的选课界面",
          "function noPickCard(" in js and "function renderNopickPicker(" in js
          and "nopick-save" in js and "nopick-cancel" in js
          and "subjectPickerHTML" in js and "buildSubjectGroups" in js, "")
    check("136. 交互是多选（checkbox 勾选/取消）+ 保存按钮",
          "function collectPickerSelection(" in js and "function saveNopickPicker(" in js
          and "function toggleDraftGroup(" in js and "'保存选课'" in js, "")
    check("137. 按科目族聚合教学组（subjectFamily + buildSubjectGroups）",
          "function subjFamily(" in js and "function buildSubjectGroups(" in js
          and "function fmtSecTimes(" in js and "function grpRowHTML(" in js, "")
    check("138. 保存走与日程同一条写回路径：settings.lessons + base_revision（409 自动重放）",
          "putObject(OBJ.lessons, st.lessons, write)" in js
          and "getObject(OBJ.lessons).then(function (fresh)" in js
          and "base.lessons = sel;" in js, "")
    check("139. 写回形态是客户端认的 {lessons:[{subject,teacher,group}]}（带 version/kind/updated_at）",
          "base.lessons = sel;" in js and "base.kind = 'pinghe-lessons';" in js
          and "base.version = 1;" in js, "")
    check("140. 保存成功 → 立刻按新组过滤课表 + 提示会同步给客户端",
          "applyLive();   // 立刻按新组过滤课表" in js
          and "客户端同步后也会用这份选课" in js, "")
    check("141. 只读口径写在页面上：只改自己的选课设置，不动平台数据",
          "只改你自己的选课设置，不会改动学校平台上的任何数据" in js, "")
    check("142. 第一次写（云端还没有 settings.lessons）会先用 GET 对齐 revision",
          "st.lessons = fresh;" in js
          and "return putObject(OBJ.lessons, st.lessons, write);" in js, "")
    check("143. 选课界面按科目分组渲染（subjectPickerHTML + bindPickerFolds + collectPickerSelection）",
          "function subjectPickerHTML(" in js and "function bindPickerFolds(" in js
          and "function collectPickerSelection(" in js
          and "function renderNopickPicker(" in js, "")
    check("144. 空数组/空对象不算教学组名（{\"groups\":[]} 不能被读成一个叫 groups 的组）",
          "if (Array.isArray(v)) { if (v.length) out.push(k); return; }" in js
          and "if (isObj(v)) { if (Object.keys(v).length) out.push(k); return; }" in js, "")
    check("145. 没选课（三元组列表为空）→ 课表给空态卡 + 按科目分组的选课界面",
          "function filterLessonsByTriples(" in js
          and "function noPickCard(" in js
          and "function renderNopickPicker(" in js
          and "var subjects = buildSubjectGroups(lessons);" in js, "")

    # ---------- [14] 课表视图四修：连堂跨行 / 无组课不默认勾上 / 并行课 / 行高统一 ----------
    print("\n[14] 课表视图：连堂跨行、无组课程、并行课、行高统一")
    check("146. 连堂跨行卡是 #tt-week 的**直接子元素**（塞进 .tt-cell 里 grid-row 无效 → 第二行会空白）",
          "wide.classList.add('tt-span');" in js
          and "blk.classList.add('tt-span');" in js
          and "spans.forEach(function (node) { host.appendChild(node); });" in js
          and "var wide = ttLessonEl(span.from, tiW, false," in js
          and "cell.appendChild(ttLessonEl(span.from, tiW, false," not in js, "")
    check("147. 连堂判定照抄 PLL：**科目族**+同老师+同组+同教室（不是原始 subject），预扫描不判时间间隔；"
          "另外还吃服务端给的跨节卡（真实形态：12:45–14:10 盖住两节）",
          # PLL ui/app.js:271-273
          "return [subjFamily(l.subject), l.teacher, l.group, l.room, l.cancelled ? 1 : 0].join('|');" in js
          # PLL ui/app.js:297-308（预扫描：相邻两格同一节课 → 合并，无时间间隔判断）
          and "cells[q + m].length === 1 && !consumed[q + m] &&" in js
          and "ttSameKey(cells[q][0]) === ttSameKey(cells[q + m][0])) m++;" in js
          and "ttGapOk" not in js and "TT_MERGE_GAP_MIN" not in js
          # 网页端额外：服务端跨节卡
          and "function ttSpanOf(l, pi)" in js
          and "if (!(end > thisEnd && end >= nextStart)) break;" in js, "")
    check("148. 被跨过的行照常画格子（consumed 只决定「格子里不放课卡」），第二行不会什么都没有",
          "} else if (b.consumed[pi]) {" in js
          and "function ttScanSpans(cells)" in js
          and "function ttSameKey(l)" in js
          and "cover: scan.cover" in js, "")
    check("149. 同一时段多门并行课不再挤成一格：折叠成「N 门并行课」摘要 + 点击展开",
          "function ttParallelInto(cell, ls, b)" in js
          and "ttParallelInto(cell, ls, b);" in js
          and "ls.length + ' 门并行课'" in js
          and "list.hidden = true;" in js
          and "tt-par-sum" in css and "tt-par-list[hidden]{display:none}" in css.replace(" ", ""), "")
    check("150. 国家理科照抄 PLL：三张轮换卡（Native Physics物理/化学/生物）**合并成一个「国家理科」条目**"
          "（不是三门独立科目），且 `default: true` 默认必选",
          "var NATIVE_SCIENCE_KEYS = ['native physics', 'native chemistry', 'native biology'," in js
          and "'国家物理', '国家化学', '国家生物', '国家理科'];" in js
          and "var NATIVE_SCIENCE_LABEL = '国家理科';" in js
          and "function isNationalScience(l)" in js
          and "fam.indexOf(NATIVE_SCIENCE_KEYS[i]) !== -1" in js
          and "function mergeNationalScience(lessons)" in js
          and "subject: NATIVE_SCIENCE_LABEL," in js
          and "teacher: teachers.length === 1 ? teachers[0]" in js
          and "fam = NATIVE_SCIENCE_LABEL;" in js
          and "teacher = '理科组';" in js
          # PLL services.py:392 `native = fam == NATIVE_SCIENCE_LABEL` —— 只有国家理科是「默认必选」
          and "'default': (fam === NATIVE_SCIENCE_LABEL)" in js, "")
    check("151. 全班必修（无组国家课程）单独成区块，**没有复选框**、不表现成「用户勾了它」；"
          "国家理科走 PLL 那条路：在科目列表里带一个 locked 复选框、标签写「默认必选」",
          "function wholeBlockHTML(items)" in js
          and "function wholeRowHTML(fam, g)" in js
          and "'全班必修（自动包含，不用选）'" in js
          and "row.appendChild(el('span', 'wc-badge', '全班必修'));" in js
          and "if (whole.length) frag.appendChild(wholeBlockHTML(whole));" in js
          # PLL ui/app.js:2237-2249 原文
          and "var label = whole ? (g['default'] ? '默认必选' : '全班必修') : ('组' + g.group);" in js
          and "if (whole) inp.disabled = true;" in js
          and "return !!g.group || g['default'];" in js
          and "if (!g.group && !g['default']) whole.push({ fam: s.subject, g: g, subject: s.subject });"
              in js, "")
    check("151b. 课表过滤语义照 PLL card_selected 三条：无组的全班必修始终显示、国家理科默认必选、"
          "其余有组课必须命中选课；两条过滤路径同一口径",
          # PLL services.py:437-441
          "if (!lGrp) { kept.push(l); return; }" in js
          and "if (!g) { kept.push(l); unknown++; return; }" in js
          and js.count("if (isNationalScience(l)) { kept.push(l); return; }") == 2
          and "function isNationalRequired(l)" in js
          and "if (!lGrp || fg[lGrp]) { kept.push(l); return; }" not in js, "")
    check("151c. 一次选修都没勾时：国家必修（全班必修 + 合并后的国家理科）照常显示，选课面板照旧给",
          "'国家必修（自动包含，不用选）'" in js
          and "ttBuildByDay(week, isNationalRequired)" in js
          and "return mergeNationalScience(filtered.lessons);" in js
          and "function nestNationalOnly(list)" in js
          and "return { lessons: nestNationalOnly(list), kept: 0, dropped: 0, unknown: 0, empty: true,"
              in js, "")
    # 2026-09-16 用户要求「每个 p 的高度统一，遍历所有格子选出最高的一个，全部都按这个标准」：
    # 旧实现把「被跨行卡盖住的行」排除在取最大值之外（`if (spanRows[row]) continue;`），
    # 那几行改用「块高 ÷ 跨行数」→ 比别的行矮（实测 p8 就矮一截）。现在不排除。
    check("152. 行高统一：所有格子一起取全局最高，每个节次行（含被跨行卡盖住的行）都用它",
          "function ttFitRows(pRows)" in js
          and "grid.style.gridTemplateRows = '';          // 先还原，量的才是自然高度" in js
          and "if (spanRows[row]) continue;" not in js
          and "全局最高：正课行、跨行卡盖住的行、以及任何画过格子的行，全部一起比" in js
          and "var each = Math.ceil(el2.offsetHeight / cnt);" in js
          and "tpl.push((isPeriod || spanRows[rr]) ? (maxH + 'px') : 'auto');" in js
          and "if (!maxH) return;" in js, "")
    check("152b. 行高在切到课表视图 / 窗口 resize 时重算（渲染常发生在日程视图，那时量到 0）",
          "if (name === 'timetable') { ttRefitSoon(0); ttUpdateNowLine(); }" in js
          and "window.addEventListener('resize', function () { ttRefitSoon(180); });" in js
          and "function ttRefitSoon(delay)" in js, "")
    check("152c. 星期表头独占第 1 行：P1 从第 2 行开始（照客户端 tt-grid，之前 P1 与表头挤同一行）",
          "var r = 2;                         // 行 1 = 星期表头；P1 从第 2 行开始（照客户端 tt-grid）" in js
          and "var r = pi + 1;" not in js, "")
    check("153. 连堂跨行卡的 CSS/落点：直接放进网格、跨行、盖在被跨过的空格上",
          "#tt-week>.tt-lesson.tt-span" in css.replace(" ", "")
          and "grid-row:1 / span 2" not in js
          and "wide.setAttribute('data-span-source', 'server');" in js
          and "blk.setAttribute('data-span-source', 'merge');" in js
          and "spans.forEach(function (node) { host.appendChild(node); });" in js, "")
    check("153b. 当前时间线：横贯整表的绝对定位绿线（pointer-events:none），照客户端 #tt-nowline 同款",
          "#tt-nowline{position:absolute;left:0;right:0" in css.replace(" ", "")
          and "pointer-events:none" in css.split("#tt-nowline{")[1].split("}")[0]
          and "#tt-nowline::before" in css and "background:var(--green-700)" in css
          and "#tt-clock{" in css
          and "line.id = 'tt-nowline';" in js and "tag.id = 'tt-clock';" in js, "")
    check("153c. 时间线的位置按「节次起止时刻的比例」插值，用行的实际 rect 算（不写死行高）",
          "function ttNowPoint(t)" in js
          and "if (t >= s && t < e) return { row: i, f: (t - s) / (e - s), gapTo: null, g: 0 };" in js
          and "var rect = row.getBoundingClientRect();" in js
          and "rect.top - gridTop + rect.height * pt.f" in js
          and "top = endA + (startB - endA) * pt.g;" in js          # 课间：像素上线性插值
          and "gridTemplateRows" not in js.split("function ttNowPoint")[1].split("function bindTimetable")[0], "")
    check("153d. 时间线只在「显示的这一周包含今天」时画；翻到别的周不画",
          "function ttWeekHasToday()" in js
          and "if (!ttWeekHasToday()) return null;" in js
          and "isoOfDate(monday) === isoOfDate(today)" in js, "")
    check("153e. 时间线每分钟自动更新，切视图 / 刷新落地 / resize 后立即重算",
          "setInterval(function () { ttUpdateNowLine(); }, 60000)" in js
          and "function startTtNowLine()" in js and "startTtNowLine();" in js
          and "if (name === 'timetable') { ttRefitSoon(0); ttUpdateNowLine(); }" in js
          and "ttUpdateNowLine();      // 时间线只在课表可见时有意义" in js, "")
    check("153f. 时间线在面板不可见（渲染发生在日程视图）时不乱画，等可见时补算",
          "if (!rect.height) return null;" in js
          and "if (!grid.querySelector('.tt-time')) return null;" in js, "")

    # ---------- [15] 日程视图 = PHL Lite 的日历形式（周 / 月 / 年） ----------
    print("\n[15] 我的日程：周 / 月 / 年三视图日历（版式照客户端，纯 DOM/CSS、零依赖）")
    check("154. 顶部有周/月/年切换控件 + 上一页/下一页/回到今天 + 区间标题",
          'id="sched-v-week"' in html and 'id="sched-v-month"' in html and 'id="sched-v-year"' in html
          and 'data-v="week"' in html and 'data-v="month"' in html and 'data-v="year"' in html
          and 'id="sched-prev"' in html and 'id="sched-next"' in html and 'id="sched-today"' in html
          and 'id="sched-label"' in html, "")
    check("155. 三个日历宿主 + 月视图横向滚动容器 + 兼容层（屏外按天条目）",
          re.search(r'id="sch-week"\s+class="sch-week"', html) is not None
          and re.search(r'id="sch-month"\s+class="cal"', html) is not None
          and re.search(r'id="sch-year"\s+class="cal-year"', html) is not None
          and 'id="sched-cal-scroll"' in html
          and re.search(r'class="sch-compat"\s+id="sched-compat"', html) is not None, "")
    check("156. 默认落在**周视图**（周按钮 is-on、aria-pressed=true）+ 周一是第一天",
          re.search(r'<button class="seg__btn is-on" id="sched-v-week"[^>]*aria-pressed="true"', html)
          is not None
          and "var WD_MON = ['一', '二', '三', '四', '五', '六', '日'];" in js
          and "t.setDate(t.getDate() - ((t.getDay() + 6) % 7));" in js, "")
    check("157. 周视图：一周七列日期卡（照客户端 .sch-week / .sch-day）",
          "function schRenderWeek(byDay)" in js and "function schMonday(d)" in js
          and "function schDayCard(iso, byDay, opts)" in js
          and ".sch-week{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))" in css, "")
    check("158. 月视图：7 列整月网格 + 上下月补位 + 超出显示「还有 N 项…」（照 .cal-cell）",
          "function schRenderMonth(byDay)" in js and "var lead = (first.getDay() + 6) % 7;" in js
          and "var tail = (7 - ((lead + nDays) % 7)) % 7;" in js
          and "'cal-cell is-out'" in js and "'还有 ' + (evs.length - max) + ' 项…'" in js
          and ".cal-cell.is-out" in css.replace(" ", ""), "")
    check("159. 年视图：12 个月小月历缩略 + 有事件的日子有点 + 点某月跳到月视图（照 .cal-mini/.mini-grid）",
          "function schRenderYear(byDay)" in js and "'mini-head'" in js and "'mini-grid'" in js
          and "function schGotoMonth(m1)" in js and "'mini-dot'" in js
          and ".cal-mini{" in css and ".mini-grid{" in css and ".mini-dot{" in css, "")
    check("160. 今天 / 选中态都有样式，且切视图保留当前聚焦日期（schState.focus / anchor 一起带过去）",
          "function schCellCls(iso, extra)" in js and "' is-sel'" in js and "' today'" in js
          and "schState.anchor = d;" in js and "schState.focus = iso;" in js
          and ".sch-day.today{" in css and ".cal-cell.today{" in css
          and ".cal-cell.is-sel{" in css and ".mini-day.today{" in css, "")
    check("161. 点日历某天 → 当天安排卡片（列出/删除/在这一天新增），删权限口径不变",
          "function schOpenDay(iso)" in js and "function schFillDay()" in js
          and "function bindScheduleCalendar()" in js and "bindScheduleCalendar();" in js
          and 'id="schm-list"' in html and 'id="schm-add"' in html
          and "function mineBy" in js and "只能删除自己创建的日程" in js, "")
    check("162. 空态文案保留（还没有日程 + 点日历某天记一笔）",
          "'还没有日程。'" in js and "点日历上任意一天记一笔。" in js and "function schHint(" in js, "")
    check("163. 窄屏：日历格子给最小宽度、在容器里横向滚动（页面不横向溢出）",
          re.search(r"\.cal\{[^}]*min-width:5\d\dpx", css) is not None
          and re.search(r"@media \(max-width:760px\)\{[\s\S]{0,400}?\.sch-week\{[^}]*overflow-x:auto",
                        css) is not None
          and re.search(r"\.sch-week\{[^}]*repeat\(7,minmax\(9\d+px", css) is not None, "")
    check("164. 零依赖：没有引任何日历库 / 外链脚本，日历是 DOM + CSS 自己画的",
          re.findall(r'(?:src|href)="(https?://[^"]+)"', html) == []
          and "fullcalendar" not in js.lower() and "tui-calendar" not in js.lower()
          and "document.createElement" in js, "")
    check("165. 数据侧未改：仍只读同一个 schedule 同步对象（无新对象、无新接口、无本地日程库）",
          "schedule: 'schedule'" in js and "OBJ.schedule" in js and "OBJ.lessons" in js
          and "schedule_range" not in strip_comments(js) and "sync/objects/schedule" not in html
          and "/app/schedule" not in js, "")
    check("166. 日程写回路径完全没动：base_revision + device + 409 重放 + 字段格式",
          "base_revision: ref.revision" in js and "device: 'PHL Web'" in js
          and "res.status === 409 && attempt === 0" in js
          and all(k in js for k in ["id: id", "day: item.day", "time: item.time",
                                    "title: item.title", "note: item.note", "created: nowIso()"]), "")

    # ---------- [16] 选课模态：一个渲染器 + 两个宿主（2026-09-16 修「模态里什么都没有」）----------
    print("\n[16] 选课模态：工具条按钮打开后必须有内容（同一个渲染器喂两个宿主）")
    check("167. **只有一个选课渲染器**：宿主当参数传（`#picker-list` 与 `#nopick-list` 同一份实现）",
          "function renderSubjectPickerInto(host, filterValue)" in js
          and "return renderSubjectPickerInto($('picker-list')," in js
          and "return renderSubjectPickerInto($('nopick-list')," in js
          # 旧实现是「写死宿主的两份渲染」：模态那个函数自己又 clear + subjectPickerHTML 一遍
          and js.count("var frag = subjectPickerHTML(subjects,") == 1, "")
    check("168. **打开模态就渲染进模态自己的 `#picker-list`**（这次漏的就是这一步）",
          re.search(r"function openPickerModal\(\)[\s\S]{0,900}?renderPickerList\(\);", js) is not None
          and re.search(r"function openPickerModal\(\)[\s\S]{0,900}?overlay\.hidden = false", js)
          is not None, "")
    check("169. 同一时刻只留一个选课界面：模态开着 → 课表空态卡那份收起（[hidden] + CSS 生效）",
          "function syncPickerHosts()" in js and "cardHost.hidden = modalOpen;" in js
          and "if (cardList) clear(cardList);" in js
          and re.search(r"\.nopick__picker\[hidden\]\{display:none\}", css.replace(" ", ""))
          is not None, "")
    check("170. 两个入口（工具条按钮 / 空态卡按钮）打开的是**同一个模态**",
          "if (pickBtn) pickBtn.addEventListener('click', openPickerModal);" in js
          and re.search(r"closest\('#tt-pick-open'\)\)\s*\{\s*[\s\S]{0,60}?openPickerModal\(\);", js)
          is not None
          and "function toggleGroupPicker() {\n  openPickerModal();\n}" in js, "")
    check("171. 两个入口**同一份勾选状态**：勾选同步进共享草稿（模态与卡片各挂一个 change 委托）",
          "function syncPickerDraftFrom(host)" in js
          and "syncPickerDraftFrom($('picker-list'));" in js
          and "ev.target.closest('#nopick-list')" in js
          and "function pickerDraftKeys()" in js, "")
    check("172. 两个入口**同一份保存逻辑**：`savePickerModal`/`saveNopickPicker` 都调 `savePickerSelection`"
          "（写回仍是 base_revision + 第一次 GET 对齐）",
          "function savePickerSelection(o)" in js
          and "savePickerSelection({ overlay: true, btnId: 'picker-save', label: '保存选课' });" in js
          and "savePickerSelection({ btnId: 'nopick-save', label: '保存选课' });" in js
          and js.count("function savePickerSelection(o)") == 1
          and js.count("putObject(OBJ.lessons, st.lessons, write)") == 2
          and js.count("getObject(OBJ.lessons).then(function (fresh)") == 1, "")
    check("173. 保存成功 → 关模态 + 草稿归零 + applyLive 立刻按新选课过滤课表",
          re.search(r"pickerTouched = false;\s*if \(o\.overlay\) closePickerModal\(\);", js) is not None
          and "applyLive();   // 立刻按新组过滤课表" in js, "")
    check("174. 有选课时打开模态预勾选当前选课（草稿回到已保存的那份，别退化）",
          "pickerTouched = false;\n  renderPickerList();" in js
          and "function savedTripleList()" in js
          and "function savedTripleKeys()" in js
          and "if (!pickerTouched) pickerDraftList = savedTripleList();" in js, "")
    check("175. 勾选键必须是 Set：`grpChecked` 调 `.has()`（老兜底放的是普通对象 → 渲染直接抛错）",
          "function grpChecked(checkedSet, fam, teacher, group)" in js
          and "function savedGroupKeys()" in js
          and "var groupKeys = {};" not in js
          and "var keys = new Set();" in js, "")
    check("176. 选课列表画了几行由渲染器**返回**（测试与脚本可以直接断言，不必数 DOM）",
          "return host.querySelectorAll('.subject-row').length;" in js
          and "var panel = cdp.evaluate" not in js, "")

    # ---------- [17] 导出课表（PNG / CSV）：方向与页面相反 ----------
    # 用户原话：「加一个导出课表的功能，可以导出这个人目前的课表，但是是**周一周二这些是竖着，
    # 然后 p 几是横着排列的**。」即：导出表 **行 = 星期、列 = 节次**（页面是行=节次、列=星期）。
    print("\n[17] 导出课表：入口（站内小下拉）+ 行=星期/列=节次的 CSV 与 Canvas PNG")
    check("177. 工具条上有「⬇ 导出课表」按钮 + 站内小下拉（PNG / CSV 两项，aria 状态齐全）",
          'id="tt-export-btn"' in html and "⬇ 导出课表" in html
          and 'id="tt-export-menu"' in html and 'role="menu"' in html
          and 'id="tt-export-png"' in html and 'id="tt-export-csv"' in html
          and "导出图片 PNG" in html and "导出表格 CSV" in html
          and 'aria-haspopup="menu"' in html and 'aria-controls="tt-export-menu"' in html
          # 用户明确要求：不要 window.confirm / window.prompt
          # （用剥掉注释的 js 判定：代码里刻意留着原话的说明，不该被算成「还在用它」）
          and "window.confirm" not in strip_comments(js) and "window.prompt" not in strip_comments(js)
          and ".tt-export__menu" in css and ".tt-export__item" in css, "")
    check("178. **方向与页面相反**：导出矩阵按「行=星期、列=节次」建，且与渲染用同一份排格结果",
          re.search(r"function ttExportMatrix\(\)[\s\S]{0,1400}?var byDay = ttBuildByDay\(week, null\);", js)
          is not None
          and "header.push(c.kind === 'P' ? (c.name + ' ' + c.start + '-' + c.end) : c.name);" in js
          and "var header = ['星期', '日期'];" in js
          # 第一行就是表头、数据行的前两列是「星期 / 日期」（不是把表头横着塞进一行）
          and "var out = [data.header.map(csvField).join(',')];" in js
          and "var line = [r.label, r.date];" in js, "")
    check("179. 导出内容 = 当前这一周、当前**过滤后**的课表（st.ttLessons / ttWeekData），不重新抓取",
          "var week = (ttWeekData && ttWeekData.week && ttWeekData.week.length)" in js
          and "? ttWeekData.week : ttBuildWeek();" in js
          and re.search(r"function ttExportMatrix\(\)[\s\S]{0,900}?ttBuildByDay\(week, null\)", js)
          is not None
          and "/app/data" not in js.split("function ttExportMatrix")[1].split("function ttExportCsvText")[0], "")
    check("180. CSV 是 **UTF-8 带 BOM**（\\ufeff），逗号/引号按 RFC4180 转义（双引号包裹 + \"\" 翻倍）",
          "'\\ufeff' + out.join('\\r\\n') + '\\r\\n'" in js
          and 'if (/[",\\r\\n]/.test(s)) return \'"\' + s.replace(/"/g, \'""\') + \'"\';' in js
          and "function csvField(v)" in js and "function ttExportCsvText(m)" in js, "")
    check("181. PNG 是**自己用 canvas 画的**：devicePixelRatio 放大 + 系统中文栈 + toBlob + download",
          "function drawExportPng(m, canvas, dpr)" in js
          and "canvas.width = Math.round(w * ratio);" in js
          and "canvas.height = Math.round(h * ratio);" in js
          and '"PingFang SC","Microsoft YaHei",system-ui,sans-serif' in js
          and "canvas.toBlob(function (blob)" in js
          and "URL.createObjectURL(blob)" in js and "URL.revokeObjectURL(url)" in js
          and "a.download = filename;" in js
          # 零外部依赖：没有 canvas 库、没有 CDN
          and re.findall(r'(?:src|href)="(https?://[^"]+)"', html) == []
          and "fabric" not in js.lower() and "html2canvas" not in js.lower()
          and "cdn." not in low_html, "")
    check("182. 文件名与标题按这一周：`课表-<周一>.png|csv`、标题「我的课表 · 起 ~ 止」",
          "return '课表-' + day + '.' + ext;" in js
          and "var day = (m && m.weekStart) || isoToday();" in js
          and "title: '我的课表 · ' + week[0].day + ' ~ ' + week[6].day," in js
          and "ttExportFilename(data, 'png')" in js and "ttExportFilename(data, 'csv')" in js, "")
    check("183. 课表没加载完 → 按钮禁用 + 点它给中文提示（不是静默无反应）",
          "function ttExportReady()" in js and "function updateTtExportState()" in js
          and "btn.disabled = !ready;" in js and "btn.title = ready" in js
          and "'课表还没加载完'" in js
          and "课表还没加载完，稍等一下再导出（数据一到就能导出）" in js
          and "updateTtExportState();" in js
          # 渲染里也要跟着刷新按钮状态（数据到 / 周切换 / 空态分支）
          and js.count("updateTtExportState();") >= 4
          and "if (!grid || !pRows || !pRows.length || !grid.children.length) return;" in js, "")

    # ---------- [18] 课程活动流（通知 / 消息 / 讨论）+ 邮件全量与附件 ----------
    # 用户原话：「我的课程页面可大幅度更新一下，直接抓取 managebac 的所有通知然后加一个
    # 最近通知卡片，然后点击那个通知就可以跳对应的卡片……然后各类消息类型你都做一下适配
    # 然后可以有对应的卡片」；以及「邮件……所有邮件都抓取然后显示」「发送附件 / 查看对方的附件」。
    print("\n[18] 课程活动流（通知 / 私信 / 讨论卡片）+ 邮件全量与附件")
    check("184. 课程页有三张活动卡片：通知 / 私信 / 讨论（宿主 id 齐全）",
          'id="mb-notifications"' in html and 'id="mb-messages"' in html
          and 'id="mb-discussions"' in html
          and html.count('class="grid-3 mb-activity"') == 1
          and ".grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in css, "")
    check("185. 三张卡片都走**独立的后端端点**（不是从课表/作业数据里凑）",
          "'/app/courses/notifications/'" in js and "'/app/courses/messages/'" in js
          and "function loadCourseActivity(force)" in js
          and "function fetchCourseActivity(path)" in js
          and "loadCourseActivity(false)['catch'](function () {});" in js, "")
    check("186. 三个渲染器都注册成面板（进课程视图渲染，数据落地后 applyLive 也会重画）",
          "renderPanel('mb-notifications', renderNotifications);" in js
          and "renderPanel('mb-messages', renderMessages);" in js
          and "renderPanel('mb-discussions', renderDiscussions);" in js
          and "function renderNotifications(panel)" in js
          and "function renderMessages(panel)" in js
          and "function renderDiscussions(panel)" in js, "")
    check("187. 「读不出来」绝不画成「没有数据」：每种上游状态都有中文说明",
          "var CO_STATUS_TEXT = {" in js
          and "unrecognized: '页面结构没认出来，无法确认有没有数据'" in js
          and "unavailable: 'ManageBac 学生页面没有找到这个入口'" in js
          and "authentication_required: 'ManageBac 会话失效了" in js
          and "rate_limited: 'ManageBac 限流了，稍后再说" .replace("再说", "再试") in js
          and "function coPlaceholder(data, what)" in js
          # 空列表只有在 status 是 ok/partial 时才敢说「暂时没有」
          and "if (status !== 'ok' && status !== 'partial') {" in js, "")
    check("188. 消息按类型分流：讨论类进私信卡，discussion 单独给讨论卡",
          "r.type === 'discussion' || r.type === 'announcement' || r.type === 'other'" in js
          and "return r.type === 'discussion';" in js, "")
    check("189. 每条活动都有回 ManageBac 的链接（同源校验过才给），且新窗口打开带 noopener",
          "a.href = row.link;" in js and "a.target = '_blank';" in js
          and "a.rel = 'noopener noreferrer';" in js
          and "if (row.link) {" in js, "")
    check("190. 刷新按钮把三个活动面板一起带上（点刷新不会漏掉通知）",
          "'mb-courses', 'mb-tasks', 'mb-notifications', 'mb-messages', 'mb-discussions'" in js
          and "loadCourseActivity(true)['catch'](function () {});" in js, "")
    check("191. 邮件：附件栏是真**下载链接**，指向 /app/mail/<uid>/attachments/<index>/",
          "function mailAttachmentsBar(uid, atts)" in js
          and "'/app/mail/' + encodeURIComponent(String(uid)) +" in js
          and "'/attachments/' + idx + '/';" in js
          and "link.setAttribute('download', label);" in js
          and "var link = el('a', 'ghost att-dl', '');" in js, "")
    check("192. 附件大小按人看的口径显示（B / KB / MB）",
          "function mailBytes(n)" in js
          and "if (v < 1024) return v + ' B';" in js
          and "(v / 1024).toFixed(1) + ' KB'" in js
          and "(v / 1024 / 1024).toFixed(1) + ' MB'" in js, "")
    check("193. 最新 10 封**后台预取**正文（列表先出来，正文随后到 → 点开即显）",
          "var MAIL_PRELOAD_N = 10;" in js
          and "var mailBodyCache = {};" in js
          and "function scheduleMailPreload(rows)" in js
          and "function mailPreloadPump()" in js
          and "scheduleMailPreload(heads);" in js
          # 串行：一次只发一个（不把邮箱服务器打爆）
          and "if (mailPreloadBusy) return;" in js
          and "mailPreloadBusy = true;" in js
          and "mailPreloadBusy = false;" in js, "")
    check("194. 没有正文时只说「正在加载…」（不显示编号、不显示技术细节）",
          "card.appendChild(el('div', 'mail-view__row', '正在加载…'));" in js
          and "正在加载" in js, "")
    check("195. 正文到手后列表补回形针；已点开的那封就地渲染出来",
          "mailBodyCache[String(uid)] = {" in js
          and "renderMailList($('mail-heads'));      // 列表补上回形针" in js
          and "if (String(st.mailOpen) === String(uid)) openMail(uid);" in js, "")
    check("196. 回形针按本地正文缓存里的附件数画（服务端列表不带附件）",
          "var cachedAtts = (mailBodyCache[String(h.uid)] || {}).attachments;" in js
          and "'att-clip'" in js and "📎" in js
          and ".mail-item .att-clip{" in css, "")
    check("197. 刷新时清空正文缓存与预取队列（重新拿最新正文）",
          "mailBodyCache = {};" in js and "mailPreloadQueue = [];" in js
          and "refreshLive" in js, "")
    check("198. 附件链接可点、可聚焦、有 hover 反馈（不是静态文本）",
          "a.att-dl{" in css and "cursor:pointer" in css
          and "a.att-dl:hover{" in css and "a.att-dl:focus-visible{" in css, "")
    check("199. 课程详情从列表进入、能返回（返回按钮复位两套视图）",
          "function openCourseDetail(course)" in js and "function closeCourseDetail()" in js
          and "if (listBox) listBox.hidden = true;" in js
          and "if (listBox) listBox.hidden = false;" in js
          and "function bindCourses()" in js and "bindCourses();" in js, "")
    check("200. 课程详情四个区块都在（基本信息 / 作业考试 / 资源 / 成绩）",
          'id="mb-detail-info"' in html and 'id="mb-detail-tasks"' in html
          and 'id="mb-detail-resources"' in html and 'id="mb-detail-grades"' in html
          and "function loadCourseDetail(classId)" in js
          and "function renderCourseDetail(details)" in js
          and "'/app/courses/' + encodeURIComponent(classId) + '/details/'" in js, "")
    # 只读口径：活动流这条链路上不能出现任何写操作
    _act = js.split("function fetchCourseActivity")[1].split("/* ---------------------------------------------------------------- 课程详情 */")[0]
    _det = js.split("function loadCourseDetail")[1].split("/** 作业/考试一行")[0]
    min_ = js.split("function mailAttachmentsBar")[1].split("/** 字节数 → 人看的大小")[0]
    check("201. 活动卡片与课程详情**全程只读**：这条链路上没有 POST / PUT / DELETE",
          # 活动流：显式 GET
          "req('GET', path, undefined, { timeoutMs: DATA_TIMEOUT_MS })" in _act
          and "'POST'" not in _act and "'PUT'" not in _act and "'DELETE'" not in _act
          # 课程详情：裸 fetch（不带 method 就是 GET），且没有改成写方法
          and "fetch('/app/courses/' + encodeURIComponent(classId) + '/details/'" in _det
          and "method:" not in _det
          and "'POST'" not in _det and "'PUT'" not in _det and "'DELETE'" not in _det, "")
    check("202. 活动流数据只进内存状态，不写回任何同步对象（settings / school / schedule 都不动）",
          "st.coNotifs = " in _act and "st.coMsgs = " in _act
          and "writeSyncObject" not in _act and "proxy/sync/objects" not in _act
          and "st.school" not in _act and "st.schedule" not in _act
          # 详情那条链路同样不写
          and "writeSyncObject" not in _det and "proxy/sync/objects" not in _det
          # 附件栏只做 <a href> 下载，没有任何写操作
          and "'POST'" not in min_ and "'PUT'" not in min_, "")

    # ---------- [19] 邮件：回复 / 转发 / 撰写带附件 ----------
    # 用户原话：「需要实现查看和下载附件、撰写、回复和转发邮件功能，所有端都要实现」。
    # 参考 CipherCore E-Mail Suite（MIT / Python）的撰写窗口语义，见
    # `D:\phix\_lab\ciphercore\cces.py` 的 `_open_compose_email_window`（2287–2540 行）。
    print("\n[19] 邮件：回复 / 转发 / 撰写带附件（参考 CipherCore E-Mail Suite）")
    _cm = js.split("function composeFromMail")[1].split("function composeAddOriginalAttachments")[0]
    check("203. 回复与转发按钮都在**正文最上面**（不是滚到末尾才看见）",
          "function mailActionBar(uid)" in js
          and "card.appendChild(mailActionBar(uid));" in js
          # 两处调用（缓存命中那条 + 按需加载那条）都要挂上
          and js.count("card.appendChild(mailActionBar(uid));") == 2
          and "'↩ 回复'" in js and "'↪ 转发'" in js
          and ".mail-actions{" in css, "")
    check("204. 回复语义照参考实现：收件人取发件人地址、主题加 `Re: `（已有就不重复加）",
          "function replyToMail(uid) { composeFromMail(uid, 'reply'); }" in js
          and "already = (mode === 'forward')" in js
          and "/^\\s*re\\s*:/i.test(origSubject)" in js
          and "prefix = (mode === 'forward') ? 'Fwd: ' : 'Re: '" in js, "")
    check("205. 转发语义照参考实现：收件人**留空**、主题加 `Fwd: `、引用原文",
          "to: (mode === 'reply') ? fromAddr : ''" in js
          and "/^\\s*(fwd|fw)\\s*:/i.test(origSubject)" in js
          and "in_reply_to: (mode === 'reply') ? uid : ''" in js, "")
    check("206. 引用块结构与参考一致（中文文案 + 逐行 `> ` 引用）",
          "'---------- 原始邮件 ----------'" in js
          and "' 写道：'" in js
          and "quote.push('> ' + line);" in js
          and "origText.split('\\n').forEach" in js, "")
    check("207. 转发**把原附件一并带上**（参考实现留了 TODO，我们做掉）",
          "function composeAddOriginalAttachments(uid, attachments)" in js
          and "if (mode === 'forward' && cached) composeAddOriginalAttachments(uid, cached.attachments);" in js
          and "'/attachments/' + Number(att.index) + '/'" in js
          and "new File([blob], name," in js
          # 回复**不带**原附件：这段里每一处调用都必须被 `mode === 'forward'` 守着
          and _cm.count("composeAddOriginalAttachments") == 2
          and all("mode === 'forward'" in ln
                  for ln in _cm.splitlines() if "composeAddOriginalAttachments" in ln), "")
    check("208. 撰写窗口有附件选择（原生 file 输入 + 按钮触发），且选完清 value 以便重复选同一文件",
          'id="compose-attach-input"' in html and 'multiple' in html
          and 'id="compose-attach-btn"' in html
          and "attachBtn.addEventListener('click', function () { attachInput.click(); });" in js
          and "attachInput.value = '';" in js, "")
    check("209. 附件在发送前可逐个移除，并显示名字与大小",
          "function composeRemoveFile(index)" in js
          and "function renderComposeAttachments()" in js
          and "'📄 ' + f.name" in js and "mailBytes(f.size)" in js
          and ".compose-att__del{" in css, "")
    check("210. 附件限额与桌面端/服务端三边一致（20 个 / 单个 20 MB / 合计 20 MB）",
          "var COMPOSE_MAX_ATTACHMENTS = 20;" in js
          and "var COMPOSE_MAX_FILE_BYTES = 20 * 1024 * 1024;" in js
          and "var COMPOSE_MAX_TOTAL_BYTES = 20 * 1024 * 1024;" in js
          and "附件最多 ' + COMPOSE_MAX_ATTACHMENTS" in js, "")
    check("211. 有附件走 multipart/form-data，没附件仍走 JSON（向后兼容本机桥）",
          "var hasFiles = composeAttachments.length > 0;" in js
          and "payload = new FormData();" in js
          and "payload.append('attachments', f, f.name);" in js
          and "return req('POST', MAIL_SEND_PATH, data, { timeoutMs: DATA_TIMEOUT_MS });" in js
          and "function sendMail(data, isForm)" in js
          and "if (isForm) {" in js, "")
    check("212. FormData **绝不能自己设 Content-Type**（手写会丢掉 boundary，服务端就解不出附件）",
          "if (typeof FormData !== 'undefined' && body instanceof FormData) {" in js
          and "opt.body = body;" in js
          and "浏览器要给这段 body 补上带 boundary 的 Content-Type" in js, "")
    check("213. 413 有专门的中文提示（附件太大），不是一句通用失败",
          "res.status === 413" in js
          and "附件太大：单个最大 20 MB、合计最大 20 MB。" in js, "")
    check("214. 打开/关闭撰写窗口都会清掉上一次的附件（不漏发上一次的文件）",
          "composeAttachments = [];" in js
          and js.count("composeAttachments = [];") >= 2
          and "st.composeFor = '';" in js, "")
    check("215. multipart 解析抽成**纯函数**（可单测），且用 `policy.default`（compat32 的 Message 没有 iter_parts，会崩连接）",
          "def parse_multipart_mail(ctype: str, raw: bytes):" in srv
          and "message_from_bytes(enveloped + bytes(raw), policy=policy.default)" in srv
          and "for part in msg.iter_parts():" in srv
          and "没有 `iter_parts()`" in srv
          and "return parse_multipart_mail(ctype, self.rfile.read(length))" in srv, "")

    # 直接单测解析器（不依赖登录态，能真正区分新旧实现）
    sys.path.insert(0, HERE)
    import server as srv_mod  # noqa: E402
    _boundary = "----phixUnit" + os.urandom(4).hex()
    _raw = (
        ("--%s\r\nContent-Disposition: form-data; name=\"to\"\r\n\r\n" % _boundary)
        + "teacher@example.edu\r\n"
        + ("--%s\r\nContent-Disposition: form-data; name=\"body_text\"\r\n\r\n" % _boundary)
        + "正文：见附件。\r\n"
        + ("--%s\r\nContent-Disposition: form-data; name=\"attachments\"; filename=\"成绩单 2026.txt\"\r\n"
           "Content-Type: text/plain; charset=utf-8\r\n\r\n" % _boundary)
        + "HELLO-中文-payload\r\n"
        + ("--%s--\r\n" % _boundary)
    ).encode("utf-8")
    _f, _a = srv_mod.parse_multipart_mail(
        "multipart/form-data; boundary=" + _boundary, _raw)
    check("216. 解析器把文本字段与附件分清楚（字段 2 个、附件 1 个）",
          _f.get("to") == "teacher@example.edu"
          and _f.get("body_text") == "正文：见附件。"
          and len(_a) == 1,
          f"fields={_f} atts={[_x['filename'] for _x in _a]}")
    check("217. 附件名（含空格与中文）与内容都原样还原，逐字节相等",
          _a and _a[0]["filename"] == "成绩单 2026.txt"
          and _a[0]["data"] == "HELLO-中文-payload".encode("utf-8")
          and _a[0]["content_type"] == "text/plain",
          str(_a[0])[:160] if _a else "no attachment")
    _bad = None
    try:
        srv_mod.parse_multipart_mail("multipart/form-data; boundary=" + _boundary, b"")
    except ValueError as exc:
        _bad = str(exc)
    check("218. 空请求体 → ValueError（调用方回 400，而不是抛出去把连接弄断）",
          _bad is not None and "空" in _bad, str(_bad))

    # 变量遮蔽：`openCompose` 里的 `cc` 是 DOM 元素，写成 `(opts && cc)` 会把元素
    # 本身塞进输入框 → 发信时带上抄送 "[object HTMLInputElement]"，
    # 服务端只能回「抄送地址格式非法」。用户实测踩到过。
    check("219. 撰写窗预填用的是 `opts.cc`，不是那个 DOM 元素（防「抄送地址格式非法：[object HTMLInputElement]」）",
          "if (cc) cc.value = (opts && opts.cc) || '';" in js
          and "if (cc) cc.value = (opts && cc) || '';" not in js
          and "opts && opts.to" in js and "opts && opts.subject" in js
          and "opts && opts.body_text" in js and "opts && opts.in_reply_to" in js, "")
    check("220. 抄送在**本地**先校验（填错当场说清楚，不把请求打出去等服务器回 400）",
          "var badCc = ccAddrs.some(function (a) { return a.indexOf('@') === -1; });" in js
          and "'抄送地址格式不对（需要包含 @）：'" in js
          and "'抄送不能超过 20 个'" in js, "")

    # 真打一发 multipart：**必须回 JSON**（未登录 → 401），绝不能是连接被断
    mp_status, mp_body = post_multipart(
        "/app/mail/send/",
        {"to": "teacher@example.edu", "subject": "附件上传回归",
         "body_text": "正文：见附件。"},
        [("attachments", "成绩单.txt", "HELLO-中文".encode("utf-8"), "text/plain; charset=utf-8")])
    mp_text = mp_body.decode("utf-8", "replace")
    check("221. 未登录时 multipart 发信回 **401 JSON**（而不是断连接 / 500）",
          mp_status == 401 and '"code": "unauthorized"' in mp_text.replace("'", '"'),
          f"HTTP {mp_status} body={mp_text[:160]}")
    check("222. 服务器没有因为解析 multipart 而崩（响应体是合法 JSON，不是空 / HTML 错误页）",
          mp_text.strip().startswith("{") and "CONNECTION_DROPPED" not in mp_text,
          mp_text[:160])

    # ------------------------------------------------------------------
    # [20] 2026-09-17 用户报的四个问题（通知 / 通讯录 / 双滚动条 / 按钮对齐）
    # ------------------------------------------------------------------
    print("\n[20] 课程通知卡片 / 通讯录路由 / 邮箱双滚动条 / 回复转发对齐")

    # ① 通知卡片写「暂时没有待办。」的根因：coPlaceholder 的第二个参数被传成 '待办'，
    #    而它内部只有 `what === '通知'` 才去读 data.notifications，其它一律读
    #    data.messages（通知接口里没有这个字段）→ 明明有数据也画成空态。
    check("223. 通知卡片读的是 `data.notifications`（占位符参数必须是「通知」）",
          "var msg = coPlaceholder(data, '通知');" in js
          and "coPlaceholder(data, '待办'" not in js, "")
    check("224. coPlaceholder 对传错的 what **直接抛错**（渲染器会显示成可读提示，不再静默画空态）",
          "coPlaceholder 的 what 只认 通知/消息/讨论" in js
          and "if (what !== '通知' && what !== '消息' && what !== '讨论')" in js, "")
    check("225. 消息 / 讨论两块没被顺手改坏（仍传「消息」「讨论」）",
          "coPlaceholder(data, '消息')" in js and "coPlaceholder(data, '讨论')" in js, "")

    # ② 通讯录：`/app/mail/contacts/` 被 `/app/mail/<uid>/` 吞掉（uid 正则连 contacts
    #    这种固定段一起匹配）→ 真拿 "contacts" 去 IMAP 找邮件 → 404「找不到这封邮件」。
    srv_src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    _get_dispatch = srv_src.split("def do_GET", 1)[-1] if "def do_GET" in srv_src else srv_src
    _pos_mail = _get_dispatch.find("_APP_MAIL_PATH_RE.match")
    _pos_contacts = _get_dispatch.find("_APP_MAIL_CONTACTS_PATH_RE.match")
    check("226. 通讯录路由排在「读一封正文」**前面**（否则 contacts 会被当成 uid）",
          _pos_contacts != -1 and _pos_mail != -1 and _pos_contacts < _pos_mail,
          "contacts@%s mail@%s" % (_pos_contacts, _pos_mail))
    check("227. 保留段有独立判据（contacts/send/read 不被当成 uid）",
          "_APP_MAIL_RESERVED" in srv_src
          and 'frozenset({"contacts", "send", "read"})' in srv_src
          and "mail_match.group(1) not in _APP_MAIL_RESERVED" in srv_src, "")

    # ③ 邮箱右边的"两根滚动条 + 往下滑一片空白"：`#mail-heads` 里是几百封的列表，
    #    根滚动高度会被撑到五万多像素。修法是把两栏绝对定位在固定高度的格子里 +
    #    `contain: paint` 把溢出关在容器内（窄屏另给一条媒体查询）。
    check("228. 邮箱两栏绝对定位在固定高度的格子里 + contain:paint（根滚动不再被列表撑长）",
          "contain:paint" in css
          and ".mail-split>.card{position:absolute;top:0;bottom:0" in css
          and ".mail-split>.card:first-child{left:0;width:max(280px, calc((100% - 16px) * .382))}" in css
          # 高度来源：.main 是 flex 列（min-height:0），.view.active flex:1 拿剩余高度，
          # .mail-split 再 flex:1 —— 没有任何写死的 vh 魔法数字（旧写法是
          # calc((100vh - 220px)/zoom)，既有滚动条又会在窄屏错位）。
          and "display:flex;flex-direction:column;min-height:0}" in css
          and ".view.active{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;overflow:auto}" in css
          and ".mail-split{display:grid;grid-template-columns:minmax(280px,1fr) 1.618fr;gap:16px;position:relative;" in css
          and "flex:1 1 auto;min-height:0;overflow:hidden;contain:paint}" in css
          and "calc((100vh - 220px) / var(--ap-zoom,1))" not in css, "")
    # 用户 2026-09-21：「正文和邮箱列表在水平方向上的宽度不要一比一，正文占 61.8% 左右」。
    # 轨道用 1fr : 1.618fr（正文 = 1.618/2.618 = 61.8%），绝对定位的两块用同一口径的
    # calc((100% - 16px) * .382) —— 轨道与卡片必须同一个比例，否则视觉上还是不一致。
    check("228b. 两栏按黄金比分宽：正文 61.8%（轨道与绝对定位两块口径一致，不再一比一）",
          "minmax(280px,1fr) 1.618fr" in css
          and "calc((100% - 16px) * .382)" in css
          and ".mail-split>.card:last-child{left:calc(max(280px, calc((100% - 16px) * .382)) + 16px);right:0}" in css
          # 旧的写死 380px 必须已经不在（防止改回一比一时断言还绿着）
          and ".mail-split>.card:first-child{left:0;width:380px}" not in css
          and "grid-template-columns:380px 1fr" not in css, "")
    check("229. 窄屏回落到常规流（flex/绝对定位只给固定视口的宽屏用，且给列表限高）",
          ".mail-split{position:static;height:auto;overflow:visible;contain:none;flex:none;" in css
          and ".mail-items{max-height:60vh;overflow-y:auto}" in css
          and ".main{overflow:visible;padding:0 16px 40px}" in css, "")
    # 用户要求：「邮件和内容的卡片长一点，当前登录的那张卡藏在屏幕下面，往下滑才看到」。
    # 做法：给两栏一个 min-height（vw 系数 + 工具条高度的偏移），多出来的部分由
    # #view-mail **自己滚** —— 文档级滚动必须保持 0（上一轮刚修掉整页被撑长）。
    check("229b. 两栏给了 min-height 把它抬高，且滚动留给视图自己（文档级仍不滚）",
          "#mail-split{min-height:calc(85vh + 55px)}" in css
          and ".view.active{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;overflow:auto}" in css,
          "")

    # ④ 回复 / 转发对齐：两个按钮定高 + 消掉 `.mail-view .ghost{margin-top:10px}`
    #    对操作条里「转发」的顶推（实测差 10px，定高之后仍差 5px）。
    check("230. 操作条里两个按钮定高 32px、等高（`.primary` 8px vs `.ghost` 7px+1px 的差被消掉）",
          ".mail-actions button{display:inline-flex;align-items:center;justify-content:center;" in css
          and "height:32px;min-height:32px" in css
          and ".mail-actions .primary{padding:0 14px}" in css
          and ".mail-actions .ghost{padding:0 13px}" in css, "")
    check("231. `.mail-view .ghost` 的 10px 顶边距**排除了**操作条（:not(.mail-actions *)）",
          ".mail-view .ghost:not(.mail-actions *){margin-top:10px}" in css
          and "\n.mail-view .ghost{margin-top:10px}" not in css, "")

    # ⑤ 用户逐条点名要删的技术细节文案（两处占位都要干净）
    check("232. 邮箱页/撰写窗不再出现被点名的技术细节文案",
          all(s not in html for s in (
              "点课程 / 作业看详情", "只发送、不落盘", "与本机客户端一致",
              "列表是服务器用你同步过来的邮箱账号实时抓取的邮件头"))
          and "compose-att-hint" not in html and "compose-footnote" not in html, "")
    check("233. `closeMail()` 重建的占位也不再写那句技术说明（点开再关掉不会又冒出来）",
          "createTextNode('列表是服务器实时抓取的邮件头" not in js
          and "createTextNode('列表是服务器用你同步过来的邮箱账号实时抓取的邮件头" not in js
          and "function mailEmptyNotice()" in js
          and "var placeholder = mailEmptyNotice();" in js, "")
    check("234. 删掉说明不等于藏起来：附件限额改挂在摘要行",
          "' / 上限 ' + cap" in js and "'未选择附件（最多 '" in js, "")

    print("\n" + "=" * 72)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
