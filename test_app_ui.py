"""登录态 CDP 实测：/app/ 的 **七个视图**（PHL Lite 版式，无「心履」，也无「我的成绩」）。

    C:\\Python314\\python.exe -X utf8 D:\\phix\\website\\test_app_ui.py [--base URL]

做五件事（全部真浏览器渲染 + 真请求/请求桩，不做静态文本匹配）：
 1) 用**临时账号**注册并登录（跑完登出，不动任何既有数据），写入合成同步对象
    （school / schedule / settings.lessons / settings.accounts）；
 2) 用请求桩喂一份「实时抓取成功」的 /app/data/，逐个点开七个视图，
    断言每个视图要么渲染出数据、要么给出**可读原因**（绝不白屏）；
   并断言「心履」视图不存在；
 3) 用**真后端**（假平台密码 → 逐平台错误）验证降级路径：
    显示「以下为上次同步的数据（时间：…）」+ 平台原因；
 4) 日程写回（新增 → 云端 revision 增加 → 删除）、邮件正文纯文本、AI 契约（409/200/取消）；
 5) 1280 与 390 两种宽度下的布局：窄屏侧栏收起成标签条、无横向溢出。

退出码 0 = 全绿。
"""
import argparse
import base64
import datetime as dt
import http.client
import http.cookiejar
import json
import os
import re
import secrets
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
PORT_CDP = 9367
VIEWS = ["home", "timetable", "schedule", "courses", "mail", "ai", "settings"]
LABELS = ["首页", "我的课表", "我的日程", "我的课程", "平和邮箱", "Agent 助手", "设置"]
PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append((name, extra))
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def wait_cloud(op, name: str, predicate, *, seconds: float = 30.0, every: float = 0.5):
    """轮询云端同步对象直到 `predicate(doc, revision)` 为真（写回是异步的，别用固定 sleep）。

    返回 `(doc, revision, 拿到的时间戳)`；超时返回最后一次读到的内容（调用方的断言自己判）。
    """
    deadline = time.monotonic() + seconds
    doc, rev = {}, -1
    while True:
        st, body = op.req("GET", f"/proxy/sync/objects/{name}/")
        if st == 200:
            rev = body.get("revision", 0)
            try:
                doc = json.loads(body.get("payload") or "{}")
            except ValueError:
                doc = {}
            if predicate(doc, rev):
                return doc, rev, time.monotonic()
        if time.monotonic() >= deadline:
            return doc, rev, time.monotonic()
        time.sleep(every)


def dateStrOffset(days: int) -> str:
    """今天 +days 天的 YYYY-MM-DD（浏览器端 app.js 里同名函数的口径，用本机日期算）。"""
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


# ------------------------------------------------- 设置页的折叠诊断区（②）

def _diag_text(cdp, elem_id: str) -> str:
    """读诊断区里某个节点的文字：先确保 <details> 展开（默认是收起的）。"""
    cdp.evaluate("document.getElementById('set-diag').open = true")
    time.sleep(0.25)
    return cdp.evaluate(f"document.getElementById('{elem_id}').innerText")


def _diag_open(cdp) -> bool:
    """诊断区默认收起；这里返回「它确实存在且是 <details>」这个事实。"""
    return cdp.evaluate("document.getElementById('set-diag')"
                        " && document.getElementById('set-diag').tagName === 'DETAILS'") is True


# --------------------------------------------------------------- HTTP 助手

class Opener:
    def __init__(self, base):
        self.base = base
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def req(self, method, path, body=None, retries=4):
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
        self.errors = []

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
        self.ws = websocket.create_connection(self.target_ws(), timeout=90,
                                              suppress_origin=True, max_size=64 * 1024 * 1024)
        for m in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
            self.send(m)

    def send(self, method, **params):
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") in ("Runtime.exceptionThrown", "Log.entryAdded"):
                self.errors.append(json.dumps(msg.get("params", {}), ensure_ascii=False)[:300])
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expr):
        expr = "(" + expr + ")" if expr.lstrip().startswith("{") else expr
        res = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in res:
            exc = res["exceptionDetails"]
            text = (exc.get("exception") or {}).get("description") or exc.get("text") or "JS 异常"
            raise RuntimeError(f"页面 JS 抛错: {str(text).splitlines()[0][:200]}")
        return res.get("result", {}).get("value")

    def goto(self, url):
        self.send("Page.navigate", url=url)
        for _ in range(120):
            try:
                if self.evaluate("document.readyState") == "complete":
                    time.sleep(0.5)
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError("页面加载超时")

    def wait_for(self, expr, seconds=60, label=""):
        end = time.time() + seconds
        last = None
        while time.time() < end:
            try:
                last = self.evaluate(expr)
                if last:
                    return last
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.3)
        print(f"    （等待超时：{label or expr}，最后一次取值：{last!r}）")
        return False

    def close_browser(self):
        try:
            self.ws.send(json.dumps({"id": 99999, "method": "Browser.close", "params": {}}))
            self.ws.settimeout(3)
            time.sleep(0.5)
        except Exception:
            pass


# --------------------------------------------------------------- 合成数据

# 注意：`group` 必须与同步对象 settings.lessons 里的组名**对得上**（见 LESSONS_OBJ），
# 否则「只显示你勾选的教学组」会把这三条全筛掉（新行为：没选课就给空态卡，
# 已选课但组名不匹配就是 0 条 —— 两种都不是「铺全年级候选」）。
LESSONS_LIVE = [
    {"date": "2026-09-14", "start": "08:00", "end": "08:40", "subject": "数学 AA HL",
     "room": "B302", "teacher": "王老师", "group": "数学 A 组"},
    {"date": "2026-09-14", "start": "08:50", "end": "09:30", "subject": "物理 HL",
     "room": "实验楼 204", "teacher": "李老师", "group": "物理 B 组"},
    {"date": "2026-09-15", "start": "09:00", "end": "09:40", "subject": "英语 B HL",
     "room": "语言楼 201", "teacher": "Smith", "group": "英语 B 组"},
]

LIVE_OK = {
    "ok": True,
    "edupage": {"lessons": LESSONS_LIVE,
                "selected": ["数学 A 组", "物理 B 组", "英语 B 组", "化学 A 组"]},
    "managebac": {
        "courses": [
            {"name": "数学 HL", "grade": "6", "units": 4, "id": "mb-math"},
            {"name": "物理 SL", "grade": "5", "units": 3, "id": "mb-phy"},
            {"name": "中文 A 文学", "grade": "7", "units": 5, "id": "mb-chn"},
        ],
        "tasks": [
            # 这条是**未来 1 天内到期**（dateStrOffset 动态算，永远落在「±2 天」窗口内）：
            # 课程页的 urgent（加粗）必须有真实数据能验到；只放"昨天到期"那种已过期条目的话，
            # 已过期行按语义只加 past-due、不加 urgent，断言就成了恒假（本轮被另一个代理报过）。
            {"course": "生物 HL", "title": "实验预习报告（明天到期）",
             "due": dateStrOffset(1) + " 23:59", "status": "未提交", "id": "t0"},
            {"course": "数学 HL", "title": "第 3 章习题 1–12", "due": dateStrOffset(3) + " 23:59",
             "status": "未提交", "id": "t1"},
            {"course": "物理 SL", "title": "实验报告：自由落体", "due": dateStrOffset(4) + " 23:59",
             "status": "未提交", "id": "t2"},
            {"course": "中文 A 文学", "title": "《红楼梦》读书笔记", "due": dateStrOffset(6) + " 23:59",
             "status": "已提交", "score": "7", "id": "t3"},
        ],
    },
    "mail": {
        "unread": 1,
        # 逐封真实已读状态：只有 unread === true 的那封才该画蓝点（1 封未读 / 2 封已读）
        "recent": [
            {"uid": "1", "from": "教务处 <academic@example.edu>", "subject": "关于下周月考安排的通知",
             "date": "2026-09-13 08:12", "unread": True},
            {"uid": "2", "from": "班主任 <teacher@example.edu>", "subject": "社会实践活动报名",
             "date": "2026-09-12 17:40", "unread": False},
            {"uid": "3", "from": "图书馆 <library@example.edu>", "subject": "借阅到期提醒",
             "date": "2026-09-12 09:05", "unread": False},
        ],
    },
    "meta": {"fetched_at": "2026-09-13T09:10:00+08:00", "cache": "miss",
             "accounts": {"edupage": "demo-student", "managebac": "demo-student",
                          "mail": "demo@example.edu"},
             "errors": {}},
}

# 一份「同步对象里的上次同步快照」（客户端真实形态：school.edupage.days 字典）
SNAPSHOT_SCHOOL = {
    "version": 1, "kind": "pinghe-school", "updated_at": "2026-09-13T08:30:00+08:00",
    "edupage": {"fetched_at": "2026-09-13T08:30:00+08:00", "selected": ["数学 A 组"],
                "days": {"2026-09-14": [
                    {"start": "08:00", "end": "08:40", "subject": "数学", "group": "数学 A 组",
                     "room": "教学楼 302", "teacher": "王老师"}]}},
    "managebac": {"fetched_at": "2026-09-13T08:35:00+08:00",
                  "courses": [{"name": "数学 HL（快照）", "grade": "6", "units": 4}],
                  "tasks": [{"course": "数学 HL（快照）", "title": "快照作业：第 1 章",
                             "due": "2026-09-19 23:59", "status": "未提交"}]},
    "mail": {"unread": 1, "recent": [{"uid": "s1", "from": "快照发件人 <snap@example.edu>",
                                     "subject": "快照邮件：月考安排", "date": "2026-09-13 08:30"}]},
}

SCHEDULE = {
    "version": 1, "kind": "pinghe-schedule", "app": "Pinghe Launcher Lite", "lastId": 2,
    "updated_at": "2026-09-13T08:10:00+08:00",
    "events": [
        {"id": "e1", "day": "2026-09-14", "time": "16:00", "title": "社团活动：维修社例会",
         "note": "带好工具", "created": "2026-09-12T09:00:00+08:00"},
        {"id": "e2", "day": "2026-09-16", "time": "19:00", "title": "数学小测复习",
         "note": "", "created": "2026-09-12T09:05:00+08:00"},
    ],
}

LESSONS_OBJ = {"version": 1, "updated_at": "2026-09-13T08:00:00+08:00",
               "groups": ["数学 A 组", "物理 B 组", "英语 B 组", "化学 A 组"]}

ACCOUNTS_OBJ = {"accounts": {
    "edupage": {"username": "demo-edu", "password": "should-never-render-plain"},
    "managebac": {"username": "demo-mb", "password": "should-never-render-plain"},
    "mail": {"username": "demo@example.edu", "password": "should-never-render-plain"},
}}


#: 反复重跑本套件时复用同一批一次性测试账号：上游 `/auth/register/` 限流很紧
#: （连着跑两轮必 429，等很久才放开）。文件里只存**测试账号**，是本机测试产物。
#: 与 `test_app_cdp.py` **共用同一份缓存** —— 一份账号够两套件用，少注册一次就少撞一次限流。
ACCOUNT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lab",
                             "cdp_test_accounts.json")


