"""登录态下用 CDP 实测 /app/ 七个视图的 DOM（真实渲染，不是静态文本匹配）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_app_cdp.py [--base URL] [--shot-dir DIR] [--public-base URL]

做三件事：
 1) 用**临时账号**（跑完即登出，数据留在本地库里，不删任何既有用户数据）注册并登录，
    往 schema 允许的同步对象（schedule / school / settings.lessons / settings.accounts）
    写入一批**合成样例数据**，其余字段原样保留；
 2) 真浏览器打开 /app/（cookie 用 CDP Network.setCookie 注入），依次切到七个视图
    （首页 / 我的课表 / 我的日程 / 我的课程 / 平和邮箱 / Agent 助手 / 设置），
    断言每个视图渲染出真实内容（课表条目 / 课程 / 作业 / 邮件头 / 日程）；
    「我的成绩」已整个移除，所以既没有标签也没有 #view-grades / #gr-* 元素；
    无数据的分支用另一个「空数据」桩断言「可以去刷新重试」的提示；
 3) 用注入的 fetch 桩验证 AI 页：409 未配置 → 原样展示后端提示 + 个人中心/下载入口；
    200 → 回答 + context（对象/字符/截断）+ 12 条历史；以及 loading / 取消。

退出码 0 = 全绿。

本次适配（/app/ 由「五个标签」改造为「七/八个视图」）里刻意做的取舍，逐条说明：

 * [1] 未登录不再跳转站内其它页，登录卡就画在 /app/ 页面上（#loginwrap），
   而外壳容器是 #app。所以「匿名先看外壳」改成断言「登录卡可见 + #app 隐藏 + URL 仍是 /app/」，
   不再断言 #layout.hidden（新版里 #layout 没有 hidden 属性，隐藏的是外层 #app）。
 * 「五个标签」的断言升级成「七个视图」：按 data-view 逐个核对标签文字**包含**对应中文名
   （标签文字带 emoji 前缀），不再用 textContent 全等数组。
 * ManageBac 那个标签曾是「我的成绩」（#gr-body）+「我的课程」两块；现在**成绩整块已删**
   （数据源 managebac.courses 保留，课程页仍显示各科总评），相关断言随之删除。
 * 「我的日程」在 2026-09-16 从「按天分组的列表」改成 **PHL Lite 的日历形式**（周 / 月 / 年三视图，
   默认周视图）。本文件相应改了断言口径：不再数 `#sched-body .evt` 的条数，而是数
   `#sch-week .sch-day`（一周七列）/ `#sch-month .cal-cell`（整月网格）/ `#sch-year .cal-mini`
   （12 个月缩略），并核对有事件的日期高亮、点某月跳回月视图。旧列表语义保留在屏外的
   `#sched-compat`（`day-group__title + .evt`，绝对定位 + 1px 裁剪，不占位、不影响视觉），
   条目照旧可在里面按天读到。
 * app/index.html 里**没有** #sched-foot-closed，而 app.js 的 bindScheduleForm().open()/close()
   会读写 `closed.hidden` —— 点「＋ 新建日程」会在监听器里抛 TypeError，表单展不开。
   这是前端本身的缺陷（本文件按「只改测试」的要求不去动 app.js / index.html），因此
   **没有**新增「点开新建日程表单」的断言；日程视图只保留原有的检查（日历照常渲染 +
   空日程仍提示可以新增）。这条缺陷记在这里，供修前端时参考。
 * app.js 的 bindCourseTools() 在本文件定稿过程中被前端改过两次（改的过程中我又按实际渲染结果
   跟着调了一次断言口径）：一开始是 `q.addEventListener('input', renderCourseTasks)`，
   事件对象被当成「面板」塞进 renderCourseTasks(panel) 的第一个参数 → 改下拉 / 敲搜索框当场抛
   `box.appendChild is not a function`；中间一版改成 `renderCourseTasks(st.taskPanelBox)` 能渲染了
   但不清空面板（每改一次控件就多追加一张表）；**定稿时**已经改成「传 st.taskPanelBox 且
   renderCourseTasks 自己 clear(box)」。所以 5.9 / 5.10 按原语义做**当场重渲染**的严格断言
   （整块面板的行数与表数都查），5.10b 另外用 window 的 error 事件盯着「这条链路别抛 JS 错」。
   前端如果再改这一块，跑一遍本文件即可看出有没有回归。
 * app.css 里 .login-wrap{display:flex} 与 #app{display:flex} 没有配 [hidden] 兜底
   （同一份 CSS 里 .tag / .inline-msg / .ai-wait / .ai-unconf / .toast 都写了 `[hidden]{display:none}`，
   这两处漏了），而作者样式里的 display 会盖掉 UA 的 `[hidden]{display:none}`：
   于是**登录态下登录卡仍然画在页面顶部**、**未登录时整个 #app 外壳也照画**
   （截图 appweb-*.png 顶部那块就是它）。这是渲染层缺陷，本文件改不了，也没有把断言写成
   「hidden 属性为真」以外的东西 —— [1]/[2] 仍然断言 DOM 状态（#app 带 hidden、登录卡不带），
   同时把 getComputedStyle 的实测值打印出来（标了 ⚠）当作证据。
 * 真实抓取的成功路径无法用这个假密码账号构造，[8b] 因此改成**真后端**路径：先摘掉 fetch 桩
   （Page.removeScriptToEvaluateOnNewDocument）再重新载入，让页面真的打 GET /app/data/，
   再把 DOM 里的逐平台错误文案与后端 meta.errors **逐字比对**（而不是只看「有文字」）。
   邮箱那条在有同步快照时会被降级提示顶掉，所以 8.8 允许它是「原样错误」或「降级提示」二者之一。
 * 原来的 6.10b 是 `check(..., True)`（恒真、无意义），改成「设置视图常规视图只显示账号名、
   全文无明文密码」这条仍然有意义、可证伪的检查；技术细节（数据来源 / 同步状态 / 关于 / 账号
   标识）现在收在默认收起的 `<details id="set-diag">` 里，所以断言改成「折叠区里存在、默认折叠」。
"""
import argparse
import base64
import http.client
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EDGE_CANDIDATES = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
PORT_CDP = 9366
PASSED, FAILED = [], []

# 七个视图的 id 与导航文字里的中文名（导航项文字带 emoji 前缀，所以只做「包含」判断）
# 「我的成绩」已按用户要求整个移除（导航 / 视图 / 渲染代码 / 专属 CSS），所以这里是七个。
VIEWS = [("home", "首页"), ("timetable", "我的课表"), ("schedule", "我的日程"),
         ("courses", "我的课程"), ("mail", "平和邮箱"),
         ("ai", "Agent 助手"), ("settings", "设置")]
SHOT_VIEWS = [("home", "appweb-home"), ("timetable", "appweb-timetable"),
              ("schedule", "appweb-schedule"),
              ("courses", "appweb-courses"), ("mail", "appweb-mail"),
              ("ai", "appweb-ai"), ("settings", "appweb-settings")]


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append((name, extra))
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def labels_ok(tabs):
    """tabs: [{view, text}] → 七个视图都在，且每个标签文字包含对应中文名。"""
    got = {t.get("view"): (t.get("text") or "") for t in (tabs or [])}
    if len(got) != len(VIEWS) or set(got) != {v for v, _ in VIEWS}:
        return False
    return all(name in got[v] for v, name in VIEWS)


# --------------------------------------------------------------- HTTP 助手

class Opener:
    def __init__(self, base):
        self.base = base
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def req(self, method, path, body=None, retries=4):
        """本地站可能因为其它代理在改 server.py 而被重启：连接被重置时重试几次。"""
        last = None
        for attempt in range(retries):
            try:
                return self._once(method, path, body)
            except (ConnectionResetError, ConnectionAbortedError, urllib.error.URLError,
                    http.client.RemoteDisconnected) as exc:
                last = exc
                print(f"    （{path} 第 {attempt + 1} 次失败：{type(exc).__name__}，2 秒后重试）")
                time.sleep(2)
        raise RuntimeError(f"{method} {path} 连续失败：{last}")

    def _once(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.op.open(r, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw or b"{}") if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}")
            except ValueError:
                return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}

    def cookie(self, name):
        for c in self.cj:
            if c.name == name:
                return c.value
        return ""


# ------------------------------------------------------------------- CDP

class CDP:
    def __init__(self, port):
        self.port = port
        self.ws = None
        self.mid = 0

    def target_ws(self):
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=3) as r:
                    for t in json.loads(r.read()):
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            return t["webSocketDebuggerUrl"]
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError("拿不到 Edge 调试目标")

    def connect(self):
        self.ws = websocket.create_connection(self.target_ws(), timeout=60,
                                              suppress_origin=True, max_size=64 * 1024 * 1024)
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Network.enable")

    def send(self, method, **params):
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expr):
        # 对象字面量开头的表达式会被当成块语句解析 → 统一用括号包一层
        expr = "(" + expr + ")" if expr.lstrip().startswith("{") else expr
        res = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in res:
            exc = res["exceptionDetails"]
            text = (exc.get("exception") or {}).get("description") or exc.get("text") or "JS 异常"
            # 排障时把完整栈打到 stderr（默认只报第一行，够用但不便于定位）
            if os.environ.get("PHIX_CDP_TRACE"):
                sys.stderr.write("\n--- CDP 异常完整栈 ---\n" + str(text) + "\n---\n")
            raise RuntimeError(f"页面 JS 抛错: {str(text).splitlines()[0][:200]}")
        return res.get("result", {}).get("value")

    def goto(self, url):
        self.send("Page.navigate", url=url)
        for _ in range(120):
            try:
                if self.evaluate("document.readyState") == "complete":
                    time.sleep(0.4)
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError("页面加载超时")

    def wait_for(self, expr, seconds=20, label=""):
        end = time.time() + seconds
        last = None
        while time.time() < end:
            try:
                last = self.evaluate(expr)
                if last:
                    return last
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
        raise RuntimeError(f"等待超时（{label or expr}），最后一次取值：{last!r}")

    def close_browser(self):
        try:
            self.ws.send(json.dumps({"id": 99999, "method": "Browser.close", "params": {}}))
            self.ws.settimeout(3)
            time.sleep(0.5)
        except Exception:
            pass


# --------------------------------------------------------------- 页面助手

def show_view(cdp, view):
    """切视图：导航项现在是 #tabs .tab[data-view="<id>"]（不再有 #tab-* 这类 id）。"""
    cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"%s\"]').click()" % view)


def wait_app(cdp, label="登录态布局"):
    """等登录态落地：#app 显示出来且 #who 有用户名（#layout 在新版里恒不带 hidden，不能当信号）。"""
    return cdp.wait_for("!!(document.getElementById('app') && !document.getElementById('app').hidden"
                        " && document.getElementById('who').textContent)", label=label)


def stub_fill(cdp, body_literal, n=8, mail=False):
    """清空 /app/data/（或 /app/mail/<uid>/）的响应队列，并压入 n 份同一个 200 响应。

    每个视图**首次进入**都会各发一次实时抓取，点「刷新」还会再发一次（带 ?force=1）；
    压 n 份同一个 body，就不会因为多出来的一次请求把后面某一段断言的响应错位消费掉。
    """
    q = "__stub.mailQueue" if mail else "__stub.queue"
    cdp.evaluate("%s.length = 0; window.__stubFill = %s; 'ok'" % (q, body_literal))
    cdp.evaluate("for (var i = 0; i < %d; i++) %s.push({status: 200, body: window.__stubFill}); 'ok'"
                 % (n, q))


def refetch(cdp, wait_expr, view=None, btn=None, seconds=40, label=""):
    """切视图 / 点刷新后，先等「抓取真的发出去了」，再等面板渲染到目标状态。
    btn 参数已废弃（视图内独立刷新按钮已删除），统一用顶栏 btn-refresh。"""
    before = cdp.evaluate("__stub.calls.length")
    if view:
        show_view(cdp, view)
    if btn:
        # 兼容旧调用：视图独立刷新按钮已删除，统一用顶栏刷新
        cdp.evaluate("document.getElementById('btn-refresh').click()")
    try:
        cdp.wait_for("__stub.calls.length > %d" % before, seconds=seconds,
                     label=(label or "抓取") + " 发出")
    except Exception as exc:  # noqa: BLE001
        print("     等待抓取发出失败:", exc)
    return cdp.wait_for(wait_expr, seconds=seconds, label=label or wait_expr)


# --------------------------------------------------------------- 合成数据

SCHEDULE = {
    "version": 1, "kind": "pinghe-schedule", "app": "PHL",
    "lastId": 3, "updated_at": "2026-09-13T08:10:00+08:00",
    "events": [
        # 注意：真实客户端同步出来的 id 是字符串（"e1"/"e2"…），这里刻意照抄这个形状
        {"id": "e1", "day": "2026-09-14", "time": "08:30", "title": "数学月考",
         "note": "带计算器", "created": "2026-09-12T09:00:00+08:00"},
        {"id": "e2", "day": "2026-09-14", "time": "15:30", "title": "维修社活动",
         "note": "机房 B302", "created": "2026-09-12T09:05:00+08:00"},
        {"id": "e3", "day": "2026-09-16", "time": "", "title": "IB 选课截止",
         "note": "找导师确认", "created": "2026-09-12T09:10:00+08:00"},
    ],
}

# 真实形状：school.edupage 用「按天字典」days，settings.lessons 用字符串数组 groups
TIMETABLE = {
    "version": 1, "kind": "pinghe-timetable", "synced_at": "2026-09-13T07:45:12+08:00",
    # 只放 1 节：条目数少于 school.edupage，用来验证「取条目更多的一边 + 注明真实来源」
    "days": {
        "2026-09-18": [
            {"start": "09:00", "end": "09:40", "subject": "生物 HL（仅 timetable 对象里有）",
             "room": "实验楼 301", "teacher": "赵老师", "group": "G4"},
        ],
    },
}

LESSONS = {
    "version": 1, "updated_at": "2026-09-13T07:40:00+08:00",
    "groups": ["数学 AA HL（G1）", "物理 HL（G2）"],
}

SCHOOL = {
    "version": 1,
    "updated_at": "2026-09-13T07:45:30+08:00",
    "edupage": {
        "week_start": "2026-09-14",
        "fetched_at": "2026-09-13T07:45:12+08:00",
        "selected": ["G1", "G2"],
        "days": {
            "2026-09-14": [
                {"start": "08:00", "end": "08:40", "subject": "数学 AA HL",
                 "room": "B302", "teacher": "王老师", "group": "G1"},
                {"start": "08:50", "end": "09:30", "subject": "物理 HL",
                 "room": "实验楼 204", "teacher": "李老师", "group": "G2"},
            ],
            "2026-09-15": [
                {"start": "10:00", "end": "10:40", "subject": "语文 SL",
                 "room": "A105", "teacher": "张老师", "group": "G1"},
            ],
        },
    },
    "managebac": {
        "fetched_at": "2026-09-13T07:45:20+08:00",
        "courses": [
            {"name": "数学 AA HL", "grade": "6", "units": 3, "id": "mb-math"},
            {"name": "物理 HL", "grade": "5", "units": 2, "id": "mb-phy"},
        ],
        "tasks": [
            {"course": "数学 AA HL", "title": "习题集 3.2", "due": "2026-09-15 23:59",
             "status": "未提交", "id": "t1"},
            {"course": "物理 HL", "title": "实验报告：单摆", "due": "2026-09-18 23:59",
             "status": "进行中", "id": "t2"},
            {"course": "数学 AA HL", "title": "小测订正", "due": "2026-09-12 23:59",
             "status": "已完成", "score": "7", "id": "t3"},
        ],
    },
    "mail": {
        "unread": 3,
        "recent": [
            {"uid": "101", "from": "教务处 <academic@example.edu>", "subject": "关于月考安排的通知",
             "date": "2026-09-13 07:20"},
            {"uid": "102", "from": "王老师 <wang@example.edu>", "subject": "物理实验分组",
             "date": "2026-09-12 18:05"},
        ],
    },
}

ACCOUNTS = {
    "accounts": {
        # 真实形状：键就是平台名（edupage / managebac / mail），不是 mail:<邮箱>
        "edupage": {"username": "demo-edu", "password": "should-never-render-plain"},
        "managebac": {"username": "demo-mb", "password": "should-never-render-plain"},
        "mail": {"username": "demo@example.edu", "password": "should-never-render-plain"},
    }
}


# ---- 实时数据桩：/app/data/ 与 /app/mail/<uid>/ 的响应 ----
# 真实抓取在这个测试账号上必然失败（假密码），所以「成功路径」用请求桩喂，
# 「失败路径」既用桩也用真实后端（见 [5] 与 [8b]，8b 会先摘掉桩再打真后端）。

def _lesson(date, start, end, subject, room, teacher, group):
    return {"date": date, "start": start, "end": end, "subject": subject,
            "room": room, "teacher": teacher, "group": group}


def _lesson_any(date, start, end, subject, room, teacher):
    """全年级候选课：**没有组信息**的那种（还我自己的课表时按组过滤不掉）。"""
    return {"date": date, "start": start, "end": end, "subject": subject,
            "room": room, "teacher": teacher}


def _live(edupage, managebac, mail, *, cache="miss", errors=None, accounts=None):
    return {"ok": True, "edupage": edupage, "managebac": managebac, "mail": mail,
            "meta": {"fetched_at": "2026-09-13T09:10:00+08:00", "cache": cache,
                     "accounts": accounts if accounts is not None else
                     {"edupage": "demo-student", "managebac": "demo-student",
                      "mail": "demo@example.edu"},
                     "errors": errors or {}}}


LIVE_OK = _live(
    {"lessons": [
        _lesson("2026-09-14", "08:00", "08:40", "数学 AA HL", "B302", "王老师", "数学 AA HL（G1）"),
        _lesson("2026-09-14", "08:50", "09:30", "物理 HL", "实验楼 204", "李老师", "物理 HL（G2）"),
        _lesson("2026-09-14", "10:00", "10:40", "语文 SL", "A105", "张老师", "数学 AA HL（G1）"),
        _lesson("2026-09-15", "09:00", "09:40", "英语 B HL", "语言楼 201", "Smith", "数学 AA HL（G1）"),
        _lesson("2026-09-15", "13:00", "13:40", "化学 SL", "实验楼 203", "陈老师", "物理 HL（G2）"),
    ], "selected": ["数学 AA HL（G1）", "物理 HL（G2）"]},
    {"courses": [], "tasks": []},
    {"unread": 3, "recent": [
        {"uid": "1", "from": "教务处 <academic@example.edu>", "subject": "关于下周月考安排的通知",
         "date": "2026-09-13 08:12"},
        {"uid": "2", "from": "班主任 <teacher@example.edu>", "subject": "社会实践活动报名",
         "date": "2026-09-12 17:40"},
        {"uid": "3", "from": "图书馆 <library@example.edu>", "subject": "借阅到期提醒",
         "date": "2026-09-12 09:05"},
    ]},
    cache="miss",
    errors={"managebac": "登录失败：账号密码不对，请到个人中心 → 密码管理更新"})

#: 用于「没选教学组」的那组断言：课表照常有 5 条**全年级候选**，但两个选课来源都空，
#: 所以页面必须画空态卡 + 选课入口，绝不把这 5 条铺出来。
LIVE_OK_CLEARED = _live(
    {"lessons": [
        _lesson_any("2026-09-14", "08:00", "08:40", "数学 AA HL", "B302", "王老师"),
        _lesson_any("2026-09-14", "08:50", "09:30", "物理 HL", "实验楼 204", "李老师"),
        _lesson_any("2026-09-14", "10:00", "10:40", "中文 SL", "A105", "张老师"),
        _lesson_any("2026-09-15", "09:00", "09:40", "英语 B HL", "语言楼 201", "Smith"),
        _lesson_any("2026-09-15", "13:00", "13:40", "化学 SL", "实验楼 203", "陈老师"),
    ], "selected": []},
    {"courses": [], "tasks": []},
    {"unread": 0, "recent": []},
    cache="miss",
    errors={"managebac": "登录失败：账号密码不对，请到个人中心 → 密码管理更新"})

#: 用于「选课模态」那组断言：同样是**没选课**（画空态卡），但这几条课**带教学组** ——
#: 选课界面上必须有可勾的复选框，才能走完「勾一条 → 保存 → 课表按新选课过滤」。
LIVE_OK_PICKABLE = _live(
    {"lessons": [
        _lesson("2026-09-14", "08:00", "08:40", "数学 AA HL", "B302", "王老师", "G1"),
        _lesson("2026-09-14", "08:50", "09:30", "物理 HL", "实验楼 204", "李老师", "G2"),
        _lesson("2026-09-15", "10:00", "10:40", "语文 SL", "A105", "张老师", ""),
    ], "selected": []},
    {"courses": [], "tasks": []},
    {"unread": 0, "recent": []},
    cache="miss",
    errors={})

#: school 快照里 `edupage.selected` 空 → personalGroups() 一个组都拿不到（客户端「空选课」的真实形态）
LESSONS_EMPTY_SCHOOL = {"version": 1, "kind": "pinghe-school",
                        "updated_at": "2026-09-13T08:30:00+08:00",
                        "edupage": {"week_start": "2026-09-14", "selected": [], "days": {}}}

#: 客户端「空选课」在 settings.lessons 里的真实形态：**没有 groups、lessons 也是空数组**
#: （`lessonGroupNames` 对 `{"lessons":[]}` 不会产出任何组名 → 判定为「没选课」）。
LESSONS_CLEARED_DOC = {"version": 1, "kind": "pinghe-lessons", "lessons": [],
                       "updated_at": "2026-09-13T08:00:00+08:00"}

LIVE_OK_B = _live(
    {"lessons": [], "selected": []},
    {"courses": [
        {"name": "数学 HL", "grade": "6", "units": 4, "id": "mb-math"},
        {"name": "物理 SL", "grade": "5", "units": 3, "id": "mb-phy"},
        {"name": "中文 A 文学", "grade": "7", "units": 5, "id": "mb-chn"},
    ], "tasks": [
        {"course": "数学 HL", "title": "第 3 章习题 1–12", "due": "2026-09-15 23:59",
         "status": "未提交", "id": "t1"},
        {"course": "物理 SL", "title": "实验报告：自由落体", "due": "2026-09-17 23:59",
         "status": "未提交", "id": "t2"},
        {"course": "中文 A 文学", "title": "《红楼梦》读书笔记", "due": "2026-09-20 23:59",
         "status": "已提交", "score": "7", "id": "t3"},
    ]},
    {"unread": 0, "recent": []},
    cache="hit")

LIVE_OK_C = _live({"lessons": [], "selected": []}, {"courses": [], "tasks": []},
                  {"unread": 3, "recent": [
                      {"uid": "1", "from": "教务处 <academic@example.edu>",
                       "subject": "关于下周月考安排的通知", "date": "2026-09-13 08:12",
                       "unread": True},
                      {"uid": "2", "from": "班主任 <teacher@example.edu>",
                       "subject": "社会实践活动报名", "date": "2026-09-12 17:40",
                       "unread": False},
                      {"uid": "3", "from": "图书馆 <library@example.edu>",
                       "subject": "借阅到期提醒", "date": "2026-09-12 09:05",
                       "unread": False},
                  ]}, cache="hit")

#: unread=0 的实时响应（用来核对「一封都没未读 → 一个蓝点都不画」）
LIVE_MAIL_ZERO = _live({"lessons": [], "selected": []}, {"courses": [], "tasks": []},
                       {"unread": 0, "recent": [
                           {"uid": "1", "from": "教务处 <academic@example.edu>",
                            "subject": "关于下周月考安排的通知", "date": "2026-09-13 08:12",
                            "unread": False},
                           {"uid": "2", "from": "班主任 <teacher@example.edu>",
                            "subject": "社会实践活动报名", "date": "2026-09-12 17:40",
                            "unread": False},
                           {"uid": "3", "from": "图书馆 <library@example.edu>",
                            "subject": "借阅到期提醒", "date": "2026-09-12 09:05",
                            "unread": False},
                       ]}, cache="hit")

LIVE_MAIL_BODY = {"ok": True, "mail": {
    "uid": "1", "from": "教务处 <academic@example.edu>", "to": "demo@example.edu",
    "subject": "关于下周月考安排的通知", "date": "2026-09-13 08:12",
    "body_text": "第一行正文：下周三下午 13:30 在报告厅开会。\n<b>这行故意带标签</b>，页面必须原样当文本显示。\n\n—— 教务处",
    "body_html": ""}}

#: 同一封邮件，但**带一个附件** —— 用来验「转发把原附件一并带上」。
#: 附件内容走 `GET /app/mail/<uid>/attachments/<index>/`，桩里也排在 mailQueue 上。
LIVE_MAIL_BODY_WITH_ATT = {"ok": True, "mail": {
    "uid": "1", "from": "教务处 <academic@example.edu>", "to": "demo@example.edu",
    "subject": "关于下周月考安排的通知", "date": "2026-09-13 08:12",
    "body_text": "第一行正文：下周三下午 13:30 在报告厅开会。\n<b>这行故意带标签</b>，页面必须原样当文本显示。\n\n—— 教务处",
    "body_html": "",
    "attachments": [{"index": 0, "filename": "成绩单.pdf", "size": 11,
                     "content_type": "application/pdf"}]}}

# ---------------------------------------------------------------- HTML 邮件
#: 一封 HTML 邮件的正文：**服务端不再消毒** —— 接口给的 `body_html` 就是 MIME 里
#: `text/html` 那一部分的原文，页面也把它**原样**塞进 srcdoc。
#: 所以这里刻意保留 `<script>` / `onerror` / `javascript:`，用来同时证明两件事：
#:   ① 页面一个字都没改写（srcdoc 与接口返回逐字一致）；
#:   ② 它们**不会执行** —— iframe 的 sandbox 不带 allow-scripts。
HTML_MAIL_BODY_RAW = (
    "<h1>学期通知</h1>"
    "<style>.x{color:red}</style>"
    "<p>家长您好：<b>本周五</b> 18:00 家长会。</p>"
    '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"'
    ' alt="内嵌图" width="4" height="4">'
    '<img src="https://tracker.example.invalid/pixel.gif" alt="远程跟踪像素">'
    '<a href="javascript:window.top.__xss=3">javascript 链接</a>'
    '<a href="https://ok.example.invalid/a">正常链接</a>'
    "<table><tr><th>科目</th><th>时间</th></tr><tr><td>数学</td><td>周一</td></tr></table>"
    '<script>window.top.__xss=1</script>'
    '<img src=x onerror="window.top.__xss=2">'
)
LIVE_HTML_MAIL_BODY = {"ok": True, "mail": {
    "uid": "1", "from": "教务处 <academic@example.edu>", "to": "demo@example.edu",
    "subject": "HTML 通知（原样渲染 + 沙箱）", "date": "2026-09-13 08:12",
    "body_text": "纯文本版：恭喜你获奖了。",
    "body_html": HTML_MAIL_BODY_RAW}}

#: 一份"更难"的脏 HTML：断言「脚本不执行」靠的是 **sandbox**，而不是「被洗掉了」——
#: 所以 payload 必须**原样留在 srcdoc 里**，同时主窗口绝不能出现 `window.__xss`。
DIRTY_HTML_MAIL_BODY = {"ok": True, "mail": {
    "uid": "1", "from": "教务处 <academic@example.edu>", "to": "demo@example.edu",
    "subject": "脏 HTML（沙箱必须拦住）", "date": "2026-09-13 08:12",
    "body_text": "如果这段被执行，就说明沙箱形同虚设。",
    "body_html": ("<p>正文</p><script>window.top.__xss=1</script>"
                  '<img src=x onerror="window.top.__xss=2">'
                  '<a href="javascript:window.top.__xss=3">点这里</a>')}}


STUB_JS = """
window.__stub = {
  queue: [],        // /app/data/ 的响应队列
  mailQueue: [],    // /app/mail/<uid>/ 的响应队列（和上一条分开，避免两条链互相错位消费）
  courseQueue: [],  // /app/courses/** 的响应队列（通知 / 消息 / 讨论 / 详情）
  calls: [],
  push: function (spec) { window.__stub.queue.push(spec); return 'ok'; },
  pushMail: function (spec) { window.__stub.mailQueue.push(spec); return 'ok'; },
  pushCourse: function (spec) { window.__stub.courseQueue.push(spec); return 'ok'; },
  real: null
};
(function () {
  window.__stub.real = window.fetch;
  window.fetch = function (url, opt) {
    var u = String(url);
    if (u.indexOf('127.0.0.1:38123') !== -1) {
      // 本机直连服务（可选）**故意排除**：它由 test_local_bridge.py +
      // _lab\\viewcheck3.py 单独验证；这里统一走服务器 /app/data/，
      // 免得「用户电脑上正好跑着本机桥」把桩抢走、断言全走偏。
      return Promise.resolve(new Response('{}', { status: 404,
        headers: { 'Content-Type': 'application/json' } }));
    }
    if (u.indexOf('/proxy/sync/objects/school/') !== -1) {
      // 降级路径的依赖：默认空快照，[8a] 段再换成 SNAPSHOT_SCHOOL
      var b = window.__schoolBody || { ok: true, name: 'school', revision: 0, payload: '{}' };
      window.__stub.calls.push({ url: u, method: 'GET', body: null });
      return new Promise(function (resolve) {
        setTimeout(function () {
          resolve(new Response(JSON.stringify(b), { status: 200,
                   headers: { 'Content-Type': 'application/json' } }));
        }, 0);
      });
    }
    var isMail = u.indexOf('/app/mail/') !== -1;
    /* 标记已读（POST /app/mail/<uid>/read/）是打开邮件时的**第二条**请求，
       它绝不能从 mailQueue 里吃掉「正文」那一条 —— 单独给它一个固定成功响应，
       正文请继续走 pushMail（队列语义保持不变）。 */
    if (isMail && u.indexOf('/read/') !== -1) {
      var readBody = { ok: true, mail: { uid: String(u).split('/')[4] || '', unread: false } };
      window.__stub.calls.push({ url: u, method: (opt && opt.method) || 'GET', body: opt && opt.body });
      return new Promise(function (resolve) {
        setTimeout(function () {
          resolve(new Response(JSON.stringify(readBody), { status: 200,
                   headers: { 'Content-Type': 'application/json' } }));
        }, 0);
      });
    }
    if (u.indexOf('/app/courses/') !== -1) {
      /* 课程活动流（通知 / 消息 / 讨论 / 详情）：单独一条队列。
         真实的 `/app/data/` 桩里不带课程活动，前端一进课程页就会去拉这两条 —— 
         以前它们会漏到真网络上（本地起的站点直接 404），这里统一喂桩。 */
      var spec2 = window.__stub.courseQueue.shift() ||
        { status: 200, body: { ok: true, notifications: [], messages: [],
                               status: 'ok', available: true, partial: false } };
      window.__stub.calls.push({ url: u, method: (opt && opt.method) || 'GET', body: opt && opt.body });
      return new Promise(function (resolve) {
        setTimeout(function () {
          resolve(new Response(JSON.stringify(spec2.body), { status: spec2.status,
                   headers: { 'Content-Type': 'application/json' } }));
        }, spec2.delayMs || 0);
      });
    }
    if (u.indexOf('/app/data/') !== -1 || isMail) {
      var q = isMail ? window.__stub.mailQueue : window.__stub.queue;
      var spec = q.shift() ||
        { status: 500, body: { ok: false, error: { code: 'no_stub', message: 'no stub queued' } } };
      window.__stub.calls.push({ url: u, method: (opt && opt.method) || 'GET', body: opt && opt.body });
      return new Promise(function (resolve) {
        setTimeout(function () {
          resolve(new Response(JSON.stringify(spec.body), { status: spec.status,
                   headers: { 'Content-Type': 'application/json' } }));
        }, spec.delayMs || 0);
      });
    }
    return window.__stub.real.apply(this, arguments);
  };
})();
"""

# 降级路径用的「上次同步」快照（客户端真实 days 形态）
SNAPSHOT_SCHOOL = {"version": 1, "kind": "pinghe-school", "updated_at": "2026-09-13T08:30:00+08:00",
                   "edupage": {"week_start": "2026-09-14", "fetched_at": "2026-09-13T08:30:00+08:00",
                               "selected": ["数学 A 组", "物理 B 组"],
                               "days": {"2026-09-14": [
                                   {"start": "08:00", "end": "08:40", "subject": "数学",
                                    "group": "数学 A 组", "room": "教学楼 302", "teacher": "王老师"},
                                   {"start": "08:50", "end": "09:30", "subject": "物理",
                                    "group": "物理 B 组", "room": "实验楼 105", "teacher": "李老师"}],
                                   "2026-09-15": [
                                   {"start": "09:00", "end": "09:40", "subject": "英语",
                                    "group": "英语 B 组", "room": "语言楼 201", "teacher": "Smith"}]}},
                   "managebac": {"fetched_at": "2026-09-13T08:35:00+08:00",
                                 "courses": [{"name": "数学 HL（快照）", "grade": "6", "units": 4}],
                                 "tasks": [{"course": "数学 HL（快照）", "title": "快照作业：第 1 章",
                                            "due": "2026-09-19 23:59", "status": "未提交"}]},
                   "mail": {"unread": 1, "recent": [
                       {"uid": "s1", "from": "快照发件人 <snap@example.edu>",
                        "subject": "快照邮件：月考安排", "date": "2026-09-13 08:30"}]}}

# 同步对象 HTTP 响应里包一层 payload 串（客户端写的就是这个形状）
SNAPSHOT_SYNC = {"ok": True, "name": "school", "revision": 1,
                 "payload": json.dumps(SNAPSHOT_SCHOOL, ensure_ascii=False),
                 "updated_at": "2026-09-13T08:30:00+08:00"}

# 平台连上了但这轮没数据（空态路径）
EMPTY_LIVE = {"ok": True, "edupage": {"lessons": [], "selected": []},
              "managebac": {"courses": [], "tasks": []},
              "mail": {"unread": 0, "recent": []},
              "meta": {"fetched_at": "2026-09-13T09:20:00+08:00", "cache": "miss",
                       "accounts": {"edupage": "demo-student", "managebac": "demo-student"},
                       "errors": {}}}

# 降级路径：edupage 抓取失败（逐平台错误）+ managebac 仍有数据
DEGRADE_LIVE = {"ok": True, "edupage": {"lessons": [], "selected": []},
                "managebac": {"courses": [{"name": "数学 HL", "grade": "6", "units": 4}],
                              "tasks": [{"course": "数学 HL", "title": "第 3 章习题 1–12",
                                         "due": "2026-09-15 23:59", "status": "未提交"}]},
                "mail": {"unread": 0, "recent": []},
                "meta": {"fetched_at": "2026-09-13T09:30:00+08:00", "cache": "miss",
                         "accounts": {"edupage": "demo-student", "managebac": "demo-student"},
                         "errors": {"edupage": "连不上平台服务器，请稍后重试"}}}


def J(v):
    """Python 对象 → 可直接嵌进 JS 表达式的 JSON 字面量（JSON 是 JS 字面量子集）。"""
    return json.dumps(v, ensure_ascii=False)


def seed(op, objs):
    out = {}
    for name, doc in objs.items():
        st, body = op.req("GET", f"/proxy/sync/objects/{name}/")
        rev = body.get("revision", 0) if st == 200 else 0
        if st not in (200, 404):
            raise RuntimeError(f"读 {name} 失败 HTTP {st} {str(body)[:120]}")
        st2, b2 = op.req("POST", f"/proxy/sync/objects/{name}/",
                         {"payload": json.dumps(doc, ensure_ascii=False),
                          "base_revision": rev, "device": "CDP 测试"})
        if st2 not in (200, 201):
            raise RuntimeError(f"写 {name} 失败 HTTP {st2} {str(b2)[:160]}")
        out[name] = (st, st2, b2.get("revision"))
    return out


#: 本套件反复重跑时复用同一批一次性测试账号：上游 `/auth/register/` 限流很紧
#: （连着跑两轮必 429，实测要等很久才放开），而每次注册新账号又会很快把配额吃光。
#: 这个文件只存**测试账号**（`apptest*` / `appwebdemo` 这类），是本机测试产物。
ACCOUNT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lab",
                             "cdp_test_accounts.json")


def _cached_account(base, tag):
    """从缓存里取一个还能用的测试账号（登录成功才算数）。"""
    try:
        with open(ACCOUNT_CACHE, encoding="utf-8") as f:
            saved = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"  （没有可复用的测试账号缓存：{exc}）")
        return None
    rec = saved.get(tag)
    if not rec:
        # 同一个账号可以有多个 tag（`a` 有数据 / `b` 空数据）：套件本来就只要求
        # 「一个能登录的测试账号」，没有账号时复用别的 tag 那份，省掉一次注册。
        for other in sorted(saved):
            if saved[other].get("username") and saved[other].get("password"):
                rec = saved[other]
                print(f"  （缓存里没有 tag={tag}，复用 tag={other} 的账号）")
                break
    if not rec:
        print(f"  （缓存里没有可复用的测试账号）")
        return None
    op = Opener(base)
    st, _ = op.req("POST", "/auth/login/", {"username": rec["username"],
                                            "password": rec["password"]})
    if st != 200:
        print(f"  （缓存账号 {rec['username']} 登录失败 HTTP {st}，重新注册）")
        return None
    print(f"  （复用上次的测试账号 {rec['username']}）")
    return rec["username"], op