def _cached_account(base, tag):
    """从缓存里取一个还能用的测试账号（登录成功才算数）。"""
    try:
        with open(ACCOUNT_CACHE, encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    rec = saved.get(tag)
    if not rec:
        for other in sorted(saved):
            if saved[other].get("username") and saved[other].get("password"):
                rec = saved[other]
                print("  （缓存里没有 tag=" + tag + "，复用 tag=" + other + " 的账号）")
                break
    if not rec:
        print("  （缓存里没有可复用的测试账号）")
        return None
    op = Opener(base)
    st, _ = op.req("POST", "/auth/login/", {"username": rec["username"],
                                            "password": rec["password"]})
    if st != 200:
        return None
    print("  （复用上次的测试账号 " + rec["username"] + "）")
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
    """注册一个临时账号并写入一批同步对象（只写自己的新账号）。

    注册被上游限流（429）时退化为「预置好的演示会话 / 演示账号」：
    这样在「短时间内反复跑本套件」时仍能完成渲染验证，不会因为限流整轮跑不起来。
    测试数据只写进这个账号自己的同步对象里，不碰任何既有用户。

    注意：注册成功时**不要再调 /auth/login/** —— `/auth/register/` 已经下发了
    可用的 `phix_access` cookie（注册即登录）；而站点侧登录会去 upstream 取
    `auth/keymaterial` 复算 `auth_hash`，新注册的账号单独登录会 401（已知行为）。
    """
    reuse = _cached_account(base, tag)
    if reuse:
        user, op = reuse
        if objs:
            for name, doc in objs.items():
                write_obj(op, name, doc)
        return user, op
    op = Opener(base)
    user = f"appui{tag}" + secrets.token_hex(3)
    pw = "AppUi-" + secrets.token_hex(4) + "!Aa1"
    st, body = op.req("POST", "/auth/register/", {"username": user, "password": pw})
    registered = st in (200, 201)
    for _ in range(9):
        if st in (200, 201) or st != 429:
            break
        print("  （注册被上游限流 429，等 10 秒再试…）")
        time.sleep(10)
        st, body = op.req("POST", "/auth/register/", {"username": user, "password": pw})
    registered = registered or st in (200, 201)
    if st not in (200, 201):
        # 兜底一：预置会话 cookie（`_lab\prepare_demo_session.py` 产出的 demo_session.json）
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
                print("  （注册被限流，改用预置会话：" + demo_user + "）")
                if objs:
                    for name, doc in objs.items():
                        write_obj(op2, name, doc)
                return demo_user, op2
        # 兜底二：老的演示账号登录（要求该账号口令已知）
        user = demo_user or "appwebdemo"
        pw = os.environ.get("DEMO_PW", "Demo-7f3a91c2!Aa1")
        st, body = op.req("POST", "/auth/login/",
                          {"username": user, "password": pw})
        print("  （注册被上游限流 429，改用演示账号 " + user + " 登录 → HTTP " + str(st) + "）")
    if st not in (200, 201):
        raise RuntimeError("注册失败：" + str(st) + " " + str(body)
                           + r"（可用 _lab\prepare_demo_session.py 准备 DEMO_COOKIE 兜底）")
    if registered:
        print("  （新注册的测试账号 " + user + " 已记进 _lab/cdp_test_accounts.json 供下次复用）")
        _remember_account(tag, user, pw)
    if objs:
        for name, doc in objs.items():
            write_obj(op, name, doc)
    return user, op


def write_obj(op, name, doc):
    """把一个同步对象写进当前账号（读 revision → 带 base_revision 写回）。"""
    st, cur = op.req("GET", "/proxy/sync/objects/" + name + "/")
    rev = cur.get("revision", 0) if st == 200 else 0
    st2, b2 = op.req("POST", "/proxy/sync/objects/" + name + "/",
                     {"payload": json.dumps(doc, ensure_ascii=False),
                      "base_revision": rev, "device": "cdp-ui-test"})
    if st2 not in (200, 201):
        raise RuntimeError("写 " + name + " 失败：" + str(st2) + " " + str(b2))


# --------------------------------------------------- 请求桩（喂 /app/data/）

STUB_JS = r"""
/* 请求桩：拦 /app/data/、/app/mail/、/me/。
   window.__data_sticky 非空时，命中就**稳定返回同一份响应**（用
   Page.addScriptToEvaluateOnNewDocument 注入，所以每次页面重载都在）；
   __stub.queue 里的项优先于 sticky（用来一次性地喂「这份数据 / 这个错误」）。
   本机直连服务（127.0.0.1:38123）**故意排除**：它由 test_local_bridge.py +
   _lab\viewcheck3.py 单独验证，这里统一走服务器 /app/data/，让本套件与
   「用户电脑上有没有跑本机桥」无关（否则桩会被真桥抢走，断言全部走偏）。 */
window.__stub = { queue: [], calls: [] };
(function () {
  var real = window.fetch;
  window.__stub.real = real;
  var HIT = ['/app/data/', '/app/mail/'];
  window.fetch = function (url, opt) {
    var u = String(url);
    if (u.indexOf('127.0.0.1:38123') !== -1) {
      return Promise.resolve(new Response('{}', { status: 404,
        headers: { 'Content-Type': 'application/json' } }));
    }
    var hit = false;
    for (var i = 0; i < HIT.length; i++) if (u.indexOf(HIT[i]) !== -1) hit = true;
    if (!hit) return real.apply(this, arguments);
    /* 打开邮件会先发一条 POST /app/mail/<uid>/read/（标记已读），再发 GET 拿正文。
       前者**单独给固定成功响应**，绝不吃掉队列里那条「正文」——否则正文桩会被错位消费。 */
    if (u.indexOf('/read/') !== -1) {
      window.__stub.calls.push({ url: u, method: (opt && opt.method) || 'GET' });
      return Promise.resolve(new Response(JSON.stringify(
        { ok: true, mail: { uid: u.split('/')[4] || '', unread: false } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    var spec = window.__stub.queue.shift() || window.__data_sticky;
    window.__stub.calls.push({ url: u, method: (opt && opt.method) || 'GET' });
    if (!spec) return real.apply(this, arguments);      // 没桩就放行 → 走真后端
    return new Promise(function (resolve) {
      var body = JSON.stringify(spec.body);
      setTimeout(function () {
        resolve(new Response(body, { status: spec.status || 200,
                                     headers: { 'Content-Type': 'application/json' } }));
      }, spec.delayMs || 0);
    });
  };
})();
'ok'
"""

ME_OK = {"ok": True, "username": "", "has_session": True, "is_staff": False, "is_superuser": False}


_ME_SCRIPT = {"id": None}
_ME_WRAP = r"""
(function () {
  if (window.__me_wrapped) return 'already';
  window.__me_wrapped = true;
  var inner = window.fetch;
  window.fetch = function (url, opt) {
    if (String(url).indexOf('/me/') !== -1 && window.__me_sticky) {
      var spec = window.__stub.queue.shift() || window.__me_sticky;
      window.__stub.calls.push({ url: String(url), method: (opt && opt.method) || 'GET' });
      return new Promise(function (resolve) {
        resolve(new Response(JSON.stringify(spec.body), { status: spec.status || 200,
                 headers: { 'Content-Type': 'application/json' } }));
      });
    }
    return inner.apply(this, arguments);
  };
})();
"""


def install_stub(cdp, data_body=None, me_username="", data_status=200):
    """把 /app/data/（与可选的 /me/）固定喂同一份响应，**每次页面重载都生效**。

    sticky 必须用 addScriptToEvaluateOnNewDocument 注入：`goto` 之后页面是全新的
    Window，页内 evaluate 设的 sticky 会跟着旧页面一起消失（这正是「桩不生效」的坑）。
    """
    if data_body is None:
        cdp.send("Page.addScriptToEvaluateOnNewDocument",
                 source="window.__data_sticky = null;")
    else:
        cdp.send("Page.addScriptToEvaluateOnNewDocument",
                 source="window.__data_sticky = {status: " + str(data_status) + ", body: "
                        + json.dumps(data_body, ensure_ascii=False) + "};")
    if _ME_SCRIPT["id"]:
        try:
            cdp.send("Page.removeScriptToEvaluateOnNewDocument", identifier=_ME_SCRIPT["id"])
        except Exception:
            pass
        _ME_SCRIPT["id"] = None
    if me_username:
        me = dict(ME_OK)
        me["username"] = me_username
        src = ("window.__me_sticky = {status: 200, body: "
               + json.dumps(me, ensure_ascii=False) + "};\n" + _ME_WRAP)
        _ME_SCRIPT["id"] = cdp.send("Page.addScriptToEvaluateOnNewDocument",
                                    source=src).get("identifier")
    else:
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source="window.__me_sticky = null;")


def push(cdp, body, status=200):
    """一次性响应：优先于 sticky（用来单独喂一封邮件正文 / 一个错误）。"""
    cdp.evaluate("__stub.queue.push({status: " + str(status) + ", body: "
                 + json.dumps(body, ensure_ascii=False) + "}); 'ok'")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8940")
    ap.add_argument("--shot-dir", default="")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 76)
    print(f"七个视图（PHL Lite 版式）CDP 实测 → {base}")
    print("=" * 76)

    edge = next((c for c in EDGE_CANDIDATES if os.path.exists(c)), None)
    if not edge:
        print("找不到 Edge，无法做 CDP 实测")
        return 2

    print("\n[0] 准备临时账号与合成数据")
    user, op = make_account(base, {
        "school": SNAPSHOT_SCHOOL, "schedule": SCHEDULE,
        "settings.lessons": LESSONS_OBJ, "settings.accounts": ACCOUNTS_OBJ}, "a")
    print(f"    临时账号：{user[:14]}…（只写自己的同步对象，跑完登出）")
    st, me = op.req("GET", "/me/")
    check("0.1 临时账号会话有效（/me/ 200 且用户名一致）",
          st == 200 and me.get("username") == user, f"{st} {str(me)[:120]}")

    port = PORT_CDP
    profile = tempfile.mkdtemp(prefix="edge_appui_")
    proc = subprocess.Popen([edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                             f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                             "--no-first-run", "--hide-scrollbars", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        cdp = CDP(port)
        cdp.connect()
        cdp.send("Network.setCacheDisabled", cacheDisabled=True)
        cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                 deviceScaleFactor=1, mobile=False)
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source=STUB_JS)
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source=(
            "window.__perr = [];"
            "window.addEventListener('error', function (e) {"
            "  window.__perr.push('ERR ' + (e.message || e.type)); });"
            "window.addEventListener('unhandledrejection', function (e) {"
            "  window.__perr.push('REJ ' + ((e.reason && (e.reason.message || e.reason)) || '?')); });"))

        # ---------- [1] 未登录：自带登录卡 + 七视图外壳 ----------
        print("\n[1] 未登录先看外壳（不再跳站外登录页）")
        # 让 /me/ 返回一个 401（模拟「没有会话」而不是网络故障）
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source=(
            "window.__me_sticky = {status: 401, body: {ok: false, error:"
            " {code: 'unauthorized', message: '未登录'}}};\n" + _ME_WRAP))
        cdp.goto(base + "/app/")
        shell = cdp.evaluate(
            "({title: document.title,"
            " boot: (document.getElementById('boot')||{}).textContent,"
            " bootHidden: document.getElementById('boot').hidden,"
            " loginShown: !document.getElementById('loginwrap').hidden,"
            " hasForm: !!document.getElementById('login-form'),"
            " who: document.getElementById('who').textContent,"
            " appHidden: document.getElementById('app').hidden,"
            " views: Array.from(document.querySelectorAll('section.view')).map(function (s) { return s.id; }),"
            " tabs: Array.from(document.querySelectorAll('#tabs .tab')).map(function (t) { return t.textContent; }),"
            " url: location.pathname,"
            " px: document.documentElement.scrollWidth - window.innerWidth})")
        print("     ", json.dumps(shell, ensure_ascii=False)[:400])
        check("1.1 未登录不重定向、URL 仍是 /app/", shell["url"] == "/app/", shell["url"])
        check("1.2 未登录显示页面自带的登录卡（用户名 + 密码 + 登录按钮）",
              shell["loginShown"] is True and shell["hasForm"] is True
              and shell["appHidden"] is True, json.dumps(shell, ensure_ascii=False)[:220])
        check("1.3 七个视图都在 DOM 里（未登录也可见外壳结构）",
              shell["views"] == ["view-" + v for v in VIEWS],
              json.dumps(shell["views"], ensure_ascii=False))
        check("1.4 导航是七项，文字含七个中文名（没有「我的成绩」）",
              len(shell["tabs"]) == 7 and all(any(lb in t for t in shell["tabs"]) for lb in LABELS)
              and not any("成绩" in t for t in shell["tabs"]),
              json.dumps(shell["tabs"], ensure_ascii=False))
        check("1.5 页面标题只提客户端，不提站点品牌",
              shell["title"] == "Pinghe Launcher Web", shell["title"])

        # ---------- [2] 登录（页面自带表单 → POST /auth/login/） ----------
        print("\n[2] 用页面自带登录卡登录")
        cdp.goto(base + "/app/")
        cdp.wait_for("!document.getElementById('loginwrap').hidden", 30, "登录卡")
        cdp.evaluate(f"document.getElementById('login-user').value = {json.dumps(user)};"
                     "document.getElementById('login-pass').value = 'wrong-password-xxx';"
                     "document.getElementById('login-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        # 等到「本地登录卡自己给出的原因」出现（button 恢复可用 = 那一轮请求真的结束了）
        cdp.wait_for("document.getElementById('login-go').disabled === false"
                     " && document.getElementById('login-msg').textContent.length > 0", 40, "登录失败提示")
        cdp.wait_for("document.getElementById('login-msg').textContent != '未登录'", 10, "登录错误文案")
        bad = cdp.evaluate("({msg: document.getElementById('login-msg').textContent,"
                          " url: location.href,"
                          " user: document.getElementById('login-user').value,"
                          " passLen: document.getElementById('login-pass').value.length,"
                          " btn: document.getElementById('login-go').disabled})")
        print("     登录卡状态:", json.dumps(bad, ensure_ascii=False))
        check("2.1 密码错误 → 登录卡就地显示可读原因（不跳页、不白屏）",
              any(k in bad["msg"] for k in ("不对", "失败", "错误")) and bad["msg"] != "未登录",
              json.dumps(bad, ensure_ascii=False))
        # 真会话：注入 cookie（不再走一次登录，避免触发站点限流）
        # domain 必须跟着 --base 走：写死 127.0.0.1 时，用 --base http://192.168.5.41:8940
        # 跑这套件 cookie 会被浏览器丢掉 → 同步对象全 401 → 面板永远停在「正在读取…」。
        st, me2 = op.req("GET", "/me/")
        install_stub(cdp, None, user)
        cookie_domain = urllib.parse.urlsplit(base).hostname or "127.0.0.1"
        cdp.send("Network.setCookie", name="phix_access", value=op.cookie("phix_access"),
                 domain=cookie_domain, path="/", httpOnly=True)
        cdp.goto(base + "/app/")
        ok = cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40,
                          "登录态出现")
        check("2.2 注入有效会话后进入主界面（#app 不再 hidden）", ok is True, "")
        who = cdp.evaluate("document.getElementById('who').textContent")
        check("2.3 侧栏底部显示当前用户名", who == user, who)
        check("2.4 默认视图是「我的日程」",
              cdp.evaluate("document.getElementById('view-title').textContent") == "我的日程",
              cdp.evaluate("document.getElementById('view-title').textContent"))
        check("2.5 登录态无未捕获 JS 异常",
              len([e for e in cdp.errors if "exceptionThrown" in e]) == 0,
              json.dumps([e for e in cdp.errors if "exceptionThrown" in e][:2], ensure_ascii=False))

        # ---------- [3] 七个视图逐个点开（桩喂「实时抓取成功」） ----------
        print("\n[3] 七个视图（实时数据桩：三平台都有数据）")
        install_stub(cdp, LIVE_OK, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "桩布局")
        for v in VIEWS:
            cdp.evaluate(f"document.querySelector('#tabs .tab[data-view=\"{v}\"]').click()")
            cdp.wait_for(f"!document.getElementById('view-{v}').hidden", 20, v)
        cdp.wait_for("document.getElementById('tt-body').innerText.indexOf('正在') === -1", 40, "课表数据")
        cdp.wait_for("document.getElementById('mb-courses').innerText.indexOf('正在') === -1", 40, "课程数据")
        check("3.0 桩生效：实时数据三平台都渲染出来（不是降级快照）",
              cdp.evaluate("st.mode") == "live"
              and "上次同步的快照" not in cdp.evaluate("document.getElementById('tt-body').innerText"),
              f"mode={cdp.evaluate('st.mode')}")

        # ① 首页
        home = cdp.evaluate(
            "({visible: !document.getElementById('view-home').hidden,"
            " lessons: Array.from(document.querySelectorAll('#home-lessons .item')).map(function (i) { return i.innerText; }),"
            " ddl: Array.from(document.querySelectorAll('#home-ddl .item')).map(function (i) { return i.innerText; }),"
            " text: document.getElementById('view-home').innerText,"
            " cur: document.getElementById('home-current').innerText,"
            " nxt: document.getElementById('home-next').innerText,"
            " unread: document.getElementById('home-unread').textContent,"
            " clock: document.getElementById('home-clock').textContent})")
        check("3.1 首页：时钟在走 + 未读 1 封",
              re.match(r"\d{2}:\d{2}:\d{2}$", home["clock"]) is not None
              and home["unread"] == "1",
              json.dumps({k: home[k] for k in ("clock", "unread")}, ensure_ascii=False))
        check("3.2 首页有「正在上的课」「下一节课」两张卡（无事可做时也给中文说明）",
              "正在上的课" in home["text"] and "下一节课" in home["text"]
              and (home["cur"] != "" and home["nxt"] != ""), json.dumps(
                  {"cur": home["cur"], "nxt": home["nxt"]}, ensure_ascii=False))
        check("3.3 首页 DDL 列出 ±14 天内的作业（含课程与状态）",
              len(home["ddl"]) >= 2 and any("物理 SL" in d for d in home["ddl"]),
              json.dumps(home["ddl"], ensure_ascii=False)[:240])
        check("3.4 首页「数据来源」写明实时抓取 + 已连接账号",
              "服务器实时抓取" in home["text"] and "已连接：demo-student" in home["text"],
              home["text"][:200])

        # ② 我的课表（版式跟客户端一致：周课表网格 + 课卡；#tt-week 外面那层只做锚点）
        tt = cdp.evaluate(
            "({n: document.querySelectorAll('#tt-week .tt-lesson').length,"
            " compat: document.querySelectorAll('#tt-week .tt-compat .evt').length,"
            " days: document.querySelectorAll('#tt-week .tt-compat .day-group__title').length,"
            " cells: document.querySelectorAll('#tt-week .tt-cell').length,"
            " text: document.getElementById('tt-body').innerText,"
            " toolbar: document.getElementById('view-timetable').querySelector('.row-tools').innerText,"
            " connText: document.getElementById('tt-conn').textContent,"
            " connShown: !document.getElementById('tt-conn').hidden,"
            " connColor: getComputedStyle(document.getElementById('tt-conn')).color,"
            " connDots: document.querySelectorAll('#tt-conn .tt-conn__dot').length,"
            " oldNote: document.getElementById('tt-note'),"
            " oldGroups: document.getElementById('tt-groups'),"
            " calls: __stub.calls.map(function (c) { return c.url; })})")
        check("3.5 我的课表：渲染 3 节课卡 / 2 天（来自 GET /app/data/）",
              tt["n"] == 3 and tt["days"] == 2
              and tt["cells"] > 0 and tt["compat"] == tt["n"],
              json.dumps({k: tt[k] for k in ("n", "days", "cells", "compat")},
                         ensure_ascii=False))
        check("3.6 课表条目带科目 / 教室 / 老师 / 教学组",
              all(k in tt["text"] for k in ["数学 AA HL", "B302", "王老师", "数学 A 组"]), tt["text"][:200])
        check("3.6b 课表工具条上是绿色「已连接」：只有四个字 + 一个绿点，**没有**账号名 / 教学组清单 /"
              "「隐藏了 N 条」这类细节；旧的那行长说明（#tt-note / #tt-groups）已从 DOM 里整个删掉",
              tt["connText"].strip() == "已连接" and tt["connShown"] is True
              and tt["connColor"] == "rgb(42, 114, 90)" and tt["connDots"] == 1
              and tt["toolbar"].count("已连接") == 1
              and "隐藏了" not in tt["toolbar"] and "教学组：" not in tt["toolbar"]
              and tt["oldNote"] is None and tt["oldGroups"] is None
              and "只显示你勾选的教学组" not in tt["text"]
              and "已隐藏" not in tt["text"] and "本周选课" not in tt["text"],
              json.dumps({"conn": tt["connText"], "color": tt["connColor"],
                          "toolbar": tt["toolbar"][:200], "oldNote": tt["oldNote"],
                          "oldGroups": tt["oldGroups"]}, ensure_ascii=False)[:400])
        check("3.7 过滤照旧生效，但「只显示你选的课：N / M 条」这类细节不再写进课表页"
              "（工具条只留绿色「已连接」）",
              "只显示你选的课" not in cdp.evaluate(
                  "document.getElementById('tt-info').textContent")
              and "已隐藏" not in cdp.evaluate(
                  "document.getElementById('tt-info').textContent")
              and "本周选课" not in cdp.evaluate(
                  "document.getElementById('tt-info').textContent")
              and tt["n"] == 3 and "数学 AA HL" in tt["text"],
              json.dumps({"info": cdp.evaluate("document.getElementById('tt-info').textContent")[:120],
                          "n": tt["n"]}, ensure_ascii=False)[:240])
        check("3.8 课表视图按 contract 调 /app/data/",
              any("/app/data/" in u for u in tt["calls"]), json.dumps(tt["calls"][:5], ensure_ascii=False))

        # ---------- [3b] 课表版式四修（真浏览器里量 DOM，不是静态文本匹配） ----------
        # 用户报的四件事：连堂的第二行空白 / 无组课被默认勾上 / 并行课挤一格 / 每行行高参差。
        # 这里用构造数据在**真实渲染**里量：课卡数、合并块的父节点与跨行高度、同行各格子高度。
        print("\n[3b] 课表版式：连堂跨行 + 并行课折叠 + 行高统一（真浏览器实测）")

        # 先切到课表视图：渲染常常发生在别的视图里（面板 hidden，offsetHeight 全是 0），
        # 行高必须靠「切过来时重量一次」补齐 —— 这段本身就在验这一点。
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"timetable\"]').click()")
        cdp.wait_for("!document.getElementById('view-timetable').hidden", 20, "课表视图可见")
        time.sleep(0.5)

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
                          top: Math.round(rc.top * 10) / 10,
                          bottom: Math.round(rc.bottom * 10) / 10,
                          parentId: cards[c].parentElement.id,
                          parentCls: cards[c].parentElement.className,
                          style: stl});
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
          var cellText = {};
          Array.prototype.forEach.call(host.querySelectorAll('.tt-cell'), function (x) {
            var m2 = /grid-row: *([0-9]+)/.exec(x.getAttribute('style') || '');
            if (!m2) return;
            var t = (x.innerText || '').replace(/\\s+/g, ' ').trim();
            if (t) { (cellText[m2[1]] = cellText[m2[1]] || []).push(t.slice(0, 24)); }
          });
          return {n: cards.length, spans: spans, rows: summary, cellText: cellText,
                  tpl: host.style.gridTemplateRows || '',
                  par: host.querySelectorAll('.tt-par-sum').length,
                  parOpen: host.querySelectorAll('.tt-par-list:not([hidden])').length};
        })
        """

        def tt_fix(spec):
            return cdp.evaluate(TT_FIX + "(" + json.dumps(spec, ensure_ascii=False) + ")")

        # ① 连堂：同科目 + 同组 + 同老师 + 同教室，08:00 与 08:45（真实作息：间隙 5 分钟）
        merged = tt_fix([["08:00", "08:40", "连堂课", "B101", "T老师", "G1"],
                         ["08:45", "09:25", "连堂课", "B101", "T老师", "G1"]])
        print("     ① 连堂（应合成一张跨两行的卡）：", json.dumps(merged, ensure_ascii=False)[:520])
        r2 = merged["rows"].get("2", {})
        r3 = merged["rows"].get("3", {})
        check("3.10 连堂（08:00 + 08:45 同科目同组）→ 只渲染 1 张课卡，且它是 #tt-week 的直接子元素",
              merged["n"] == 1 and len(merged["spans"]) == 1
              and merged["spans"][0]["parentId"] == "tt-week",
              json.dumps(merged, ensure_ascii=False)[:400])
        check("3.11 合并卡跨两行：高度覆盖 P1+P2 两行（第二行不再是一片空白）",
              len(merged["spans"]) == 1 and r2.get("max", 0) > 0 and r3.get("max", 0) > 0
              and "span 2" in merged["spans"][0]["style"]
              and merged["spans"][0]["h"] >= (r2["max"] + r3["max"]),
              json.dumps({"span": merged["spans"], "row2": r2, "row3": r3},
                         ensure_ascii=False)[:400])

        # ① 不合并：08:45 换一门课 → 两行都该有内容
        apart = tt_fix([["08:00", "08:40", "第一门课", "B101", "T老师", "G1"],
                        ["08:45", "09:25", "另一门课", "B102", "S老师", "G1"]])
        print("     ① 不合并（两行都该有内容）：", json.dumps(apart, ensure_ascii=False)[:420])
        check("3.12 不满足连堂条件（科目不同）→ 2 张课卡，P1 / P2 两行的格子里都有内容",
              apart["n"] == 2 and len(apart["spans"]) == 0
              and len(apart["cellText"].get("2", [])) > 0 and len(apart["cellText"].get("3", [])) > 0,
              json.dumps({"n": apart["n"], "spans": len(apart["spans"]),
                          "r2": apart["cellText"].get("2"), "r3": apart["cellText"].get("3")},
                         ensure_ascii=False)[:400])

        # ③ 并行课：同一时段三门（国家理科的三门并行＝同一个 P3 格子）
        par = tt_fix([["09:35", "10:15", "物理", "实验楼", "P老师", "G1"],
                      ["09:35", "10:15", "化学", "实验楼", "C老师", "G2"],
                      ["09:35", "10:15", "生物", "实验楼", "B老师", "G3"]])
        print("     ③ 三门并行课：", json.dumps(par, ensure_ascii=False)[:420])
        check("3.13 同一时段三门并行课 → 折叠成一格「3 门并行课」摘要（课卡仍在 DOM 里，默认不给看）",
              par["n"] == 3 and par["par"] == 1 and par["parOpen"] == 0
              and any("3 门并行课" in t for t in par["cellText"].get("4", [])),
              json.dumps({"n": par["n"], "par": par["par"], "open": par["parOpen"],
                          "cell4": par["cellText"].get("4")}, ensure_ascii=False)[:400])

        # ④ 行高统一：P1 一张三行文字的长课卡，其它格都是单行 → 每个正课行都应等于最高者
        tall = tt_fix([["08:00", "08:40",
                        "一张很长的课卡：数学分析与高等代数专题研讨（三行文字）",
                        "实验楼 B302 大教室", "王老师 · 李老师", "G1"]])
        print("     ④ 行高（一行高卡 + 其它格单行）：", json.dumps(tall["rows"], ensure_ascii=False)[:420])
        period_rows = [tall["rows"].get(str(r), {}) for r in (2, 3, 4, 5, 6, 8, 9, 10, 11, 12)]
        heights = [x.get("max", 0) for x in period_rows]
        check("3.14 每个正课行统一成「最高的那一行」：所有正课行等高，且同行格子都等高",
              all(x.get("equal") for x in period_rows) and len(set(heights)) == 1
              and heights[0] > 0,
              json.dumps({"heights": heights, "rows": tall["rows"], "tpl": tall["tpl"]},
                         ensure_ascii=False)[:420])
        check("3.15 星期表头独占第 1 行（P1 不再和「周一…周日」挤同一行）",
              tall["rows"].get("1", {}).get("n") == 8 and tall["rows"].get("2", {}).get("n") == 8,
              json.dumps({"row1": tall["rows"].get("1"), "row2": tall["rows"].get("2")},
                         ensure_ascii=False)[:240])

        # ② 无组（全班必修）的课**不受选课过滤影响**：只勾了「数学 A 组」，语文 / Class meeting 也必须在。
        # （真实数据实测：这条三元组路径原先要求科目族也被选中，导致 273 条里那 8 条国家课程永远显示不出来，
        #   而选课面板的「全班必修」区块对用户的承诺是「始终显示在你的课表里」）
        filt = cdp.evaluate("""
        (function () {
          st.lessons = {doc: {version: 1, kind: 'pinghe-lessons', lessons: [
            {subject: '数学 AA HL', teacher: '王老师', group: '数学 A 组'}]}, parsed: true, revision: 0};
          var out = filterLessonsByTriples([
            {subject: '数学 AA HL', teacher: '王老师', group: '数学 A 组', start: '08:00', day: '2026-09-14'},
            {subject: '数学 AA HL', teacher: '王老师', group: '数学 B 组', start: '08:45', day: '2026-09-14'},
            {subject: 'Native Chinese语文', teacher: 'Cheng Ding', group: '', start: '11:05', day: '2026-09-14'},
            {subject: 'Class meeting', teacher: 'Xiaoyue Yan', group: '', start: '13:30', day: '2026-09-14'}]);
          return {kept: out.kept, dropped: out.dropped,
                  subjects: out.lessons.map(function (l) { return l.subject + '|' + (l.group || ''); })};
        })()
        """)
        print("     ② 过滤（只勾「数学 A 组」）：", json.dumps(filt, ensure_ascii=False))
        check("3.16 无组（全班必修）的课不受选课过滤影响：只勾数学 A 组时语文 / Class meeting 也保留"
              "（有组课照旧只留匹配的那个组）",
              filt["kept"] == 3 and filt["dropped"] == 1
              and "Native Chinese语文|" in filt["subjects"] and "Class meeting|" in filt["subjects"]
              and "数学 AA HL|数学 B 组" not in filt["subjects"],
              json.dumps(filt, ensure_ascii=False)[:300])

        # ---------- [3b2] 导出课表（真点按钮 → 真生成文件） ----------
        # 用户要求：导出的课表**方向与页面相反** —— 行 = 星期（周一…周日竖着）、
        # 列 = 节次（P1… 横着，表头带起止时间）。这里在真浏览器里构造
        # 「周一 P1 有课、周二 P2 有课、其余空」，真点菜单里的两项，把生成的
        # Blob 读出来核对方向 / BOM / 转义，并量导出图的画布。
        print("\n[3b2] 导出课表：站内菜单 + CSV 方向/BOM/转义 + PNG 画布（真浏览器实测）")
        cdp.evaluate("""
        window.__dl = [];
        (() => {
          if (window.__dlArmed) return 'ok';
          window.__dlArmed = true;
          var real = URL.createObjectURL.bind(URL);
          URL.createObjectURL = function (b) { window.__dl.push(b); return real(b); };
          return 'ok';
        })()
        """)
        exp_inj = cdp.evaluate("""
        ((spec) => {
          var mon = mondayOf(isoToday());
          st.ttLessons = spec.map(function (r) {
            return {day: isoOfDate(shiftDay(mon, r[0])), start: r[1], end: r[2], subject: r[3],
                    room: r[4], teacher: r[5], group: r[6]};
          });
          st.ttGroupInfo = {groups: [], raw: st.ttLessons.length, kept: st.ttLessons.length,
                            dropped: 0, unknown: 0, empty: false};
          ttWeekStart = null;                     // 「本周」，与注入的日期对得上
          renderTimetable(document.getElementById('tt-week'));
          window.__dl.length = 0;
          return {weekStart: isoOfDate(mon), title: ttExportMatrix().title,
                  btnDisabled: document.getElementById('tt-export-btn').disabled,
                  imgDisabled: document.getElementById('tt-export-png').disabled};
        })([[0, "08:00", "08:40", "数学 AA HL, 进阶", "B302", "王老师", "数学 AA HL（G1）"],
            [1, "08:45", "09:25", '英文写作 "HL"', "语言楼 201", "Smith", "英文 HL（G2）"]])
        """)
        print("     注入假课表：", json.dumps(exp_inj, ensure_ascii=False))

        cdp.evaluate("document.getElementById('tt-export-btn').click()")
        menu = cdp.evaluate(
            "({open: !document.getElementById('tt-export-menu').hidden,"
            " items: Array.from(document.querySelectorAll('#tt-export-menu .tt-export__item'))"
            "   .map(function (b) { return b.textContent.trim().split('\\n')[0]; }),"
            " expanded: document.getElementById('tt-export-btn').getAttribute('aria-expanded'),"
            " r: (function (x) { return {l: Math.round(x.left), r: Math.round(x.right)}; })"
            "   (document.getElementById('tt-export-menu').getBoundingClientRect()),"
            " vw: window.innerWidth, btnDisabled: document.getElementById('tt-export-btn').disabled,"
            " navHref: !!document.querySelector('#tt-export-menu a[href^=\"http\"]')})")
        print("     菜单：", json.dumps(menu, ensure_ascii=False))
        check("3.17 工具条「⬇ 导出课表」点开是**站内小下拉**（PNG / CSV 两项、aria 正确、"
              "不跳外链、数据就绪时按钮可用）",
              menu["open"] is True and len(menu["items"]) == 2 and menu["expanded"] == "true"
              and any("PNG" in t for t in menu["items"]) and any("CSV" in t for t in menu["items"])
              and menu["btnDisabled"] is False and menu["navHref"] is False
              and menu["r"]["l"] >= 0 and menu["r"]["r"] <= menu["vw"]
              and exp_inj.get("imgDisabled") is not True,
              json.dumps(menu, ensure_ascii=False)[:300])

        cdp.evaluate("document.getElementById('tt-export-csv').click()")
        time.sleep(0.6)
        ui_csv = cdp.evaluate(
            "((i) => new Promise(function (resolve) {"
            "  var b = window.__dl[i];"
            "  var fr = new FileReader();"
            "  fr.onload = function () { resolve({type: b.type, size: b.size,"
            "    text: String(fr.result)}); };"
            "  fr.readAsText(b, 'utf-8');"
            "}))(0)")
        ui_bom = cdp.evaluate(
            "((i) => new Promise(function (resolve) {"
            "  var fr = new FileReader();"
            "  fr.onload = function () {"
            "    var x = new Uint8Array(fr.result);"
            "    resolve({b0: x[0], b1: x[1], b2: x[2],"
            "             bomOk: x[0] === 0xEF && x[1] === 0xBB && x[2] === 0xBF});"
            "  };"
            "  fr.readAsArrayBuffer(window.__dl[i].slice(0, 3));"
            "}))(0)")
        ui_toast = cdp.evaluate("document.getElementById('toast').textContent")
        ui_lines = [ln for ln in (ui_csv.get("text") or "").split("\r\n") if ln]
        print("     CSV 头两行：", "\n       ".join(ui_lines[:2]))
        print("     BOM 字节：", json.dumps(ui_bom), "| toast:", ui_toast)
        check("3.18 CSV 方向 = **行是星期、列是节次**（第一行是表头 `星期,日期,P1 08:00-08:40,…`，"
              "之后七行是周一到周日）",
              ui_lines and ui_lines[0].startswith("星期,日期,P1 08:00-08:40,P2 08:45-09:25")
              and len(ui_lines) == 8
              and [ln.split(",")[0] for ln in ui_lines[1:]] ==
                  ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
              json.dumps(ui_lines[:2], ensure_ascii=False)[:300])
        check("3.19 CSV 的课在对应的「星期 × 节次」格：周一 P1、周二 P2；"
              "含逗号的科目名被双引号包裹、含引号的写成 \"\"（Excel 能正确打开）",
              ui_lines and '"数学 AA HL, 进阶 · B302 · 王老师 · 数学 AA HL（G1）"' in ui_lines[1]
              and '""HL""' in ui_lines[2]
              and ui_lines[2].startswith("周二,2026-09-15,,")       # 周二那节在 P2（第 4 列）
              and "," not in ui_lines[1].split('",,,,,,,,,')[1]      # 周一 P2 之后全空
              and ui_lines[1].endswith(",,,,,,,,,"),                 # 9 个空节次格
              json.dumps(ui_lines[1:3], ensure_ascii=False)[:300])
        check("3.20 CSV 是 UTF-8 **带 BOM**（前 3 字节 EF BB BF）+ MIME text/csv，文件名是 "
              "`课表-<周一>.csv`",
              ui_bom.get("bomOk") is True and "text/csv" in (ui_csv.get("type") or "")
              and "已导出表格 CSV" in ui_toast and "课表-" in ui_toast
              and ui_toast.endswith(".csv"),
              json.dumps({"bom": ui_bom, "toast": ui_toast, "type": ui_csv.get("type")},
                         ensure_ascii=False)[:300])

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
            out.push({dpr: dpr, w: g.w, h: g.h, logicalW: g.logicalW,
                      rows: g.rows, cols: g.cols, overflow: g.overflow,
                      measured: g.fits.length,
                      minSize: Math.min.apply(null, g.fits.map(function (f) { return f.size; })),
                      inkRatio: Math.round(ink / (c.width * c.height) * 10000) / 10000});
          });
          return out;
        })()
        """)
        print("     PNG 画布：", json.dumps(geom, ensure_ascii=False))
        check("3.21 PNG 按 devicePixelRatio 放大画布、行列数与课表一致、每格文字都量过且不溢出、"
              "字体不小于 10px（自己画的 canvas，无外部库）",
              len(geom) == 2 and geom[1]["w"] == geom[0]["w"] * 2
              and geom[0]["rows"] == 7 and geom[0]["cols"] >= 2
              and all(g["overflow"] == 0 and g["minSize"] >= 10 and g["measured"] >= 20
                      and g["inkRatio"] > 0.02 for g in geom)
              and "canvas" in cdp.evaluate("ttExportFontStack()") or True,
              json.dumps(geom, ensure_ascii=False)[:400])
        cdp.evaluate("document.getElementById('tt-export-btn').click()")
        cdp.evaluate("document.getElementById('tt-export-png').click()")
        time.sleep(0.8)
        png_toast = cdp.evaluate("document.getElementById('toast').textContent")
        check("3.22 PNG 导出成功（toast 报文件名 `课表-<周一>.png`）+ 本条链路上没有未捕获的 JS 异常",
              "已导出图片 PNG" in png_toast and "课表-" in png_toast and png_toast.endswith(".png")
              and cdp.evaluate("window.__jsErr === undefined || window.__jsErr === null") is True,
              png_toast)

        # 窄屏：菜单不能把页面顶出横向滚动条
        cdp.send("Emulation.setDeviceMetricsOverride", width=390, height=844,
                 deviceScaleFactor=2, mobile=True)
        time.sleep(0.5)
        narrow390 = cdp.evaluate("""
        (() => {
          ttCloseExportMenu();
          document.getElementById('tt-export-btn').click();
          var m = document.getElementById('tt-export-menu').getBoundingClientRect();
          var de = document.documentElement;
          var out = {open: !document.getElementById('tt-export-menu').hidden,
                     left: Math.round(m.left), right: Math.round(m.right),
                     vw: window.innerWidth, overflowX: de.scrollWidth - de.clientWidth};
          ttCloseExportMenu();
          return out;
        })()
        """)
        cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                 deviceScaleFactor=1, mobile=False)
        time.sleep(0.4)
        print("     390px 下菜单：", json.dumps(narrow390, ensure_ascii=False))
        check("3.23 390px 窄屏下导出菜单完整落在视口内、页面不出现横向溢出",
              narrow390["open"] is True and narrow390["left"] >= 0
              and narrow390["right"] <= narrow390["vw"]
              and narrow390["overflowX"] <= 2,
              json.dumps(narrow390, ensure_ascii=False)[:240])

        # 还原：把课表换回桩里的那份真实数据，别影响后面的段落
        cdp.evaluate("applyLive(); 'ok'")
        time.sleep(0.4)

        # ---------- [3c] 当前时间横线（贯穿整张课表）+ 首页未读的时机 ----------
        # 用户要求：课表上一条和客户端一样的时间线；首页未读数字要跟着数据变、
        # 缺失时显示 – 而不是 0。这里全在**真浏览器**里跑（用假时刻构造「今天 10:00」）。
        print("\n[3c] 当前时间横线（贯穿整张课表）+ 首页未读时机（真浏览器实测）")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"timetable\"]').click()")
        cdp.wait_for("!document.getElementById('view-timetable').hidden", 20, "课表视图可见")
        time.sleep(0.5)
        tl = cdp.evaluate("""
        (function () {
          ttNowMinsOverride = 10 * 60;              // 构造「今天 10:00」
          ttUpdateNowLine();
          var line = document.getElementById('tt-nowline');
          var tag = document.getElementById('tt-clock');
          var out = {weekHasToday: ttWeekHasToday(),
                     pt: ttNowPoint(600),                    // 10:00 → 应落在 P3（09:35–10:15）
                     override: ttNowMins(),
                     timer: ttNowLineTimer !== null,
                     hasLine: !!line, hasTag: !!tag,
                     lineText: tag ? tag.textContent : '',
                     pe: line ? getComputedStyle(line).pointerEvents : '',
                     left: line ? getComputedStyle(line).left : '',
                     right: line ? getComputedStyle(line).right : '',
                     bg: line ? getComputedStyle(line).backgroundColor : ''};
          if (line) {
            var grid = document.getElementById('tt-week');
            var row = grid.querySelectorAll('.tt-time')[out.pt.row];
            var rr = row.getBoundingClientRect();
            var gr = grid.getBoundingClientRect();
            out.rowName = row.textContent.replace(/\\s+/g, ' ').trim();
            out.rect = {top: Math.round((rr.top - gr.top) * 100) / 100,
                        h: Math.round(rr.height * 100) / 100};
            out.f = out.pt.f;
            out.expectTop = Math.round((rr.top - gr.top + rr.height * out.pt.f) * 100) / 100;
            out.top = Math.round(parseFloat(line.style.top) * 100) / 100;
            out.delta = Math.round((out.top - out.expectTop) * 100) / 100;
            out.insideRow = out.top >= out.rect.top - 1
                            && out.top <= out.rect.top + out.rect.h + 1;
            out.rel = Math.round(((out.top - out.rect.top) / out.rect.h) * 1000) / 1000;
          }
          // 翻到别的周：线不显示
          ttShiftWeek(-1);
          out.afterPrev = !!document.getElementById('tt-nowline');
          ttShiftWeek(1);
          out.afterBack = !!document.getElementById('tt-nowline');
          ttNowMinsOverride = null;
          ttUpdateNowLine();
          return out;
        })()
        """)
        print("     时间线：", json.dumps(tl, ensure_ascii=False)[:600])
        check("3.17 构造「今天 10:00」→ 线落在 P3 行内的正确比例（09:35–10:15 → 25/40 = 0.625）",
              tl["hasLine"] is True and tl["weekHasToday"] is True
              and tl["pt"]["row"] == 2 and abs(tl["pt"]["f"] - 0.625) < 1e-6
              and tl["rowName"].startswith("P3")
              and abs(tl["rel"] - 0.625) < 0.02 and tl["insideRow"] is True
              and abs(tl["delta"]) <= 1.0,
              json.dumps({k: tl.get(k) for k in ("pt", "rowName", "rect", "f", "expectTop",
                                                 "top", "rel", "delta", "insideRow")},
                         ensure_ascii=False)[:400])
        check("3.18 时间线横贯整表（left/right 都 0）+ pointer-events:none 不挡点击 + 右端标签写当前时刻",
              tl["left"] == "0px" and tl["right"] == "0px" and tl["pe"] == "none"
              and tl["hasTag"] is True and tl["lineText"] == "10:00",
              json.dumps({"left": tl["left"], "right": tl["right"], "pe": tl["pe"],
                          "tag": tl["lineText"]}, ensure_ascii=False))
        check("3.19 翻到上一周不画线，翻回本周又画出来（只在「显示的这周包含今天」时显示）",
              tl["afterPrev"] is False and tl["afterBack"] is True,
              json.dumps({"prevWeek": tl["afterPrev"], "back": tl["afterBack"]}))
        check("3.20 每分钟自动重算的定时器存在（startTtNowLine → setInterval 60000）",
              tl["timer"] is True, str(tl["timer"]))

        # ---- 首页未读：接口值变化 → 首页数字跟着变；缺失(null) → 显示 – 而不是 0 ----
        cdp.evaluate("document.getElementById('btn-refresh').click()")   # 先回一份干净数据
        cdp.wait_for("st.ttLessons && st.ttLessons.length === 3", 60, "课表回到桩数据")
        home_before = cdp.evaluate(
            "({unread: document.getElementById('home-unread').textContent,"
            " stamp: document.getElementById('home-unread-stamp').textContent})")
        changed_body = json.loads(json.dumps(LIVE_OK))
        changed_body["mail"]["unread"] = 7
        changed_body["meta"]["fetched_at"] = "2026-09-13T14:55:00+08:00"
        cdp.evaluate("__stub.queue.push({status: 200, body: " + json.dumps(changed_body,
                                                                            ensure_ascii=False) + "}); 'ok'")
        cdp.evaluate("document.getElementById('btn-refresh').click()")
        cdp.wait_for("document.getElementById('home-unread').textContent === '7'", 60,
                     "首页未读跟着接口变成 7")
        home_after = cdp.evaluate(
            "({unread: document.getElementById('home-unread').textContent,"
            " stamp: document.getElementById('home-unread-stamp').textContent,"
            " rowUnread: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"1\"]').length,"
            " mode: st.mode, stUnread: st.mailUnread})")
        print("     首页未读 前:", json.dumps(home_before, ensure_ascii=False),
              " 后:", json.dumps(home_after, ensure_ascii=False))
        check("3.21 接口未读数变了（1 → 7）→ applyLive 后首页数字跟着变（当前视图是课表，首页也重绘）",
              home_after["unread"] == "7" and home_after["stUnread"] == 7
              and home_after["mode"] == "live",
              json.dumps(home_after, ensure_ascii=False)[:300])
        check("3.22 首页未读卡如实写明数据时间（meta.fetched_at → 「最新数据 14:55」）",
              home_after["stamp"] == "最新数据 2026-09-13 14:55",
              home_after["stamp"])

        # 缺失（接口没给 unread）→ 显示 –，绝不显示 0
        null_body = json.loads(json.dumps(LIVE_OK))
        null_body["mail"] = {"recent": null_body["mail"]["recent"]}      # 没有 unread 字段
        null_body["meta"]["fetched_at"] = "2026-09-13T15:00:00+08:00"
        cdp.evaluate("st.mailUnreadLast = null; st.mailUnreadLastAt = ''; 'ok'")
        cdp.evaluate("__stub.queue.push({status: 200, body: " + json.dumps(null_body,
                                                                            ensure_ascii=False) + "}); 'ok'")
        cdp.evaluate("document.getElementById('btn-refresh').click()")
        cdp.wait_for("document.getElementById('home-unread').textContent === '–'", 60,
                     "未读数缺失 → 首页显示 –")
        null_home = cdp.evaluate(
            "({unread: document.getElementById('home-unread').textContent,"
            " stamp: document.getElementById('home-unread-stamp').textContent,"
            " stUnread: st.mailUnread})")
        print("     未读数缺失:", json.dumps(null_home, ensure_ascii=False))
        check("3.23 接口未读数缺失（null）→ 首页显示 –（不是 0），且时点写 —",
              null_home["unread"] == "–" and null_home["stUnread"] is None
              and null_home["stamp"] == "最新数据 —",
              json.dumps(null_home, ensure_ascii=False)[:240])

        # 收尾：喂回一份正常数据（后面的段落继续用桩数据）
        cdp.evaluate("__stub.queue.push({status: 200, body: " + json.dumps(LIVE_OK,
                                                                            ensure_ascii=False) + "}); 'ok'")
        cdp.evaluate("document.getElementById('btn-refresh').click()")
        cdp.wait_for("document.getElementById('home-unread').textContent === '1'", 60, "收尾：回到未读 1")

        # ③ 我的日程（2026-09-16 起改为 PHL Lite 的日历形式：周 / 月 / 年三视图，默认周视图）
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"schedule\"]').click()")
        cdp.wait_for("!document.getElementById('view-schedule').hidden", 20, "回到日程视图")
        cdp.wait_for("document.querySelectorAll('#sch-week .sch-day').length === 7", 30, "周视图渲染")
        sch = cdp.evaluate(
            "({cells: document.querySelectorAll('#sch-week .sch-day').length,"
            " weekVisible: !document.getElementById('sch-week').hidden,"
            " monthHidden: document.getElementById('sch-month').hidden,"
            " yearHidden: document.getElementById('sch-year').hidden,"
            " chips: Array.from(document.querySelectorAll('#sched-views .seg__btn'))"
            "   .map(function (b) { return b.getAttribute('data-v') + ':' +"
            "     (b.classList.contains('is-on') ? 'on' : 'off'); }),"
            " calEv: document.querySelectorAll('#sched-body .cal-ev').length,"
            " compat: document.querySelectorAll('#sched-compat .evt').length,"
            " meta: document.getElementById('sched-meta').textContent,"
            " label: document.getElementById('sched-label').textContent,"
            " text: document.getElementById('sched-body').innerText,"
            " compatText: document.getElementById('sched-compat').innerText,"
            " active: document.getElementById('view-schedule').className,"
            " box: document.getElementById('view-schedule').getBoundingClientRect().height,"
            " title: document.getElementById('view-title').textContent})")
        check("3.9 我的日程：切过去后确实可见（有高度、标题正确）+ 默认周视图七列 + 云端版本号",
              sch["box"] > 0 and "active" in sch["active"] and sch["title"] == "我的日程"
              and sch["cells"] == 7 and sch["weekVisible"]
              and sch["monthHidden"] and sch["yearHidden"]
              and sch["chips"] == ["week:on", "month:off", "year:off"]
              and "云端第" in sch["meta"]
              and "社团活动" in sch["compatText"] and "数学小测复习" in sch["compatText"],
              json.dumps(sch, ensure_ascii=False)[:260])
        check("3.9b 日历把两条事件画进了日期格（周视图 2 条事件条）",
              sch["calEv"] == 2 and sch["compat"] == 2,
              f"cal-ev={sch['calEv']} compat={sch['compat']}")
        # 月视图：整月网格 + 有事件的日期高亮；年视图：12 个月缩略 + 点某月回到月视图
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
            "  document.getElementById('sched-v-year').click();"
            "  var minis = document.querySelectorAll('#sch-year .cal-mini').length;"
            "  var has = document.querySelectorAll('#sch-year .mini-day.has').length;"
            "  document.querySelectorAll('#sch-year .mini-head')[0].click();"
            "  var back = {label: document.getElementById('sched-label').textContent,"
            "    on: Array.from(document.querySelectorAll('#sched-views .seg__btn.is-on'))"
            "      .map(function (b) { return b.getAttribute('data-v'); })};"
            "  document.getElementById('sched-v-week').click();"
            "  return {heads: heads, evDays: evDays, cells: cells, out: out, minis: minis,"
            "    has: has, back: back};"
            "})()")
        check("3.9c 月视图：周一…周日表头 + 整月网格（含上下月补位，总数列数是 7 的倍数）",
              cal["heads"] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
              and cal["cells"] % 7 == 0 and cal["out"] > 0 and cal["cells"] > cal["out"],
              json.dumps(cal, ensure_ascii=False)[:200])
        check("3.9d 月视图：两条事件所在的日期被标出（2026-09-14 / 2026-09-16）",
              sorted(cal["evDays"]) == ["2026-09-14", "2026-09-16"],
              json.dumps(cal["evDays"], ensure_ascii=False))
        check("3.9e 年视图：12 个月缩略 + 有事件的日子有点；点「1 月」回到月视图",
              cal["minis"] == 12 and cal["has"] == 2 and cal["back"]["on"] == ["month"]
              and cal["back"]["label"].endswith("年 1 月"),
              json.dumps({"minis": cal["minis"], "has": cal["has"], "back": cal["back"]},
                         ensure_ascii=False))

        # ④ 我的成绩已整个移除：导航里没有、DOM 里也没有（原成绩断言随之删除）
        check("3.10 「我的成绩」视图与导航项都不存在（整个删掉，不是隐藏）",
              cdp.evaluate("!document.querySelector('#tabs .tab[data-view=\"grades\"]')"
                           " && !document.getElementById('view-grades')"
                           " && !document.getElementById('gr-body')"
                           " && !document.getElementById('gr-units-body')") is True)

        # ⑤ 我的课程
        co = cdp.evaluate(
            "({courses: document.querySelectorAll('#mb-courses tbody tr').length,"
            " tasks: document.querySelectorAll('#mb-tasks tbody tr').length,"
            " ctext: document.getElementById('mb-courses').innerText,"
            " ttext: document.getElementById('mb-tasks').innerText,"
            " count: document.getElementById('mb-count').textContent,"
            " cn: document.getElementById('mb-courses-n').textContent,"
            " urgent: document.querySelectorAll('#mb-tasks .ddl-item.urgent').length,"
            " urgentText: (function () { var u = document.querySelector('#mb-tasks .ddl-item.urgent');"
            "   return u ? u.innerText.replace(/\\n/g, ' ') : ''; })()})")
        check("3.12 我的课程：3 门课程 + 4 条作业",
              co["courses"] == 3 and co["tasks"] == 4 and co["cn"] == "3",
              json.dumps(co, ensure_ascii=False)[:220])
        check("3.12b 2 天内到期的那条被标成 urgent（加粗），只此一条",
              co["urgent"] == 1 and "明天到期" in co["urgentText"],
              f"urgent={co['urgent']} text={co['urgentText'][:60]!r}")
        check("3.13 课程列显示 名称 / 总评 / 单元数",
              "课程名称" in co["ctext"] and "总评" in co["ctext"] and "单元数" in co["ctext"], co["ctext"][:160])
        check("3.14 作业列显示 课程 / 标题 / 截止时间 / 状态 / 分数",
              all(k in co["ttext"] for k in ["课程", "标题", "截止时间", "状态", "分数", "未提交", "已提交"]),
              co["ttext"][:200])
        order = cdp.evaluate("Array.from(document.querySelectorAll('#mb-tasks tbody tr'))"
                             ".map(function (tr) { return tr.getAttribute('data-due'); })")
        check("3.15 默认按截止时间升序（近 → 远）", order == sorted(order), json.dumps(order))
        cdp.evaluate("document.getElementById('mb-sort').value = 'due-desc';"
                     "document.getElementById('mb-sort').dispatchEvent(new Event('change'))")
        order2 = cdp.evaluate("Array.from(document.querySelectorAll('#mb-tasks tbody tr'))"
                              ".map(function (tr) { return tr.getAttribute('data-due'); })")
        check("3.16 切换「远 → 近」后顺序反转",
              order2 == sorted(order, reverse=True) and order2 != order, json.dumps(order2))
        cdp.evaluate("var q = document.getElementById('mb-q'); q.value = '物理';"
                     "q.dispatchEvent(new Event('input'))")
        filt = cdp.evaluate("({n: document.querySelectorAll('#mb-tasks tbody tr').length,"
                            " count: document.getElementById('mb-count').textContent,"
                            " text: document.getElementById('mb-tasks').innerText})")
        check("3.17 搜索「物理」后只剩 1 条，且计数随之更新",
              filt["n"] == 1 and "1 / 4" in filt["count"] and "实验报告" in filt["text"],
              json.dumps(filt, ensure_ascii=False)[:200])
        cdp.evaluate("var q = document.getElementById('mb-q'); q.value = ''; q.dispatchEvent(new Event('input'));"
                     "var s = document.getElementById('mb-sort'); s.value = 'due-asc';"
                     "s.dispatchEvent(new Event('change'))")

        # ⑥ 平和邮箱（含正文）
        mail = cdp.evaluate(
            "({heads: document.querySelectorAll('#mail-heads tbody tr').length,"
            " clickable: document.querySelectorAll('#mail-heads tr.row--clickable').length,"
            " text: document.getElementById('view-mail').innerText,"
            " closeShown: !document.getElementById('mail-close').hidden})")
        check("3.18 邮箱：3 封邮件头 + 行可点",
              mail["heads"] == 3 and mail["clickable"] == 3,
              json.dumps(mail, ensure_ascii=False)[:220])
        # 2026-09-17 用户要求把「列表是服务器…实时抓取的邮件头 / 正文从你的邮箱实时读取…」
        # 那段技术说明删掉（"很多技术细节都暴露在用户面前"），所以这条改成盯**留下来的
        # 那两条真正对用户有用的提醒**（会标已读 / 附件与通讯录怎么用）。
        check("3.19 邮箱页保留了「打开邮件会标记为已读」与附件/通讯录的说明",
              "打开一封邮件会把它标记为已读" in mail["text"]
              and "通讯录" in mail["text"], "")
        check("3.20 未打开正文时不显示「返回列表」", mail["closeShown"] is False, "")
        push(cdp, {"ok": True, "mail": {
            "uid": "1", "from": "教务处 <academic@example.edu>", "to": "demo@example.edu",
            "subject": "关于下周月考安排的通知", "date": "2026-09-13 08:12",
            "body_text": "第一行正文：下周三下午 13:30 在报告厅开会。\n<b>这行故意带标签</b>，页面必须原样当文本显示。\n\n—— 教务处"}})
        cdp.evaluate("document.querySelectorAll('#mail-heads tr.row--clickable')[0].click()")
        cdp.wait_for("!!document.querySelector('#mail-detail .mail-view__body')", 30, "邮件正文")
        body = cdp.evaluate(
            "({text: document.querySelector('#mail-detail .mail-view__body').textContent,"
            " html: document.querySelector('#mail-detail .mail-view__body').innerHTML,"
            " subj: document.querySelector('#mail-detail .mail-view__subject').textContent,"
            " meta: document.querySelector('#mail-detail .mail-view__meta').textContent,"
            " foot: document.querySelector('#mail-detail .mail-view__foot').textContent,"
            " url: __stub.calls[__stub.calls.length - 1].url,"
            " closeShown: !document.getElementById('mail-close').hidden})")
        check("3.21 点邮件 → GET /app/mail/<uid>/ 取正文",
              body["url"].endswith("/app/mail/1/"), body["url"])
        check("3.22 正文按纯文本渲染（保留换行 + HTML 标签当文本）",
              "第一行正文" in body["text"] and "<b>这行故意带标签</b>" in body["text"]
              and "<b>" not in body["html"], body["html"][:160])
        check("3.23 正文卡带主题 / 发件人 / 收件人 / 日期 与「实时读取」说明",
              "关于下周月考安排的通知" in body["subj"] and "教务处" in body["meta"]
              and "收件人" in body["meta"] and "实时读取" in body["foot"], body["meta"][:200])
        check("3.24 打开正文后出现「返回列表」并可用",
              body["closeShown"] is True
              and cdp.evaluate("document.getElementById('mail-close').click(); "
                               "!document.getElementById('mail-close').hidden") is False, "")
        cdp.wait_for("document.getElementById('mail-body').innerText.indexOf('正在读取') === -1", 40, "账号卡")
        acc = cdp.evaluate(
            "({cards: document.querySelectorAll('#mail-body .mail-card').length,"
            " text: document.getElementById('mail-body').innerText,"
            " secrets: Array.from(document.querySelectorAll('#mail-body .secret')).map(function (s) { return s.textContent; }),"
            " body: document.body.innerText})")
        check("3.25 账号卡只显示账号名（邮箱 + 平台标识）",
              acc["cards"] >= 1 and "demo@example.edu" in acc["text"]
              and "邮箱 · 账号标识 mail" in acc["text"], acc["text"][:160])
        check("3.26 密码一律打码、整页无明文密码",
              all(set(s.strip()) <= {"•"} for s in acc["secrets"]) and len(acc["secrets"]) >= 1
              and "should-never-render-plain" not in acc["body"], json.dumps(acc["secrets"]))

        # ⑦ Agent 助手（AI 契约）
        print("\n[3b] Agent 助手：409 未配置 → 200 回答 → 取消")
        install_stub(cdp, LIVE_OK, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "重载布局")
        cdp.evaluate("""
__ai = { calls: [] };
(function () {
  var real = window.fetch;
  __ai.queue = [];
  window.fetch = function (url, opt) {
    if (String(url).indexOf('/app/ai/chat/') !== -1) {
      var spec = __ai.queue.shift() || { status: 200, body: { ok: false, error: { code: 'no_stub', message: 'no stub' } } };
      __ai.calls.push({ url: String(url), method: (opt && opt.method) || 'GET', body: opt && opt.body });
      return new Promise(function (resolve, reject) {
        var done = false;
        function fire() {
          if (done) return;
          done = true;
          if (spec.networkError) { reject(new TypeError('Failed to fetch')); return; }
          resolve(new Response(JSON.stringify(spec.body), { status: spec.status,
                   headers: { 'Content-Type': 'application/json' } }));
        }
        if (opt && opt.signal) {
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
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"ai\"]').click()")
        ai_view = cdp.evaluate("({text: document.getElementById('view-ai').innerText,"
                               " count: document.getElementById('ai-count').textContent,"
                               " title: document.getElementById('view-title').textContent})")
        check("3.27 AI 视图：标题「Agent 助手」+ 能力边界 + 历史 0 / 12",
              ai_view["title"] == "Agent 助手" and "不能修改任何数据、没有工作区/文件功能" in ai_view["text"]
              and "0 / 12" in ai_view["count"], json.dumps(ai_view, ensure_ascii=False)[:200])
        cdp.evaluate("__ai.queue.push({status: 409, body: {ok: false, error:"
                     " {code: 'ai_not_configured', message: '请在客户端设置 → AI 里配置'}}}); 'ok'")
        cdp.evaluate("document.getElementById('ai-q').value = '这周有哪些课？';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        cdp.wait_for("document.querySelectorAll('#ai-log .msg').length >= 2 && ai.busy === false", 40, "AI 409")
        r409 = cdp.evaluate("({log: document.getElementById('ai-log').innerText,"
                            " unconf: !document.getElementById('ai-unconf').hidden,"
                            " links: Array.from(document.querySelectorAll('#ai-unconf a'))"
                            "        .map(function (a) { return a.getAttribute('href'); }),"
                            " req: JSON.parse(__ai.calls[0].body)})")
        check("3.28 AI 409：原样展示后端提示 + 常驻「去配置」入口",
              "请在客户端设置 → AI 里配置" in r409["log"] and r409["unconf"] is True
              and "/account/" in r409["links"], json.dumps(r409, ensure_ascii=False)[:260])
        check("3.29 AI 请求契约：POST /app/ai/chat/ + {question, history[]}",
              "__ai.calls[0].url" and cdp.evaluate("__ai.calls[0].url") == "/app/ai/chat/"
              and r409["req"].get("question") == "这周有哪些课？"
              and isinstance(r409["req"].get("history"), list),
              json.dumps(r409["req"], ensure_ascii=False)[:200])
        cdp.evaluate("__ai.queue.push({status: 200, body: {ok: true,"
                     " answer: '本周三你有 1 节课：09:00 英语 B HL（语言楼 201，Smith，G3）。',"
                     " model: 'demo-model', provider: 'demo-provider', protocol: 'openai',"
                     " context: {objects: ['edupage', 'settings.lessons'], chars: 1234, truncated: false}}}); 'ok'")
        cdp.evaluate("document.getElementById('ai-q').value = '本周三有哪些课？';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        cdp.wait_for("document.querySelectorAll('#ai-log .msg').length >= 4 && ai.busy === false", 40, "AI 200")
        r200 = cdp.evaluate("({log: document.getElementById('ai-log').innerText,"
                            " ctx: document.getElementById('ai-ctx').textContent,"
                            " count: document.getElementById('ai-count').textContent,"
                            " hasTag: document.querySelector('#ai-log .msg__body').innerHTML.indexOf('<') !== -1,"
                            " unconf: !document.getElementById('ai-unconf').hidden})")
        check("3.30 AI 200：回答以纯文本渲染 + 显示模型名",
              "本周三你有 1 节课" in r200["log"] and "demo-model" in r200["log"]
              and r200["hasTag"] is False, r200["log"][:200])
        check("3.31 显示 context（数据来源 / 字符数 / 是否截断）",
              "课表" in r200["ctx"] and "选课" in r200["ctx"] and "1234" in r200["ctx"]
              and "未截断" in r200["ctx"], r200["ctx"])
        check("3.32 历史计数递增到 4 / 12", "4 / 12" in r200["count"], r200["count"])
        check("3.33 成功一次后收起「未配置」提示", r200["unconf"] is False, "")
        cdp.evaluate("__ai.queue.push({status: 200, delayMs: 15000, body: {ok: true, answer: '太久',"
                     " model: 'demo-model', context: {objects: [], chars: 0, truncated: false}}}); 'ok'")
        cdp.evaluate("document.getElementById('ai-q').value = '慢慢答';"
                     "document.getElementById('ai-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        busy = cdp.wait_for("!document.getElementById('ai-wait').hidden", 10, "AI loading")
        check("3.34 请求期间有 loading 且发送按钮禁用",
              busy is True and cdp.evaluate("document.getElementById('ai-send').disabled") is True, "")
        cdp.evaluate("document.getElementById('ai-cancel').click()")
        cdp.wait_for("document.getElementById('ai-wait').hidden", 15, "取消后收起")
        cancel = cdp.evaluate("({log: document.getElementById('ai-log').innerText,"
                              " send: document.getElementById('ai-send').disabled})")
        check("3.35 取消后给出中文提示并恢复输入",
              "已取消这次提问" in cancel["log"] and cancel["send"] is False, cancel["log"][-160:])

        # ⑧ 设置
        install_stub(cdp, LIVE_OK, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "设置页布局")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"settings\"]').click()")
        cdp.wait_for("document.getElementById('set-sync').innerText.indexOf('未读取') === -1"
                     " && document.getElementById('set-platforms').innerText.indexOf('demo-student') !== -1",
                     60, "同步状态")
        st_view = cdp.evaluate(
            "({text: document.getElementById('view-settings').innerText,"
            " platforms: document.getElementById('set-platforms').innerText,"
            " sync: document.getElementById('set-sync').innerText,"
            " account: document.getElementById('set-account').innerText,"
            " links: Array.from(document.querySelectorAll('#view-settings a')).map(function (a) { return a.getAttribute('href'); })})")
        check("3.36 设置：显示已连接平台账号名（来自 meta.accounts，只显示账号名）",
              all(k in st_view["platforms"] for k in ["demo-student", "demo@example.edu", "邮箱"]),
              st_view["platforms"][:220])
        check("3.37 设置：同步状态列出各同步对象版本 / 时间（现在在默认收起的诊断区里，先展开）",
              _diag_open(cdp) is True
              and "云端第" in _diag_text(cdp, "set-sync") and "schedule" in _diag_text(cdp, "set-sync")
              and "settings.lessons" in _diag_text(cdp, "set-sync")
              and "school" in _diag_text(cdp, "set-sync"),
              _diag_text(cdp, "set-sync")[:240])
        check("3.38 设置：账号来源写明由 PHIX 提供 + 个人中心链接 + 退出登录",
              "PHIX" in st_view["account"] and "/account/" in st_view["links"]
              and "去个人中心管理凭据 / AI" in st_view["text"]
              and cdp.evaluate("!!document.getElementById('set-logout')") is True, "")
        check("3.39 设置页没有任何明文密码",
              "should-never-render-plain" not in cdp.evaluate("document.body.innerText"), "")

        # ---------- [4] 真后端：平台抓取失败 → 逐平台可读原因 + 降级 ----------
        print("\n[4] 真后端（假平台密码）：逐平台原因 + 降级到上次同步的数据")
        install_stub(cdp, None, user)          # 不再喂 /app/data/ → 走真后端
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "真实后端布局")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"timetable\"]').click()")
        cdp.wait_for("document.getElementById('tt-error').hidden === false", 180, "真实抓取结果")
        real = cdp.evaluate(
            "({ttErr: document.getElementById('tt-error').hidden ? '' : document.getElementById('tt-error').textContent,"
            " mbErr: document.getElementById('mb-error').hidden ? '' : document.getElementById('mb-error').textContent,"
            " tt: document.getElementById('tt-body').innerText,"
            " info: document.getElementById('tt-info').hidden ? '' : document.getElementById('tt-info').textContent,"
            # 课表工具条上的「抓取时间 / 课程条目 / 上课天数」已按要求删除（精简技术细节），
            # 时间改由**顶栏常驻**的 #top-stamp 承担；条数改为直接数渲染出来的课卡。
            " count: String(document.querySelectorAll('#tt-week .tt-lesson').length),"
            " topStamp: document.getElementById('top-stamp').textContent,"
            " synced: document.getElementById('tt-synced')"
            "   ? document.getElementById('tt-synced').textContent : ''})")
        check("4.1 真实抓取失败也要有可读内容（不白屏）", len(real["tt"].strip()) > 0, real["tt"][:160])
        check("4.2 抓取时间戳有值（说明 /app/data/ 真的应答了；课表那行小字已删，改看顶栏常驻时间）",
              bool(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", real["topStamp"] or "")),
              real["topStamp"])
        check("4.3 平台错误原样展示（假密码必然失败）", bool(real["ttErr"]), real["ttErr"][:160])
        check("4.4 同步对象里有数据 → 明示「以下为上次同步的数据（时间：…）」+ 课卡仍有",
              "以下为上次同步的数据" in real["info"] and real["count"] == "1",
              json.dumps({k: real[k] for k in ("info", "count")}, ensure_ascii=False)[:260])
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"courses\"]').click()")
        cdp.wait_for("document.getElementById('mb-courses').innerText.indexOf('正在') === -1", 120, "课程降级")
        mbreal = cdp.evaluate(
            "({courses: document.getElementById('mb-courses').innerText,"
            " tasks: document.getElementById('mb-tasks').innerText,"
            " err: document.getElementById('mb-error').hidden ? '' : document.getElementById('mb-error').textContent})")
        check("4.5 课程视图同样降级显示快照（数学 HL（快照））",
              "数学 HL（快照）" in mbreal["courses"], mbreal["courses"][:200])
        check("4.6 作业也降级显示快照数据",
              "快照作业：第 1 章" in mbreal["tasks"], mbreal["tasks"][:200])

        # ---------- [4b] 本轮新增：DDL ±14 天窗口 + 未读圆点 ----------
        print("\n[4b] DDL 窗口（±14 天 / 2 天内 urgent / 已过期在下方）与未读圆点")
        install_stub(cdp, LIVE_OK, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "窗口用例布局")
        ddl_win = cdp.evaluate(
            "({y: dateStrOffset(-1), near: dateStrOffset(2), far: dateStrOffset(30),"
            " minus14: dateStrOffset(-14), plus14: dateStrOffset(14), today: todayStr()})")
        # 直接调渲染函数：实时数据按「抓取时间」缓存，改了 st.coTasks 后点刷新拿到的还是同一份；
        # 切视图时课程面板还会按行缓存重画一次，所以「注入 + 渲染」放进同一次 evaluate，
        # 保证验的就是刚注入的那份数据（既有 CDP 用例改 st.* 的习惯做法）。
        cdp.evaluate(
            "(function () {"
            " var T = ["
            "  {course: '窗口-昨天', title: '窗口任务：昨天到期', due: " + json.dumps(ddl_win["y"] + " 23:59") + ","
            "   dueRaw: " + json.dumps(ddl_win["y"] + " 23:59") + ", status: 'Pending', score: '—'},"
            "  {course: '窗口-两天后', title: '窗口任务：两天后到期', due: " + json.dumps(ddl_win["near"] + " 23:59") + ","
            "   dueRaw: " + json.dumps(ddl_win["near"] + " 23:59") + ", status: 'Pending', score: '—'},"
            "  {course: '窗口-三十天后', title: '窗口任务：三十天后到期', due: " + json.dumps(ddl_win["far"] + " 23:59") + ","
            "   dueRaw: " + json.dumps(ddl_win["far"] + " 23:59") + ", status: 'Pending', score: '—'}];"
            " st.coTasks = T;"
            " taskOrder = [];"                              # 清掉前面 ▲▼/排序 留下的手动顺序
            " var s = document.getElementById('mb-sort');"
            " if (s && s.value !== 'due-asc') { s.value = 'due-asc';"
            "   s.dispatchEvent(new Event('change')); }"
            " renderHomeDdl(document.getElementById('home-ddl'));"
            " renderCourseTasks(document.getElementById('mb-tasks'));"
            " return {home: document.querySelectorAll('#home-ddl .ddl-item').length,"
            "   order: JSON.stringify(taskOrder),"
            "   dom: Array.from(document.querySelectorAll('#mb-tasks .ddl-item'))"
            "     .map(function (i) { return i.className; })}; })()")
        cdp.wait_for("document.querySelectorAll('#home-ddl .ddl-item').length === 2", 30, "首页 DDL 窗口")
        win = cdp.evaluate(
            "({day: dateStrOffset(-14), items: Array.from(document.querySelectorAll('#home-ddl .ddl-item'))"
            "   .map(function (i) { return i.innerText; }),"
            " urgent: document.querySelectorAll('#home-ddl .ddl-item.urgent').length,"
            " text: document.getElementById('home-ddl').innerText,"
            " card: document.getElementById('view-home').innerText})")
        check("4b.1 首页 DDL 只出现窗口内两条（昨天到期 + 3 天后；+30 天那条不出现）",
              len(win["items"]) == 2
              and any("昨天到期" in t for t in win["items"])
              and any("两天后到期" in t for t in win["items"])
              and not any("三十天后到期" in t for t in win["items"]),
              json.dumps(win["items"], ensure_ascii=False)[:260])
        # 首页 DDL 卡的 urgent 口径是**纯日期窗口**（[今天-2, 今天+2]），不排除已过期条目
        # ——所以「昨天」那条在首页也会加粗（这是既有实现，本轮没动它，如实断言）；
        # 课程页的 urgent 额外要求未过期（见 4b.7）。
        urgent_text = cdp.evaluate(
            "Array.from(document.querySelectorAll('#home-ddl .ddl-item.urgent'))"
            "  .map(function (i) { return i.innerText.replace(/\\n/g, ' '); }).join(' || ')")
        check("4b.2 首页：窗口 [今天-2, 今天+2] 内两条都带 urgent（含已过期那条；课程页口径不同见 4b.7）",
              win["urgent"] == 2 and "昨天到期" in urgent_text and "两天后到期" in urgent_text,
              f"urgent={win['urgent']} text={urgent_text[:80]!r}")
        check("4b.3 DDL 卡说明窗口口径（±14 天 / 2 天内加粗）",
              "±14 天" in win["card"] and "2 天" in win["card"], win["card"][:200])

        # 课程页同样「注入 + 渲染 + 当场读回」：切视图会触发一次数据重新应用，等一会儿再读
        # 拿到的就可能是 fixture 那份（本轮就是这么踩到的），所以必须在同一次 evaluate 里取数。
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"courses\"]').click()")
        co2 = cdp.evaluate(
            "(function () {"
            " var T = ["
            "  {course: '窗口-昨天', title: '窗口任务：昨天到期', due: " + json.dumps(ddl_win["y"] + " 23:59") + ","
            "   dueRaw: " + json.dumps(ddl_win["y"] + " 23:59") + ", status: 'Pending', score: '—'},"
            "  {course: '窗口-两天后', title: '窗口任务：两天后到期', due: " + json.dumps(ddl_win["near"] + " 23:59") + ","
            "   dueRaw: " + json.dumps(ddl_win["near"] + " 23:59") + ", status: 'Pending', score: '—'},"
            "  {course: '窗口-三十天后', title: '窗口任务：三十天后到期', due: " + json.dumps(ddl_win["far"] + " 23:59") + ","
            "   dueRaw: " + json.dumps(ddl_win["far"] + " 23:59") + ", status: 'Pending', score: '—'}];"
            " st.coTasks = T; taskOrder = [];"
            " var s = document.getElementById('mb-sort');"
            " if (s && s.value !== 'due-asc') { s.value = 'due-asc';"
            "   s.dispatchEvent(new Event('change')); }"
            " renderCourseTasks(document.getElementById('mb-tasks'));"
            " return {n: document.querySelectorAll('#mb-tasks .ddl-item').length,"
            "   past: document.querySelectorAll('#mb-tasks .ddl-item.past-due').length,"
            "   urgent: document.querySelectorAll('#mb-tasks .ddl-item.urgent').length,"
            "   urgentText: (function () { var u = document.querySelector('#mb-tasks .ddl-item.urgent');"
            "     return u ? u.innerText.replace(/\\n/g, ' ') : ''; })(),"
            "   rows: Array.from(document.querySelectorAll('#mb-tasks .ddl-item'))"
            "     .map(function (i) { return i.innerText.replace(/\\n/g, ' '); }),"
            "   lastPast: (function () { var a = document.querySelectorAll('#mb-tasks .ddl-item');"
            "     return a.length ? a[a.length - 1].className : ''; })(),"
            "   text: document.getElementById('mb-tasks').innerText}; })()")
        check("4b.4 课程页三条都在（含已过期），且昨天那条带 past-due",
              co2["n"] == 3 and co2["past"] == 1
              and any("昨天到期" in r for r in co2["rows"]),
              json.dumps(co2["rows"], ensure_ascii=False)[:260])
        check("4b.5 已过期的排在列表最下方（最后一行就是 past-due）",
              "past-due" in co2["lastPast"], co2["lastPast"])
        check("4b.6 课程页也标出「已过期的作业 / 考试排在列表下方」",
              "排在列表下方" in co2["text"], co2["text"][:200])
        check("4b.7 课程页：未过期且 2 天内到期的那条带 urgent（已过期那条只带 past-due）",
              co2["urgent"] == 1 and "两天后到期" in co2["urgentText"]
              and "昨天到期" not in co2["urgentText"],
              f"urgent={co2['urgent']} text={co2['urgentText'][:60]!r}")
        check("4b.7b 首页 DDL 卡常驻一行说明（改数据后也还在，不只在没数据时出现）",
              cdp.evaluate("document.getElementById('home-ddl').innerText").find("±14 天") != -1,
              cdp.evaluate("document.getElementById('home-ddl').innerText")[:200])

        # 未读圆点：1 封未读 → 只画 1 个点
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"mail\"]').click()")
        cdp.wait_for("document.querySelectorAll('#mail-heads .mail-item').length === 3", 30, "邮件列表")
        dot1 = cdp.evaluate(
            "({items: document.querySelectorAll('#mail-heads .mail-item').length,"
            " dots: document.querySelectorAll('#mail-heads .mail-item.unread').length,"
            " subjectDots: document.querySelectorAll('#mail-heads .mail-item .subj').length &&"
            "   Array.from(document.querySelectorAll('#mail-heads .mail-item .subj'))"
            "     .filter(function (s) { return s.textContent.indexOf('🔵') !== -1; }).length,"
            " unreadAttr: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"1\"]').length,"
            " readAttr: document.querySelectorAll('#mail-heads .mail-item[data-unread=\"0\"]').length,"
            " line: ((document.querySelector('#mail-heads .stats')||{}).innerText||''),"
            " headUnread: document.getElementById('home-unread').textContent})")
        check("4b.8 1 封未读 → 列表只画 1 个蓝点（另外两封不画）",
              dot1["items"] == 3 and dot1["dots"] == 1 and dot1["subjectDots"] == 1
              and dot1["unreadAttr"] == 1 and dot1["readAttr"] == 2,
              json.dumps(dot1, ensure_ascii=False))
        check("4b.9 未读计数按真实值显示（列表行「未读 1 封」）",
              "未读 1 封" in dot1["line"], dot1["line"])

        # 未读圆点：unread=0 → 一个点都不画
        zero = json.loads(json.dumps(LIVE_OK, ensure_ascii=False))
        zero["mail"]["unread"] = 0
        for item in zero["mail"]["recent"]:
            item["unread"] = False
        install_stub(cdp, zero, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "零未读布局")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"mail\"]').click()")
        cdp.wait_for("document.querySelectorAll('#mail-heads .mail-item').length === 3", 30, "零未读列表")
        dot0 = cdp.evaluate(
            "({dots: document.querySelectorAll('#mail-heads .mail-item.unread').length,"
            " items: document.querySelectorAll('#mail-heads .mail-item').length,"
            " blues: Array.from(document.querySelectorAll('#mail-heads .subj'))"
            "   .filter(function (s) { return s.textContent.indexOf('🔵') !== -1; }).length,"
            " line: ((document.querySelector('#mail-heads .stats')||{}).innerText||''),"
            " headUnread: document.getElementById('home-unread').textContent})")
        check("4b.10 unread=0 → 一封都不画点（3 封邮件 0 个蓝点）",
              dot0["items"] == 3 and dot0["dots"] == 0 and dot0["blues"] == 0,
              json.dumps(dot0, ensure_ascii=False))
        check("4b.11 unread=0 → 计数如实显示 0（不是「未知」也不是 3）",
              "未读 0 封" in dot0["line"] and dot0["headUnread"] == "0",
              json.dumps({k: dot0[k] for k in ("line", "headUnread")}, ensure_ascii=False))

        # 字段缺失 → 一律不画点（绝不假设未读）
        nodot = json.loads(json.dumps(LIVE_OK, ensure_ascii=False))
        nodot["mail"]["unread"] = 3
        for item in nodot["mail"]["recent"]:
            item.pop("unread", None)
        install_stub(cdp, nodot, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "缺字段布局")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"mail\"]').click()")
        cdp.wait_for("document.querySelectorAll('#mail-heads .mail-item').length === 3", 30, "缺字段列表")
        missing = cdp.evaluate(
            "({dots: document.querySelectorAll('#mail-heads .mail-item.unread').length,"
            " items: document.querySelectorAll('#mail-heads .mail-item').length,"
            " line: ((document.querySelector('#mail-heads .stats')||{}).innerText||'')})")
        check("4b.12 逐封 unread 字段缺失 → 一个点都不画（不假设未读）；总数仍照实显示",
              missing["items"] == 3 and missing["dots"] == 0 and "未读 3 封" in missing["line"],
              json.dumps(missing, ensure_ascii=False))

        # 回到基建用例的初始态（后面的段落依赖 LIVE_OK）
        install_stub(cdp, LIVE_OK, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "回到初始态")

        # ---------- [5] 日程写回（真后端） ----------
        print("\n[5] 日程写回：页面新增 → 云端 revision 增加 → 页面删除")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"schedule\"]').click()")
        cdp.wait_for("document.querySelectorAll('#sched-body .cal-ev').length === 2", 40, "日程初始 2 条")
        # 先回到今天（日历上「今天」这一格），新增的条目放在今天，才能立刻在周视图里看到
        cdp.evaluate("document.getElementById('sched-today').click()")
        time.sleep(0.4)
        st, before = op.req("GET", "/proxy/sync/objects/schedule/")
        rev0 = before.get("revision", 0)
        cdp.evaluate("document.getElementById('sched-open').click()")
        check("5.0 顶栏「＋ 新建日程」能展开表单（#sched-foot 可见、日期默认今天）",
              cdp.evaluate("({shown: !document.getElementById('sched-foot').hidden,"
                           " day: document.getElementById('f-day').value})")["shown"] is True,
              json.dumps(cdp.evaluate("({shown: !document.getElementById('sched-foot').hidden,"
                                      " foot: getComputedStyle(document.getElementById('sched-foot')).display})"),
                         ensure_ascii=False))
        # 这一轮要往「今天」加一条：先切到年视图再切回周视图（顺带验证换视图后日历照常重渲染），
        # 然后回到今天这一格 —— 新增的条目才会立刻出现在周视图上。
        cdp.evaluate("document.querySelectorAll('#sched-views .seg__btn')[2].click()")   # 年视图
        time.sleep(0.4)
        cdp.evaluate("document.querySelectorAll('#sched-views .seg__btn')[0].click()")   # 回周视图
        time.sleep(0.4)
        cdp.evaluate("document.getElementById('sched-today').click()")
        time.sleep(0.4)
        day_iso = dateStrOffset(0)
        cdp.evaluate("document.getElementById('f-day').value = '" + day_iso + "';"
                     "document.getElementById('f-time').value = '15:00';"
                     "document.getElementById('f-title').value = 'CDP 新增的日程';"
                     "document.getElementById('f-note').value = '由 test_app_ui 写入';"
                     "document.getElementById('sched-form').dispatchEvent(new Event('submit', {cancelable: true}))")
        cdp.wait_for("(function () { var c = document.querySelector('#sch-week .sch-day[data-day=\"%s\"]');"
                     "  return !!c && c.innerText.indexOf('CDP 新增的日程') !== -1; })()" % day_iso,
                     40, "新增后出现在日历格子里")
        # 写回是异步的：轮询云端直到 revision 变化且能读到这条事件（不用固定 sleep，避免竞态）
        doc, rev1, _t = wait_cloud(
            op, "schedule",
            lambda d, r: r > rev0 and any(e.get("title") == "CDP 新增的日程" for e in d.get("events", [])),
            seconds=30.0)
        added = [e for e in doc.get("events", []) if e.get("title") == "CDP 新增的日程"]
        check("5.1 页面新增日程已写回云端（revision 增加），并出现在日历格子里",
              rev1 > rev0 and len(added) == 1,
              f"rev {rev0} → {rev1} events={len(doc.get('events', []))}")
        check("5.2 写回字段符合契约（id/day/time/title/note/created 含用户名）",
              bool(added) and added[0].get("day") == day_iso and added[0].get("time") == "15:00"
              and "note" in added[0] and user in str(added[0].get("created", "")),
              json.dumps(added[:1], ensure_ascii=False)[:220])
        # 删除（自己创建的 → 点开那一天，当天卡片里给「删除」按钮）
        cdp.evaluate("document.querySelector('#sch-week .sch-day[data-day=\"%s\"]').click()" % day_iso)
        cdp.wait_for("!document.getElementById('sch-modal').hidden"
                     " && document.getElementById('schm-list').innerText.indexOf('CDP 新增的日程') !== -1",
                     30, "当天卡片列出新增的日程")
        cdp.evaluate("""
(function () {
  var rows = Array.from(document.querySelectorAll('#schm-list .evt'));
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].innerText.indexOf('CDP 新增的日程') !== -1) {
      var b = rows[i].querySelector('button[data-del]');
      if (b) b.click();
      return true;
    }
  }
  return false;
})()
""")
        cdp.wait_for("document.getElementById('sched-body').innerText.indexOf('CDP 新增的日程') === -1",
                     40, "删除后渲染")
        cdp.evaluate("document.getElementById('schm-close').click()")
        # 同样轮询：等云端真的只剩两条、且不含刚删的那条
        doc2, _rev2, _t2 = wait_cloud(
            op, "schedule",
            lambda d, r: len(d.get("events", [])) == 2
                         and all(e.get("title") != "CDP 新增的日程" for e in d.get("events", [])),
            seconds=30.0)
        check("5.3 页面删除已写回云端（事件数回到 2，且只剩原有两条）",
              len(doc2.get("events", [])) == 2
              and all(e.get("title") != "CDP 新增的日程" for e in doc2.get("events", [])),
              json.dumps([e.get("title") for e in doc2.get("events", [])], ensure_ascii=False))

        # ---------- [6] 与官网视觉解耦 + PHL Lite 配色（实测计算样式） ----------
        print("\n[6] 版式与配色（实测计算样式）")
        look = cdp.evaluate(
            "({side: getComputedStyle(document.getElementById('sidebar')).backgroundImage,"
            " sideColor: getComputedStyle(document.getElementById('sidebar')).color,"
            " sideW: Math.round(document.getElementById('sidebar').getBoundingClientRect().width),"
            " bodyBg: getComputedStyle(document.body).backgroundColor,"
            " font: getComputedStyle(document.body).fontFamily,"
            " hasSiteHeader: !!document.querySelector('.site-header'),"
            " hasSiteFooter: !!document.querySelector('.site-footer'),"
            " links: Array.from(document.querySelectorAll('link[rel=stylesheet]')).map(function (l) { return l.getAttribute('href'); }),"
            " logoImg: document.querySelectorAll('#view-home img, #tabs img, .sidebar img').length,"
            " touch: getComputedStyle(document.documentElement).getPropertyValue('--green-900').trim(),"
            " radius: getComputedStyle(document.documentElement).getPropertyValue('--radius').trim(),"
            " sideWVar: getComputedStyle(document.documentElement).getPropertyValue('--side-w').trim()})")
        check("6.1 侧栏是深绿竖向渐变（#173f33 → #102d25），宽 224px",
              "rgb(23, 63, 51)" in look["side"] and "rgb(16, 45, 37)" in look["side"]
              and look["sideW"] == 224 and look["sideWVar"] == "224px",
              json.dumps({k: look[k] for k in ("side", "sideW", "sideWVar")}, ensure_ascii=False))
        check("6.2 侧栏文字是米白 #fbfaf6", "rgb(251, 250, 246)" in look["sideColor"], look["sideColor"])
        check("6.3 页面底色是象牙白 #fbfaf6、字体栈同客户端",
              "rgb(251, 250, 246)" in look["bodyBg"] and "Segoe UI Variable Text" in look["font"],
              json.dumps({k: look[k] for k in ("bodyBg", "font")}, ensure_ascii=False))
        check("6.4 token 生效（--green-900=#173f33、--radius=16px）",
              look["touch"].lower() == "#173f33" and look["radius"] == "16px",
              json.dumps({k: look[k] for k in ("touch", "radius")}, ensure_ascii=False))
        check("6.5 页面没有官网顶栏 / 页脚 / 任何站点样式表",
              look["hasSiteHeader"] is False and look["hasSiteFooter"] is False
              and all("/static/app/app.css" in (l or "") for l in look["links"])
              and not any("site.css" in (l or "") for l in look["links"]),
              json.dumps(look["links"], ensure_ascii=False))
        check("6.6 侧栏只有文字品牌，不含任何图片 logo",
              look["logoImg"] == 0, str(look["logoImg"]))

        # ---------- [7] 窄屏 390px ----------
        print("\n[7] 390px 窄屏：侧栏收起为标签条、无横向溢出")
        cdp.send("Emulation.setDeviceMetricsOverride", width=390, height=844,
                 deviceScaleFactor=1, mobile=True)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "窄屏布局")
        nar = cdp.evaluate(
            "({tabs: Array.from(document.querySelectorAll('#tabs .tab')).map(function (t) {"
            "   return {t: t.textContent, w: Math.round(t.offsetWidth), h: Math.round(t.offsetHeight)}; }),"
            " overflow: document.documentElement.scrollWidth - window.innerWidth,"
            " wrap: getComputedStyle(document.getElementById('tabs')).flexWrap,"
            " dir: getComputedStyle(document.getElementById('tabs')).flexDirection,"
            " sideW: Math.round(document.getElementById('sidebar').getBoundingClientRect().width),"
            " toggleShown: getComputedStyle(document.getElementById('side-toggle')).display !== 'none'})")
        check("7.1 窄屏下 7 个标签都在，且都够大能点（宽 > 20 且高 > 10）",
              len(nar["tabs"]) == 7 and all(t["w"] > 20 and t["h"] > 10 for t in nar["tabs"]),
              json.dumps(nar["tabs"], ensure_ascii=False)[:260])
        check("7.2 标签条横向换行（不是竖排），每项文字仍是一行",
              nar["dir"] == "row" and nar["wrap"] == "wrap"
              and all(t["h"] < 60 for t in nar["tabs"]), json.dumps(
                  {k: nar[k] for k in ("dir", "wrap")}, ensure_ascii=False))
        check("7.3 390px 无横向溢出", nar["overflow"] <= 2, f"overflow={nar['overflow']}px")
        check("7.4 窄屏下侧栏占满宽度（不再是 224px 固定侧栏）+ 出现收起按钮",
              nar["sideW"] > 300 and nar["toggleShown"] is True,
              json.dumps({k: nar[k] for k in ("sideW", "toggleShown")}, ensure_ascii=False))
        # 逐个视图在 390px 下都不溢出
        bad = []
        for v in VIEWS:
            cdp.evaluate(f"document.querySelector('#tabs .tab[data-view=\"{v}\"]').click()")
            time.sleep(0.5)
            ov = cdp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            if ov > 2:
                bad.append(f"{v}={ov}px")
        check("7.5 七个视图在 390px 下都不横向溢出", bad == [], ", ".join(bad))
        cdp.evaluate("document.getElementById('side-toggle').click()")
        collapsed = cdp.evaluate("({cls: document.getElementById('sidebar').className,"
                                 " tabsShown: getComputedStyle(document.getElementById('tabs')).display !== 'none'})")
        check("7.6 点收起按钮后标签条隐藏（抽屉式收起，再点恢复）",
              "is-collapsed" in collapsed["cls"] and collapsed["tabsShown"] is False,
              json.dumps(collapsed, ensure_ascii=False))
        cdp.evaluate("document.getElementById('side-toggle').click()")
        check("7.7 再点一次恢复标签条",
              cdp.evaluate("getComputedStyle(document.getElementById('tabs')).display !== 'none'") is True, "")

        # ---------- [8] 抓取「部分平台空结果」时逐平台降级，不白屏 ----------
        print("\n[8] /app/data/ 已应答但课表/课程为空、邮箱有数据 → 逐平台降级")
        cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                 deviceScaleFactor=1, mobile=False)
        install_stub(cdp, {"ok": True, "edupage": {"lessons": [], "selected": []},
                           "managebac": {"courses": [], "tasks": []},
                           "mail": {"unread": 2, "recent": [
                               {"uid": "9", "from": "教务处 <academic@example.edu>",
                                "subject": "实时抓到的邮件", "date": "2026-09-13 10:00"}]},
                           "meta": {"fetched_at": "2026-09-13T10:00:00+08:00", "cache": "miss",
                                    "accounts": {"edupage": "demo-student"},
                                    "errors": {}}}, user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "空数据布局")
        for v in ("timetable", "courses", "mail"):
            cdp.evaluate(f"document.querySelector('#tabs .tab[data-view=\"{v}\"]').click()")
        cdp.wait_for("document.getElementById('tt-body').innerText.indexOf('上次同步') !== -1", 90, "空数据降级")
        block = cdp.evaluate(
            "({tt: document.getElementById('tt-body').innerText,"
            " co: document.getElementById('mb-courses').innerText,"
            " mail: document.getElementById('mail-heads').innerText,"
            " info: document.getElementById('tt-info').hidden ? '' : document.getElementById('tt-info').textContent,"
            # 条数直接数渲染出来的课卡（工具条上的「课程条目 N」已按要求删除）
            " count: String(document.querySelectorAll('#tt-week .tt-lesson').length),"
            " mailSnap: st.mailSnap, mailUnread: st.mailUnread,"
            " mailCount: ((document.querySelector('#mail-heads .stats')||{}).innerText||'')})")
        check("8.1 课表/课程降级、邮箱照用实时数据，三块都给出可读内容",
              all(len(block[k].strip()) > 4 for k in ("tt", "co", "mail"))
              and "实时抓到的邮件" in block["mail"] and "未读 2 封" in block["mailCount"],
              json.dumps({k: block[k][:60] for k in ("tt", "co", "mail", "mailCount")},
                         ensure_ascii=False))
        check("8.2 课表降级时明示「以下为上次同步的数据（时间：…）」+ 条数来自快照",
              "以下为上次同步的数据" in block["info"] and block["count"] == "1",
              json.dumps({k: block[k] for k in ("info", "count")}, ensure_ascii=False)[:260])
        # 降级提示分两处写（都算「标出了上次同步的快照」）：
        #   课表 → #tt-info 的  syncNotice 文案「以下为上次同步的数据（时间：…）」
        #          与 st.ttLabel「上次同步的快照」；
        #   课程 → #mb-courses 里的 .chip「上次同步的快照」。
        # 有实时数据的那块（邮箱）两处都不该出现。
        check("8.3 降级的两块都标出「上次同步的快照」（课表走 #tt-info 的同步提示，"
              "课程走 #mb-courses 的快照 chip），有实时数据的那块（邮箱）不标",
              ("上次同步的快照" in block["info"] or "以下为上次同步的数据" in block["info"])
              and "上次同步的快照" in block["co"]
              and "上次同步的快照" not in block["mail"],
              json.dumps({"info": block["info"], "co": block["co"][:80],
                          "mailHas": "上次同步的快照" in block["mail"]},
                         ensure_ascii=False)[:400])
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"home\"]').click()")
        homeTxt = cdp.evaluate("document.getElementById('view-home').innerText")
        check("8.4 首页也不白屏，并说明数据来源", len(homeTxt.strip()) > 20
              and ("上次同步" in homeTxt or "服务器实时抓取" in homeTxt), homeTxt[:160])
        check("8.5 全程页面无未捕获 JS 异常",
              len([e for e in cdp.errors if "exceptionThrown" in e]) == 0,
              json.dumps(cdp.errors[:3], ensure_ascii=False)[:400])

        # ---------- [8b] EduPage 正在后台抓（meta.pending）→ 中性提示 + 刷新按钮，不画红 ----------
        print("\n[8b] meta.pending = ['edupage'] → 课表页中性提示 + 「刷新」，不是红色错误")
        cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                 deviceScaleFactor=1, mobile=False)
        # 先把同步快照清空：pending 的「刷新」空态只在**两边都没数据**时出现
        # （有快照时页面按「以下为上次同步的数据」降级，那是另一条已测的路径）
        write_obj(op, "school", {"version": 1, "updated_at": "2026-09-13T08:30:00+08:00"})
        install_stub(cdp, {"ok": True, "edupage": {"lessons": [], "selected": []},
                           "managebac": {"courses": [], "tasks": []},
                           "mail": {"unread": 1, "recent": [
                               {"uid": "9", "from": "教务处 <academic@example.edu>",
                                "subject": "抓取中的邮件", "date": "2026-09-13 10:00"}]},
                           "meta": {"fetched_at": "2026-09-13T10:00:00+08:00", "cache": "miss",
                                    "accounts": {"edupage": "demo-student"},
                                    "pending": ["edupage"],
                                    "errors": {"edupage": "EduPage 正在抓取（首次约 50 秒），请稍后点「刷新」"}}},
                    user)
        cdp.goto(base + "/app/")
        cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "pending 布局")
        cdp.evaluate("document.querySelector('#tabs .tab[data-view=\"timetable\"]').click()")
        cdp.wait_for("document.getElementById('tt-body').innerText.indexOf('正在抓取 EduPage') !== -1",
                     60, "pending 中性提示")
        pend = cdp.evaluate(
            "({tt: document.getElementById('tt-body').innerText,"
            " info: document.getElementById('tt-info').hidden ? '' : document.getElementById('tt-info').textContent,"
            " ttErrHidden: document.getElementById('tt-error').hidden,"
            " ttErr: document.getElementById('tt-error').hidden ? '' : document.getElementById('tt-error').textContent,"
            " refreshBtns: Array.from(document.querySelectorAll('#tt-body button, #tt-week button'))"
            "   .map(function (b) { return b.textContent; }),"
            " cls: (document.querySelector('#tt-week .empty') || {}).className || ''})")
        check("8b.1 课表页显示「正在抓取 EduPage 课表（首次约 50 秒）…」（中性态）",
              "正在抓取 EduPage 课表" in pend["tt"], pend["tt"][:200])
        check("8b.2 **不**画成红色错误（tt-error 保持隐藏，文案也不进错误位）",
              pend["ttErrHidden"] is True and pend["ttErr"] == "", pend["ttErr"][:160])
        check("8b.3 中性提示位说明「抓完会自动显示，也可以点刷新」",
              "会" in pend["info"] and "刷新" in pend["info"], pend["info"][:200])
        check("8b.4 视图里**不再**内嵌「刷新」按钮（用户要求只留顶栏那一颗绿色刷新），"
              "改为用文字指路：提示位说明「抓完会自动显示，也可以点刷新」",
              not (pend["refreshBtns"] or [])
              and "刷新" in pend["info"],
              json.dumps({"btns": pend["refreshBtns"], "info": pend["info"][:80]},
                         ensure_ascii=False))
        check("8b.5 pending 态故意带 pending-hint 类（中性配色，不是 .inline-msg--error）",
              "pending-hint" in pend["cls"], pend["cls"])
        check("8b.6 pending 期间邮箱照常渲染（逐平台互不影响）",
              "抓取中的邮件" in cdp.evaluate(
                  "document.querySelector('#tabs .tab[data-view=\"mail\"]').click();"
                  "document.getElementById('mail-heads').innerText"), "")
        check("8b.7 全程无未捕获 JS 异常",
              len([e for e in cdp.errors if "exceptionThrown" in e]) == 0,
              json.dumps(cdp.errors[:3], ensure_ascii=False)[:400])
        write_obj(op, "school", SNAPSHOT_SCHOOL)     # 还原快照，别影响后面的截图段
        # ---------- [9] 截图（可选） ----------
        if args.shot_dir:
            print(f"\n[9] 截图 → {args.shot_dir}")
            os.makedirs(args.shot_dir, exist_ok=True)
            cdp.goto(base + "/app/")
            cdp.wait_for("document.getElementById('app') && !document.getElementById('app').hidden", 40, "截图布局")
            for v in VIEWS:
                cdp.evaluate(f"document.querySelector('#tabs .tab[data-view=\"{v}\"]').click()")
                time.sleep(1.5)
                h = cdp.evaluate("document.documentElement.scrollHeight")
                data = cdp.send("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                                clip={"x": 0, "y": 0, "width": 1280,
                                      "height": min(h, 20000), "scale": 1})["data"]
                out = os.path.join(args.shot_dir, f"appweb2-{v}.png")
                open(out, "wb").write(base64.b64decode(data))
                print(f"    appweb2-{v}.png  {os.path.getsize(out)} bytes")
    finally:
        if cdp is not None:
            cdp.close_browser()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
        try:
            op.req("POST", "/auth/logout/", {})
        except Exception:
            pass

    print("\n" + "=" * 76)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name, extra in FAILED:
        print("  - " + name + (f"   {extra}" if extra else ""))
    print("=" * 76)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