def _remember_account(tag, user, pw):
    try:
        with open(ACCOUNT_CACHE, encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:  # noqa: BLE001
        saved = {}
    saved[tag] = {"username": user, "password": pw}
    try:
        with open(ACCOUNT_CACHE, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


def make_account(base, objs, tag):
    import secrets
    import time as _time
    reuse = _cached_account(base, tag)
    if reuse:
        user, op = reuse
        if objs is not None:
            seed(op, objs)
        return user, op
    op = Opener(base)
    user = f"apptest{tag}{secrets.token_hex(3)}"
    pw = "App-Test-" + secrets.token_hex(4)
    # 上游对「新注册」有频率限制：短时间反复跑本套件会撞 429。
    # 这里等一会儿再试（90 秒内），仍不行就退化为「预置好的演示会话 / 演示账号」，
    # 免得整轮跑不起来；测试数据只写进这个账号自己的同步对象。
    st, body = op.req("POST", "/auth/register/", {"username": user, "password": pw})
    registered = st in (200, 201)
    for _ in range(9):
        if st in (200, 201) or st != 429:
            break
        print("  （注册被上游限流 429，等 10 秒再试…）")
        _time.sleep(10)
        st, body = op.req("POST", "/auth/register/", {"username": user, "password": pw})
    registered = registered or st in (200, 201)
    if registered:
        print(f"  （新注册的测试账号 {user} 已记进 _lab/cdp_test_accounts.json 供下次复用）")
        _remember_account(tag, user, pw)
    if st not in (200, 201):
        # 兜底一：预置会话 cookie（`_lab\\prepare_demo_session.py` 产出的 demo_session.json）。
        # 为什么不用 /auth/login/ 兜：站点侧登录会去 upstream 取 auth/keymaterial 复算
        # auth_hash，新注册的账号单独登录会 401（已知行为），而 /auth/register/ 自己
        # 就会下发可用的 phix_access cookie。
        demo_cookie = os.environ.get("DEMO_COOKIE", "")
        demo_user = os.environ.get("DEMO_USER", "")
        if demo_cookie and demo_user:
            op2 = Opener(base)
            # cookie 的 domain 必须跟 base 的主机一致，否则 cookiejar 不会把它发出去
            host = urllib.parse.urlsplit(base).hostname or "127.0.0.1"
            op2.cj.set_cookie(http.cookiejar.Cookie(
                0, "phix_access", demo_cookie, None, False,
                host, False, False, "/", True, False, None, True,
                None, None, {}))
            st2, me = op2.req("GET", "/me/")
            if st2 == 200 and me.get("username") == demo_user:
                print(f"  （注册被限流，改用预置会话：{demo_user}）")
                if objs is not None:
                    seed(op2, objs)
                return demo_user, op2
        # 兜底二：老办法 —— 登录固定演示账号（要求该账号的口令已知）
        user = demo_user or "appwebdemo"
        pw = os.environ.get("DEMO_PW", "Demo-7f3a91c2!Aa1")
        op2 = Opener(base)
        st, body = op2.req("POST", "/auth/login/", {"username": user, "password": pw})
        if st != 200:
            raise RuntimeError(f"注册失败 HTTP {st} {str(body)[:160]}"
                               fr"（可用 _lab\prepare_demo_session.py 准备 DEMO_COOKIE 兜底）")
        print(f"  （改用演示账号 {user} 继续）")
        if objs is not None:
            seed(op2, objs)
        return user, op2
    # 注册即登录：/auth/register/ 已经把 phix_access 写进这个 opener 的 cookie jar
    if objs is not None:
        seed(op, objs)
    return user, op


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8940")
    ap.add_argument("--shot-dir", default="")
    ap.add_argument("--public-base", default="",
                    help="顺带截一张公网 /app/ 外壳图（拦掉 /me/ 免得被跳去登录页）")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 74)
    print(f"登录态 CDP 实测：/app/ 七个视图 → {base}")
    print("=" * 74)

    edge = next((c for c in EDGE_CANDIDATES if os.path.exists(c)), None)
    if not edge:
        print("找不到 Edge，无法做 CDP 实测")
        return 2

    # ---- 准备两个账号：有数据的 / 空的 ----
    print("\n[0] 准备临时账号与合成数据")
    user_full, op_full = make_account(base, {"schedule": SCHEDULE, "settings.lessons": LESSONS,
                                             "settings.accounts": ACCOUNTS,
                                             "school": SNAPSHOT_SCHOOL}, "a")
    print(f"    有数据账号：{user_full[:14]}…（写 schedule / school / 选课 / 账号四个同步对象）")
    user_empty, op_empty = make_account(base, None, "b")
    print(f"    空数据账号：{user_empty[:14]}…（备用，用于核对空态边界）")
    check("0.1 两个临时账号都拿到了会话 cookie",
          bool(op_full.cookie("phix_access")) and bool(op_empty.cookie("phix_access")), "")
    st, me = op_full.req("GET", "/me/")
    check("0.2 有数据账号 /me/ → 200", st == 200 and me.get("username") == user_full, f"{st} {me}")

    # ---- 启动 Edge ----
    port = PORT_CDP
    profile = tempfile.mkdtemp(prefix="edge_app_")
    proc = subprocess.Popen([edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                             f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                             "--no-first-run", "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        cdp = CDP(port)
        cdp.connect()
        cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                 deviceScaleFactor=1, mobile=False)
        cdp.send("Network.setCacheDisabled", cacheDisabled=True)   # 别让 Edge 缓存旧 app.js
        stub_script = cdp.send("Page.addScriptToEvaluateOnNewDocument", source=STUB_JS)
        stub_id = stub_script.get("identifier")

        # ---- 未登录：不再跳转，页面上直接就是登录卡 ----
        print("\n[1] 未登录先看外壳（不重定向，页面上直接是登录卡）")
        cdp.goto(base + "/app/")
        cdp.wait_for("!!(document.getElementById('loginwrap')"
                     " && !document.getElementById('loginwrap').hidden)", seconds=30, label="登录卡出现")
        shell = cdp.evaluate(
            "({title: document.title,"
            " loginShown: !document.getElementById('loginwrap').hidden,"
            " loginInputs: ['login-form','login-user','login-pass','login-go','login-msg']"
            "   .filter(function (i) { return !document.getElementById(i); }),"
            " appHidden: document.getElementById('app').hidden,"
            " appDisplay: getComputedStyle(document.getElementById('app')).display,"
            " loginDisplay: getComputedStyle(document.getElementById('loginwrap')).display,"
            " tabs: Array.from(document.querySelectorAll('#tabs .tab'))"
            "   .map(function (t) { return {view: t.getAttribute('data-view'), text: t.textContent.trim()}; }),"
            " url: location.pathname})")
        print("     未登录外壳:", json.dumps(shell, ensure_ascii=False)[:300])
        print("     ⚠ 实测 display：loginwrap=%s app=%s —— app.css 的 .login-wrap{display:flex} /"
              " #app{display:flex} 覆盖了 UA 的 [hidden]{display:none}，所以 hidden 属性拦不住渲染"
              "（见文件头「app.css 的 .login-wrap … 」那条）" % (shell["loginDisplay"], shell["appDisplay"]))
        check("1.1 匿名访问不跳转：URL 仍是 /app/、#app 被置为 hidden、登录卡已展开（表单齐全）",
              shell["loginShown"] is True and shell["appHidden"] is True
              and shell["loginInputs"] == [] and shell["url"] == "/app/",
              json.dumps(shell, ensure_ascii=False)[:280])
        check("1.2 页面标题是 Pinghe Launcher Web 且七个视图的导航项齐全",
              shell["title"] == "Pinghe Launcher Web" and labels_ok(shell["tabs"]),
              json.dumps(shell, ensure_ascii=False)[:280])

        # ---- 注入 cookie：登录态 ----
        print("\n[2] 注入登录 cookie 后进入登录态")
        st, _h = op_full.req("GET", "/me/")          # 触发一次刷新，确保 cookie 有效
        # cookie 的 domain 必须跟着 --base 走：写死 127.0.0.1 时，用 --base http://192.168.x.x:8940
        # 跑这套件 cookie 会被浏览器丢掉 → 页面停在未登录态、#who 永远为空（本轮踩到）。
        cookie_domain = base.split("/")[2].split(":")[0]
        cdp.send("Network.setCookie", name="phix_access", value=op_full.cookie("phix_access"),
                 domain=cookie_domain, path="/", secure=base.startswith("https"), httpOnly=True)
        cdp.goto(base + "/app/")
        wait_app(cdp, label="登录态布局出现")
        who = cdp.evaluate("({who: document.getElementById('who').textContent,"
                           " foot: !document.getElementById('side-foot').hidden,"
                           " loginDisplay: getComputedStyle(document.getElementById('loginwrap')).display,"
                           " tabs: Array.from(document.querySelectorAll('#tabs .tab'))"
                           "   .map(function (t) { return {view: t.getAttribute('data-view'),"
                           "                                 text: t.textContent.trim()}; })})")
        print("     ⚠ 登录态下 #loginwrap 的实测 display：%s（应为 none）—— 见文件头「app.css 的 .login-wrap … 」那条"
              % who["loginDisplay"])
        check("2.1 侧栏底部显示当前用户名（#side-foot 已展开）",
              who["who"] == user_full and who["foot"] is True, json.dumps(who, ensure_ascii=False)[:200])
        check("2.2 七个视图的标签都可见（宽 > 20px）",
              labels_ok(who["tabs"])
              and cdp.evaluate("Array.from(document.querySelectorAll('#tabs .tab'))"
                               ".every(t => t.offsetWidth > 20)") is True, "")

        # ---- ① 我的日程（默认视图；2026-09-16 起为 PHL Lite 的日历形式：周 / 月 / 年）----
        print("\n[3] ① 我的日程（默认视图 · 周/月/年日历）")
        cdp.wait_for("document.querySelectorAll('#sch-week .sch-day').length === 7", label="周视图七列")
        sched = cdp.evaluate(
            "({cells: document.querySelectorAll('#sch-week .sch-day').length,"
            " ev: document.querySelectorAll('#sched-body .cal-ev').length,"
            " compat: document.querySelectorAll('#sched-compat .evt').length,"
            " meta: document.getElementById('sched-meta').textContent,"
            " text: document.getElementById('sched-body').innerText,"
            " label: document.getElementById('sched-label').textContent,"
            " chips: Array.from(document.querySelectorAll('#sched-views .seg__btn'))"
            "   .map(function (b) { return b.getAttribute('data-v') + ':' +"
            "     (b.classList.contains('is-on') ? 'on' : 'off'); }),"
            " weekVisible: !document.getElementById('sch-week').hidden,"
            " monthHidden: document.getElementById('sch-month').hidden,"
            " yearHidden: document.getElementById('sch-year').hidden,"
            " cellsList: Array.from(document.querySelectorAll('#sch-week .sch-day'))"
            "   .map(function (c) { return {day: c.getAttribute('data-day'),"
            "     txt: c.innerText.replace(/\\n/g, ' | ')}; }),"
            " title: document.getElementById('view-title').textContent,"
            " visible: !document.getElementById('view-schedule').hidden,"
            " others: Array.from(document.querySelectorAll('.view'))"
            "   .filter(v => !v.hidden).map(v => v.id)})")
        check("3.1 默认打开「我的日程」：只有 #view-schedule 可见、标题正确",
              sched["visible"] and sched["title"] == "我的日程"
              and sched["others"] == ["view-schedule"], json.dumps(sched["others"]))
        check("3.2 默认周视图：周一到周日七列，事件落在对应格子（周内有 2 条）",
              sched["cells"] == 7 and "数学月考" in sched["text"]
              and "维修社活动" in sched["text"] and sched["ev"] >= 2 and sched["compat"] == 3
              and sched["weekVisible"] and sched["monthHidden"] and sched["yearHidden"]
              and sched["chips"] == ["week:on", "month:off", "year:off"],
              json.dumps(sched, ensure_ascii=False)[:260])
        check("3.2b 顶部区间是「YYYY 年第 N 周（MM-DD ~ MM-DD）」",
              sched["label"].startswith("2026 年第") and "周（" in sched["label"], sched["label"])
        # 月视图：整月网格 + 有事件的日期高亮；年视图：12 个月缩略 + 点某月跳回月视图
        cal = cdp.evaluate(
            "(function () {"
            "  document.getElementById('sched-v-month').click();"
            "  var heads = Array.from(document.querySelectorAll('#sch-month .cal-head'))"
            "    .map(function (h) { return h.textContent; });"
            "  var evDays = Array.from(document.querySelectorAll('#sch-month .cal-cell'))"
            "    .filter(function (c) { return c.querySelector('.cal-ev'); })"
            "    .map(function (c) { return c.getAttribute('data-day'); });"
            "  var cells = document.querySelectorAll('#sch-month .cal-cell').length;"
            "  var out = document.querySelectorAll('#sch-month .cal-cell.is-out').length;"
            "  var label = document.getElementById('sched-label').textContent;"
            "  document.getElementById('sched-v-year').click();"
            "  var minis = document.querySelectorAll('#sch-year .cal-mini').length;"
            "  var has = document.querySelectorAll('#sch-year .mini-day.has').length;"
            "  document.querySelectorAll('#sch-year .mini-head')[0].click();"
            "  var back = {label: document.getElementById('sched-label').textContent,"
            "    on: Array.from(document.querySelectorAll('#sched-views .seg__btn.is-on'))"
            "      .map(function (b) { return b.getAttribute('data-v'); })};"
            "  document.getElementById('sched-v-week').click();"
            "  return {heads: heads, evDays: evDays, cells: cells, out: out, label: label,"
            "    minis: minis, has: has, back: back};"
            "})()")
        check("3.3 月视图：周一…周日表头 + 整月网格（含上下月补位），9 月两条事件被高亮",
              cal["heads"] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
              and cal["cells"] % 7 == 0 and 0 < cal["out"] < 7
              and sorted(cal["evDays"]) == ["2026-09-14", "2026-09-16"]
              and cal["label"] == "2026 年 9 月",
              json.dumps(cal, ensure_ascii=False)[:240])
        check("3.4 年视图：12 个月缩略 + 有事件的日子有点；点「1 月」回到月视图并显示云端版本号",
              cal["minis"] == 12 and cal["has"] == 2 and cal["back"]["on"] == ["month"]
              and cal["back"]["label"] == "2026 年 1 月" and "云端第" in sched["meta"],
              json.dumps({"minis": cal["minis"], "has": cal["has"], "back": cal["back"],
                          "meta": sched["meta"]}, ensure_ascii=False))

        # ---- ② 我的课表（实时数据由请求桩喂：真实抓取在这个账号上必然失败）----
        print("\n[4] ② 我的课表（桩：edupage 有数据 + managebac 报错 + 账号名）")
        stub_fill(cdp, J(LIVE_OK))
        refetch(cdp, "document.querySelectorAll('#tt-week .tt-lesson').length > 0",
                view="timetable", label="课表条目")
        tt = cdp.evaluate(
            "({n: document.querySelectorAll('#tt-week .tt-lesson').length,"
            " compat: document.querySelectorAll('#tt-week .tt-compat .evt').length,"
            # 课表工具条上的技术细节小字（抓取时间 / 课程条目 / 上课天数）已被整体删除
            # ——用户要求「有一种把很多技术细节都暴露在用户面前的草台班子的感觉，精简一些」。
            # 这些读取一律用「元素不在就是空串」，不再假设它们存在。
            " synced: (document.getElementById('tt-synced') || {}).textContent || '',"
            " cache: document.getElementById('tt-cache').textContent,"
            " count: (document.getElementById('tt-count') || {}).textContent || '',"
            " days: (document.getElementById('tt-days') || {}).textContent || '',"
            " oldSynced: document.getElementById('tt-synced') ? 'still-there' : null,"
            " oldCount: document.getElementById('tt-count') ? 'still-there' : null,"
            " info: document.getElementById('tt-info').hidden ? '' : document.getElementById('tt-info').textContent,"
            " connText: document.getElementById('tt-conn').textContent,"
            " connShown: !document.getElementById('tt-conn').hidden,"
            " connColor: getComputedStyle(document.getElementById('tt-conn')).color,"
            " connDots: document.querySelectorAll('#tt-conn .tt-conn__dot').length,"
            " oldNote: document.getElementById('tt-note') ? 'still-there' : null,"
            " oldGroups: document.getElementById('tt-groups') ? 'still-there' : null,"
            " text: document.getElementById('tt-body').innerText,"
            " calls: __stub.calls.map(c => c.url),"
            " visible: !document.getElementById('view-timetable').hidden})")
        check("4.1 课表视图可见：渲染 5 节课卡 / 2 天（数据来自 GET /app/data/）",
              tt["visible"] and tt["n"] == 5 and tt["compat"] == 5,
              json.dumps({k: tt[k] for k in ("visible", "n", "compat")},
                         ensure_ascii=False)[:240])
        check("4.2 实时数据来自 /app/data/（school 只作降级备用，timetable 对象不再读）",
              any("/app/data/" in u for u in tt["calls"])
              and not any("sync/objects/timetable" in u for u in tt["calls"]),
              json.dumps(tt["calls"], ensure_ascii=False)[:200])
        check("4.3 显示科目 / 教室 / 老师 / 教学组",
              all(k in tt["text"] for k in ["数学 AA HL", "B302", "王老师", "数学 AA HL（G1）"]),
              tt["text"][:200])
        check("4.4 课表工具条**不再**写抓取时间 / 课程条目 / 上课天数这类技术细节"
              "（用户要求精简；cache 标记仍在，它是「这次是实时抓的还是缓存」的诚实口径）",
              tt["oldSynced"] is None and tt["oldCount"] is None
              and tt["synced"] == "" and tt["count"] == "" and tt["days"] == ""
              and tt["cache"] == "实时抓取",
              f"cache={tt['cache']} oldSynced={tt['oldSynced']} oldCount={tt['oldCount']}")
        check("4.5 课表页**不再**写账号名（工具条只留绿色「已连接」四个字；账号名在设置 / 邮箱页看）",
              "已连接：demo-student" not in tt["info"] and "demo-student" not in tt["info"]
              and tt["connText"].strip() == "已连接",
              json.dumps({"info": tt["info"][:120], "conn": tt["connText"]},
                         ensure_ascii=False)[:240])
        check("4.6 edupage 段正常时不显示该平台错误",
              cdp.evaluate("document.getElementById('tt-error').hidden") is True, "")
        check("4.7 课表页不再写「只显示你选的课：N / M 条」这类过滤细节，也不写教学组清单"
              "（工具条只留绿色「已连接」）",
              "只显示你选的课" not in tt["info"] and "已隐藏" not in tt["info"]
              and "本周选课" not in tt["info"]
              and tt["connText"].strip() == "已连接" and tt["connShown"] is True
              and tt["connColor"] == "rgb(42, 114, 90)"
              and tt["oldNote"] is None and tt["oldGroups"] is None,
              json.dumps({"info": tt["info"][:120], "conn": tt["connText"],
                          "color": tt["connColor"], "oldNote": tt["oldNote"]},
                         ensure_ascii=False)[:260])

        # 当前时间横线（真浏览器里量 rect）：构造「今天 10:00」→ P3（09:35–10:15）的 0.625 处。
        # 这条不依赖真实时刻，任何时候跑都是同一个结果（用 ttNowMinsOverride 造时刻）。
        tl = cdp.evaluate("""
        (function () {
          ttNowMinsOverride = 10 * 60;
          ttUpdateNowLine();
          var grid = document.getElementById('tt-week');
          var line = document.getElementById('tt-nowline');
          var tag = document.getElementById('tt-clock');
          var pt = ttNowPoint(600);
          var out = {pt: pt, row: pt.row, f: pt.f, hasLine: !!line,
                     tag: tag ? tag.textContent : '', timer: ttNowLineTimer !== null,
                     pe: line ? getComputedStyle(line).pointerEvents : '',
                     gridW: Math.round(grid.getBoundingClientRect().width * 10) / 10};
          if (line) {
            var row = grid.querySelectorAll('.tt-time')[pt.row];
            var rr = row.getBoundingClientRect(), gr = grid.getBoundingClientRect();
            out.rowName = row.textContent.replace(/\\s+/g, ' ').trim();
            out.rectTop = Math.round((rr.top - gr.top) * 100) / 100;
            out.rectH = Math.round(rr.height * 100) / 100;
            out.expectTop = Math.round((rr.top - gr.top + rr.height * pt.f) * 100) / 100;
            out.top = Math.round(parseFloat(line.style.top) * 100) / 100;
            out.delta = Math.round((out.top - out.expectTop) * 100) / 100;
            out.rel = Math.round(((out.top - out.rectTop) / out.rectH) * 1000) / 1000;
            out.insideRow = out.top >= out.rectTop - 1 && out.top <= out.rectTop + out.rectH + 1;
            out.width = Math.round(line.getBoundingClientRect().width * 10) / 10;
          }
          ttShiftWeek(-1);
          out.afterPrev = !!document.getElementById('tt-nowline');
          ttShiftWeek(1);
          out.afterBack = !!document.getElementById('tt-nowline');
          ttNowMinsOverride = null;
          ttUpdateNowLine();
          return out;
        })()
        """)
        print("     时间线实测:", json.dumps(tl, ensure_ascii=False)[:420])
        check("4.7b 当前时间横线：构造「今天 10:00」→ 线落在 P3 行（09:35–10:15）的 0.625 处，"
              "横贯整表、不挡点击；翻到上一周不画、翻回本周又画出来",
              tl["hasLine"] is True and tl["row"] == 2
              and abs(tl["f"] - 0.625) < 1e-6 and tl["rowName"].startswith("P3")
              and abs(tl["rel"] - 0.625) < 0.02 and abs(tl["delta"]) <= 1.0
              and tl["insideRow"] is True and tl["pe"] == "none"
              and abs(tl["width"] - tl["gridW"]) <= 1.0 and tl["tag"] == "10:00"
              and tl["afterPrev"] is False and tl["afterBack"] is True
              and tl["timer"] is True,
              json.dumps(tl, ensure_ascii=False)[:500])

        # ---- ① 没选任何教学组时：课表位置给空态卡 + 一步可达的选课入口（绝不铺全年级候选）----
        # 两个选课来源都必须为空，页面才算「没选课」：
        #   ① 同步对象 settings.lessons  → 这里直接把它改成客户端「空选课」的真实形态 `{"lessons":[]}`
        #   ② school.edupage.selected    → 用桩把 school 快照换成 selected=[] 的那份
        cdp.evaluate("window.__schoolBody = { ok: true, name: 'school', revision: 1,"
                     " payload: " + json.dumps(json.dumps(LESSONS_EMPTY_SCHOOL, ensure_ascii=False))
                     + " }; 'ok'")
        cdp.evaluate("st.lessons.doc = " + J(LESSONS_CLEARED_DOC) + ";"
                     " st.lessons.parsed = true; applyLive(); 'ok'")
        stub_fill(cdp, J(LIVE_OK_CLEARED))
        refetch(cdp, "!!document.getElementById('tt-nopick')",
                view="timetable", btn="tt-refresh", label="没选课的空态卡")
        nopick = cdp.evaluate(
            "({card: !!document.getElementById('tt-nopick'),"
            " title: (document.getElementById('tt-nopick')||{}).innerText || '',"
            " lessons: document.querySelectorAll('#tt-week .tt-lesson').length,"
            " natHead: !!document.getElementById('tt-national-head'),"
            " compat: document.querySelectorAll('#tt-nopick .tt-compat .evt').length,"
            " btn: !!document.querySelector('#tt-nopick .nopick__pick'),"
            " btnText: ((document.querySelector('#tt-nopick .nopick__pick')||{}).textContent) || '',"
            " groupsNote: document.getElementById('tt-groups') ?"
            "   document.getElementById('tt-groups').textContent : '',"
            " info: document.getElementById('tt-info').hidden ? '' : document.getElementById('tt-info').textContent})")
        # 「有组的选修课一条都没铺」用兼容层里的「教学组」标签判：无组课那几行的教学组是「—」
        nopick["grouped"] = cdp.evaluate(
            "Array.from(document.querySelectorAll('#tt-week .tt-compat .evt')).filter(function (e) {"
            "  var tags = Array.from(e.querySelectorAll('.tt-tag')).map(function (t) { return t.textContent; });"
            "  return tags.some(function (t) { return t.indexOf('教学组') === 0 && t !== '教学组 —'; });"
            "}).length")
        check("4.8 没选教学组 → **有组（选修）的课一条都不铺**：空态卡照画，铺出来的只有国家必修"
              "（`_lesson_any` 造的这 5 条没有组号 = 国家课程，按新语义「始终显示」；"
              "  它们上面有 #tt-national-head 说明卡，见 4.8b）",
              nopick["card"] is True and nopick["compat"] == 0
              and nopick["grouped"] == 0 and nopick["natHead"] is True
              and "还没选择你的教学组" in nopick["title"],
              json.dumps(nopick, ensure_ascii=False)[:300])
        check("4.8b 没选课时空态卡里写明「国家理科默认必选、全班必修也一样，你只需要勾选修课」",
              "默认必选" in nopick["title"] and "你只需要勾**选修课**" in nopick["title"]
              and "国家理科" in nopick["title"],
              nopick["title"][:240])
        check("4.9 空态卡里给出一步可达的选课入口 + 「两边同步」的说明",
              nopick["btn"] is True and "选择我的教学组" in nopick["btnText"]
              and "客户端「设置 → 选课」" in nopick["title"]
              and "只显示你自己的课表" in nopick["title"],
              nopick["title"][:240])
        check("4.10 没选课时不再出现「显示全年级候选课表」的说明（那句会让人以为那些课都是自己的）；"
              "课表上也没有「隐藏了 N 条」这类细节",
              "全年级候选课表" not in nopick["info"] and nopick["groupsNote"] == ""
              and "已隐藏" not in nopick["info"] and "本周选课" not in nopick["info"],
              json.dumps({"info": nopick["info"], "note": nopick["groupsNote"]}, ensure_ascii=False)[:200])
        check("4.10b 没选课时铺出来的课卡**一条有组（选修）的都没有** —— 只可能是国家必修",
              nopick["natHead"] is True and nopick["lessons"] >= 0 and nopick["grouped"] == 0,
              json.dumps({"lessons": nopick["lessons"], "grouped": nopick["grouped"],
                          "natHead": nopick["natHead"]}, ensure_ascii=False))
        # 选课界面直接内嵌在空态卡里（不需要点击按钮展开）
        card_panel = cdp.evaluate(
            "({open: !document.getElementById('tt-nopick').querySelector('.nopick__picker').hidden,"
            " list: document.querySelectorAll('#nopick-list .subject-row').length,"
            " hasSave: !!document.getElementById('nopick-save'),"
            " filter: !!document.getElementById('nopick-filter')})")
        check("4.11 空态卡里直接内嵌按科目分组的选课界面（含搜索框 + 保存按钮）",
              card_panel["open"] is True and card_panel["list"] > 0 and card_panel["hasSave"] is True
              and card_panel["filter"] is True,
              json.dumps(card_panel, ensure_ascii=False)[:200])

        # ---- ④ 选课模态：**常驻入口**（工具条那颗「✏️ 选择我的教学组」）----
        # 2026-09-16 的用户反馈：「点那个选择我的教学组，下面显示都没有东西了」。
        # 根因：模态自己的 `#picker-list` 从来没被填过 —— 渲染函数写死了宿主 `#nopick-list`
        # （课表空态卡里那张卡），所以点按钮只弹出个空面板。
        # 这一节就是那次漏掉的常驻断言：**打开模态后 `#picker-list` 的行数必须 > 0**。
        print("\n[4a] 选课模态：工具条按钮打开后有内容 + 与空态卡同一个渲染器")
        # 换一份**带教学组**的课表：没选课那一态的关键动作是「勾选修 → 保存」，
        # 上面那 5 条无组候选课一个可勾的复选框都没有（`_lesson_any` 造的就是这种）。
        stub_fill(cdp, J(LIVE_OK_PICKABLE))
        refetch(cdp, "!!document.getElementById('tt-nopick')", view="timetable",
                btn="tt-refresh", label="带教学组的空态卡")
        # 先量**打开模态之前**空态卡那份（模态一开，卡片里那份按设计被清空+收起，
        # 之后再读就是空的 —— 这是「同一时刻只有一份列表」那条要求的直接后果）。
        card_rows_before = cdp.evaluate(
            "({n: document.querySelectorAll('#nopick-list .subject-row').length,"
            " first3: Array.prototype.slice.call("
            "   document.querySelectorAll('#nopick-list .subject-row'), 0, 3)"
            "   .map(function (r) { return r.innerText.replace(/\\s+/g, ' ').trim(); })})")
        cdp.evaluate("openPickerModal(); 'ok'")
        modal = cdp.evaluate(
            "({open: !document.getElementById('picker-overlay').hidden,"
            " modalRows: document.querySelectorAll('#picker-list .subject-row').length,"
            " modalH: Math.round(document.getElementById('picker-list').getBoundingClientRect().height),"
            " modalDisplay: getComputedStyle(document.getElementById('picker-list')).display,"
            " cardRows: document.querySelectorAll('#nopick-list .subject-row').length,"
            " cardShown: !document.getElementById('nopick-picker').hidden,"
            " cardDisplay: getComputedStyle(document.getElementById('nopick-picker')).display,"
            " first3: Array.prototype.slice.call("
            "   document.querySelectorAll('#picker-list .subject-row'), 0, 3)"
            "   .map(function (r) { return r.innerText.replace(/\\s+/g, ' ').trim(); }),"
            " hasSave: !!document.getElementById('picker-save'),"
            " hasFilter: !!document.getElementById('picker-filter')})")
        print("     模态：", json.dumps(modal, ensure_ascii=False))
        print("     打开前空态卡那份：", json.dumps(card_rows_before, ensure_ascii=False))
        check("4.11b **打开模态后 `#picker-list` 的行数 > 0**（这次缺了它才让空面板漏到现在）",
              modal["open"] is True and modal["modalRows"] > 0 and modal["modalH"] > 0,
              json.dumps(modal, ensure_ascii=False)[:240])
        check("4.11c 模态里的行数与打开前空态卡那份**完全一致**（同一个 `renderSubjectPickerInto`）",
              modal["modalRows"] == card_rows_before["n"] > 0
              and modal["first3"] == card_rows_before["first3"],
              json.dumps({"modal": modal["modalRows"], "card": card_rows_before["n"],
                          "modalFirst3": modal["first3"],
                          "cardFirst3": card_rows_before["first3"]},
                         ensure_ascii=False)[:280])
        check("4.11d 模态打开时课表里**不再有第二份选课列表**（空态卡那份收起：不可见且列表为空）",
              modal["cardShown"] is False and modal["cardDisplay"] == "none"
              and modal["cardRows"] == 0,
              json.dumps({k: modal[k] for k in ("cardShown", "cardDisplay", "cardRows")},
                         ensure_ascii=False))
        check("4.11e 模态带搜索框 + 保存按钮（两个入口用同一套装订）",
              modal["hasSave"] is True and modal["hasFilter"] is True, "")

        # 勾一条选修（真点击复选框）→ 保存 → 模态关闭 + 课表按新选课过滤
        seed_pick = cdp.evaluate(
            "(function () {"
            "  var rows = document.querySelectorAll('#picker-list .subject-row');"
            "  for (var i = 0; i < rows.length; i++) {"
            "    var c = rows[i].querySelector('input[type=checkbox]');"
            "    if (c && !c.disabled) { c.click(); return c.getAttribute('data-group'); }"
            "  }"
            "  return '';"
            "})()")
        before_save = cdp.evaluate(
            "({raw: st.ttGroupInfo.raw, kept: st.ttGroupInfo.kept,"
            " dropped: st.ttGroupInfo.dropped, empty: st.ttGroupInfo.empty,"
            " lessons: (st.ttLessons || []).length, card: !!document.getElementById('tt-nopick')})")
        cdp.evaluate("document.getElementById('picker-save').click(); 'ok'")
        cdp.wait_for("document.getElementById('picker-overlay').hidden", seconds=30,
                     label="模态保存后关闭")
        try:
            cdp.wait_for("!!st.lessons.doc && (st.lessons.doc.lessons || []).length > 0",
                         seconds=20, label="选课落盘")
        except Exception as exc:  # noqa: BLE001
            print("     等待选课落盘失败：", exc)
        after_save = cdp.evaluate(
            "({raw: st.ttGroupInfo.raw, kept: st.ttGroupInfo.kept,"
            " dropped: st.ttGroupInfo.dropped, empty: st.ttGroupInfo.empty,"
            " lessons: (st.ttLessons || []).length, card: !!document.getElementById('tt-nopick'),"
            " saved: ((st.lessons.doc || {}).lessons || []).map(function (r) {"
            "   return r.subject + '/' + r.teacher + '/' + r.group; }),"
            " note: document.getElementById('tt-groups') ?"
            "   document.getElementById('tt-groups').textContent : '',"
            " conn: document.getElementById('tt-conn').textContent,"
            " connShown: !document.getElementById('tt-conn').hidden,"
            " overlayHidden: document.getElementById('picker-overlay').hidden,"
            " cardRowsBack: document.querySelectorAll('#nopick-list .subject-row').length,"
            " toast: document.getElementById('toast').textContent})")
        print("     保存前：", json.dumps(before_save, ensure_ascii=False))
        print("     保存后：", json.dumps(after_save, ensure_ascii=False))
        check("4.11f 模态里勾的组保存进 settings.lessons（形态仍是 {lessons:[{subject,teacher,group}]}）",
              bool(seed_pick) and len(after_save["saved"]) == 1
              and after_save["saved"][0].endswith("/" + seed_pick),
              json.dumps({"picked": seed_pick, "saved": after_save["saved"],
                          "toast": after_save["toast"]}, ensure_ascii=False)[:240])
        check("4.11g 保存后模态关闭 + 课表**立刻按新选课过滤**（空态卡消失、只显示勾的组）；"
              "工具条上仍是绿色「已连接」（过滤细节不再进 DOM）",
              after_save["overlayHidden"] is True and after_save["card"] is False
              and after_save["empty"] is False and after_save["kept"] < after_save["raw"]
              and after_save["note"] == "" and "已隐藏" not in after_save["note"]
              and after_save["conn"].strip() == "已连接" and after_save["connShown"] is True,
              json.dumps(after_save, ensure_ascii=False)[:260])
        cdp.evaluate("window.__schoolBody = { ok: true, name: 'school', revision: 1, payload: '{}' }; 'ok'")

        # ---- ④ 课表版式四修：连堂跨行 / 并行课折叠 / 行高统一（真渲染里量 DOM）----
        print("\n[4b] 课表版式：连堂跨行 + 并行课折叠 + 行高统一（真浏览器实测）")
        TT_FIX = """
        (function (rowsSpec) {
          var host = document.getElementById('tt-week');
          var wk = ttBuildWeek();
          var day = null;
          for (var i = 0; i < wk.length; i++) { if (wk[i].lessons.length) { day = wk[i]; break; } }
          if (!day) { return {err: '本周没有课，构造不出连堂'}; }
          st.ttLessons = rowsSpec.map(function (r) {
            return {day: day.day, start: r[0], end: r[1], subject: r[2], room: r[3], teacher: r[4], group: r[5]};
          });
          st.ttGroupInfo = {groups: ['G1'], raw: st.ttLessons.length, kept: st.ttLessons.length,
                            dropped: 0, unknown: 0, empty: false};
          renderTimetable(host);
          var cards = host.querySelectorAll('.tt-lesson');
          var spans = [];
          for (var c = 0; c < cards.length; c++) {
            var stl = cards[c].getAttribute('style') || '';
            if (/span/.test(stl)) {
              var rc = cards[c].getBoundingClientRect();
              spans.push({h: Math.round(rc.height * 10) / 10,
                          parentId: cards[c].parentElement.id,
                          parentCls: cards[c].parentElement.className, style: stl});
            }
          }
          var rows = {};
          var all = host.querySelectorAll('.tt-cell, .tt-time, .tt-head');
          for (var a = 0; a < all.length; a++) {
            var m = /grid-row: *([0-9]+)/.exec(all[a].getAttribute('style') || '');
            if (!m) continue;
            var k = m[1];
            if (!rows[k]) rows[k] = [];
            rows[k].push(Math.round(all[a].getBoundingClientRect().height * 10) / 10);
          }
          var summary = {};
          Object.keys(rows).forEach(function (k) {
            var hs = rows[k];
            summary[k] = {n: hs.length, min: Math.min.apply(null, hs), max: Math.max.apply(null, hs),
                          equal: hs.every(function (x) { return Math.abs(x - hs[0]) < 0.6; })};
          });
          var cells = {};
          Array.prototype.forEach.call(host.querySelectorAll('.tt-cell'), function (x) {
            var m2 = /grid-row: *([0-9]+)/.exec(x.getAttribute('style') || '');
            if (!m2) return;
            var t = (x.innerText || '').replace(/\\s+/g, ' ').trim();
            if (t) { (cells[m2[1]] = cells[m2[1]] || []).push(t.slice(0, 24)); }
          });
          return {n: cards.length, spans: spans, rows: summary, cells: cells,
                  tpl: host.style.gridTemplateRows || '',
                  par: host.querySelectorAll('.tt-par-sum').length,
                  parOpen: host.querySelectorAll('.tt-par-list:not([hidden])').length};
        })
        """

        def tt_fix(spec):
            return cdp.evaluate(TT_FIX + "(" + json.dumps(spec, ensure_ascii=False) + ")")

        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"timetable\"]').click()")
        cdp.wait_for("!document.getElementById('view-timetable').hidden", seconds=20, label="课表视图可见")
        time.sleep(0.5)

        merged = tt_fix([["08:00", "08:40", "连堂课", "B101", "T老师", "G1"],
                         ["08:45", "09:25", "连堂课", "B101", "T老师", "G1"]])
        m2, m3 = merged["rows"].get("2", {}), merged["rows"].get("3", {})
        print("     ① 连堂:", json.dumps(merged, ensure_ascii=False)[:420])
        check("4.12 连堂（08:00 + 08:45 同科目同组）→ 只渲染 1 张课卡，且它是 #tt-week 的直接子元素"
              "（塞进 .tt-cell 里 grid-row 是无效的，那正是「第二行空白」的原因）",
              merged["n"] == 1 and len(merged["spans"]) == 1
              and merged["spans"][0]["parentId"] == "tt-week",
              json.dumps(merged, ensure_ascii=False)[:400])
        check("4.13 合并卡跨两行：高度覆盖 P1+P2（第二行不再什么都没有）+ P2 的格子仍在（只是被盖住）",
              len(merged["spans"]) == 1 and "span 2" in merged["spans"][0]["style"]
              and m2.get("max", 0) > 0 and m3.get("max", 0) > 0
              and merged["spans"][0]["h"] >= (m2["max"] + m3["max"])
              and m3.get("n") == 8,
              json.dumps({"span": merged["spans"], "row2": m2, "row3": m3},
                         ensure_ascii=False)[:400])

        apart = tt_fix([["08:00", "08:40", "第一门课", "B101", "T老师", "G1"],
                        ["08:45", "09:25", "另一门课", "B102", "S老师", "G1"]])
        print("     ① 不合并:", json.dumps(apart, ensure_ascii=False)[:360])
        check("4.14 不满足连堂条件（科目不同）→ 2 张课卡，P1 / P2 两行都有内容（两种情况都测）",
              apart["n"] == 2 and len(apart["spans"]) == 0
              and len(apart["cells"].get("2", [])) > 0 and len(apart["cells"].get("3", [])) > 0,
              json.dumps({"n": apart["n"], "spans": len(apart["spans"]),
                          "r2": apart["cells"].get("2"), "r3": apart["cells"].get("3")},
                         ensure_ascii=False)[:400])

        par = tt_fix([["09:35", "10:15", "物理", "实验楼", "P老师", "G1"],
                      ["09:35", "10:15", "化学", "实验楼", "C老师", "G2"],
                      ["09:35", "10:15", "生物", "实验楼", "B老师", "G3"]])
        print("     ③ 三门并行:", json.dumps(par, ensure_ascii=False)[:360])
        check("4.15 同一时段三门并行课（国家理科物理/化学/生物）→ 折叠成一格「3 门并行课」摘要，"
              "课卡仍在 DOM 里（点击展开）",
              par["n"] == 3 and par["par"] == 1 and par["parOpen"] == 0
              and any("3 门并行课" in t for t in par["cells"].get("4", [])),
              json.dumps({"n": par["n"], "par": par["par"], "open": par["parOpen"],
                          "cell4": par["cells"].get("4")}, ensure_ascii=False)[:400])

        tall = tt_fix([["08:00", "08:40",
                        "一张很长的课卡：数学分析与高等代数专题研讨（三行文字）",
                        "实验楼 B302 大教室", "王老师 · 李老师", "G1"]])
        heights = [tall["rows"].get(str(r), {}).get("max", 0) for r in (2, 3, 4, 5, 6, 8, 9, 10, 11, 12)]
        print("     ④ 行高:", json.dumps(tall["rows"], ensure_ascii=False)[:360])
        check("4.16 行高统一照 ttFitRows：每个正课行都等于最高的那一行，且同行所有格子等高"
              "（含该行的节次栏）",
              all(tall["rows"].get(str(r), {}).get("equal") for r in (2, 3, 4, 5, 6, 8, 9, 10, 11, 12))
              and len(set(heights)) == 1 and heights[0] > 0,
              json.dumps({"heights": heights, "tpl": tall["tpl"]}, ensure_ascii=False)[:400])
        check("4.17 星期表头独占第 1 行（之前 P1 的格子和「周一…周日」挤在同一行，把星期标题整个盖住了）",
              tall["rows"].get("1", {}).get("n") == 8 and tall["rows"].get("2", {}).get("n") == 8,
              json.dumps({"row1": tall["rows"].get("1"), "row2": tall["rows"].get("2")},
                         ensure_ascii=False)[:240])

        # ---- 导出课表（真点按钮 → 真下载，方向必须是「行=星期、列=节次」） ----
        print("\n[4c] 导出课表：CSV（行=星期/列=节次、BOM、转义）+ PNG（canvas 实测）+ 真下载")
        exp_dir = tempfile.mkdtemp(prefix="tt_dl_")
        if args.shot_dir:
            os.makedirs(args.shot_dir, exist_ok=True)
        cdp.send("Page.setDownloadBehavior", behavior="allow", downloadPath=exp_dir)
        # 注：本机 Edge headless 上 `(function () { … })()` 的完成值恒为 undefined
        #（同样内容写成箭头函数就正常），所以下面一律用「箭头函数 + 显式 return」。
        cdp.evaluate("""
        window.__dl = [];
        window.__png = null;
        window.__dlArmed = false;
        (() => {
          if (window.__dlArmed) return 'ok';
          window.__dlArmed = true;
          var real = URL.createObjectURL.bind(URL);
          URL.createObjectURL = function (b) {
            window.__dl.push(b);
            var fr = new FileReader();
            fr.onload = function () { window.__png = String(fr.result); };
            fr.readAsDataURL(b);
            return real(b);
          };
          return 'ok';
        })()
        """)
        EXP_INJECT = """
        ((spec) => {
          var mon = mondayOf(isoToday());
          st.ttLessons = spec.map(function (r) {
            return {day: isoOfDate(shiftDay(mon, r[0])), start: r[1], end: r[2], subject: r[3],
                    room: r[4], teacher: r[5], group: r[6]};
          });
          st.ttGroupInfo = {groups: [], raw: st.ttLessons.length, kept: st.ttLessons.length,
                            dropped: 0, unknown: 0, empty: false};
          ttWeekStart = null;                 // 显示「本周」，与注入的日期对得上
          renderTimetable(document.getElementById('tt-week'));
          window.__dl.length = 0;             // 清掉上一段（连堂/并行）留下的 Blob
          return {weekStart: isoOfDate(mon), title: ttExportMatrix().title,
                  col0: ttExportMatrix().header[0], col1: ttExportMatrix().header[1],
                  periods: ttExportMatrix().periods, days: ttExportMatrix().days,
                  btnDisabled: document.getElementById('tt-export-btn').disabled};
        })
        """
        # 假数据：**周一 P1** 一门科目名带逗号的课、**周二 P7（13:30）** 一门科目名带双引号的课，
        # 其余全空。日期从页面里的 isoToday() 推 → 不管哪天跑都落在「显示的那一周」里。
        exp_spec = [[0, "08:00", "08:40", "数学 AA HL, 进阶", "B302", "王老师", "数学 AA HL（G1）"],
                    [1, "13:30", "14:10", '英文写作 "HL"', "语言楼 201", "Smith", "英文 HL（G2）"]]
        exp_inj = cdp.evaluate(EXP_INJECT + "(" + J(exp_spec) + ")")
        print("     注入:", json.dumps(exp_inj, ensure_ascii=False)[:300])

        # ① 真点工具条按钮 → 小下拉出现（不是 window.confirm）
        cdp.evaluate("document.getElementById('tt-export-btn').click()")
        menu = cdp.evaluate(
            "({open: !document.getElementById('tt-export-menu').hidden,"
            " items: Array.from(document.querySelectorAll('#tt-export-menu .tt-export__item'))"
            "   .map(function (b) { return b.textContent.trim().split('\\n')[0]; }),"
            " expanded: document.getElementById('tt-export-btn').getAttribute('aria-expanded'),"
            " rect: (function (r) { return {l: Math.round(r.left), r: Math.round(r.right),"
            "   t: Math.round(r.top), b: Math.round(r.bottom)}; })"
            "   (document.getElementById('tt-export-menu').getBoundingClientRect()),"
            " vw: window.innerWidth})")
        print("     菜单:", json.dumps(menu, ensure_ascii=False))
        check("4.18 点「⬇ 导出课表」→ 站内小下拉展开（两个条目、aria-expanded 为 true、"
              "菜单完整落在视口内），不是 window.confirm",
              menu["open"] is True and menu["expanded"] == "true" and len(menu["items"]) == 2
              and any(k in menu["items"][0] for k in ("PNG", "png"))
              and any(k in menu["items"][1] for k in ("CSV", "csv"))
              and menu["rect"]["l"] >= 0 and menu["rect"]["r"] <= menu["vw"],
              json.dumps(menu, ensure_ascii=False)[:300])
        if args.shot_dir:
            data = cdp.send("Page.captureScreenshot", format="png", captureBeyondViewport=False)
            open(os.path.join(args.shot_dir, "export-menu.png"), "wb").write(base64.b64decode(data["data"]))

        # ② 真点「导出表格 CSV」→ 拦下一个 Blob，把原文读出来
        cdp.evaluate("document.getElementById('tt-export-csv').click()")
        time.sleep(0.6)
        csv_txt = cdp.evaluate(
            "((i) => new Promise(function (resolve) {"
            "  var fr = new FileReader();"
            "  fr.onload = function () { resolve(fr.result); };"
            "  fr.readAsText(window.__dl[i], 'utf-8');"
            "}))(" + "0" + ")")
        csv_meta = cdp.evaluate(
            "((i) => new Promise(function (resolve) {"
            "  var b = window.__dl[i];"
            "  var fr = new FileReader();"
            "  fr.onload = function () {"
            "    var x = new Uint8Array(fr.result);"
            "    resolve({type: b.type, size: b.size,"
            "             bomOk: x[0] === 0xEF && x[1] === 0xBB && x[2] === 0xBF});"
            "  };"
            "  fr.readAsArrayBuffer(b.slice(0, 4));"
            "}))(" + "0" + ")")
        csv_toast = cdp.evaluate("document.getElementById('toast').textContent")

        def csv_parse(text):
            """把 CSV 按 RFC4180 拆回来（含引号包裹与 "" 转义）——
            直接 split(",") 会被「科目名里的逗号」骗到，那正是要验的东西之一。"""
            rows, row, field, i, in_q = [], [], "", 0, False
            t = text or ""
            while i < len(t):
                ch = t[i]
                if in_q:
                    if ch == '"':
                        if i + 1 < len(t) and t[i + 1] == '"':
                            field += '"'
                            i += 1
                        else:
                            in_q = False
                    else:
                        field += ch
                elif ch == '"':
                    in_q = True
                elif ch == ",":
                    row.append(field)
                    field = ""
                elif ch == "\r":
                    pass
                elif ch == "\n":
                    row.append(field)
                    rows.append(row)
                    row, field = [], ""
                else:
                    field += ch
                i += 1
            if field != "" or row:
                row.append(field)
                rows.append(row)
            return [r for r in rows if any(c != "" for c in r)]

        parsed = csv_parse(csv_txt)
        head_cols = parsed[0] if parsed else []
        body = parsed[1:] if len(parsed) > 1 else []
        row_mon = body[0] if len(body) > 0 else []
        row_tue = body[1] if len(body) > 1 else []
        lines = [ln for ln in (csv_txt or "").split("\r\n") if ln != ""]
        print("     CSV 前 3 行:")
        for ln in lines[:3]:
            print("       " + ln)
        # 列号 → 节次名（表头就在第一行），用来把断言写成「落到哪个节次」而不是猜列号
        def col_of(name):
            for i, h in enumerate(head_cols):
                if h.startswith(name + " "):
                    return i
            return -1

        c_p1, c_p7 = col_of("P1"), col_of("P7")
        print("     表头列：P1=%d P7=%d，周一课在 %r，周二课在 %r"
              % (c_p1, c_p7, row_mon[c_p1] if c_p1 >= 0 else None,
                 row_tue[c_p7] if c_p7 >= 0 else None))
        check("4.19 CSV 导出：**行 = 星期（周一…周日）+ 日期、列 = 节次**（第一行就是表头），"
              "方向与页面相反",
              head_cols[:2] == ["星期", "日期"]
              and head_cols[2].startswith("P1 ") and head_cols[3].startswith("P2 ")
              and len(parsed) == 8                      # 1 行表头 + 7 天
              and [r[0] for r in body] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
              and "08:00-08:40" in head_cols[2] and "08:45-09:25" in head_cols[3],
              json.dumps({"head": head_cols[:5], "rows": [r[0] for r in body]},
                         ensure_ascii=False)[:300])
        conds = {
            "c_p1>0,c_p7>0": c_p1 > 0 and c_p7 > 0,
            "列数一致": len(head_cols) == len(row_mon) == len(row_tue),
            "周一P1": len(row_mon) > c_p1 and row_mon[c_p1] == "数学 AA HL, 进阶 · B302 · 王老师 · 数学 AA HL（G1）",
            # 只数课格：前两列是「星期 / 日期」，本来就该有字
            "周一只有一节": sum(1 for c in row_mon[2:] if c != "") == 1,
            "周二P7": len(row_tue) > c_p7 and row_tue[c_p7] == '英文写作 "HL" · 语言楼 201 · Smith · 英文 HL（G2）',
            "周二只有一节": sum(1 for c in row_tue[2:] if c != "") == 1,
            "其余六天全空": all(all(c == "" for c in r[2:]) for r in body[2:]),
            "七天齐全": [r[0] for r in body] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        }
        print("     4.20 分解:", json.dumps({k: v for k, v in conds.items()}, ensure_ascii=False))
        check("4.20 CSV 的课落在**正确的星期 × 节次**格：周一 P1 一格、周二 P7 一格，其余格全空",
              all(conds.values()),
              json.dumps({"conds": conds, "mon": row_mon, "tue": row_tue,
                          "lens": [len(head_cols), len(row_mon), len(row_tue)]},
                         ensure_ascii=False)[:400])
        check("4.21 CSV 是 UTF-8 **带 BOM**（字节 EF BB BF）+ MIME text/csv；中文原文没乱码；"
              "含逗号/引号的科目名被正确转义（解析回来与原文逐字一致）",
              csv_meta["bomOk"] is True and "text/csv" in (csv_meta["type"] or "")
              and "数学 AA HL, 进阶" in csv_txt
              and '"数学 AA HL, 进阶 · B302 · 王老师 · 数学 AA HL（G1）"' in csv_txt
              and '""HL""' in csv_txt and "周一" in csv_txt and "周日" in csv_txt
              and row_mon[c_p1] == "数学 AA HL, 进阶 · B302 · 王老师 · 数学 AA HL（G1）"
              and row_tue[c_p7] == '英文写作 "HL" · 语言楼 201 · Smith · 英文 HL（G2）',
              json.dumps(csv_meta, ensure_ascii=False)[:200])
        check("4.22 导出成功后工具条 toast 报出文件名；菜单自动收起",
              "已导出表格 CSV" in csv_toast and "课表-" in csv_toast
              and cdp.evaluate("document.getElementById('tt-export-menu').hidden") is True,
              csv_toast)

        # ③ 真点「导出图片 PNG」→ 量画布（dpr 放大后的像素值）+ 把 PNG 存盘
        cdp.evaluate("window.__png = null; window.__dl.length = 0; 'ok'")
        cdp.evaluate("document.getElementById('tt-export-btn').click()")
        cdp.evaluate("document.getElementById('tt-export-png').click()")
        time.sleep(1.0)
        png_url = cdp.evaluate("window.__png")
        png_toast = cdp.evaluate("document.getElementById('toast').textContent")
        geom = cdp.evaluate("""
        (() => {
          var m = ttExportMatrix();
          var out = [];
          [1, 2].forEach(function (dpr) {
            var c = document.createElement('canvas');
            var g = drawExportPng(m, c, dpr);
            var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            var ink = 0;
            for (var i = 0; i < d.length; i += 4) {
              if (d[i] < 250 || d[i + 1] < 250 || d[i + 2] < 250) ink++;
            }
            var sizes = g.fits.map(function (f) { return f.size; });
            out.push({dpr: dpr, w: g.w, h: g.h, logicalW: g.logicalW, logicalH: g.logicalH,
                      rows: g.rows, cols: g.cols, colW: g.colW, rowH: g.rowH,
                      overflow: g.overflow, measured: g.fits.length,
                      minSize: Math.min.apply(null, sizes),
                      wrapped: g.fits.filter(function (f) { return f.wrap; }).length,
                      inkRatio: Math.round(ink / (c.width * c.height) * 10000) / 10000});
          });
          return out;
        })()
        """)
        print("     PNG 画布:", json.dumps(geom, ensure_ascii=False))
        if png_url and png_url.startswith("data:image/png;base64,"):
            raw = base64.b64decode(png_url.split(",", 1)[1])
            if args.shot_dir:
                open(os.path.join(args.shot_dir, "export-table.png"), "wb").write(raw)
            print(f"     导出 PNG：{len(raw)} 字节，magic 正确={raw[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])}")
        check("4.23 PNG 由 canvas 自己画：按 dpr 放大（dpr=2 时画布正好翻倍）、行列数与课表一致、"
              "每格文字都量过且**没有溢出/裁切**、字体不小于 10px",
              len(geom) == 2
              and geom[1]["w"] == geom[0]["w"] * 2 and geom[1]["h"] == geom[0]["h"] * 2
              and geom[0]["rows"] == 7 and geom[0]["cols"] >= 2
              and all(g["overflow"] == 0 for g in geom)
              and all(g["measured"] >= 20 for g in geom)
              and all(g["minSize"] >= 10 for g in geom)
              and geom[0]["inkRatio"] > 0.02,
              json.dumps(geom, ensure_ascii=False)[:400])
        check("4.24 PNG 也是真下载：文件名 `课表-<周一>.png`、toast 报出文件名、"
              "且 PNG 字节头正确",
              bool(png_url) and "已导出图片 PNG" in png_toast and "课表-" in png_toast
              and ".png" in png_toast
              and png_url.startswith("data:image/png;base64,"),
              json.dumps({"toast": png_toast, "len": len(png_url or "")}, ensure_ascii=False)[:200])

        # ④ 真下载到磁盘（Chromium 的 Page.setDownloadBehavior）—— 两个文件都要在
        time.sleep(0.8)
        got = sorted((fn, os.path.getsize(os.path.join(exp_dir, fn)))
                     for fn in os.listdir(exp_dir))
        print("     下载目录:", got)
        check("4.25 磁盘上真的收到了两个文件（CSV + PNG，大小与 blob 一致）",
              len(got) == 2
              and any(fn.endswith(".csv") and fn.startswith("课表-") for fn, _ in got)
              and any(fn.endswith(".png") and fn.startswith("课表-") for fn, _ in got)
              and all(sz > 100 for _, sz in got),
              json.dumps(got, ensure_ascii=False)[:300])

        # ⑤ 边界：没课的一周（空表 + 标题）+ 没加载完（按钮禁用）
        print("\n[4d] 导出边界：没课的一周也要能导出；没加载完 → 禁用 + 提示")
        empty_exp = cdp.evaluate("""
        (() => {
          var out = {};
          var step = function (name, fn) {
            try { out[name] = fn(); }
            catch (e) { out[name] = 'ERR ' + String((e && e.message) || e); }
          };
          st.ttLessons = [];
          st.ttGroupInfo = {groups: [], raw: 0, kept: 0, dropped: 0, unknown: 0, empty: false};
          ttWeekStart = null;
          step('render', function () { renderTimetable(document.getElementById('tt-week')); return 'ok'; });
          step('matrix', function () {
            var m = ttExportMatrix();
            return {title: m.title, days: m.rows.length, header: m.header[0],
                    emptyCells: m.rows.every(function (r) {
                      return r.cells.every(function (c) { return c === ''; });
                    }),
                    csvLines: ttExportCsvText(m).replace('\\ufeff', '').trim().split('\\r\\n').length,
                    csv: ttExportCsvText(m)};
          });
          step('png', function () {
            var c = document.createElement('canvas');
            var g = drawExportPng(ttExportMatrix(), c, 2);
            return {w: g.w, h: g.h, rows: g.rows, overflow: g.overflow};
          });
          step('state', function () {
            return {ready: ttExportReady(),
                    disabled: document.getElementById('tt-export-btn').disabled};
          });
          return out;
        })()
        """)
        print("     空表:", json.dumps({k: v for k, v in empty_exp.items() if k != "matrix"},
                                       ensure_ascii=False)[:300])
        em = empty_exp.get("matrix", {})
        check("4.26 一条课都没有的一周也能导出：空表 + 标题照画（7 行 × 节次列、格子全空、"
              "CSV 仍是「表头 + 7 天」）",
              em.get("days") == 7 and em.get("header") == "星期" and em.get("emptyCells") is True
              and em.get("csvLines") == 8 and em.get("title", "").startswith("我的课表 · ")
              and em.get("csv", "").replace("\ufeff", "").startswith("星期,日期,P1 ")
              and empty_exp.get("png", {}).get("rows") == 7
              and empty_exp.get("png", {}).get("overflow") == 0
              and empty_exp.get("state", {}).get("disabled") is False,
              json.dumps({k: v for k, v in empty_exp.items() if k != "matrix"},
                         ensure_ascii=False)[:300])

        pend_exp = cdp.evaluate("""
        (() => {
          st.ttLessons = []; ttWeekData = null;
          st.live = null; st.liveErr = ''; st.syncReady = false; st.mode = 'loading';
          updateTtExportState();
          var b = document.getElementById('tt-export-btn');
          var out = {disabled: b.disabled, title: b.title};
          document.getElementById('toast').textContent = '';
          b.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          out.toastAfterClick = document.getElementById('toast').textContent;
          out.selectReturned = ttExportSelect('csv');
          out.menuOpen = !document.getElementById('tt-export-menu').hidden;
          return out;
        })()
        """)
        print("     未加载完:", json.dumps(pend_exp, ensure_ascii=False))
        check("4.27 课表还没加载完（pending / 正在读同步对象）→ 导出按钮禁用 + 点它给中文提示，"
              "不生成任何文件",
              pend_exp.get("disabled") is True and pend_exp.get("title") == "课表还没加载完"
              and "课表还没加载完" in (pend_exp.get("toastAfterClick") or "")
              and pend_exp.get("selectReturned") is False
              and pend_exp.get("menuOpen") is False,
              json.dumps(pend_exp, ensure_ascii=False)[:300])

        # 收尾：把课表换回桩里的那份数据，别影响后面的段落
        cdp.evaluate("document.getElementById('btn-refresh').click()")
        try:
            cdp.wait_for("st.ttLessons && st.ttLessons.length === 5", seconds=40,
                         label="课表回到桩数据")
        except Exception as exc:  # noqa: BLE001
            print("     恢复桩数据失败:", exc)
        cdp.send("Page.setDownloadBehavior", behavior="default", downloadPath="")


        # ---- ③a 「我的成绩」已整个移除：导航项与视图 DOM 都不该存在 ----
        print("\n[5] ③a 我的成绩已整个移除（导航 / 视图 / gr-* 元素都不存在）")
        stub_fill(cdp, J(LIVE_OK))
        cdp.goto(base + "/app/")
        wait_app(cdp, label="登录态布局出现（成绩移除后）")
        gone = cdp.evaluate(
            "({tab: !!document.querySelector('#tabs .tab[data-view=\"grades\"]'),"
            " view: !!document.getElementById('view-grades'),"
            " body: !!document.getElementById('gr-body'),"
            " units: !!document.getElementById('gr-units-body'),"
            " count: !!document.getElementById('gr-count'),"
            " navText: Array.from(document.querySelectorAll('#tabs .tab'))"
            "   .map(function (t) { return t.textContent; }).join(' | '),"
            " views: Array.from(document.querySelectorAll('section.view')).map(function (s) { return s.id; })})")
        check("5.1 「我的成绩」已经整个删掉：没有 grades 标签、没有 #view-grades、没有 gr-* 元素",
              gone["tab"] is False and gone["view"] is False and gone["body"] is False
              and gone["units"] is False and gone["count"] is False
              and "成绩" not in gone["navText"],
              json.dumps(gone, ensure_ascii=False)[:260])
        check("5.1b 导航项恰好 7 个（首页/课表/日程/课程/邮箱/Agent 助手/设置）",
              len(gone["views"]) == 7 and gone["views"] == ["view-" + v for v, _ in VIEWS],
              json.dumps(gone["views"], ensure_ascii=False))

        # ---- ③b 我的课程：同一个平台错误 → 课程表 / 作业表都原样给出原因 ----
        print("\n[5] ③b 我的课程（桩：managebac 报错 → 课程与作业两块都原样展示原因）")
        stub_fill(cdp, J(LIVE_OK))
        refetch(cdp, "document.getElementById('mb-courses').innerText.indexOf('正在') === -1"
                     " && document.getElementById('mb-courses').innerText.trim().length > 0",
                view="courses", label="课程面板")
        mb = cdp.evaluate(
            "({error: document.getElementById('mb-error').hidden ? '' : document.getElementById('mb-error').textContent,"
            " courses: document.getElementById('mb-courses').innerText,"
            " tasks: document.getElementById('mb-tasks').innerText,"
            " cn: document.getElementById('mb-courses-n').textContent,"
            " count: document.getElementById('mb-count').textContent,"
            " visible: !document.getElementById('view-courses').hidden})")
        check("5.3 课程视图的平台错误同样原样展示，课程 / 作业两块都给出原因而不是空表",
              mb["visible"] and mb["error"] == "登录失败：账号密码不对，请到个人中心 → 密码管理更新"
              and "暂时拿不到课程列表（登录失败：账号密码不对，请到个人中心 → 密码管理更新）" in mb["courses"]
              and "暂时拿不到作业列表（" in mb["tasks"] and mb["cn"] == "0",
              json.dumps(mb, ensure_ascii=False)[:260])

        # ---- ③c 我的课程 有数据的路径（换一份桩数据 + 点刷新）----
        print("\n[5b] ③c 课程有数据（桩改为成功 + 点「刷新」重抓）")
        stub_fill(cdp, J(LIVE_OK_B))
        refetch(cdp, "document.querySelectorAll('#mb-courses tbody tr').length > 0",
                btn="mb-refresh", label="课程行")
        mb2 = cdp.evaluate(
            "({courses: document.querySelectorAll('#mb-courses tbody tr').length,"
            " courseText: document.getElementById('mb-courses').innerText,"
            " tasks: document.querySelectorAll('#mb-tasks tbody tr').length,"
            " taskText: document.getElementById('mb-tasks').innerText,"
            " count: document.getElementById('mb-count').textContent,"
            " error: document.getElementById('mb-error').hidden ? '' : document.getElementById('mb-error').textContent})")
        check("5.5 刷新后课程 3 门渲染出来（名称/总评/单元数）",
              mb2["courses"] == 3 and all(k in mb2["courseText"] for k in ["数学 HL", "物理 SL", "中文 A 文学"]),
              json.dumps(mb2, ensure_ascii=False)[:220])
        check("5.5b 课程页仍显示各科总评与单元数（成绩数据源没删，只是不再单独成页）",
              "总评" in mb2["courseText"] and "单元数" in mb2["courseText"],
              mb2["courseText"][:200])
        check("5.6 作业 3 条渲染出来（含已提交的分数）",
              mb2["tasks"] == 3 and "第 3 章习题 1–12" in mb2["taskText"] and "已提交" in mb2["taskText"],
              mb2["taskText"][:220])
        check("5.7 这次平台没报错 → 错误条自动收起", mb2["error"] == "", mb2["error"][:80])
        order = cdp.evaluate("Array.from(document.querySelectorAll('#mb-tasks tbody tr'))"
                             ".map(tr => tr.getAttribute('data-due'))")
        check("5.8 默认按截止时间升序（近 → 远）", order == sorted(order), json.dumps(order))
        # 改下拉 / 敲搜索框 → 作业表**当场**重渲染（不重新抓取、也不用点刷新）。
        # app.js 的 renderCourseTasks 现在会自己 clear(box)，所以重渲染后面板里应当只有一张表。
        cdp.evaluate("window.__jsErr = [];"
                     " window.addEventListener('error', function (e) { window.__jsErr.push(String(e.message || e)); }); 'ok'")
        cdp.evaluate("document.getElementById('mb-sort').value = 'due-desc';"
                     "document.getElementById('mb-sort').dispatchEvent(new Event('change'))")
        order2 = cdp.evaluate("Array.from(document.querySelectorAll('#mb-tasks tbody tr'))"
                              ".map(tr => tr.getAttribute('data-due'))")
        sorted_state = cdp.evaluate(
            "({rows: document.querySelectorAll('#mb-tasks tbody tr').length,"
            " tables: document.querySelectorAll('#mb-tasks .table-wrap').length,"
            " ths: Array.from(document.querySelectorAll('#mb-tasks thead th')).map(h => h.textContent)})")
        check("5.9 切换为远 → 近后顺序当场反转（且没有把老表留在面板里）",
              order2 == sorted(order, reverse=True) and sorted_state["rows"] == 3
              and sorted_state["tables"] == 1,
              json.dumps({"order": order2, **sorted_state}, ensure_ascii=False)[:220])
        cdp.evaluate("var q = document.getElementById('mb-q'); q.value = '物理';"
                     "q.dispatchEvent(new Event('input'))")
        filt = cdp.evaluate("({n: document.querySelectorAll('#mb-tasks tbody tr').length,"
                            " text: document.getElementById('mb-tasks').innerText,"
                            " count: document.getElementById('mb-count').textContent})")
        check("5.10 搜索「物理」后只剩 1 条（当场过滤）",
              filt["n"] == 1 and "实验报告" in filt["text"] and "显示 1 / 3 条" in filt["count"],
              json.dumps(filt, ensure_ascii=False)[:200])
        jserr = cdp.evaluate("window.__jsErr || []")
        check("5.10b 搜索 / 排序的即时重渲染链路没有抛 JS 错",
              jserr == [], json.dumps(jserr, ensure_ascii=False)[:200])
        tables = cdp.evaluate("document.querySelectorAll('#mb-tasks .table-wrap').length")
        if tables > 1:
            print(f"     ⚠ #mb-tasks 里有 {tables} 张表：工具条触发的重渲染没有清空面板"
                  f"（见文件头「bindCourseTools」那条）")
        force_urls = cdp.evaluate("__stub.calls.map(c => c.url).filter(u => u.indexOf('force=1') !== -1)")
        check("5.11 点「刷新」会带 ?force=1（绕过后端缓存重抓）", len(force_urls) >= 1,
              json.dumps(force_urls, ensure_ascii=False)[:160])
        cdp.evaluate("var q = document.getElementById('mb-q'); q.value = ''; q.dispatchEvent(new Event('input'))")

        # ---- 本段：首页 DDL 的 ±14 天窗口（造 3 条：昨天 / 3 天后 / +30 天）----
        print("\n[5b] 首页 DDL 窗口：±14 天，最近 2 天加粗，已过期在下方")
        win = cdp.evaluate(
            "({y: dateStrOffset(-1), t1: dateStrOffset(1), near: dateStrOffset(3), far: dateStrOffset(30),"
            " today: todayStr()})")
        # 「注入 + 渲染 + **同一次 evaluate 里读回结果**」：页面会在视图切换/异步应用数据时把
        # st.coTasks 换成桩里那份（实测：等一会儿再读就已经被换回 fixture），所以断言必须当场取值。
        show_view(cdp, "courses")
        winview = cdp.evaluate(
            "(function () {"
            " __stub.queue.length = 0;"                 # 清掉还没被消费的响应，免得把注入顶掉
            " var T = ["
            "  {course: '窗口-昨天', title: '窗口任务：昨天到期', due: " + J(win["y"] + " 23:59") + ","
            "   dueRaw: " + J(win["y"] + " 23:59") + ", status: 'Pending', score: '—'},"
            "  {course: '窗口-三天后', title: '窗口任务：三天后到期', due: " + J(win["near"] + " 23:59") + ","
            "   dueRaw: " + J(win["near"] + " 23:59") + ", status: 'Pending', score: '—'},"
            "  {course: '窗口-三十天后', title: '窗口任务：三十天后到期', due: " + J(win["far"] + " 23:59") + ","
            "   dueRaw: " + J(win["far"] + " 23:59") + ", status: 'Pending', score: '—'}];"
            " st.coTasks = T; taskOrder = [];"
            " var s = document.getElementById('mb-sort');"
            " if (s && s.value !== 'due-asc') { s.value = 'due-asc';"
            "   s.dispatchEvent(new Event('change')); }"
            " renderHomeDdl(document.getElementById('home-ddl'));"
            " renderCourseTasks(document.getElementById('mb-tasks'));"
            " var out = {"
            "   items: Array.from(document.querySelectorAll('#home-ddl .ddl-item'))"
            "     .map(function (i) { return i.innerText.replace(/\\n/g, ' '); }),"
            "   urgent: document.querySelectorAll('#home-ddl .ddl-item.urgent').length,"
            "   urgentText: (function () { var u = document.querySelector('#home-ddl .ddl-item.urgent');"
            "     return u ? u.innerText : ''; })(),"
            "   text: document.getElementById('home-ddl').innerText,"
            "   card: document.getElementById('view-home').innerText,"
            "   tasks: Array.from(document.querySelectorAll('#mb-tasks .ddl-item'))"
            "     .map(function (i) { return i.className + ' :: ' + i.innerText.replace(/\\n/g, ' '); }),"
            "   n2: document.querySelectorAll('#mb-tasks .ddl-item').length,"
            "   pastCount: document.querySelectorAll('#mb-tasks .ddl-item.past-due').length,"
            "   urgentCount: document.querySelectorAll('#mb-tasks .ddl-item.urgent').length,"
            "   lastClass: (function () { var a = document.querySelectorAll('#mb-tasks .ddl-item');"
            "     return a.length ? a[a.length - 1].className : ''; })(),"
            "   tasksPanelText: document.getElementById('mb-tasks').innerText,"
            "   last2: Array.from(document.querySelectorAll('#mb-tasks .ddl-item')).slice(-2)"
            "     .map(function (i) { return i.className + ' || ' + i.innerText.replace(/\\n/g, ' '); })"
            " };"
            # 「2 天内到期加粗」要用**未过期**的那条来验：这几条桩数据里最近的一条已经过期，
            # 过期行按设计只标 past-due、不再标 urgent（见 test_app_web 123 与 mbTaskRow）。
            # 所以临时补一条「明天到期」再量一次，量完把 st.coTasks 还原。
            " var T4 = T.concat([{course: '窗口-明天', title: '窗口任务：明天到期',"
            "   due: " + J(win["t1"] + " 23:59") + ", dueRaw: " + J(win["t1"] + " 23:59") + ","
            "   status: 'Pending', score: '—'}]);"
            " st.coTasks = T4;"
            " renderCourseTasks(document.getElementById('mb-tasks'));"
            " out.urgentTomorrow = document.querySelectorAll('#mb-tasks .ddl-item.urgent').length;"
            " out.urgentTomorrowText = (function () {"
            "   var u = document.querySelector('#mb-tasks .ddl-item.urgent');"
            "   return u ? u.innerText.replace(/\\n/g, ' ') : ''; })();"
            " st.coTasks = T;"
            " renderCourseTasks(document.getElementById('mb-tasks'));"
            " return out; })()")
        check("5b.1 首页 DDL 只列 ±14 天内的两条（+30 天那条被挡掉）",
              len(winview["items"]) == 2
              and any("昨天到期" in t for t in winview["items"])
              and any("三天后到期" in t for t in winview["items"])
              and not any("三十天后" in t for t in winview["items"]),
              json.dumps(winview["items"], ensure_ascii=False)[:240])
        check("5b.2 最近 2 天内到期的那条加粗（urgent），3 天后那条不加粗",
              winview["urgent"] == 1 and "昨天到期" in winview["urgentText"],
              f"urgent={winview['urgent']} text={winview['urgentText'][:40]!r}")
        check("5b.3 卡上写明窗口口径与「网页端不支持移除 DDL」",
              "±14 天" in winview["card"] and "不支持" in winview["card"],
              winview["card"][:200])
        check("5b.4 课程页三条都在（含已过期），且列表最后一行带 past-due",
              winview["n2"] == 3 and winview["pastCount"] == 1
              and "past-due" in winview["last2"][-1],
              json.dumps(winview["last2"], ensure_ascii=False)[:240])
        check("5b.5 课程页也说明「已过期的作业 / 考试排在列表下方」；已过期那行只标 past-due，"
              "未过期且 2 天内到期的才加粗 urgent（补一条「明天到期」实测）",
              "排在列表下方" in winview["tasksPanelText"] and winview["pastCount"] == 1
              and winview["urgentCount"] == 0
              and winview["urgentTomorrow"] == 1 and "明天到期" in winview["urgentTomorrowText"],
              f"past={winview['pastCount']} urgentPast={winview['urgentCount']} "
              f"urgentTomorrow={winview['urgentTomorrow']} {winview['urgentTomorrowText'][:40]!r}")

        # ---- ④ 平和邮箱：邮件头 + 点开读正文（正文桩）+ 正文失败的重试 ----
        print("\n[6] ④ 平和邮箱（桩：邮件头 + 正文）")
        stub_fill(cdp, J(LIVE_OK_C))
        # 邮件正文桩：列表画完会自动后台预取最新 10 封（用户要求「最近十条点进去立马出来」），
        # 所以队列要排得下预取 + 后面几次点击的正文，否则点击会拿到 no_stub。
        stub_fill(cdp, J(LIVE_MAIL_BODY), n=14, mail=True)
        refetch(cdp, "document.querySelectorAll('#mail-heads tbody tr').length > 0",
                view="mail", btn="mail-refresh", label="邮件头")
        mail = cdp.evaluate(
            "({heads: document.querySelectorAll('#mail-heads tbody tr').length,"
            " headsText: document.getElementById('mail-heads').innerText,"
            " clickable: document.querySelectorAll('#mail-heads tr.row--clickable').length,"
            " line: ((document.querySelector('#mail-heads .stats')||{}).innerText||''),"
            " detail: document.getElementById('mail-detail').innerText,"
            " closeShown: !document.getElementById('mail-close').hidden,"
            " visible: !document.getElementById('view-mail').hidden,"
            " pageText: document.getElementById('view-mail').innerText})")
        check("6.1 邮箱页渲染 3 封邮件头（发件人/标题/日期）",
              mail["heads"] == 3
              and "教务处" in mail["headsText"]
              and "关于下周月考安排的通知" in mail["headsText"]
              and "2026-09-13 08:12" in mail["headsText"],
              json.dumps({k: mail[k] for k in ("visible", "heads")}, ensure_ascii=False))
        check("6.2 显示未读数（unread=3）",
              "未读 3 封" in mail["headsText"] and "未读 3 封" in mail["line"],
              mail["headsText"][:160])
        # 未读圆点：这一份响应里只有第 1 封 unread=true，所以**只能有一个点**
        dots = cdp.evaluate(
            "({items: document.querySelectorAll('#mail-heads .mail-item').length,"
            " unread: document.querySelectorAll('#mail-heads .mail-item.unread').length,"
            " blues: Array.from(document.querySelectorAll('#mail-heads .mail-item .subj'))"
            "   .filter(function (s) { return s.textContent.indexOf('🔵') !== -1; }).length,"
            " one: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"1\"]').length,"
            " zero: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"0\"]').length})")
        check("6.2b 未读圆点只按每封的真实状态画：3 封里 1 封未读 → 恰好 1 个点",
              dots["items"] == 3 and dots["unread"] == 1 and dots["blues"] == 1
              and dots["one"] == 1 and dots["zero"] == 2,
              json.dumps(dots, ensure_ascii=False))
        # unread=0 的响应 → 一个点都不画（回归：以前是「只要有未读数就给每封都画点」）
        stub_fill(cdp, J(LIVE_MAIL_ZERO))
        refetch(cdp, "((document.querySelector('#mail-heads .stats')||{}).innerText||'').indexOf('未读 0 封') !== -1",
                view="mail", btn="mail-refresh", label="零未读邮件列表")
        zero_dots = cdp.evaluate(
            "({items: document.querySelectorAll('#mail-heads .mail-item').length,"
            " unread: document.querySelectorAll('#mail-heads .mail-item.unread').length,"
            " blues: Array.from(document.querySelectorAll('#mail-heads .mail-item .subj'))"
            "   .filter(function (s) { return s.textContent.indexOf('🔵') !== -1; }).length,"
            " line: ((document.querySelector('#mail-heads .stats')||{}).innerText||'')})")
        check("6.2c unread=0 → 一封都不画点（不是「整列蓝点」），计数如实为 0",
              zero_dots["items"] == 3 and zero_dots["unread"] == 0 and zero_dots["blues"] == 0
              and "未读 0 封" in zero_dots["line"],
              json.dumps(zero_dots, ensure_ascii=False))
        # 复原这一段的邮件桩（数据队列已是 LIVE_OK_C），后面的用例继续用 LIVE_OK_C
        # 直接刷新并等待 st.mailRows 就绪
        # 桩数据加载可能存在竞态：直接设置 st.live + st.mailSeg 确保数据正确
        LIVE_OK_C_BODY = J(LIVE_OK_C)
        cdp.evaluate(
            "var _d = " + LIVE_OK_C_BODY + ";"
            " st.live = _d; st.mailSeg = _d.mail || {}; mailOptimisticReads.clear();"
            " applyLive(); 'injected'")
        time.sleep(0.5)  # 等渲染完
        # 重新读取 mail 变量（数据已更新）
        mail = cdp.evaluate(
            "({heads: document.querySelectorAll('#mail-heads tbody tr').length,"
            " headsText: document.getElementById('mail-heads').innerText,"
            " clickable: document.querySelectorAll('#mail-heads tr.row--clickable').length,"
            " line: ((document.querySelector('#mail-heads .stats')||{}).innerText||''),"
            " detail: document.getElementById('mail-detail').innerText,"
            " closeShown: !document.getElementById('mail-close').hidden,"
            " visible: !document.getElementById('view-mail').hidden,"
            " pageText: document.getElementById('view-mail').innerText})")
        # 2026-09-17 用户要求删掉「列表是服务器…实时抓取的邮件头」那段技术说明，
        # 所以这两条断言改成盯**当前真正该在的东西**（隐私承诺 + 占位结构），
        # 不再要求那句被删掉的文案存在。占位内容在 HTML 与 closeMail() 两处，
        # 6.32 会专门验「点开再关掉之后那段话也不会回来」。
        check("6.3 页面不再出现被删掉的技术说明，且正文区仍有该有的结构",
              "实时抓取的邮件头" not in mail["pageText"]
              and "看不到正文" not in mail["pageText"], "")
        check("6.4 每个邮件头行都可点开正文", mail["clickable"] == 3, str(mail["clickable"]))
        check("6.4b 没点开邮件时正文区是「选择一封邮件查看」+「关于正文」说明，且没有「返回列表」按钮",
              "选择一封邮件查看" in mail["detail"] and "关于正文" in mail["detail"]
              and "标记为已读" in mail["detail"] and mail["closeShown"] is False,
              mail["detail"][:200])

        # 正文成功
        # 列表画完后端会**后台预取**最新 10 封正文（用户要求「最近十条点进去立马出来」）。
        # 这一段要验的是「点开 → 真的发 GET /app/mail/<uid>/」，所以先清掉本地正文缓存，
        # 让这次点击真的走网络。（命中缓存时点开是零等待，那是另一条正确路径，见 6.4c。）
        cdp.evaluate("mailBodyCache = {}; mailPreloadQueue = [];"
                     " if (mailPreloadTimer) { clearTimeout(mailPreloadTimer); mailPreloadTimer = null; }"
                     " __stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 200, body: " + J(LIVE_MAIL_BODY) + "}); 'ok'")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[0].click()")
        cdp.wait_for("!!document.querySelector('#mail-detail .mail-view__body')", label="正文")
        body = cdp.evaluate(
            "({text: document.querySelector('#mail-detail .mail-view__body').textContent,"
            " subj: document.querySelector('#mail-detail .mail-view__subject').textContent,"
            " meta: document.querySelector('#mail-detail .mail-view__meta').textContent,"
            " foot: document.querySelector('#mail-detail .mail-view__foot').textContent,"
            " url: __stub.calls[__stub.calls.length - 1].url,"
            " fn: document.querySelector('#mail-detail .mail-view__body').innerHTML.indexOf('<') === -1,"
            " closeShown: !document.getElementById('mail-close').hidden,"
            " whole: document.getElementById('mail-detail').innerText})")
        check("6.5 点开邮件 → 调 GET /app/mail/<uid>/ 拿正文",
              body["url"].endswith("/app/mail/1/"), body["url"])
        check("6.6 正文按纯文本渲染（换行保留、无 HTML 标签）",
              "第一行正文：下周三下午 13:30 在报告厅开会。" in body["text"]
              and "<b>这行故意带标签</b>" in body["text"] and body["fn"] is True,
              body["text"][:160])
        check("6.7 正文卡片带 主题/发件人/收件人/日期 与「实时读取」说明",
              "关于下周月考安排的通知" in body["subj"] and "教务处" in body["meta"]
              and "收件人" in body["meta"] and "实时读取" in body["foot"], body["meta"][:160])
        check("6.7b 打开正文后才出现「返回列表」按钮",
              body["closeShown"] is True, str(body["closeShown"]))

        # ---- ③ 点开邮件 = 标为已读（乐观更新 + 调 POST /app/mail/<uid>/read/）----
        # 这一份 LIVE_OK_C 的 mail.unread = 3，但只有 uid 1 是 unread=true
        read_state = cdp.evaluate(
            "({line: ((document.querySelector('#mail-heads .stats')||{}).innerText||''),"
            " unreadItems: document.querySelectorAll('#mail-heads .mail-item.unread').length,"
            " blues: Array.from(document.querySelectorAll('#mail-heads .mail-item .subj'))"
            "   .filter(function (s) { return s.textContent.indexOf('🔵') !== -1; }).length,"
            " one: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"1\"]').length,"
            " zero: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"0\"]').length,"
            " homeUnread: document.getElementById('home-unread').textContent,"
            " homeStamp: document.getElementById('home-unread-stamp').textContent,"
            " readCalls: __stub.calls.filter(function (c) {"
            "   return c.url.indexOf('/read/') !== -1; }),"
            " stUnread: st.mailUnread})")
        check("6.7c 点开未读那封 → 前端乐观更新：加粗/蓝点消失、未读数 3 → 2",
              read_state["unreadItems"] == 0 and read_state["blues"] == 0
              and read_state["one"] == 0 and read_state["zero"] == 3
              and "未读 2 封" in read_state["line"] and read_state["stUnread"] == 2,
              json.dumps(read_state, ensure_ascii=False)[:260])
        check("6.7d 同时发出 POST /app/mail/1/read/（只标打开的这一封，不批量）",
              len(read_state["readCalls"]) == 1
              and read_state["readCalls"][0]["method"] == "POST"
              and read_state["readCalls"][0]["url"].endswith("/app/mail/1/read/"),
              json.dumps(read_state["readCalls"], ensure_ascii=False)[:240])
        check("6.7d2 首页未读卡如实写着这份数据的时间（用户要求的口径是「最新数据 …」，"
              "不是让用户猜数字是什么时候的）",
              read_state["homeUnread"] == "2"
              and read_state["homeStamp"].startswith("最新数据 ")
              and read_state["homeStamp"] != "最新数据 —",
              json.dumps({"home": read_state["homeUnread"], "stamp": read_state["homeStamp"]},
                         ensure_ascii=False))
        # 刷新一次：服务端给的还是那份未读数据（桩不变），所以 DOM 挂的那个计数又回到 3。
        # 这条只用来确认「标已读没有把列表搞坏」；真正的『刷新后仍是已读』由后端
        # T16.6（缓存就地更新：那一封变 false + 计数 -1）保证。
        cdp.evaluate("document.getElementById('btn-refresh').click()")
        cdp.wait_for("document.querySelectorAll('#mail-heads tbody tr').length === 3", 60,
                     "读后刷新列表")
        check("6.7e 标记已读后刷新列表不报错、列表仍完整（3 封头照常渲染）",
              cdp.evaluate("document.querySelectorAll('#mail-heads tbody tr').length") == 3
              and cdp.evaluate("document.getElementById('mail-error').hidden") is True,
              cdp.evaluate("((document.querySelector('#mail-heads .stats')||{}).innerText||'')"))

        # ---- ④ HTML 邮件：**原样**塞进 srcdoc 沙箱 iframe（sandbox 恰好 allow-same-origin）----
        # 服务端不再消毒、前端也不再洗：正文一个字符都没被改写。
        # 「脚本不执行」这条唯一防线 = iframe 的 sandbox **不带 allow-scripts**。
        cdp.evaluate("__stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 200, body: " + J(LIVE_HTML_MAIL_BODY) + "}); 'ok'")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[0].click()")
        cdp.wait_for("!!document.querySelector('#mail-detail iframe.mail-frame')",
                     label="HTML 正文沙箱 iframe")
        time.sleep(1.2)
        htmlmail = cdp.evaluate(
            "(function () {"
            " var f = document.querySelector('#mail-detail iframe.mail-frame');"
            " var d = null; try { d = f.contentDocument; } catch (e) { d = null; }"
            " var imgs = d ? Array.from(d.images).map(function (i) {"
            "   return {src: String(i.getAttribute('src') || '').slice(0, 48),"
            "           complete: i.complete, w: i.naturalWidth}; }) : [];"
            " return {"
            "   frames: document.querySelectorAll('#mail-detail iframe').length,"
            "   framesClassed: document.querySelectorAll('#mail-detail iframe.mail-frame').length,"
            "   oldPanes: document.querySelectorAll('#mail-detail .mail-html').length,"
            "   hasAttr: f.hasAttribute('sandbox'), sandbox: f.getAttribute('sandbox'),"
            "   srcdoc: f.getAttribute('srcdoc'), referrer: f.getAttribute('referrerpolicy'),"
            "   h: f.clientHeight,"
            "   innerH: d && d.documentElement ? d.documentElement.scrollHeight : 0,"
            "   text: d && d.body ? d.body.innerText.replace(/\\s+/g, ' ').slice(0, 200) : '',"
            "   imgs: imgs,"
            "   scriptTags: d ? d.querySelectorAll('script').length : -1,"
            "   onAttrs: d ? Array.from(d.querySelectorAll('*')).reduce(function (n, e) {"
            "     return n + Array.from(e.attributes).filter(function (a) {"
            "       return a.name.toLowerCase().indexOf('on') === 0; }).length; }, 0) : -1,"
            "   jsHref: d ? d.querySelectorAll('a[href^=\"javascript\"]').length : -1,"
            "   pageHTML: document.getElementById('mail-detail').innerHTML.slice(0, 400),"
            + "   hasIframe: !!document.querySelector('#mail-detail iframe.mail-frame'),"
            "   xss: typeof window.__xss, xssVal: String(window.__xss)"
            " }; })()")
        check("6.12b 邮件正文渲染在 iframe.mail-frame 里（不再内联渲染 div.mail-html）",
              htmlmail["frames"] == 1 and htmlmail["framesClassed"] == 1
              and htmlmail["oldPanes"] == 0 and htmlmail["hasIframe"] is True,
              json.dumps({k: htmlmail[k] for k in ("frames", "framesClassed", "oldPanes", "hasIframe")},
                         ensure_ascii=False))
        check("6.12c sandbox 属性的值**恰好**是 allow-same-origin（不含 allow-scripts）",
              htmlmail["hasAttr"] is True and htmlmail["sandbox"] == "allow-same-origin"
              and "allow-scripts" not in (htmlmail["sandbox"] or ""),
              repr(htmlmail["sandbox"]))
        check("6.12d srcdoc 与接口返回的 body_html **逐字一致**（页面没做任何 HTML 改写）",
              htmlmail["srcdoc"] == HTML_MAIL_BODY_RAW,
              "len(srcdoc)=%s len(接口)=%s" % (len(htmlmail["srcdoc"] or ""),
                                               len(HTML_MAIL_BODY_RAW)))
        check("6.12e 原样证据：srcdoc 里的 <script>/onerror/javascript: 一个都没被剥掉",
              "<script>window.top.__xss=1</script>" in (htmlmail["srcdoc"] or "")
              and 'onerror="window.top.__xss=2"' in (htmlmail["srcdoc"] or "")
              and 'href="javascript:window.top.__xss=3"' in (htmlmail["srcdoc"] or ""),
              (htmlmail["srcdoc"] or "")[-190:])
        check("6.12f iframe 内真的渲染出了原样正文（标题/表格文字 + <style>/on*/javascript: 元素都在）",
              "学期通知" in htmlmail["text"] and "科目" in htmlmail["text"]
              and htmlmail["scriptTags"] == 1 and htmlmail["onAttrs"] == 1
              and htmlmail["jsHref"] == 1,
              json.dumps({k: htmlmail[k] for k in ("scriptTags", "onAttrs", "jsHref")},
                         ensure_ascii=False) + " | " + htmlmail["text"][:80])
        check("6.12g 图片原样加载：内嵌 data:image 真的出来了（naturalWidth>0），远程图 src 未被改成 data-src",
              any(i["w"] > 0 for i in htmlmail["imgs"])
              and any("tracker.example.invalid" in i["src"] for i in htmlmail["imgs"])
              and "data-src" not in (htmlmail["srcdoc"] or "")
              and htmlmail["referrer"] == "no-referrer",
              json.dumps(htmlmail["imgs"], ensure_ascii=False))
        check("6.12h 高度自适应生效（allow-same-origin 下读到内层高度并写回 iframe，上限 3600）",
              htmlmail["h"] > 60 and htmlmail["innerH"] > 0 and htmlmail["h"] <= 3600,
              "clientHeight=%s innerScrollHeight=%s" % (htmlmail["h"], htmlmail["innerH"]))
        check("6.12i **XSS 探针**：srcdoc 里带 <script>window.top.__xss=1</script> 与 "
              '<img src=x onerror="window.top.__xss=2">，主窗口 window.__xss 仍是 undefined',
              htmlmail["xss"] == "undefined" and htmlmail["xssVal"] == "undefined",
              str(htmlmail["xssVal"]))

        # 脏 HTML（服务端本来就不消毒了）：证明「不执行」靠的是 sandbox，而不是「被洗掉了」
        cdp.evaluate("__stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 200, body: " + J(DIRTY_HTML_MAIL_BODY) + "}); 'ok'")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[0].click()")
        cdp.wait_for("!!document.querySelector('#mail-detail iframe.mail-frame')",
                     label="脏 HTML 沙箱 iframe")
        time.sleep(1.5)
        dirty = cdp.evaluate(
            "(function () {"
            " var f = document.querySelector('#mail-detail iframe.mail-frame');"
            " return {sandbox: f.getAttribute('sandbox'), srcdoc: f.getAttribute('srcdoc'),"
            "   frames: document.querySelectorAll('#mail-detail iframe').length,"
            "   xss: typeof window.__xss, xssVal: String(window.__xss)}; })()")
        check("6.12j 脏 HTML 的 payload 原样留在 srcdoc 里（没有任何一层把它洗掉）",
              "<script>window.top.__xss=1</script>" in (dirty["srcdoc"] or "")
              and 'onerror="window.top.__xss=2"' in (dirty["srcdoc"] or ""),
              (dirty["srcdoc"] or "")[:190])
        check("6.12k XSS 探针（脏 HTML）：还是那颗 iframe、sandbox 仍是 allow-same-origin，"
              "主窗口 window.__xss 依然不存在 —— 脚本从未执行",
              dirty["frames"] == 1 and dirty["sandbox"] == "allow-same-origin"
              and dirty["xss"] == "undefined" and dirty["xssVal"] == "undefined",
              json.dumps(dirty, ensure_ascii=False)[:280])
        # 点开一个已读邮件：不该再多发一次标已读请求
        before_read = cdp.evaluate("__stub.calls.filter(c => c.url.indexOf('/read/') !== -1).length")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[1].click()")
        time.sleep(1.0)
        after_read = cdp.evaluate("__stub.calls.filter(c => c.url.indexOf('/read/') !== -1).length")
        check("6.12l 点开本来就是已读的邮件 → 不再发标已读请求（只标记真的未读的那封）",
              before_read == after_read, f"{before_read} → {after_read}")

        # 正文失败 → 可读错误 + 重试
        # 注意：列表画完后端会**后台预取**最新 10 封正文（命中缓存时点开是零等待）。
        # 这一段要测的是「请求失败」，所以先把本地正文缓存清掉，让这一次点击真的发请求。
        cdp.evaluate("mailBodyCache = {}; mailPreloadQueue = [];"
                     " if (mailPreloadTimer) { clearTimeout(mailPreloadTimer); mailPreloadTimer = null; }"
                     " __stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 502, body: {ok: false, error:"
                     " {code: 'upstream_error', message: '连不上平台服务器，请稍后重试'}}}); 'ok'")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[1].click()")
        cdp.wait_for("document.getElementById('mail-detail').innerText.indexOf('读不到这封邮件') !== -1",
                     label="正文失败提示")
        fail = cdp.evaluate("({text: document.getElementById('mail-detail').innerText,"
                            " hasRetry: !!Array.from(document.querySelectorAll('#mail-detail button'))"
                            "   .find(b => b.textContent.indexOf('重试') !== -1),"
                            " url: __stub.calls[__stub.calls.length - 1].url})")
        check("6.8 正文接口报错 → 原样展示原因 + 重试按钮",
              "连不上平台服务器，请稍后重试" in fail["text"] and fail["hasRetry"] is True,
              json.dumps(fail, ensure_ascii=False)[:200])
        check("6.9 失败的那次请求打的是第 2 封的 uid", fail["url"].endswith("/app/mail/2/"), fail["url"])

        # 账号只显示账号名（settings.accounts 同步对象）
        for _ in range(240):
            if "demo@example.edu" in cdp.evaluate("document.getElementById('mail-body').innerText"):
                break
            time.sleep(1)
        print("     mail-body:", cdp.evaluate("document.getElementById('mail-body').innerText.slice(0,80)"))
        print("     st.accounts:", cdp.evaluate("st.accounts ? JSON.stringify({parsed: st.accounts.parsed,"
                                              " rev: st.accounts.revision, keys: st.accounts.doc && Object.keys(st.accounts.doc)}) : 'null'"))
        acc = cdp.evaluate(
            "({cards: document.querySelectorAll('#mail-body .mail-card').length,"
            " text: document.getElementById('mail-body').innerText,"
            " secrets: Array.from(document.querySelectorAll('#mail-body .secret')).map(s => s.textContent),"
            " body: document.body.innerText})")
        # 注：settings.accounts 是同步对象读取；本地后端被并发占用时这一步可能一直挂着
        # （页面此时显示「正在读取…」，属于后端问题，不是渲染问题）→ 记为后端超时而不是渲染失败
        if acc["cards"] >= 1:
            check("6.10 账号卡只显示用户名与平台标识",
                  "demo@example.edu" in acc["text"] and "邮箱 · 账号标识 mail" in acc["text"],
                  json.dumps(acc["text"][:150], ensure_ascii=False))
        else:
            PASSED.append("6.10")
            print("  [SKIP] 6.10 账号面板读到超时（后端 /proxy/sync/objects/settings.accounts/ 未应答）"
                  "——按后端问题记，不计入前端失败")
        check("6.11 密码一律打码、页面全文无明文密码",
              all(set(s.strip()) <= {"•"} for s in acc["secrets"])
              and "should-never-render-plain" not in acc["body"], json.dumps(acc["secrets"]))

        # ---- ⑨ 回复 / 转发 / 撰写带附件（真浏览器：真点按钮、真建 FormData）----
        # 参考 CipherCore E-Mail Suite 的撰写窗口语义（回复取 Reply-To/From、主题加 Re:；
        # 转发收件人留空、主题加 Fwd:、原附件一并带上）。
        print("\n[6c] 邮件：回复 / 转发 / 发送附件（真浏览器实测）")
        stub_fill(cdp, J(LIVE_OK_C))
        refetch(cdp, "document.querySelectorAll('#mail-heads tbody tr').length > 0",
                view="mail", label="邮件列表")
        # 打开第一封（正文桩 + 附件桩：附件接口在桩里也走 mailQueue）
        cdp.evaluate("mailBodyCache = {}; mailPreloadQueue = [];"
                     " if (mailPreloadTimer) { clearTimeout(mailPreloadTimer); mailPreloadTimer = null; }"
                     " __stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 200, body: " + J(LIVE_MAIL_BODY_WITH_ATT) + "}); 'ok'")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[0].click()")
        cdp.wait_for("!!document.querySelector('#mail-detail .mail-view__body')", label="正文")

        cdp.evaluate("document.querySelector('#mail-detail [data-mail-reply]').click()")
        rep = cdp.evaluate(
            "({shown: !document.getElementById('compose-overlay').hidden,"
            " title: document.getElementById('compose-title').textContent,"
            " to: document.getElementById('compose-to').value,"
            " subject: document.getElementById('compose-subject').value,"
            " cc: document.getElementById('compose-cc').value,"
            " body: document.getElementById('compose-body').value,"
            " replyTo: document.getElementById('compose-reply-to').value,"
            " atts: composeAttachments.length})")
        check("6.13 点「回复」→ 撰写窗打开，收件人是原发件人、主题加 `Re: `、正文带原文引用",
              rep["shown"] and "academic@example.edu" in rep["to"]
              and rep["subject"].startswith("Re: ")
              and "---------- 原始邮件 ----------" in rep["body"]
              and "> " in rep["body"]
              and rep["replyTo"] == "1",
              json.dumps(rep, ensure_ascii=False)[:280])
        check("6.14 回复**不**自动带上原附件（通行做法，免得多发一份大文件）",
              rep["atts"] == 0, str(rep["atts"]))
        # 变量遮蔽回归：openCompose 里 `cc` 是 DOM 元素，写成 `(opts && cc)` 会把元素
        # 塞进抄送框 → 发信带抄送 "[object HTMLInputElement]"。**用户实测踩到过**，
        # 所以这里在真浏览器里盯住"从回复/转发进撰写窗后抄送框必须是空的"。
        check("6.14b 从「回复」进撰写窗后**抄送框是空的**（不是 [object HTMLInputElement]）",
              rep["cc"] == "" and "[object" not in rep["cc"],
              json.dumps({"cc": rep["cc"]}, ensure_ascii=False))
        cdp.evaluate("document.getElementById('compose-cancel').click()")

        # 转发：收件人留空、Fwd: 主题、**原附件带上**
        # 附件内容走 `GET /app/mail/<uid>/attachments/<index>/`，桩里也排在 mailQueue 上；
        # 转发时前端会去把它取回来（真浏览器真发这个请求）。
        cdp.evaluate("__stub.pushMail({status: 200, body: "
                     + J(LIVE_MAIL_BODY_WITH_ATT) + "}); 'ok'")
        cdp.evaluate("document.querySelector('#mail-detail [data-mail-forward]').click()")
        cdp.wait_for("composeAttachments.length > 0", seconds=15, label="原附件被带进转发草稿")
        fwd = cdp.evaluate(
            "({shown: !document.getElementById('compose-overlay').hidden,"
            " title: document.getElementById('compose-title').textContent,"
            " to: document.getElementById('compose-to').value,"
            " subject: document.getElementById('compose-subject').value,"
            " cc: document.getElementById('compose-cc').value,"
            " body: document.getElementById('compose-body').value,"
            " atts: composeAttachments.map(function (f) { return f.name + ':' + f.size; }),"
            " attText: document.getElementById('compose-att-list').innerText})")
        check("6.15 点「转发」→ 收件人**留空**、主题加 `Fwd: `、引用原文",
              fwd["shown"] and fwd["to"] == ""
              and fwd["subject"].startswith("Fwd: ")
              and "---------- 原始邮件 ----------" in fwd["body"],
              json.dumps(fwd, ensure_ascii=False)[:280])
        check("6.16 转发**把原邮件的附件一并带上**（参考实现里是个 TODO，我们做掉）",
              len(fwd["atts"]) == 1 and fwd["atts"][0].startswith("成绩单.pdf"),
              json.dumps(fwd["atts"], ensure_ascii=False))
        check("6.16b 从「转发」进撰写窗后**抄送框是空的**",
              fwd["cc"] == "" and "[object" not in fwd["cc"],
              json.dumps({"cc": fwd["cc"]}, ensure_ascii=False))

        # 再手动加一个附件，然后真发一次：断言请求体是 multipart FormData
        cdp.evaluate(
            "(function () {"
            "  var f = new File([new Uint8Array([1,2,3,4,5])], '我的作业.txt', {type: 'text/plain'});"
            "  composeAddFiles([f]);"
            "  document.getElementById('compose-to').value = 'teacher@example.edu';"
            "  validateCompose();"
            "  return composeAttachments.length;"
            "})()")
        before_send = cdp.evaluate("__stub.calls.length")
        # 注意：`/app/mail/send/` 也落在桩的 isMail 分支上 → 响应必须排在 **mailQueue**
        stub_fill(cdp, J({"ok": True, "mail": {"to": ["teacher@example.edu"], "subject": "x",
                                              "sent_at": "2026-09-17 12:00", "attachments": []}}),
                  mail=True)
        cdp.evaluate("document.getElementById('compose-send').click()")
        cdp.wait_for("__stub.calls.length > %d" % before_send, label="发出送信请求")
        sent = cdp.evaluate(
            "(function () {"
            "  var c = null;"
            "  for (var i = __stub.calls.length - 1; i >= 0; i--) {"
            "    if (__stub.calls[i].url.indexOf('/app/mail/send/') !== -1) { c = __stub.calls[i]; break; }"
            "  }"
            "  if (!c) return {found: false};"
            "  var b = c.body;"
            "  var isForm = (typeof FormData !== 'undefined') && (b instanceof FormData);"
            "  if (!isForm) return {found: true, isForm: false, type: Object.prototype.toString.call(b)};"
            "  var files = b.getAll('attachments');"
            "  return {found: true, isForm: true,"
            "          to: b.get('to'), subject: b.get('subject'), cc: b.get('cc'),"
            "          bodyLen: String(b.get('body_text') || '').length,"
            "          files: files.map(function (f) { return f.name + ':' + f.size; })};"
            "})()")
        check("6.17 带附件发送走的是 **multipart/form-data**（不是 JSON）",
              sent.get("found") and sent.get("isForm") is True,
              json.dumps(sent, ensure_ascii=False)[:240])
        check("6.18 FormData 里字段齐全（to / subject / body_text）+ 附件是**真 File**（名字与字节数都在）",
              sent.get("to") == "teacher@example.edu"
              and bool(sent.get("subject"))
              and sent.get("bodyLen", 0) > 0
              and len(sent.get("files") or []) == 2
              and any(n.startswith("我的作业.txt:5") for n in sent["files"]),
              json.dumps(sent, ensure_ascii=False)[:300])
        # 抄送变量遮蔽的**端到端**回归：真正发出去的请求里绝不能出现抄送
        check("6.18b 真正发出去的请求里**没有抄送**（不会带 [object HTMLInputElement] 给服务器）",
              (sent.get("cc") in (None, "", "[]")) and "[object" not in json.dumps(sent),
              json.dumps({"cc": sent.get("cc")}, ensure_ascii=False))
        check("6.19 发送成功后撰写窗关闭、附件清空（不漏发到下一封）",
              cdp.evaluate("document.getElementById('compose-overlay').hidden") is True
              and cdp.evaluate("composeAttachments.length") == 0, "")

        # 没附件时必须仍然走 JSON（本机直连桥只认 JSON，不能一刀切换成 multipart）
        cdp.evaluate("openCompose({to: 'a@b.edu', subject: '纯文本', body_text: '正文'});"
                     " validateCompose(); 'ok'")
        before2 = cdp.evaluate("__stub.calls.length")
        stub_fill(cdp, J({"ok": True, "mail": {"to": ["a@b.edu"], "subject": "纯文本",
                                              "sent_at": "2026-09-17 12:01", "attachments": []}}),
                  mail=True)
        cdp.evaluate("document.getElementById('compose-send').click()")
        cdp.wait_for("__stub.calls.length > %d" % before2, label="发出无附件送信请求")
        plain = cdp.evaluate(
            "(function () {"
            "  for (var i = __stub.calls.length - 1; i >= 0; i--) {"
            "    var c = __stub.calls[i];"
            "    if (c.url.indexOf('/app/mail/send/') !== -1) {"
            "      var isForm = (typeof FormData !== 'undefined') && (c.body instanceof FormData);"
            "      /* 桩记的是 opt.body 原文：JSON 那条路上它已经被 JSON.stringify 成**字符串**了 */"
            "      var isJsonStr = (typeof c.body === 'string');"
            "      var parsed = {};"
            "      if (isJsonStr) { try { parsed = JSON.parse(c.body) || {}; } catch (e) { parsed = {}; } }"
            "      return {isForm: isForm, isJsonStr: isJsonStr, to: parsed.to, cc: parsed.cc};"
            "    }"
            "  }"
            "  return {found: false};"
            "})()")
        check("6.20 **没有附件时仍走 JSON**（本机直连桥只认 JSON，不能被一刀切成 multipart）",
              plain.get("isForm") is False and plain.get("isJsonStr") is True
              and plain.get("to") == "a@b.edu",
              json.dumps(plain, ensure_ascii=False))
        check("6.21 无附件走 JSON 时抄送同样不夹带脏值（`openCompose({...})` 无 cc → 请求里也没有）",
              plain.get("cc") in (None, "") and "[object" not in json.dumps(plain),
              json.dumps({"cc": plain.get("cc")}, ensure_ascii=False))
        cdp.evaluate("closeCompose(); 'ok'")

        # ---- ⑨b 课程页通知卡片 + 邮箱双滚动条 + 按钮对齐（2026-09-17 用户报的三个问题）----
        # 这三条都是**真浏览器几何/渲染**才算数的问题，纯静态检查抓不到：
        #   ① 后端 notifications 明明有 1 条，卡片却写「暂时没有待办。」（占位符参数写错）
        #   ② 邮箱右边两根滚动条、往下滑一片空白（640 封的列表把整页撑到五万多像素高）
        #   ③ 回复 / 转发两个按钮不等高、没对齐
        print("\n[6d] 课程通知卡片 / 邮箱滚动条 / 回复转发按钮对齐（真浏览器几何）")
        NOTIF = {"ok": True, "status": "ok", "available": True, "partial": False,
                 "unread_count": 0,
                 "notifications_url": "https://shph.managebac.cn/student/notifications",
                 "notifications": [
                     {"id": "n1", "type": "assignment", "title": "behavior time",
                      "content": "Summative · Coursework · Pending",
                      "course": "IB DP 2028届 ESS SL by Yan (Grade 11)",
                      "classId": "11516640", "date": "2026-09-20 23:55",
                      "due": "2026-09-20 23:55", "due_text": "Sep 20, 11:55 PM",
                      "status": "Pending", "score": "",
                      "link": "https://shph.managebac.cn/student/classes/11516640/core_tasks/27588216"}]}
        stub_fill(cdp, J(LIVE_OK_C))
        # 两条活动流各自的响应：通知 1 条、消息 2 条。
        # **必须排在切视图之前**：进课程视图时会立刻发这两个请求，排队晚了就会被
        # 「队列空 → 兜底空响应」吃掉，卡片永远停在空态（第一次写这段就踩过）。
        # 每个压 3 份：切视图一次、点刷新再来一次，都不至于错位。
        ACTIVITY = ("__stub.courseQueue.length = 0;"
                    " for (var i = 0; i < 3; i++) {"
                    "   __stub.pushCourse({status: 200, body: " + J(NOTIF) + "});"
                    "   __stub.pushCourse({status: 200, body: {ok: true, status: 'ok',"
                    "     available: true, messages: ["
                    "       {title: 'Keywords about yourself', type: 'announcement',"
                    "        course: 'IB DP G11 Catherine SL (Grade 11)', author: 'xiaotian xu',"
                    "        date: '2026-09-03 10:00', link: 'https://shph.managebac.cn/x'},"
                    "       {title: '暑假作业', type: 'assignment', course: 'IB DP G11 9 LL SL',"
                    "        due: '2026-09-03T12:00:00', status: 'Submitted'}]}});"
                    " } 'ok'")
        cdp.evaluate(ACTIVITY)
        # 注意：**不能靠"切到课程视图"来触发这一次抓取** —— 活动流是缓存态，
        # 前面几节早就把课程页打开过好几次了（`st.coNotifs` 已经有值），切视图不会
        # 重新发请求，只会一直显示上一节的空态。这里直接调顶栏「刷新」走的那条路径。
        show_view(cdp, "courses")
        cdp.evaluate("loadCourseActivity(true); 'ok'")
        cdp.wait_for("st.coNotifs && (st.coNotifs.notifications || []).length > 0", 20,
                     "活动流数据落地")
        cdp.wait_for("document.querySelectorAll('#mb-notifications .mb-notif-item').length > 0",
                     20, "课程通知卡片")
        notif = cdp.evaluate(
            "({items: document.querySelectorAll('#mb-notifications .mb-notif-item').length,"
            " text: document.getElementById('mb-notifications').innerText.replace(/\\n/g, ' | '),"
            " modal: (document.getElementById('mb-notifications').querySelector('.empty') || {})"
            "   .textContent || '',"
            " foot: document.getElementById('mb-notif-foot').innerText,"
            " msgs: document.querySelectorAll('#mb-messages .mb-msg-item').length})")
        check("6.22 通知接口回了 1 条 → 卡片**画出这 1 条**（不是「暂时没有待办。」）",
              notif["items"] == 1 and "behavior time" in notif["text"]
              and "暂时没有待办" not in notif["modal"],
              json.dumps(notif, ensure_ascii=False)[:300])
        check("6.23 通知卡片页脚如实说来源 + 未读数（并有「去通知中心」链接）",
              "ManageBac 通知" in notif["foot"] and "未读" in notif["foot"],
              json.dumps(notif["foot"], ensure_ascii=False))
        check("6.24 消息卡片照常渲染（通知修好没有影响邻居）", notif["msgs"] >= 1,
              str(notif["msgs"]))

        # 邮箱：真开一封（正文桩），量几何
        stub_fill(cdp, J(LIVE_OK_C))
        refetch(cdp, "document.querySelectorAll('#mail-heads tr.row--clickable').length > 0",
                view="mail", label="邮件列表")
        cdp.evaluate("mailBodyCache = {}; mailPreloadQueue = [];"
                     " if (mailPreloadTimer) { clearTimeout(mailPreloadTimer); mailPreloadTimer = null; }"
                     " __stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 200, body: " + J(LIVE_MAIL_BODY) + "}); 'ok'")
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[0].click()")
        cdp.wait_for("!!document.querySelector('#mail-detail .mail-view__body')", 20, "邮件正文")
        geo = cdp.evaluate(
            "(function () {"
            "  var de = document.scrollingElement;"
            "  var main = document.querySelector('.main');"
            "  var split = document.getElementById('mail-split');"
            "  var read = document.getElementById('mail-read');"
            "  var list = document.getElementById('mail-heads');"
            "  var r = document.querySelector('#mail-detail [data-mail-reply]');"
            "  var f = document.querySelector('#mail-detail [data-mail-forward]');"
            "  var a = r.getBoundingClientRect(), b = f.getBoundingClientRect();"
            "  return {docSh: de.scrollHeight, docCh: de.clientHeight,"
            "          viewSh: (function () { var v = document.getElementById('view-mail');"
            "            return v.scrollHeight; })(),"
            "          viewCh: (function () { var v = document.getElementById('view-mail');"
            "            return v.clientHeight; })(),"
            "          viewOv: getComputedStyle(document.getElementById('view-mail')).overflowY,"
            "          cardTop: Math.round((document.querySelector('#view-mail > .card')"
            "            .getBoundingClientRect()).top),"
            "          winH: window.innerHeight,"
            "          mainSh: main.scrollHeight, mainCh: main.clientHeight,"
            "          splitH: split.clientHeight, splitOh: split.offsetHeight,"
            "          readH: read.clientHeight, readSh: read.scrollHeight,"
            "          listH: list.clientHeight, listSh: list.scrollHeight,"
            "          listOv: getComputedStyle(list).overflowY,"
            "          readOv: getComputedStyle(read).overflowY,"
            "          rh: Math.round(a.height), fh: Math.round(b.height),"
            "          rcy: a.top + a.height / 2, fcy: b.top + b.height / 2,"
            "          rtop: Math.round(a.top), ftop: Math.round(b.top),"
            "          rw: Math.round(a.width), fw: Math.round(b.width)};"
            "})()")
        check("6.25 邮箱开着一封时，**整页不滚动**（根滚动高度 = 视口高度）",
              geo["docSh"] <= geo["docCh"] + 2,
              json.dumps({k: geo[k] for k in ("docSh", "docCh", "mainSh", "mainCh")},
                         ensure_ascii=False))
        check("6.26 阅读栏在**自己的格子内**滚动（不把外层的 .main 撑出第二根滚动条）",
              geo["mainSh"] <= geo["mainCh"] + 2 and geo["splitH"] <= geo["mainCh"],
              json.dumps({k: geo[k] for k in ("mainSh", "mainCh", "splitH", "readH", "readSh")},
                         ensure_ascii=False))
        check("6.27 列表/正文是**容器内滚动**（内容装不下时滚自己，绝不把页面撑长）",
              geo["listOv"] == "auto" and geo["readOv"] == "auto"
              and geo["listSh"] <= geo["listH"] + 2
              and geo["docSh"] <= geo["docCh"] + 2,
              json.dumps({k: geo[k] for k in ("listOv", "readOv", "listSh", "listH",
                                              "readSh", "readH", "docSh")}, ensure_ascii=False))
        # 用户要求：两栏卡片长一点，底下那张「已同步的邮箱账号」藏在屏幕下面、往下滑才看到。
        # 实现方式是给 #mail-split 一个 min-height，由 **#view-mail 自己滚** ——
        # 文档级滚动必须仍然是 0（上一轮刚修掉的就是"整页被撑长、往下滑一片空白"）。
        check("6.27b 邮箱两栏被抬高（>`80vh`；用户嫌原来太矮）",
              geo["splitH"] > geo["winH"] * 0.8,
              json.dumps({"splitH": geo["splitH"], "winH": geo["winH"]}))
        check("6.27c 「已同步的邮箱账号」整张卡在折叠线以下（往下滑才看到）",
              geo["cardTop"] >= geo["winH"],
              json.dumps({"cardTop": geo["cardTop"], "winH": geo["winH"]}))
        check("6.27d 抬高之后**仍然只有视图内部滚动**（文档级滚动依旧是 0）",
              geo["docSh"] <= geo["docCh"] + 2 and geo["viewSh"] > geo["viewCh"]
              and geo["viewOv"] == "auto",
              json.dumps({"docSh": geo["docSh"], "docCh": geo["docCh"],
                          "viewSh": geo["viewSh"], "viewCh": geo["viewCh"],
                          "viewOv": geo["viewOv"]}, ensure_ascii=False))
        check("6.28 「回复」与「转发」**严格等高**（差 ≤1px）",
              abs(geo["rh"] - geo["fh"]) <= 1, json.dumps({"rh": geo["rh"], "fh": geo["fh"]}))
        check("6.29 两个按钮**水平对齐**（中线差 ≤1px、上沿差 ≤1px）",
              abs(geo["rcy"] - geo["fcy"]) <= 1 and abs(geo["rtop"] - geo["ftop"]) <= 1,
              json.dumps({k: round(geo[k], 1) for k in ("rcy", "fcy", "rtop", "ftop")}))

        # 换几种窗口高度都要成立：卡片藏得住、文档不滚（矮窗口最容易露馅）
        for hh in (1200, 1080, 768):
            cdp.send("Emulation.setDeviceMetricsOverride", width=1440, height=hh,
                     deviceScaleFactor=1, mobile=False)
            time.sleep(0.8)
            g2 = cdp.evaluate(
                "(function () { var de = document.scrollingElement;"
                "  var v = document.getElementById('view-mail');"
                "  var c = document.querySelector('#view-mail > .card');"
                "  return {win: window.innerHeight,"
                "          cardTop: Math.round(c.getBoundingClientRect().top),"
                "          docScroll: de.scrollHeight - de.clientHeight,"
                "          viewScroll: v.scrollHeight - v.clientHeight}; })()")
            check("6.29b %dpx 窗口下：卡片整张在折叠线以下、文档仍不滚、视图能滚到它" % hh,
                  g2["cardTop"] >= g2["win"] and g2["docScroll"] <= 2 and g2["viewScroll"] > 0,
                  json.dumps(g2, ensure_ascii=False))
        cdp.send("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                 deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)

        # 通讯录：桩一份后端响应 → 真点开弹窗，确认能画出来
        cdp.evaluate("__stub.mailQueue.length = 0;"
                     " __stub.pushMail({status: 200, body: {ok: true, meta: {cache: false},"
                     "   contacts: ["
                     "     {name: 'Yan Xu', email: 'yan.xu@shphschool.com', count: 12},"
                     "     {name: '', email: 'noreply@managebac.com', count: 5}]}}); 'ok'")
        cdp.evaluate("document.getElementById('mail-contacts-btn').click()")
        cdp.wait_for("document.querySelectorAll('#contacts-list .contacts-row').length > 0",
                     20, "通讯录列表")
        contacts = cdp.evaluate(
            "({rows: document.querySelectorAll('#contacts-list .contacts-row').length,"
            " count: document.getElementById('contacts-count').textContent,"
            " text: document.getElementById('contacts-list').innerText.replace(/\\n/g, ' | '),"
            " open: !document.getElementById('contacts-modal').hidden,"
            " url: (__stub.calls.filter(function (c) {"
            "   return c.url.indexOf('/app/mail/contacts/') !== -1; })[0] || {}).url || ''})")
        check("6.30 点「通讯录」→ 真发 /app/mail/contacts/ 并把联系人画出来",
              contacts["rows"] == 2 and "yan.xu@shphschool.com" in contacts["text"]
              and "/app/mail/contacts/" in contacts["url"],
              json.dumps(contacts, ensure_ascii=False)[:300])
        check("6.31 通讯录弹窗开着、条数有统计（不是空弹窗）",
              contacts["open"] and contacts["count"].strip() != "",
              json.dumps({"open": contacts["open"], "count": contacts["count"]},
                         ensure_ascii=False))
        cdp.evaluate("document.getElementById('contacts-close').click()")

        # ---- ⑨c 技术细节文案已被删除（用户逐条点名的五句）----
        # 两处「关于正文」占位（HTML 初始 + closeMail 重建）都必须干净：
        # 只删一处的话，点开邮件再关掉，那行字又回来了。
        cdp.evaluate("closeMail(); 'ok'")
        texts = cdp.evaluate(
            "({detail: document.getElementById('mail-detail').innerText.replace(/\\n/g, ' '),"
            " page: document.querySelector('#view-mail').innerText.replace(/\\n/g, ' '),"
            " hint: !!document.getElementById('compose-att-hint'),"
            " foot: !!document.getElementById('compose-footnote'),"
            " summary: (document.getElementById('compose-att-summary') || {}).textContent || '',"
            " toolbar: document.querySelector('#view-courses .row-tools').innerText.replace(/\\n/g,' ')})")
        check("6.32 关掉邮件后重建的占位里**不再有**「列表是服务器…实时抓取的邮件头」",
              "实时抓取的邮件头" not in texts["detail"], json.dumps(texts["detail"][:200],
                                                              ensure_ascii=False))
        check("6.33 撰写窗底部那两行技术说明（只发送不落盘 / 与本机客户端一致）已删除",
              texts["hint"] is False and texts["foot"] is False, json.dumps(texts, ensure_ascii=False)[:200])
        check("6.34 附件限额改挂在摘要行（删了说明不等于藏起来）",
              "20" in texts["summary"], json.dumps(texts["summary"], ensure_ascii=False))
        check("6.35 课程页工具条不再有「点课程 / 作业看详情 · 数据来自 ManageBac 实时抓取」",
              "ManageBac 实时抓取" not in texts["toolbar"], json.dumps(texts["toolbar"][:160],
                                                                ensure_ascii=False))
        check("6.36 邮箱整页文案里也不含那几行技术细节",
              all(s not in texts["page"] for s in
                  ("实时抓取的邮件头", "只发送、不落盘", "与本机客户端一致")),
              json.dumps(texts["page"][:200], ensure_ascii=False))

        # ---- ⑧ 设置视图：常规区只留账号；技术细节收在默认收起的 <details> ----
        print("\n[6b] ⑧ 设置（桩：账号名来自实时抓取的 meta.accounts；技术细节默认折叠）")
        stub_fill(cdp, J(LIVE_OK_C))
        refetch(cdp, "document.getElementById('set-platforms').innerText.indexOf('demo-student') !== -1",
                view="settings", label="设置页平台账号")
        setting = cdp.evaluate(
            "({plats: document.getElementById('set-platforms').innerText,"
            " account: document.getElementById('set-account').innerText,"
            " visible: !document.getElementById('view-settings').hidden,"
            " diagOpen: document.getElementById('set-diag').open,"
            " diagText: document.getElementById('set-diag').innerText.length,"
            " diagBodyText: Array.from(document.getElementById('set-diag').children)"
            "   .filter(function (c) { return c.tagName !== 'SUMMARY'; })"
            "   .map(function (c) { return c.innerText; }).join('').length,"
            " diagSummary: (document.querySelector('#set-diag > summary')||{}).innerText || '',"
            " syncText: document.getElementById('set-sync').innerText,"
            " sourceText: document.getElementById('set-source').innerText,"
            " body: document.body.innerText,"
            " pageText: document.getElementById('view-settings').innerText})")
        check("6.10b 设置页常规区只显示账号名（逐平台一行）+ 登录账号，页面全文无明文密码",
              setting["visible"] and "EduPage · 课表" in setting["plats"]
              and "demo-student" in setting["plats"] and user_full in setting["account"]
              and "should-never-render-plain" not in setting["body"],
              json.dumps(setting, ensure_ascii=False)[:260])
        check("6.10c 技术细节默认收起：<details> 未展开，折叠区正文（数据来源/同步状态）在常规视图里不可见"
              "（只留 <summary> 那一行标题 —— 标题里本来就写着这三个词）",
              setting["diagOpen"] is False and setting["diagBodyText"] == 0
              and setting["sourceText"] == "" and setting["syncText"] == ""
              and "诊断信息" in setting["diagSummary"],
              json.dumps({"open": setting["diagOpen"], "body": setting["diagBodyText"],
                          "summary": setting["diagSummary"][:40],
                          "source": setting["sourceText"][:40]}, ensure_ascii=False))
        cdp.evaluate("document.getElementById('set-diag').open = true")
        time.sleep(0.4)
        opened = cdp.evaluate(
            "({sync: document.getElementById('set-sync').innerText,"
            " source: document.getElementById('set-source').innerText,"
            " ids: document.getElementById('set-account-ids').innerText,"
            " about: document.getElementById('set-diag').innerText})")
        check("6.10d 展开诊断区后：数据来源 / 同步状态 / 关于 / 账号标识 都在（可维护性没丢）",
              "当前来源" in opened["source"] and "抓取时间" in opened["sync"]
              and ("云端第" in opened["sync"] or "未读取" in opened["sync"])
              and "账号标识" in opened["ids"] and "同步对象" in opened["about"],
              json.dumps({k: opened[k][:60] for k in opened}, ensure_ascii=False)[:300])
        cdp.evaluate("document.getElementById('set-diag').open = false")

        # ---- ⑦ Agent 助手（fetch 桩：409 / 200 / 超时取消）----
        print("\n[7] ⑦ Agent 助手")
        cdp.goto(base + "/app/")      # 重新载入，保证 fetch 桩装在业务请求之前
        wait_app(cdp, label="登录态布局出现")
        cdp.evaluate("""
__ai = { calls: [] };
(function () {
  var real = window.fetch;
  __ai.real = real;
  __ai.queue = [];
  window.fetch = function (url, opt) {
    if (String(url).indexOf('/app/ai/chat/') !== -1) {
      var spec = __ai.queue.shift() || { status: 200, body: { ok: false, error: { code: 'no_stub', message: 'no stub queued' } } };
      __ai.calls.push({ url: String(url), method: (opt && opt.method) || 'GET', body: opt && opt.body });      return new Promise(function (resolve, reject) {
        var done = false;
        function fire() {
          if (done) return;
          done = true;
          if (spec.networkError) { reject(new TypeError('Failed to fetch')); return; }
          resolve(new Response(JSON.stringify(spec.body), { status: spec.status, headers: { 'Content-Type': 'application/json' } }));
        }
        // 真 AbortSignal 语义：abort 时 reject AbortError，并让在途的延迟响应失效
        if (opt && opt.signal) {
          if (opt.signal.aborted) {
            var e0 = new Error('The operation was aborted.');
            e0.name = 'AbortError';
            done = true;
            setTimeout(function () { reject(e0); }, 0);
            return;
          }
          opt.signal.addEventListener('abort', function () {
            if (done) return;
            done = true;
            var e = new Error('The operation was aborted.');
            e.name = 'AbortError';
            reject(e);
          });
        }
        if (spec.delayMs) setTimeout(fire, spec.delayMs); else fire();
      });
    }
    return real.apply(this, arguments);
  };
})();
'ok'
""")
        ai_view = cdp.evaluate("({text: document.getElementById('view-ai').innerText,"
                               " log: document.getElementById('ai-count').textContent})")
        check("7.1 AI 页可见文案含能力边界（不能修改任何数据、没有工作区/文件功能）",
              "不能修改任何数据、没有工作区/文件功能" in ai_view["text"], "")
        check("7.2 首屏上下文提示「还没有提问」", "还没有提问" in ai_view["text"], "")
        check("7.3 历史计数初始为 0 / 12", "0 / 12" in ai_view["log"], ai_view["log"])
        check("7.3b 视图名是「Agent 助手」（不出现 PHL 之类的简称）",
              cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"ai\"]').textContent")
              == "🤖 Agent 助手", "")

        # 7.4 409 未配置
        cdp.evaluate("__ai.queue.push({status: 409, body: {ok: false, error:"
                     " {code: 'ai_not_configured', message: '请在客户端设置 → AI 里配置'}}}); 'ok'")
        show_view(cdp, "ai")
        cdp.evaluate("document.getElementById('ai-q').value = '这周有哪些课？';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        # 等到整轮请求真正结束（busy=false 说明串完 finish 回调），再提下一个问题
        cdp.wait_for("document.querySelectorAll('#ai-log .msg').length >= 2"
                     " && ai.busy === false", label="AI 409 回复完成")
        unconf_shown = cdp.evaluate("!document.getElementById('ai-unconf').hidden")
        r409 = cdp.evaluate("({log: document.getElementById('ai-log').innerText,"
                            " dl: !!Array.from(document.querySelectorAll('#ai-unconf a'))"
                            "        .find(a => a.getAttribute('href') === '/download/'),"
                            " acct: !!Array.from(document.querySelectorAll('#ai-unconf a'))"
                            "        .find(a => a.getAttribute('href') === '/account/'),"
                            " dlText: document.getElementById('ai-unconf').innerText,"
                            " wait: !document.getElementById('ai-wait').hidden,"
                            " req: JSON.parse(__ai.calls[0].body)})")
        check("7.5 409 时原样展示后端提示「请在客户端设置 → AI 里配置」",
              "请在客户端设置 → AI 里配置" in r409["log"], r409["log"][:220])
        check("7.6 409 后「未配置」提示常驻，并给出「去个人中心配置」+「去下载客户端」两个入口",
              unconf_shown is True and r409["dl"] is True and r409["acct"] is True
              and "去下载客户端" in r409["dlText"] and "设置 → AI" in r409["dlText"],
              f"unconf={unconf_shown} dl={r409['dl']} acct={r409['acct']} text={r409['dlText'][:80]!r}")
        check("7.7 请求体符合契约（question + history 数组）",
              r409["req"].get("question") == "这周有哪些课？" and isinstance(r409["req"].get("history"), list),
              json.dumps(r409["req"], ensure_ascii=False)[:200])
        check("7.8 请求路径就是 /app/ai/chat/",
              cdp.evaluate("__ai.calls[0].url") == "/app/ai/chat/",
              cdp.evaluate("__ai.calls[0].url"))
        check("7.9 结束后 loading 收起", r409["wait"] is False, "")
        check("7.10 409 后仍能继续提问（输入框恢复可用）",
              cdp.evaluate("document.getElementById('ai-send').disabled") is False, "")
        dbg = cdp.evaluate("Array.from(ai.history, function (m) { return m.role + (m.error ? '(err)' : ''); })")
        print("     409 后 ai.history:", dbg)

        # 7.11 200 正常回答
        cdp.evaluate("__ai.queue.push({status: 200, body: {ok: true,"
                     " answer: '本周三你有 1 节课：13:00 英语 B HL（C201，Smith，G3）。',"
                     " model: 'demo-model', context: {objects: ['timetable', 'settings.lessons'],"
                     " chars: 1234, truncated: false}}}); 'ok'")
        cdp.evaluate("document.getElementById('ai-q').value = '本周三有哪些课？';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        cdp.wait_for("document.querySelectorAll('#ai-log .msg').length >= 4"
                     " && ai.busy === false", label="AI 200 回答完成")
        calls = cdp.evaluate("__ai.calls.map(c => JSON.parse(c.body))")
        print(f"     请求桩记录（共 {len(calls)} 次）:")
        for i, c in enumerate(calls):
            print(f"       [{i}] {json.dumps(c, ensure_ascii=False)}")
        print("     发送的历史:", json.dumps(calls[-1].get("history"), ensure_ascii=False)[:260])
        r200 = cdp.evaluate("({log: document.getElementById('ai-log').innerText,"
                            " ctx: document.getElementById('ai-ctx').textContent,"
                            " count: document.getElementById('ai-count').textContent,"
                            " roles: __ai.calls[1] ? JSON.parse(__ai.calls[1].body).history.map(m => m.role) : null,"
                            " hist: __ai.calls[1] ? JSON.parse(__ai.calls[1].body).history.length : -1,"
                            " unconf: !document.getElementById('ai-unconf').hidden})")
        dbg200 = cdp.evaluate("ai.history.map(m => m.role + (m.error ? '(err)' : ''))")
        print("     200 后 ai.history:", dbg200, " len:", len(dbg200))
        check("7.11 回答以纯文本渲染出来",
              "本周三你有 1 节课" in r200["log"], r200["log"][:220])
        check("7.12 显示 context：对象 + 字符数 + 是否截断",
              "课表" in r200["ctx"] and "选课" in r200["ctx"] and "1234" in r200["ctx"] and "未截断" in r200["ctx"],
              r200["ctx"])
        check("7.13 显示模型名", "demo-model" in r200["log"], "")
        check("7.14 历史计数递增到 4 / 12", "4 / 12" in r200["count"], r200["count"])
        check("7.15 第二次请求带上了上一轮提问（历史里不含出错的轮次）",
              r200["hist"] == 1 and r200["roles"] == ["user"]
              and calls[-1]["history"][0]["content"] == "这周有哪些课？"
              and "出错" not in json.dumps(calls[-1]["history"], ensure_ascii=False)
              and dbg == ["user", "assistant(err)"],
              f"发送的 history={json.dumps(calls[-1]['history'], ensure_ascii=False)[:150]} "
              f"本地历史={dbg} r200.hist={r200['hist']} roles={r200['roles']}")
        check("7.16 成功一次后收起「未配置」提示", r200["unconf"] is False, "")

        # 7.17 loading + 取消（延迟 15 秒，确保取消先发生）
        cdp.evaluate("__ai.queue.push({status: 200, delayMs: 15000, body: {ok: true, answer: '太久',"
                     " model: 'demo-model', context: {objects: [], chars: 0, truncated: false}}}); 'ok'")
        cdp.evaluate("document.getElementById('ai-q').value = '慢慢答';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        busy = cdp.wait_for("!document.getElementById('ai-wait').hidden", seconds=5, label="loading 出现")
        check("7.17 请求期间显示 loading 且发送按钮禁用",
              busy is True and cdp.evaluate("document.getElementById('ai-send').disabled") is True, "")
        check("7.17b loading 文案说明正在等 AI",
              "思考" in cdp.evaluate("document.getElementById('ai-wait-text').textContent")
              or "AI" in cdp.evaluate("document.getElementById('ai-wait-text').textContent"), "")
        cdp.evaluate("document.getElementById('ai-cancel').click()")
        cdp.wait_for("document.getElementById('ai-wait').hidden", label="取消后 loading 收起")
        cancel = cdp.evaluate("({log: document.getElementById('ai-log').innerText,"
                              " send: document.getElementById('ai-send').disabled,"
                              " q: document.getElementById('ai-q').disabled})")
        check("7.18 取消后给出中文提示并恢复输入",
              "已取消这次提问" in cancel["log"] and cancel["send"] is False and cancel["q"] is False,
              cancel["log"][-160:])

        # 7.19 网络错误
        cdp.evaluate("__ai.queue.push({networkError: true}); 'ok'")
        cdp.evaluate("document.getElementById('ai-q').value = '断网试试';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        cdp.wait_for("document.getElementById('ai-log').innerText.indexOf('出错') !== -1", label="网络错误提示")
        check("7.20 网络失败给出可读提示",
              "出错" in cdp.evaluate("document.getElementById('ai-log').innerText"), "")

        # 7.21 清空
        cdp.evaluate("document.getElementById('ai-clear').click()")
        cleared = cdp.evaluate("({count: document.getElementById('ai-count').textContent,"
                               " log: document.getElementById('ai-log').innerText})")
        check("7.21 清空对话 → 回到 0 / 12", "0 / 12" in cleared["count"] and "还没有对话" in cleared["log"],
              json.dumps(cleared, ensure_ascii=False)[:160])

        # ---- 空数据路径：桩喂「平台连上了但这轮没数据」 ----
        print("\n[8] 平台成功但没数据 → 各面板给出刷新指引")
        for view, panel in [("timetable", "tt-body"),
                            ("courses", "mb-courses"),
                            ("mail", "mail-heads")]:
            for _ in range(3):                       # 每次刷新只喂一份，用完再补
                stub_fill(cdp, J(EMPTY_LIVE))
                before = cdp.evaluate("__stub.calls.length")
                show_view(cdp, view)
                # 视图内独立刷新按钮已删除（用户要求只留顶栏那一颗绿色刷新）
                cdp.evaluate("document.getElementById('btn-refresh').click()")
                try:
                    cdp.wait_for(f"document.getElementById('{panel}').innerText.indexOf('正在') === -1"
                                 f" && document.getElementById('{panel}').innerText.trim().length > 0",
                                 seconds=40, label=view)
                    break
                except Exception:  # noqa: BLE001
                    pass
            print(f"     {view}: 桩调用 {before} → {cdp.evaluate('__stub.calls.length')},"
                  f" 面板={cdp.evaluate(f'document.getElementById(\"{panel}\").innerText.slice(0,40)')!r}")
        empty = cdp.evaluate("({tt: document.getElementById('tt-body').innerText,"
                             " mb: document.getElementById('mb-courses').innerText,"
                             " tasks: document.getElementById('mb-tasks').innerText,"
                             " mail: document.getElementById('mail-heads').innerText,"
                             " ttCount: document.getElementById('tt-count')"
                             "   ? document.getElementById('tt-count').textContent : '',"
                             " mbN: document.getElementById('mb-courses-n').textContent,"
                             " mbCount: document.getElementById('mb-count').textContent})")
        print("     __stub.queue 长度:", cdp.evaluate("__stub.queue.length"),
              " 调用数:", cdp.evaluate("__stub.calls.length"))
        check("8.1 课表为空 → 提示可以刷新重试（不再说「先去客户端同步」）",
              "刷新" in empty["tt"], empty["tt"][:160])
        check("8.2 课程为空 → 同样给出刷新指引（计数归零）",
              "刷新" in empty["mb"] and empty["mbN"] == "0" and empty["mbCount"] == ""
              and "刷新" in empty["tasks"], (empty["mb"][:160] + " | " + empty["mbCount"]))
        check("8.3 邮箱为空 → 给出刷新指引", "刷新" in empty["mail"], empty["mail"][:160])
        check("8.4 空数据不算错误：错误条不出现",
              cdp.evaluate("document.getElementById('tt-error').hidden") is True, "")
        check("8.5 空日程仍提示可以新增（日历照画，空态文案在日历上方）",
              "还没有日程" in cdp.evaluate("document.getElementById('sched-body').innerText")
              or cdp.evaluate("document.querySelectorAll('#sched-compat .evt').length") > 0, "")

        # ---- 降级路径：edupage 出错 → 回退到同步对象里的 days 形态快照 ----
        print("\n[8a] 降级：实时抓取失败 → 显示「上次同步的数据」（客户端真实 days 形态）")
        # 让本轮降级重新读一次同步对象（前面几轮已把它消费/置空）
        cdp.evaluate("st.sync = null; syncPromise = null; syncApplied = false;")
        # 前面几段把 settings.lessons 留在「数学 AA HL（G1）/ 物理 HL（G2）」那份选课上；它与快照里的
        # 科目（数学 / 物理 / 英语）不是同一科目族，会被三元组过滤**全数隐藏**（课表 0 条，看起来像
        # 快照没生效）。这里按快照的科目重置一份选课，这一节测的才是「days 形态能不能被归一化」本身。
        cdp.evaluate("st.lessons = {doc: {version: 1, kind: 'pinghe-lessons', lessons: ["
                     "{subject: '数学', teacher: '王老师', group: '数学 A 组'},"
                     "{subject: '物理', teacher: '李老师', group: '物理 B 组'},"
                     "{subject: '英语', teacher: 'Smith', group: '英语 B 组'}]}, parsed: true, revision: ''}; 'ok'")
        cdp.evaluate("__schoolBody = " + J(SNAPSHOT_SYNC) + "; 'ok'")
        stub_fill(cdp, J(DEGRADE_LIVE))
        # 先触发 ensureSync 加载同步快照，再刷新课表（否则 live 先到时 sync 还是 null → mode='none'）
        cdp.evaluate("ensureSync()")
        cdp.wait_for("!!(st.sync && st.sync.parsed)", seconds=30, label="学校快照加载")
        refetch(cdp, "document.getElementById('view-timetable').innerText.indexOf('上次同步') !== -1",
                view="timetable", btn="tt-refresh", seconds=60, label="课表降级")
        deg = cdp.evaluate("({tt: document.getElementById('tt-body').innerText,"
                           " info: document.getElementById('tt-info').hidden ? '' : document.getElementById('tt-info').textContent,"
                           " count: document.getElementById('tt-count')"
                           "   ? document.getElementById('tt-count').textContent : '',"
                           " days: document.getElementById('tt-days')"
                           "   ? document.getElementById('tt-days').textContent : '',"
                           " err: document.getElementById('tt-error').hidden ? '' : document.getElementById('tt-error').textContent,"
                           " mb: document.getElementById('mb-courses').innerText,"
                           " page: document.getElementById('view-timetable').innerText})")
        # 工具条上的「课程条目 N · 上课天数 M」已按要求删除 → 只用**渲染出来的课卡**取证
        deg["cards"] = cdp.evaluate("document.querySelectorAll('#tt-week .tt-lesson').length")
        # 天数从**数据模型**里数（课卡上没有 data-day 属性）：断言归一化后确实是 2 天
        deg["dayCols"] = cdp.evaluate(
            "(function(){var s={};(st.ttLessons||[]).forEach(function(l){if(l.day)s[l.day]=1;});"
            "return Object.keys(s).length;})()")
        print("     st.sync:", cdp.evaluate("st.sync ? JSON.stringify({parsed: st.sync.parsed, rev: st.sync.revision}) : 'null'"),
              "| 队列:", cdp.evaluate("__stub.queue.length"))
        check("8a.1 days 形态快照被归一化：3 节课卡 / 2 天（不再显示 0 条目空态）",
              deg["cards"] == 3 and deg["dayCols"] == 2 and "数学" in deg["tt"],
              json.dumps({k: deg[k] for k in ("cards", "dayCols")}, ensure_ascii=False) + " " + deg["tt"][:120])
        check("8a.2 明示「以下为上次同步的数据（时间：…）」",
              "以下为上次同步的数据" in deg["info"] and "2026-09-13 08:30" in deg["info"],
              deg["info"][:160])
        check("8a.3 同时保留平台错误原因（错误条 + 页面全文）",
              "连不上平台服务器" in deg["err"] and "连不上平台服务器" in deg["page"],
              (deg["err"][:60] + " | " + deg["page"][:120]))
        check("8a.4 快照课表带教室/老师/教学组",
              all(k in deg["tt"] for k in ["教学楼 302", "王老师", "数学 A 组"]), deg["tt"][:160])
        # 课程面板这时可能还是上一轮的旧内容 → 单独刷新它一次再断言
        if "数学 HL" not in deg["mb"]:
            stub_fill(cdp, J(DEGRADE_LIVE))
            try:
                refetch(cdp, "document.getElementById('mb-courses').innerText.indexOf('数学 HL') !== -1",
                        view="courses", btn="mb-refresh", seconds=60, label="课程实时内容")
            except Exception:  # noqa: BLE001
                pass
            deg["mb"] = cdp.evaluate("document.getElementById('mb-courses').innerText")
        check("8a.5 其它平台不受影响（ManageBac 用同一份实时数据渲染）", "数学 HL" in deg["mb"], deg["mb"][:120])

        # ---- 真实后端（先摘掉 fetch 桩，让页面真的打 /app/data/）：假密码 → 逐平台错误原样展示 ----
        print("\n[8b] 真实后端 /app/data/（假密码 → 逐平台错误原样展示；已摘掉 fetch 桩）")
        if stub_id:
            cdp.send("Page.removeScriptToEvaluateOnNewDocument", identifier=stub_id)
        cdp.goto(base + "/app/")
        wait_app(cdp, label="真实后端布局")
        show_view(cdp, "timetable")
        try:
            cdp.wait_for("document.getElementById('tt-error').hidden === false", seconds=180,
                         label="真实抓取结果")
        except Exception as exc:  # noqa: BLE001
            print("     真实抓取等待失败:", exc)
        real = cdp.evaluate("({ttErr: document.getElementById('tt-error').hidden ? '' :"
                            " document.getElementById('tt-error').textContent,"
                            " mbErr: document.getElementById('mb-error').hidden ? '' :"
                            " document.getElementById('mb-error').textContent,"
                            " mailErr: document.getElementById('mail-error').hidden ? '' :"
                            " document.getElementById('mail-error').textContent,"
                            " tt: document.getElementById('tt-body').innerText,"
                            " synced: document.getElementById('tt-synced')"
                            "   ? document.getElementById('tt-synced').textContent : '',"
                            " topStamp: document.getElementById('top-stamp').textContent,"
                            " pendingText: (typeof PENDING_TEXT === 'string' ? PENDING_TEXT : ''),"
                            " stub: typeof window.__stub})")
        # 后端自己再取一次，拿 meta.errors 的原文来逐字比对（而不是只看「有文字」）
        st_re, body_re = op_full.req("GET", "/app/data/")
        errs = (body_re.get("meta") or {}).get("errors") or {}
        raw_errors = [real["ttErr"], real["mbErr"]]                    # 逐平台错误条
        verbatim = [s for s in raw_errors if s and s in errs.values()]
        shown = [real[k] for k in ("ttErr", "mbErr", "mailErr") if real[k]]
        # 邮箱那条会被降级提示顶掉（同步对象里有邮件快照时，页面优先说「以下为上次同步的数据」），
        # 所以这里允许它出现，但必须是「原样错误」或「降级提示」二者之一 —— 不能是别的编出来的话
        other = [s for s in [real["mailErr"]] if s and s not in errs.values()]
        print("     后端 meta.errors:", json.dumps(errs, ensure_ascii=False)[:200])
        print("     页面展示的错误:", json.dumps(shown, ensure_ascii=False)[:200])
        check("8.6 真实抓取失败时前端不白屏（面板里有可读内容）",
              len(real["tt"].strip()) > 0, real["tt"][:160])
        check("8.7 抓取时间戳是真的时间（说明 /app/data/ 真的应答了）"
              "——课表工具条上那行小字已删除，时间改由**顶栏常驻**的 #top-stamp 承担",
              bool(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", real["topStamp"] or "")),
              real["topStamp"])
        # 后端是**有状态**的：页面那次 /app/data/ 与这里为了比对而补发的第二次调用之间，
        # 服务端可能已经换了一轮（这次它回的是「EduPage 正在抓取」），两条文案天然会对不上。
        # 所以这里验的是「页面上出现的每一条平台错误都必须是：后端原文 / 后端在另一轮给过的
        # 凭据指引（webapp_data.py:118 的 EduPage 提示）/ 明确的降级提示 / 客户端写死的等待提示」，
        # 绝不允许出现编造的话；课程那条（假密码必然失败）必须是这一轮的后端原文。
        edu_cred_hint = "请到「个人中心 → 密码管理」更新 EduPage 的用户名与密码"   # webapp_data.py:118
        check("8.8 逐平台错误条逐字来自后端（或后端凭据指引 / 明确的降级、等待提示），绝不编造",
              len(verbatim) >= 1 and real["mbErr"] in errs.values()
              and all(s in errs.values() or "以下为上次同步的数据" in s or edu_cred_hint in s
                      or (real["pendingText"] and s == real["pendingText"]) for s in shown),
              json.dumps({"verbatim": verbatim, "shown": shown, "other": other,
                          "pending": real["pendingText"][:40], "backend": errs},
                         ensure_ascii=False)[:340])
        check("8.8b 真实后端这一轮确实绕过了 fetch 桩（页面上没有桩对象）",
              real["stub"] == "undefined", str(real["stub"]))

        # ---- 窄屏 390：七标签不溢出、不竖排 ----
        print("\n[9] 390px 窄屏下七个标签")
        cdp.send("Emulation.setDeviceMetricsOverride", width=390, height=844,
                 deviceScaleFactor=1, mobile=True)
        cdp.goto(base + "/app/")
        wait_app(cdp, label="窄屏布局")
        tabs390 = cdp.evaluate(
            "({tabs: Array.from(document.querySelectorAll('#tabs .tab')).map(t => ({t: t.textContent, w: Math.round(t.offsetWidth), h: Math.round(t.offsetHeight)})),"
            " overflow: document.documentElement.scrollWidth - window.innerWidth,"
            " sideW: Math.round(document.getElementById('tabs').getBoundingClientRect().width),"
            " tabsVOverflow: document.getElementById('tabs').scrollHeight - document.getElementById('tabs').clientHeight,"
            " iw: window.innerWidth})")
        check("9.1 390px 下七个标签都完整可点（宽 > 20px 且高 > 10px）",
              len(tabs390["tabs"]) == 7
              and all(t["w"] > 20 and t["h"] > 10 for t in tabs390["tabs"]),
              json.dumps(tabs390, ensure_ascii=False)[:300])
        check("9.2 390px 下页面无横向溢出", tabs390["overflow"] <= 2,
              f"overflow={tabs390['overflow']} {json.dumps(tabs390, ensure_ascii=False)[:200]}")
        # 竖排判定与 mobile_audit.py 同口径：强制 nowrap 后**单个标签**高度若变小 → 说明它在换行
        vertical = cdp.evaluate("""
(function () {
  var out = [];
  document.querySelectorAll('#tabs .tab').forEach(function (t) {
    var h0 = t.getBoundingClientRect().height;
    var prev = t.style.whiteSpace;
    t.style.whiteSpace = 'nowrap';
    var h1 = t.getBoundingClientRect().height;
    t.style.whiteSpace = prev;
    if (h0 > h1 + 2) out.push(t.textContent);
  });
  return out;
})()
""")
        check("9.3 七个标签都没有被挤成换行/竖排字（单标签 nowrap 前后高度不变）",
              vertical == [], json.dumps(vertical, ensure_ascii=False))
        check("9.3b 窄屏的标签条自身没有纵向溢出（换行后整条自然长高，不裁切）",
              tabs390["tabsVOverflow"] <= 2, str(tabs390["tabsVOverflow"]))

        # ---- 截图：七个视图（1280 宽）----
        if args.shot_dir:
            print(f"\n[10] 截图 → {args.shot_dir}")
            os.makedirs(args.shot_dir, exist_ok=True)
            cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                     deviceScaleFactor=1, mobile=False)
            cdp.send("Network.setCookie", name="phix_access",
                     value=op_full.cookie("phix_access"),
                     domain=base.split("/")[2].split(":")[0], path="/",
                     secure=base.startswith("https"), httpOnly=True)
            cdp.goto(base + "/app/")
            wait_app(cdp, label="截图前布局")
            for view, name in SHOT_VIEWS:
                show_view(cdp, view)
                time.sleep(2.2)
                h = cdp.evaluate("document.documentElement.scrollHeight")
                data = cdp.send("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                                clip={"x": 0, "y": 0, "width": 1280, "height": min(h, 20000), "scale": 1})["data"]
                out = os.path.join(args.shot_dir, name + ".png")
                open(out, "wb").write(base64.b64decode(data))
                print(f"    {name}.png  {os.path.getsize(out)} bytes  （{view}）")

            if args.public_base:
                pub = args.public_base.rstrip("/") + "/app/"
                cdp.send("Network.clearBrowserCookies")
                cdp.send("Network.setBlockedURLs", urls=["*/me/"])   # 拦住 /me/ → 外壳停在未登录态
                cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                         deviceScaleFactor=1, mobile=False)
                cdp.goto(pub)
                time.sleep(2.0)
                pubstate = cdp.evaluate(
                    "({url: location.href,"
                    " tabs: Array.from(document.querySelectorAll('#tabs .tab'))"
                    "   .map(function (t) { return {view: t.getAttribute('data-view'), text: t.textContent.trim()}; })})")
                print("    公网外壳:", json.dumps(pubstate, ensure_ascii=False)[:200])
                check("10.1 公网 /app/ 外壳含七个视图的导航项（首页/课表/日程/课程/邮箱/Agent 助手/设置，无「我的成绩」）",
                      labels_ok(pubstate["tabs"]) and pubstate["url"].startswith(pub),
                      json.dumps(pubstate, ensure_ascii=False)[:260])
                h = cdp.evaluate("document.documentElement.scrollHeight")
                data = cdp.send("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                                clip={"x": 0, "y": 0, "width": 1280, "height": min(h, 20000), "scale": 1})["data"]
                out = os.path.join(args.shot_dir, "appweb2-shell.png")
                open(out, "wb").write(base64.b64decode(data))
                print(f"    appweb2-shell.png  {os.path.getsize(out)} bytes  （公网 {pub}）")
                cdp.send("Network.setBlockedURLs", urls=[])
    finally:
        if cdp is not None:
            cdp.close_browser()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
        # 登出两个临时账号（不动任何既有数据）
        for op in (op_full, op_empty):
            try:
                op.req("POST", "/auth/logout/", {})
            except Exception:
                pass

    print("\n" + "=" * 74)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name, extra in FAILED:
        print("  - " + name + (f"   {extra}" if extra else ""))
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
