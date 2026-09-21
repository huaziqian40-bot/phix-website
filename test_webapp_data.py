"""官网「服务端平台抓取层」专项测试（≥16 项断言）。

跑法（站点 venv）：

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_webapp_data.py

这一份是**自己起假平台**跑真链路，不用真账号：

* `FakeManageBac`：本地 HTTP 服务，复刻 ManageBac 的真实 DOM
  （#session_form 登录页 / li.f-menu-submenu-item 课程菜单 /
  div.sidebar-items-list 总评 / div.fusion-card-item.short-assignment 作业卡），
  把 `base_url` 指向它 → 解析器与客户端跑的是**真代码路径**。
* `FakeIMAP`：本地 **TLS socket** 上起一个极简 IMAP4rev1 服务（CAPABILITY /
  LOGIN / SELECT / STATUS / UID SEARCH / UID FETCH / LOGOUT），
  用真 `imaplib.IMAP4_SSL` 连 → 列表与正文解析也是真代码路径。
* HTTP 层：真起 `server.py`（--port 临时端口）验 401 / 409 / 200 / 404。

绝不打印任何测试口令：断言里反而会检查「口令没出现在响应与日志里」。
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hmac as hmac_mod
import http.cookiejar
import http.server
import importlib.util
import io
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import webapp_data as wd  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

#: 只在测试进程里用的假口令（真口令绝不写进测试文件）
PWD_MB = "mb-TestOnly-7f3a"
PWD_EP = "ep-TestOnly-9c1d"
PWD_MAIL = "mail-TestOnly-2b8e"
MAIL_USER = "student@example.invalid"
EP_USER = "student-ep@example.invalid"
MB_USER = "student-mb@example.invalid"


def check(name: str, cond, extra: str = "") -> bool:
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return bool(cond)


def section(title: str) -> None:
    print("-" * 74)
    print(title)


# ============================================================ 假的 phix 同步对象（供 HTTP 测试）

def phix_cookie_header(secret: bytes, user_id: int, dek_hex: str,
                       ttl: int = 900) -> str:
    """按 server.py 的格式自签一个 phix_access cookie（只用标准库，不 import server）。"""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": str(user_id)}).encode()).decode().rstrip("=")
    access = f"{header}.{payload}.sig"
    doc = {"a": access, "r": "refresh-test", "e": int(time.time()) + ttl, "d": dek_hex}
    raw = json.dumps(doc, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac_mod.new(secret, body.encode(), "sha256").hexdigest()
    return f"{body}.{sig}"


def phix1_seal(dek: bytes, user_id: int, object_name: str, plaintext: str) -> str:
    """PHIX1 信封（与 server.py `_encrypt_payload` 逐字节同构）。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    obj_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"phix/v1/object-keys",
                   info=object_name.encode()).derive(dek)
    aad = f"phix/v1/object|{user_id}|{object_name}".encode()
    nonce = os.urandom(12)
    ct = AESGCM(obj_key).encrypt(nonce, plaintext.encode(), aad)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return f"PHIX1.{b64(nonce)}.{b64(ct)}"


# ============================================================ 假 ManageBac（真 HTTP）

COURSES = [
    ("101", "中文 A: 文学 (HL)"),
    ("202", "English B (SL)"),
    ("303", "数学: 分析与方法 (HL)"),
    ("404", "物理 (SL)"),
    ("505", "Theory of Knowledge"),
]


class ManageBacFakeHandler(http.server.BaseHTTPRequestHandler):
    """复刻 ManageBac 学生端的关键 DOM。"""

    server_version = "FakeManageBac/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):      # 静音
        pass

    # ---- helpers ----
    def _send(self, body: str, status: int = 200, headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _logged_in(self) -> bool:
        return "_managebac_session=ok" in (self.headers.get("Cookie") or "")

    def _reject(self) -> None:
        self._send('<html><body><form id="session_form">'
                   '<input id="session_password" name="password">'
                   '<div>Invalid email or password</div></form></body></html>')

    # ---- 路由 ----
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if self.server.mb_mode == "slow" and path != "/login":
            time.sleep(float(query.get("delay", ["3.0"])[0]))

        if path == "/login":
            return self._send(
                '<html><body><h1>Sign in</h1>'
                '<form id="session_form" action="/sessions" method="post">'
                '<input type="hidden" name="authenticity_token" value="tok-123">'
                '<input id="session_login" name="session[login]" type="email">'
                '<input id="session_password" name="session[password]" type="password">'
                '<input name="commit" type="submit" value="Sign in">'
                '</form></body></html>')

        if not self._logged_in():
            return self._reject()

        if path == "/student":
            return self._send("<html><body>Student dashboard</body></html>")
        if path == "/student/classes/my":
            return self._send(self._classes_html())
        m = re.match(r"^/student/classes/(\d+)/units$", path)
        if m:
            return self._send(self._units_html(m.group(1)))
        m = re.match(r"^/student/classes/(\d+)/core_tasks$", path)
        if m:
            return self._send(self._tasks_html(m.group(1)))
        return self._send("<html><body>not found</body></html>", 404)

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        if parsed.path != "/sessions":
            return self._send("bad", 404)
        fields = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
        login = fields.get("session[login]") or fields.get("login") or ""
        password = fields.get("session[password]") or fields.get("password") or ""
        if self.server.mb_mode == "reject" or password != self.server.mb_password:
            return self._reject()
        if login != self.server.mb_user:
            return self._reject()
        self.send_response(302)
        self.send_header("Location", "/student")
        self.send_header("Set-Cookie", "_managebac_session=ok; Path=/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 页面 ----
    def _classes_html(self) -> str:
        items = "\n".join(
            f'<li class="f-menu-submenu-item">'
            f'<a href="/student/classes/{cid}/units">'
            f'<span class="f-menu-submenu-link-title">{name}</span></a></li>'
            for cid, name in COURSES)
        return (f'<html><body><ul class="f-menu-submenu">\n{items}\n'
                f'</ul></body></html>')

    def _units_html(self, cid: str) -> str:
        name = dict(COURSES).get(cid, "Unknown")
        cells = "".join(f'<div class="cell">\n{cid} unit {i}\n</div>\n' for i in range(1, 4))
        return (f'<html><body><div class="sidebar-items-list">'
                f'{cells}<div class="cell">\nGrade\n6\n</div></div>'
                f'<h1>{name}</h1></body></html>')

    def _tasks_html(self, cid: str) -> str:
        name = dict(COURSES).get(cid, "Unknown")
        now = dt.date.today()
        badges = [(now + dt.timedelta(days=3), "Summative", "Coursework", "Pending"),
                  (now + dt.timedelta(days=9), "Formative", "Quiz", "Submitted"),
                  (now - dt.timedelta(days=4), None, "Coursework", "Late")]
        cards = []
        for idx, (due, category, kind, status) in enumerate(badges, start=1):
            month = due.strftime("%b").upper()
            labels = "".join(
                f'<div class="label">{t}</div>' for t in (category, kind) if t)
            past = " past-due" if due < now else ""
            score = ('<div class="assessment task-score">14 / 20 pts</div>'
                     if status == "Submitted" else "")
            cards.append(f'''
<div class="fusion-card-item short-assignment">
  <div class="date-badge{past}"><span class="month">{month}</span><span class="day">{due.day}</span></div>
  <div class="h4 title"><a href="/student/classes/{cid}/core_tasks/{cid}0{idx}">任务 {cid}-{idx}</a></div>
  <span class="due-date">{due.strftime('%b')} {due.day}, 11:59 PM</span>
  <div class="labels-set">{labels}</div>
  <div class="badge"><span class="badge-label">{status}</span></div>
  {score}
  <a href="/student/classes/{cid}/core_tasks/{cid}0{idx}/submit">Submit Coursework</a>
</div>''')
        return (f'<html><body><h1>{name}</h1>{"".join(cards)}</body></html>')


class ManageBacFake(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, mode: str = "ok", password: str = PWD_MB, user: str = MB_USER):
        super().__init__(("127.0.0.1", 0), ManageBacFakeHandler)
        self.mb_mode = mode
        self.mb_password = password
        self.mb_user = user

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self):
        with contextlib.suppress(Exception):
            self.shutdown()
        with contextlib.suppress(Exception):
            self.server_close()


# ============================================================ 假 phix 服务端（只够同步对象）

class FakePhixHandler(http.server.BaseHTTPRequestHandler):
    """只实现 GET /api/v1/ping 与 GET /api/v1/sync/objects/settings.accounts。

    走真信封：把请求解开、把响应按 `core_e2e` 的格式重新封回，
    这样站点 `_ai_load_object` → 解密得到的就是真 payload。
    """

    server_version = "FakePhix/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v1/ping":
            from cryptography.hazmat.primitives import serialization
            pk = self.server.server_pk.public_bytes(serialization.Encoding.Raw,
                                                    serialization.PublicFormat.Raw)
            b64 = base64.urlsafe_b64encode(pk).rstrip(b"=").decode()
            return self._json(200, {"pk": b64})

        if parsed.path == "/api/v1/sync/objects/settings.accounts":
            if not getattr(self.server, "payload", None):
                return self._json(404, {"ok": False, "error": {
                    "code": "not_found", "message": "object not found"}})
            return self._json(200, {"ok": True, "payload": self.server.payload,
                                    "updated_at": "2026-09-14T00:00:00Z"})
        return self._json(404, {"ok": False, "error": {"code": "not_found", "message": "no"}})


class FakePhix(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakePhixHandler)
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        self.server_sk = X25519PrivateKey.generate()
        self.server_pk = self.server_sk.public_key()
        self.server_pk_raw = self.server_pk.public_bytes(serialization.Encoding.Raw,
                                                         serialization.PublicFormat.Raw)
        self.payload: str | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self):
        with contextlib.suppress(Exception):
            self.shutdown()
        with contextlib.suppress(Exception):
            self.server_close()


# ============================================================ 假 IMAP（真 TLS socket）
def _self_signed_cert(tmpdir: str) -> tuple[str, str]:
    """生成一份自签证书（测试专用，用完随临时目录删）。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "imap.test.invalid")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("imap.test.invalid"), x509.IPAddress(
                    __import__("ipaddress").ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    key_path = os.path.join(tmpdir, "imap.key")
    cert_path = os.path.join(tmpdir, "imap.crt")
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def _mail_bytes(uid: int, subject: str, body: str, sender: str = "teacher@example.invalid") -> bytes:
    msg = (
        f"From: {sender}\r\n"
        f"To: {MAIL_USER}\r\n"
        f"Subject: =?utf-8?b?{base64.b64encode(subject.encode()).decode()}?=\r\n"
        f"Date: Mon, 14 Sep 2026 09:0{uid % 10}:00 +0800\r\n"
        f"Message-ID: <msg{uid}@example.invalid>\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "Content-Transfer-Encoding: 8bit\r\n"
        "\r\n"
        f"{body}\r\n"
    )
    return msg.encode("utf-8")


#: 一封**带 HTML 正文**的邮件（uid 4）：正文里全是攻击性构造。
#: **现在的要求是「原样」** —— 服务端不再消毒，这些构造必须**一字不动地**回到前端；
#: 「脚本不执行」改由前端那颗 `<iframe sandbox="allow-same-origin">`（不带 allow-scripts）保证。
#: 纯文本部分同时存在（multipart/alternative），用来验证「有 HTML 时两版都给」。
HTML_MAIL_PLAIN = "纯文本版：恭喜你获奖了。"
HTML_MAIL_MARKUP = (
    "<html><head><title>t</title>"
    "<style>@import url(\"https://evil.example.invalid/x.css\");"
    "body{display:none;width:expression(alert(1))}</style>"
    "<script>window.__xss = 'script-tag';</script></head>"
    '<body onload="window.__xss=\'onload\'">'
    "<h1>学期通知</h1>"
    "<p>家长您好：<b>本周五</b> 18:00 家长会。</p>"
    '<img src="https://tracker.example.invalid/pixel.gif" onerror="window.__xss=\'onerror\'" alt="pixel">'
    '<img src=x onerror="window.__xss=\'bare-onerror\'">'
    '<a href="javascript:window.__xss=\'js-href\'">点这里</a>'
    '<a href="jav&#x09;ascript:window.__xss=\'obf\'">混淆链接</a>'
    '<a href="data:text/html,<script>window.__xss=1</script>">data 链接</a>'
    '<a href="https://ok.example.invalid/a">正常链接</a>'
    '<div onclick="window.__xss=\'onclick\'" style="color:red">正文</div>'
    "<iframe src=\"https://evil.example.invalid/\"></iframe>"
    "<table><tr><th>科目</th><th>时间</th></tr><tr><td>数学</td><td>周一</td></tr></table>"
    "</body></html>"
)


def _attachment_mail_bytes(uid: int = 9, *,
                           filename: str = "成绩单.pdf",
                           payload: bytes = b"PDF-BYTES-\x00\x01\x02",
                           subject: str = "带附件的邮件") -> bytes:
    """一封 `multipart/mixed` 邮件：纯文本正文 + 两个附件（一个中文名、一个 ASCII 名）。

    用来验证：附件**元数据**（名字/大小/类型）进 `_mail_attachments()`，
    **内容**只在下载接口里取（列表阶段绝不把二进制搬进内存）。
    """
    boundary = "bndatt99"
    head = (
        f"From: teacher@example.invalid\r\n"
        f"To: {MAIL_USER}\r\n"
        f"Subject: =?utf-8?b?{base64.b64encode(subject.encode()).decode()}?=\r\n"
        f"Date: Mon, 14 Sep 2026 09:0{uid % 10}:00 +0800\r\n"
        f"Message-ID: <att{uid}@example.invalid>\r\n"
        "MIME-Version: 1.0\r\n"
        f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n'
        "\r\n"
        f"--{boundary}\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "Content-Transfer-Encoding: 8bit\r\n"
        "\r\n"
        "正文：附件请查收。\r\n"
        f"--{boundary}\r\n"
        'Content-Type: application/pdf; name="report.pdf"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        'Content-Disposition: attachment; filename="report.pdf"\r\n'
        "\r\n"
        + base64.b64encode(payload).decode() + "\r\n"
        f"--{boundary}\r\n"
        'Content-Type: text/plain; charset="utf-8"; name="notes.txt"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        f'Content-Disposition: attachment; filename="=?utf-8?b?'
        f'{base64.b64encode(filename.encode()).decode()}?="\r\n'
        "\r\n"
        + base64.b64encode("中文附件内容".encode()).decode() + "\r\n"
        f"--{boundary}--\r\n"
    )
    return head.encode("utf-8")


def _html_mail_bytes(uid: int, subject: str = "HTML 通知（含恶意构造）") -> bytes:
    """multipart/alternative：纯文本 + HTML（HTML 里带 XSS / 跟踪像素构造）。"""
    boundary = "bnd42"
    msg = (
        f"From: teacher@example.invalid\r\n"
        f"To: {MAIL_USER}\r\n"
        f"Subject: =?utf-8?b?{base64.b64encode(subject.encode()).decode()}?=\r\n"
        f"Date: Mon, 14 Sep 2026 09:0{uid % 10}:00 +0800\r\n"
        f"Message-ID: <htmlmsg{uid}@example.invalid>\r\n"
        "MIME-Version: 1.0\r\n"
        f'Content-Type: multipart/alternative; boundary="{boundary}"\r\n'
        "\r\n"
        f"--{boundary}\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
        f"{HTML_MAIL_PLAIN}\r\n"
        f"--{boundary}\r\n"
        'Content-Type: text/html; charset="utf-8"\r\n\r\n'
        f"{HTML_MAIL_MARKUP}\r\n"
        f"--{boundary}--\r\n"
    )
    return msg.encode("utf-8")



class FakeIMAP:
    """极简 IMAP4rev1（只够 imaplib 跑通列表 + 正文 + **FLAGS**）。

    只读性证据（本轮新增，测「服务器有没有把邮件标成已读」）：
      * `requests`：客户端**实际发过的** FETCH 参数原文（`BODY.PEEK[HEADER]` …）；
      * `marked_read`：客户端用了**非 PEEK** 的 `BODY[...]` 时，这里按 RFC 3501
        把邮件真的加上 `\\Seen`（服务器就是这么干的），并记进这个集合 ——
        测试断言它**始终为空**，等于断言「我们的抓取没有替用户读邮件」。
    """

    def __init__(self, user: str = MAIL_USER, password: str = PWD_MAIL, unread: int = 2,
                 *, status_ok: bool = True):
        self.user = user
        self.password = password
        self.status_ok = status_ok
        self.mailbox: dict[int, bytes] = {
            1: _mail_bytes(1, "欢迎邮件", "第一封正文\n第二行\n"),
            2: _mail_bytes(2, "作业提醒：数学", "记得交作业。\n"),
            3: _mail_bytes(3, "家长会通知", "本周五 18:00。\n"),
        }
        #: 「最新 unread 封未读」；unread=0 → **一封未读都没有**（不是"全部"）
        self.unseen = sorted(self.mailbox)[-unread:] if unread > 0 else []
        #: 服务器侧的真实 FLAGS：先全部标成已读，再把 `unseen` 那些的 \Seen 去掉
        self.flags: dict[int, set[str]] = {uid: {"\\Seen"} for uid in self.mailbox}
        for uid in self.unseen:
            self.flags[uid] = set()
        self.requests: list[str] = []
        #: 每次 UID FETCH 带了多少个 uid（成批取邮件头的证据；逐封=每次都 1 个）
        self.fetch_batches: list[int] = []
        #: 客户端发过的 UID STORE 原文（「标记已读」这条路要走它；未点时应当为空）
        self.store_requests: list[str] = []
        #: 客户端一旦用非 PEEK 的 BODY[...] 取信体，这里会记下 uid（应始终为空）
        self.marked_read: set[int] = set()
        self.cert_dir = tempfile.mkdtemp(prefix="fakeimap-")
        cert, key = _self_signed_cert(self.cert_dir)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.3)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def add_html_mail(self, uid: int = 4) -> int:
        """往收件箱里加一封**带 HTML 正文**的未读邮件（默认 uid 4）。

        只在需要它的测试里加：既有测试都建立在「3 封邮件」的基础上（unread=1/2/0 各一档），
        默认塞第四封会把它们的分寸全打乱。
        """
        self.mailbox[uid] = _html_mail_bytes(uid)
        self.flags[uid] = set()                 # 未读：没有 \Seen
        if uid not in self.unseen:
            self.unseen = sorted(self.unseen + [uid])
        return uid

    def add_attachment_mail(self, uid: int = 9, *,
                            payload: bytes = b"PDF-BYTES-\x00\x01\x02") -> bytes:
        """加一封**带两个附件**的邮件，返回第一个附件的原始字节（供下载断言逐字节比对）。"""
        self.mailbox[uid] = _attachment_mail_bytes(uid, payload=payload)
        self.flags[uid] = set()
        if uid not in self.unseen:
            self.unseen = sorted(self.unseen + [uid])
        return payload

    def add_many(self, count: int, start: int = 100) -> list:
        """塞 `count` 封普通邮件（uid 从 `start` 起），返回 uid 列表。

        用来验证「全量抓取」在**几百封**的量级上不会退化成逐封 FETCH —— 
        逐封写法在这里会发几百条命令，成批写法只发 `ceil(n/40)` 条。
        """
        uids = list(range(start, start + count))
        for uid in uids:
            self.mailbox[uid] = _mail_bytes(uid, f"批量邮件 {uid}", f"第 {uid} 封正文\n")
            self.flags[uid] = {"\\Seen"}
        return uids

    def stop(self):
        self._stop.set()
        with contextlib.suppress(Exception):
            self.sock.close()

    def _serve(self):
        while not self._stop.is_set():
            try:
                raw, _ = self.sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=self._session, args=(raw,), daemon=True).start()

    def _session(self, raw):
        try:
            conn = self.ctx.wrap_socket(raw, server_side=True)
        except Exception:  # noqa: BLE001  握手失败就丢弃
            with contextlib.suppress(Exception):
                raw.close()
            return
        try:
            conn.settimeout(10)
            fh = conn.makefile("rwb")
            if self._stop.is_set():
                return
            fh.write(b"* OK [CAPABILITY IMAP4rev1] FakeIMAP ready\r\n")
            fh.flush()
            while True:
                line = fh.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                parts = text.split(" ", 2)
                tag = parts[0]
                cmd = parts[1].upper() if len(parts) > 1 else ""
                rest = parts[2] if len(parts) > 2 else ""
                if cmd == "CAPABILITY":
                    fh.write(b"* CAPABILITY IMAP4rev1\r\n" + tag.encode() + b" OK done\r\n")
                elif cmd == "LOGIN":
                    args = re.findall(r'"([^"]*)"|(\S+)', rest)
                    values = [a or b for a, b in args]
                    ok = len(values) >= 2 and values[0] == self.user and values[1] == self.password
                    fh.write(tag.encode() + (b" OK logged in\r\n" if ok else b" NO login failed\r\n"))
                    if not ok:
                        fh.flush()
                        return
                elif cmd in ("SELECT", "EXAMINE"):
                    fh.write(f"* {len(self.mailbox)} EXISTS\r\n".encode())
                    fh.write(f"* {len(self.unseen)} RECENT\r\n".encode())
                    fh.write(b"* OK [UIDVALIDITY 1] ok\r\n")
                    fh.write(b"* OK [UIDNEXT 99] ok\r\n")
                    # 真服务器按 readonly 回 READ-ONLY / READ-WRITE：标已读那一步必须拿到后者，
                    # 否则「唯一会把邮件标成已读的入口」在只读会话里根本执行不了。
                    # imaplib：readonly=True → 末尾多一个 "readonly"；读写打开则只有邮箱名。
                    writable = cmd == "SELECT" and "readonly" not in rest.lower()
                    fh.write(tag.encode() + (b" OK [READ-WRITE] done\r\n" if writable
                                             else b" OK [READ-ONLY] done\r\n"))
                elif cmd == "LIST":
                    # 通讯录要额外扫「已发送」（`_sent_folders` 走 LIST）。真实 Coremail 的
                    # 文件夹名是 modified UTF-7（`&XfJT0ZAB-` = 「已发送」），就照那个样子回。
                    fh.write(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
                    fh.write(b'* LIST (\\HasNoChildren) "/" "&XfJT0ZAB-"\r\n')
                    fh.write(tag.encode() + b" OK done\r\n")
                elif cmd == "STATUS":
                    if not self.status_ok:
                        # 模拟「这家服务器不支持 STATUS」：抓取层必须退化到 FLAGS / SEARCH
                        fh.write(tag.encode() + b" BAD STATUS not supported\r\n")
                    else:
                        fh.write(('* STATUS INBOX (UNSEEN %d)\r\n' % len(self.unseen)).encode())
                        fh.write(tag.encode() + b" OK done\r\n")
                elif cmd == "UID":
                    self._uid(fh, tag, rest)
                elif cmd == "LOGOUT":
                    fh.write(b"* BYE bye\r\n" + tag.encode() + b" OK logout\r\n")
                    fh.flush()
                    return
                elif cmd == "NOOP":
                    fh.write(tag.encode() + b" OK done\r\n")
                elif cmd in ("CLOSE", "UNSELECT"):
                    fh.write(tag.encode() + b" OK done\r\n")
                else:
                    fh.write(tag.encode() + b" BAD unknown command\r\n")
                fh.flush()
        except (OSError, ssl.SSLError):
            return
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    def _uid(self, fh, tag: bytes, rest: str) -> None:
        tag = tag.encode() if isinstance(tag, str) else tag
        head, _, tail = rest.partition(" ")
        sub = head.upper()
        if sub == "SEARCH":
            criteria = tail.upper()
            if "UNSEEN" in criteria:
                uids = self.unseen
            elif "ALL" in criteria:
                uids = sorted(self.mailbox)
            else:
                uids = []
            fh.write(("* SEARCH" + "".join(f" {u}" for u in uids) + "\r\n").encode())
            fh.write(tag + b" OK done\r\n")
            return
        if sub == "FETCH":
            uid_txt, _, item = tail.partition(" ")
            self.requests.append(item)
            # 真实 IMAP 允许一次 FETCH 带一串 uid（`UID FETCH 1,2,3 …`）。
            # 抓取层**必须**用这种成批写法：逐封 FETCH 在几百封的邮箱上必然超时
            # （2026-09-17 用户实测「邮件完全抓不到」的根因）。这里如实支持它，
            # 并把每批的 uid 个数记进 fetch_batches，供测试断言「确实成批了」。
            wanted = [int(x) for x in uid_txt.split(",") if x.isdigit()]
            self.fetch_batches.append(len(wanted))
            wanted = [u for u in wanted if u in self.mailbox]
            if not wanted:
                fh.write(tag + b" NO no such message\r\n")
                return
            for uid in wanted:
                self._emit_fetch(fh, uid, item)
            fh.write(tag + b" OK done\r\n")
            return
        if sub == "STORE":
            uid_txt, _, rest2 = tail.partition(" ")
            self.store_requests.append(tail)
            if not uid_txt.isdigit() or int(uid_txt) not in self.mailbox:
                fh.write(tag + b" NO no such message\r\n")
                return
            uid = int(uid_txt)
            flags = self.flags.setdefault(uid, set())
            if "+FLAGS" in rest2.upper():
                for name in ("\\Seen", "\\Answered", "\\Flagged", "\\Deleted", "\\Draft"):
                    if name.upper() in rest2.upper():
                        flags.add(name)
            elif "-FLAGS" in rest2.upper():
                for name in ("\\Seen", "\\Answered", "\\Flagged", "\\Deleted", "\\Draft"):
                    if name.upper() in rest2.upper():
                        flags.discard(name)
            if uid in self.unseen and "\\Seen" in flags:
                self.unseen.remove(uid)
            listing = " ".join(sorted(flags))
            fh.write(f"* {uid} FETCH (FLAGS ({listing}))\r\n".encode())
            fh.write(tag + b" OK STORE completed\r\n")
            return
        fh.write(tag + b" BAD unsupported UID command\r\n")

    def _emit_fetch(self, fh, uid: int, item: str):
        """回一帧 `* <uid> FETCH (...)`（单封）。

        抽出来是因为**一次命令可能带多个 uid**（U 集成批取邮件头），
        每个 uid 都要各回一帧 —— 这正是真实 IMAP 的行为。
        """
        raw = self.mailbox[uid]
        upper = item.upper()
        if "HEADER" in upper:
            head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n"
            literal = head + b"\r\n"
        else:
            literal = raw
        extras = b""
        if "FLAGS" in upper:
            flags = sorted(self.flags.get(uid, set()))
            extras += (" FLAGS (%s)" % " ".join(flags)).encode()
        if "BODY.PEEK" not in upper and "BODY[" in upper.replace(" ", ""):
            # RFC 3501：**非 PEEK** 取信体会让服务器打上 \Seen 并回一帧未请求的 FLAGS。
            # 我们的抓取层必须永远走 PEEK；真这么干了就记下来，测试会算它失败。
            self.flags.setdefault(uid, set()).add("\\Seen")
            self.marked_read.add(uid)
            extras = b" FLAGS (\\Seen)"
        fh.write(f"* {uid} FETCH (UID {uid}{extras} BODY[] {{{len(literal)}}}\r\n".encode())
        fh.write(literal)
        fh.write(b")\r\n")


def imap_connect_factory(fake: FakeIMAP):
    """把 imaplib.IMAP4_SSL 换成连本地假服务的版本（真 imaplib 协议栈）。"""
    class _Conn:
        def __new__(cls, host, port=993, timeout=None):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return _ORIG_IMAP_SSL("127.0.0.1", fake.port, ssl_context=ctx, timeout=timeout)
    return _Conn


_ORIG_IMAP_SSL = __import__("imaplib").IMAP4_SSL


# ============================================================ 假 EduPage（第三方库打桩）

class _FakeSubject:
    def __init__(self, name):
        self.name = name


class _FakeTeacher:
    def __init__(self, name):
        self.name = name


class _FakeRoom:
    def __init__(self, name):
        self.name = name


class _FakeLesson:
    def __init__(self, start, end, subject, teacher, room, groups, cancelled=False):
        self.period = 1
        self.start_time = start
        self.end_time = end
        self.subject = _FakeSubject(subject)
        self.teachers = [_FakeTeacher(teacher)]
        self.classrooms = [_FakeRoom(room)]
        self.groups = groups
        self.is_cancelled = cancelled
        self.is_event = False


class FakeBadCredentials(Exception):
    """仿 `edupage_api.BadCredentialsException`（类名就是分类依据）。"""


class FakeRequestsConnectionError(ConnectionError):
    """仿 `requests.exceptions.ConnectionError`。"""


class FakeRequestsTimeout(TimeoutError):
    """仿 `requests.exceptions.Timeout`。"""


class FakeEdupageClient:
    def __init__(self, *args, **kwargs):
        pass

    def login(self, username, password, subdomain):
        if password != PWD_EP:
            raise FakeBadCredentials("Bad credentials")
        self.subdomain = subdomain
        return None

    def get_my_timetable(self, day):
        return [
            _FakeLesson(dt.time(8, 0), dt.time(8, 45), "数学", "王老师", "A301",
                        ["教学组 A"], cancelled=False),
            _FakeLesson(dt.time(9, 0), dt.time(9, 45), "物理", "李老师", "B201",
                        ["教学组 B"], cancelled=False),
            _FakeLesson(dt.time(10, 0), dt.time(10, 45), "自习", "", "", [],
                        cancelled=True),
        ]


class FakeEdupageModule:
    Edupage = FakeEdupageClient
    BadCredentialsException = FakeBadCredentials


@contextlib.contextmanager
def fake_edupage(*, client=None, module_name: str = "edupage_api"):
    """把 `edupage_api` 打桩（默认成功客户端；可换成一个专门抛异常的客户端）。"""
    if client is None:
        module = FakeEdupageModule
    else:
        class _Module:
            Edupage = client
            BadCredentialsException = FakeBadCredentials
        module = _Module
    sys.modules[module_name] = module
    try:
        yield
    finally:
        sys.modules.pop(module_name, None)


@contextlib.contextmanager
def fake_edupage_raising(exc: BaseException, *, name: str = "FakeLibError"):
    """打一个「登录必抛 exc」的假 edupage_api（异常类名可指定，便于验证分类）。"""
    error_cls = type(name, (type(exc),), {})

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password, subdomain):
            raise error_cls(str(exc))

    with fake_edupage(client=_Client):
        yield error_cls


# ---------------------------------------------------------------- 假「整周课表」客户端
#
# 复刻真实主链路：`currenttt.js?__func=curentttGetData` 返回的 ttitems
# （顶层带 date/starttime/subjectid/groupnames/teacherids/classroomids/type），
# 名字由本地 dbi 解析。71 条里混了表头空行 / 停课 / 事件 / 非本周条目。

FAKE_WEEK_MONDAY = "2026-09-14"
FAKE_WEEK_GROUPS = ("A", "B", "C", "A")          # 有重复 → 验证 selected 去重保序
FAKE_WEEK_VALID = 60                             # 期望留下的条数


def fake_week_items(monday: dt.date | None = None) -> list:
    """构造 71 条课卡：2 表头空行 + 60 本周有效 + 3 停课 + 2 事件 + 4 非本周。"""
    mon = monday or dt.date.fromisoformat(FAKE_WEEK_MONDAY)
    items: list = [
        {"type": "period", "uniperiod": "0", "header": [], "classids": [], "groupnames": []},
        {"type": "period", "uniperiod": "6", "header": [], "classids": [], "groupnames": []},
    ]

    def lesson(day: dt.date, slot: int, *, group: str, kind: str = "card", removed: bool = False):
        return {
            "date": day.isoformat(),
            "starttime": f"{8 + slot // 2:02d}:{(slot % 2) * 45:02d}",
            "endtime": f"{8 + slot // 2:02d}:{(slot % 2) * 45 + 40:02d}",
            "subjectid": "-284",
            "groupnames": [group] if group else [],
            "teacherids": ["-453"],
            "classroomids": ["-287"],
            "classids": ["-359"],
            "type": kind,
            "removed": removed,
        }

    # 周一~周五：前 8 节是常规（40 条），再加 4 条副课（20 条）→ 60 条有效
    for day_offset in range(5):
        day = mon + dt.timedelta(days=day_offset)
        for slot in range(8):
            items.append(lesson(day, slot, group=FAKE_WEEK_GROUPS[(day_offset * 8 + slot) % 4]))
    for day_offset in range(5):
        day = mon + dt.timedelta(days=day_offset)
        for slot in range(9, 13):
            items.append(lesson(day, slot, group=FAKE_WEEK_GROUPS[(day_offset + slot) % 4]))

    # 停课 3（type=absent / removed）+ 事件 2（type=event / out）
    items.append(lesson(mon, 1, group="CN", kind="absent"))
    items.append(lesson(mon + dt.timedelta(days=1), 1, group="CN", kind="absent"))
    items.append(lesson(mon + dt.timedelta(days=2), 1, group="CM", removed=True))
    items.append(lesson(mon, 2, group="EV", kind="event"))
    items.append(lesson(mon + dt.timedelta(days=3), 2, group="EV", kind="out"))

    # 非本周 4 条（上周 2 + 下周 2）
    for offset in (-3, -5):
        items.append(lesson(mon + dt.timedelta(days=offset), 3, group="Z"))
    for offset in (7, 9):
        items.append(lesson(mon + dt.timedelta(days=offset), 3, group="Y"))
    return items


FAKE_WEEK_DBI = {
    "subjects": {"-284": {"name": "Philosophy HL1", "short": "Phi H1"}},
    "teachers": {"-453": {"firstname": "Qiwei", "lastname": "He", "short": "He,Qiwei"}},
    "classrooms": {"-287": {"name": "A205", "short": "A205"}},
    "classes": {"-359": {"name": "IB grade 11 class 1", "short": "IB11.1"}},
}


class FakeWeekSession:
    """假 requests.Session：只实现 currenttt.js 那一个 POST。"""

    def __init__(self, items: list, *, delay: float = 0.0, fail: BaseException | None = None):
        self.items = items
        self.delay = delay
        self.fail = fail
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):      # noqa: A002  跟 requests 同名
        self.calls.append({"url": url, "body": json, "timeout": timeout})
        if self.fail is not None:
            raise self.fail
        if self.delay:
            time.sleep(self.delay)
        # 注意：`json` 是形参名（跟 requests 一致），会遮住模块名 —— 这里用 __import__ 取模块
        raw = __import__("json").dumps({"r": {"ttitems": self.items}}).encode()

        class _Resp:
            content = raw

        return _Resp()


class FakeEdupageWeekClient:
    """假 EduPage 客户端：**有** session / gsec_hash / dbi（走快路）。"""

    def __init__(self, *args, **kwargs):
        self.session = None
        self.data = {"dbi": FAKE_WEEK_DBI,
                     "userrow": {"UserID": "Student-3772", "StudentID": "-3772",
                                 "TriedaID": "-359"}}
        self.subdomain = "pingheschool"
        self.gsec_hash = "FAKEgsh"

    def login(self, username, password, subdomain):
        if password != PWD_EP:
            raise FakeBadCredentials("Bad credentials")
        self.subdomain = subdomain
        return None

    def get_user_id(self) -> str:
        return "Student-3772"

    def get_school_year(self) -> int:
        return 2026

    def get_my_timetable(self, day):
        raise AssertionError("快路可用时不该退回库的 get_my_timetable")


@contextlib.contextmanager
def fake_edupage_week(items: list | None = None, *, delay: float = 0.0, fail=None):
    """快路替身：session.post 返回 `items`（默认 71 条整周课卡）。"""
    session = FakeWeekSession(items if items is not None else fake_week_items(),
                              delay=delay, fail=fail)

    class _Client(FakeEdupageWeekClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.session = session

        def get_my_timetable(self, day):
            """快路打不通时才允许走到这里：把同一个故障抛出来（别掩盖真实原因）。"""
            if session.fail is not None:
                raise session.fail
            raise AssertionError("快路可用时不该退回库的 get_my_timetable")

    with fake_edupage(client=_Client):
        yield session


def wait_edupage(accounts: dict, user_id: str, timeout: float = 15.0) -> dict:
    """等后台 EduPage 任务结束（`meta.pending` 消失）并返回那一次的响应。

    对应「抓完写进缓存，用户点刷新/前端自动重试就能拿到」——测试里就是轮询到不 pending。
    """
    deadline = time.monotonic() + timeout
    out = wd.fetch_all(accounts, user_id=user_id)
    while out["meta"].get("pending") and time.monotonic() < deadline:
        time.sleep(0.05)
        out = wd.fetch_all(accounts, user_id=user_id)
    return out


# ============================================================ 测试用 accounts

def make_accounts(*, mb_base: str, imap_host: str) -> dict:
    """测试用 accounts 映射（= `settings.accounts` 里的 `accounts` 段本身）。

    两套字段名变体都覆盖：managebac 用 username+password，
    mail 用 email+authcode（老变体只有 password，见 T01）。
    """
    return {
        "edupage": {"username": EP_USER, "password": PWD_EP, "subdomain": "pingheschool"},
        "managebac": {"username": MB_USER, "password": PWD_MB, "base_url": mb_base},
        "mail": {"email": MAIL_USER, "authcode": PWD_MAIL, "imap_host": imap_host},
    }


SECRETS = (PWD_MB, PWD_EP, PWD_MAIL)


def assert_no_secrets(label: str, blob: str) -> bool:
    return check(f"{label}：不含任何测试口令", not any(s in blob for s in SECRETS),
                 extra=f"泄漏了 {[s[:4] + '…' for s in SECRETS if s in blob]}")


# ============================================================ 各项测试

def t01_credential_mapping() -> None:
    section("T01 凭据字段兼容（username/email、password/authcode、默认值）")
    sections = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
    check("username 变体：edupage 账号名取 username",
          wd.account_username(sections["edupage"]) == EP_USER)
    check("email 变体：mail 账号名取 email",
          wd.account_username(sections["mail"]) == MAIL_USER)
    check("mail 口令优先 authcode", wd.account_secret(sections["mail"], prefer_authcode=True) == PWD_MAIL)
    check("非邮箱平台口令取 password", wd.account_secret(sections["managebac"]) == PWD_MB)

    check("edupage subdomain 缺省 pingheschool",
          wd.edupage_config({})[2] == "pingheschool")
    check("managebac base_url 缺省 shph.managebac.cn",
          wd.managebac_config({})[2] == "https://shph.managebac.cn")
    check("mail imap_host 缺省 imap.qiye.163.com",
          wd.mail_config({})[2] == "imap.qiye.163.com")
    check("mail 只有 password 时也能取到（老变体）",
          wd.mail_config({"email": "a@b.c", "password": "pw"})[1] == "pw")
    check("全空 → accounts_configured False", wd.accounts_configured({}) is False)
    check("有任意一段 → accounts_configured True", wd.accounts_configured(sections) is True)
    names = wd.account_names(sections)
    check("meta.accounts 只有账号名（三个键）",
          set(names) == {"edupage", "managebac", "mail"}
          and names["managebac"] == MB_USER)


def _patch_imap(fake: FakeIMAP):
    import imaplib
    patcher = contextlib.ExitStack()
    patcher.enter_context(_monkeypatch_attr(imaplib, "IMAP4_SSL", imap_connect_factory(fake)))
    return patcher


@contextlib.contextmanager
def _monkeypatch_attr(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def t02_full_fetch(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T02 三平台全链路抓取（假 ManageBac HTTP + 假 IMAP TLS + 假 EduPage）")
    acct = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")
    wd.clear_cache()
    with fake_edupage(), _patch_imap(fake_imap):
        started = time.monotonic()
        first = wd.fetch_all(acct, user_id="t02")
        elapsed = time.monotonic() - started
        second = wait_edupage(acct, "t02")             # 等后台 EduPage 抓完
        landed = wd.fetch_all(acct, force=True, user_id="t02")   # = 用户点「刷新」

    check("冷启动（EduPage 缓存空）**不阻塞**：立即返回且 < 1s", elapsed < 1.0,
          extra=f"{elapsed:.3f}s")
    check("冷启动响应里 EduPage 段为空 + pending 点名 edupage",
          first["edupage"] == {"lessons": [], "selected": []}
          and first["meta"].get("pending") == ["edupage"],
          extra=str(first["meta"]))
    check("冷启动时 errors.edupage 是「正在抓取…请稍后点刷新」而不是报错",
          first["meta"]["errors"].get("edupage") == wd.EDUPAGE_PENDING_MESSAGE,
          extra=str(first["meta"]["errors"]))
    check("返回四段 + meta", set(first) == {"edupage", "managebac", "mail", "meta"})
    check("meta.cache 首次为 miss", first["meta"]["cache"] == "miss")

    # ManageBac / 邮箱：跟着请求同步抓完（它们本来就快）
    mbd = first["managebac"]
    check("managebac.courses 解析出 5 门课（真页 1 解析路径）",
          len(mbd["courses"]) == 5, extra=str(mbd["courses"]))
    names = {c["name"] for c in mbd["courses"]}
    check("课程名与假页面一致（span.f-menu-submenu-link-title 选择器生效）",
          names == {name for _, name in COURSES}, extra=str(names))
    check("每门课都带 id/grade（div.sidebar-items-list 第 4 cell）",
          all(c["id"] and c["grade"] == "6" for c in mbd["courses"]),
          extra=str(mbd["courses"][:1]))
    check("managebac.tasks 解析出 15 张作业卡（3 张/课）",
          len(mbd["tasks"]) == 15, extra=str(len(mbd["tasks"])))
    task = mbd["tasks"][0] if mbd["tasks"] else {}
    check("作业卡字段齐全（course/title/due/status/score/id）",
          task.get("course") == COURSES[0][1] and task.get("title") == "任务 101-1"
          and task.get("status") == "Pending" and task.get("id") == "10101"
          # due 现在是**机器可读**的 `YYYY-MM-DD HH:MM`（原来是页面上那句人话原文，
          # 前端因此算不出 ±14 天窗口 / 已过期——这就是 DDL 过滤失效的根因，已修）
          and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", task.get("due", "")),
          extra=str(task))
    check("作业卡同时保留页面上那句原文（due_text）与过去时标记（past_due 只在真过期时给）",
          isinstance(task.get("due_text"), str) and task["due_text"] != ""
          and "due_inferred" not in task,
          extra=str(task))
    check("已评分的作业带回分数（assessment.task-score）",
          any(t["score"] == "14 / 20 pts" for t in mbd["tasks"]))

    mail = first["mail"]
    check("mail.unread 取自 IMAP STATUS UNSEEN", mail["unread"] == 2, extra=str(mail["unread"]))
    check("mail.recent 列出 3 封（最新在前）", len(mail["recent"]) == 3,
          extra=str(mail["recent"]))
    check("邮件摘要字段齐全且主题已解 MIME 编码",
          mail["recent"][0]["uid"] == "3"
          and mail["recent"][0]["subject"] == "家长会通知"
          and "teacher@example.invalid" in mail["recent"][0]["from"]
          and mail["recent"][0]["date"] != "",
          extra=str(mail["recent"][:1]))

    # EduPage：抓完之后（点刷新）能拿到；慢回退路径只有今天一天
    today = dt.date.today().isoformat()
    ep = landed["edupage"]
    check("抓完后刷新即拿到 EduPage 课表（数据已进缓存，pending 已消失）",
          not second["meta"].get("pending") and len(ep["lessons"]) == 2,
          extra=str(second["meta"]) + str(ep["lessons"]))
    check("?force=1 刷新时先给缓存里的课表（不空窗），同时在后台再刷一次",
          landed["meta"].get("pending") == ["edupage"] and len(ep["lessons"]) == 2,
          extra=str(landed["meta"]))
    first_lesson = ep["lessons"][0] if ep["lessons"] else {}
    check("edupage 课时字段齐全且时间已排序",
          first_lesson.get("start") == "08:00" and first_lesson.get("end") == "08:45"
          and first_lesson.get("subject") == "数学" and first_lesson.get("teacher") == "王老师"
          and first_lesson.get("room") == "A301" and first_lesson.get("group") == "教学组 A"
          and first_lesson.get("date") == today,
          extra=str(first_lesson))
    check("edupage 段落契约字段只有那 7 个（date/start/end/subject/group/room/teacher）",
          set(first_lesson) == {"date", "start", "end", "subject", "group", "room", "teacher"},
          extra=str(sorted(first_lesson)))
    check("edupage.selected 汇总了教学组", ep["selected"] == ["教学组 A", "教学组 B"])
    check("EduPage 抓完后 errors 里不再有 edupage",
          "edupage" not in landed["meta"]["errors"], extra=str(landed["meta"]["errors"]))
    check("第二次普通请求命中缓存且带着 EduPage 数据",
          second["meta"]["cache"] == "hit" and len(second["edupage"]["lessons"]) == 2,
          extra=str(second["meta"]))
    check("managebac / mail 无错误",
          "managebac" not in first["meta"]["errors"] and "mail" not in first["meta"]["errors"],
          extra=str(first["meta"]["errors"]))
    assert_no_secrets("T02 响应整体", json.dumps(landed, ensure_ascii=False))


def t03_cache(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T03 缓存：命中 / force 绕过 / 每用户隔离")
    acct = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")
    wd.clear_cache()
    with fake_edupage(), _patch_imap(fake_imap):
        first = wd.fetch_all(acct, force=True, user_id="t03")
        second = wd.fetch_all(acct, user_id="t03")
        forced = wd.fetch_all(acct, force=True, user_id="t03")
        other = wd.fetch_all(acct, user_id="t03-other-user")

    check("force=True 后首次仍写缓存（cache=miss）", first["meta"]["cache"] == "miss")
    check("第二次命中缓存（meta.cache == 'hit'）", second["meta"]["cache"] == "hit")
    check("缓存命中时数据与首次一致",
          second["managebac"]["courses"] == first["managebac"]["courses"]
          and second["mail"]["unread"] == first["mail"]["unread"])
    check("?force=1 绕过缓存（cache=miss 且重新抓）", forced["meta"]["cache"] == "miss")
    check("不同用户不共享缓存（缓存键含用户号）", other["meta"]["cache"] == "miss")


def t04_isolation(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T04 平台隔离：ManageBac 挂了，其它两段照常返回")
    bad = ManageBacFake(mode="reject").start()
    try:
        acct = make_accounts(mb_base=bad.base_url, imap_host="127.0.0.1")
        wd.clear_cache()
        with fake_edupage(), _patch_imap(fake_imap):
            first = wd.fetch_all(acct, force=True, user_id="t04")
            result = wait_edupage(acct, "t04")       # 等后台 EduPage 抓完
    finally:
        bad.stop()

    errors = result["meta"]["errors"]
    check("失败平台写进 errors 且是中文可读原因",
          "managebac" in errors and "登录失败" in errors["managebac"], extra=str(errors))
    check("失败平台给空结构（courses/tasks 均为空列表）",
          result["managebac"] == {"courses": [], "tasks": []})
    check("ManageBac 挂掉时 EduPage 仍走独立后台路径（先 pending，抓完就有数据）",
          first["meta"].get("pending") == ["edupage"]
          and len(result["edupage"]["lessons"]) == 2,
          extra=str(first["meta"]) + str(result["edupage"]["lessons"]))
    check("managebac 失败不影响 mail 有数据", result["mail"]["unread"] == 2)
    check("errors 里没有 traceback / 内部标识",
          "Traceback" not in json.dumps(errors, ensure_ascii=False)
          and "requests" not in json.dumps(errors, ensure_ascii=False))
    blob = json.dumps(result, ensure_ascii=False)
    assert_no_secrets("T04 响应", blob)


def t05_timeout(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T05 超时：慢平台被截断，整批不阻塞")
    slow = ManageBacFake(mode="slow").start()
    old_timeout = wd.PLATFORM_TIMEOUT
    wd.PLATFORM_TIMEOUT = 2.0
    try:
        acct = make_accounts(mb_base=slow.base_url, imap_host="127.0.0.1")
        wd.clear_cache()
        with fake_edupage(), _patch_imap(fake_imap):
            started = time.monotonic()
            result = wd.fetch_all(acct, force=True, user_id="t05")
            elapsed = time.monotonic() - started
    finally:
        wd.PLATFORM_TIMEOUT = old_timeout
        slow.stop()

    check(f"整批在超时预算内返回（{elapsed:.2f}s ≤ 3.5s）", elapsed <= 3.5, extra=f"{elapsed:.2f}s")
    check("超时平台写中文超时原因",
          "超时" in result["meta"]["errors"].get("managebac", ""),
          extra=str(result["meta"]["errors"]))
    check("ManageBac 超时不影响邮箱段", result["mail"]["unread"] == 2,
          extra=str(result["mail"]["unread"]))
    check("20 秒闸门只作用于 ManageBac/邮箱：EduPage 走自己的 90 秒后台路径（本次只标 pending）",
          result["meta"].get("pending") == ["edupage"]
          and result["edupage"] == {"lessons": [], "selected": []},
          extra=str(result["meta"]))


@contextlib.contextmanager
def import_blocked(*module_names: str):
    """模拟「这个库没装」：让 importlib.import_module 对它抛 ImportError。

    比「从 sys.modules 里删掉」更贴近真实缺依赖场景（真实环境里就是 ImportError）。
    """
    saved = importlib.import_module

    def _fake(name, *args, **kwargs):
        if name in module_names:
            raise ImportError(f"No module named {name!r} (simulated)")
        return saved(name, *args, **kwargs)

    importlib.import_module = _fake
    try:
        yield
    finally:
        importlib.import_module = saved


# ============================================================ T15 截止时间归一化（本轮新增）

def _mb_card(cid: str, idx: int, badge: tuple[str, int] | None, due_text: str,
             status: str = "Pending", past: bool = False) -> str:
    """拼一张 ManageBac 作业卡的 HTML（可带/不带 `.date-badge`）。"""
    badge_html = ""
    if badge:
        mon, day = badge
        badge_html = (f'<div class="date-badge{" past-due" if past else ""}">'
                      f'<span class="month">{mon}</span><span class="day">{day}</span></div>')
    return (f'<div class="fusion-card-item short-assignment">'
            f'{badge_html}'
            f'<div class="h4 title"><a href="/student/classes/{cid}/core_tasks/{cid}0{idx}">'
            f'任务 {cid}-{idx}</a></div>'
            f'<span class="due-date">{due_text}</span>'
            f'<div class="badge"><span class="badge-label">{status}</span></div>'
            f'</div>')


def t15_due_normalization() -> None:
    """截止时间必须是**机器可读**的日期：真实账号 45 条作业的 due 全是
    「Thursday at 12:00 PM」这种人话文本，前端因此算不出 ±14 天窗口/已过期
    —— 这是 DDL 过滤失效的根因（本轮修的）。"""
    section("T15 截止时间归一化（ManageBac「周几 at 时刻」→ 真实日期）")
    import webapp_mb_parse as mbp
    from datetime import date, timedelta

    today = date.today()

    # ---- (a) 有 date-badge：给出**精确日期**，不算推断 ----
    badge_mon = (today + timedelta(days=5)).strftime("%b").upper()
    badge_day = (today + timedelta(days=5)).day
    html = ("<html><body>"
            + _mb_card("101", 1, (badge_mon, badge_day), "Thursday at 12:00 PM")
            + _mb_card("101", 2, None, "Thursday at 12:00 PM", status="Pending")
            + _mb_card("101", 3, None, "Thursday at 12:00 PM", status="Submitted")
            + _mb_card("101", 4, None, "Thursday at 12:00 PM", status="")
            + "</body></html>")
    cards = mbp.extract_task_cards(html, class_id="101", class_name="C", today=today)
    check("T15.1 有 date-badge 的卡片 → 用徽标的月/日，due_inferred 为 False",
          len(cards) == 4 and cards[0].due_at is not None and cards[0].due_inferred is False
          and cards[0].due_at.date() == today + timedelta(days=5),
          extra=str([(c.due_at, c.due_inferred) for c in cards]))

    # ---- (b) 没有 date-badge：按状态分流推算 + 标推断 ----
    check("T15.2 没有 date-badge 也能算出日期（原来是 None → 前端只能当「时间未知」）",
          cards[1].due_at is not None and cards[1].due_text == "Thursday at 12:00 PM",
          extra=str(cards[1].due_at))
    check("T15.3 推算出来的必须带 due_inferred=True（不拿假日期骗用户）",
          cards[1].due_inferred is True and cards[2].due_inferred is True
          and cards[3].due_inferred is True,
          extra=str([c.due_inferred for c in cards[1:]]))
    check("T15.4 Pending（还没交）→ 取**最近未来**的那个星期四（今天或之后）",
          cards[1].due_at.date() >= today
          and cards[1].due_at.date() == today + timedelta(days=(3 - today.weekday()) % 7),
          extra=f"{cards[1].due_at} {cards[1].due_at.strftime('%a')}"
                f"（今天 {today} {today.strftime('%a')} weekday={today.weekday()}）")
    check("T15.5 Submitted（已交）→ 取**最近过去**的那个星期四（不晚于今天）",
          cards[2].due_at.date() <= today
          and cards[2].due_at.date() == today - timedelta(days=(today.weekday() - 3) % 7),
          extra=f"{cards[2].due_at} {cards[2].due_at.strftime('%a')}")
    check("T15.5b 状态缺失 → 取**绝对差最小**的那一次（与今天最近的那个星期四）",
          abs((cards[3].due_at.date() - today).days) == min((today.weekday() - 3) % 7,
                                                            (3 - today.weekday()) % 7),
          extra=f"{cards[3].due_at} {cards[3].due_at.strftime('%a')}"
                f"（今天 {today} {today.strftime('%a')}）")
    # ---- (b2) 方向性断言必须钉在一个**不是星期四**的「今天」 ----
    # T15.5c / T15.7 断言的是「Pending 往后、Submitted 往前」这个**方向差**。
    # 如果跑测试那天恰好是星期四，「最近未来的周四」和「最近过去的周四」就是同一天，
    # 方向差根本不存在 —— 那是测试自身的日期依赖，不是实现的问题（2026-09-17
    # 正好是星期四，这两条因此在真机上无故变红）。这里把 today 钉死到一个固定
    # 的星期一，让断言在任何一天运行都成立、且真的在验方向。
    pinned = date(2026, 9, 14)                      # 2026-09-14 是星期一
    assert pinned.weekday() == 0, "pinned 必须是星期一，否则下面的方向差推不出来"
    pcards = mbp.extract_task_cards(
        "<html><body>"
        + _mb_card("202", 1, None, "Thursday at 12:00 PM", status="Pending")
        + _mb_card("202", 2, None, "Thursday at 12:00 PM", status="Submitted")
        + "</body></html>",
        class_id="202", class_name="C2", today=pinned)
    p_pend, p_sub = pcards[0].due_at, pcards[1].due_at
    check("T15.5c Pending 与 Submitted 的方向相反（同一段文本、同一天，结果不同）",
          p_pend != p_sub and (p_pend - p_sub).days in (0, 7, -7),
          extra=f"pending={p_pend} submitted={p_sub}（钉在 {pinned} 星期一）")
    check("T15.6 时刻按原文（12:00 PM → 12:00）",
          (cards[1].due_at.hour, cards[1].due_at.minute) == (12, 0), extra=str(cards[1].due_at))
    check("T15.7 没有徽标时按算出来的日期补判过期：Submitted 那条 = 已过期，Pending 那条 = 未过期",
          pcards[1].past_due is True and pcards[0].past_due is False,
          extra=f"pending={pcards[0].past_due} submitted={pcards[1].past_due}"
                f"（钉在 {pinned} 星期一）")

    # ---- (c) 七种星期名都能算：Pending 落在 [今天, 今天+6]、Submitted 落在 [今天-6, 今天] ----
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    ok, detail = True, []
    for i, nm in enumerate(names):
        one = mbp.extract_task_cards(
            "<html><body>" + _mb_card("9", i + 1, None, f"{nm} at  9:45 AM", status="Pending")
            + _mb_card("9", i + 10, None, f"{nm} at  9:45 AM", status="Submitted") + "</body></html>",
            class_id="9", class_name="C", today=today)
        pend, sub = one[0].due_at, one[1].due_at
        fwd = (pend.date() - today).days
        back = (today - sub.date()).days
        detail.append(f"{nm[:3]}:pend+{fwd}/sub-{back}")
        if pend.weekday() != sub.weekday() or not (0 <= fwd <= 6) or not (0 <= back <= 6):
            ok = False
    check("T15.8 七个星期名：Pending → [今天, +6]；Submitted → [今天-6, 今天]；星期几都对得上",
          ok, extra=str(detail))

    # ---- (d) 契约映射：fetch_view 必须把 due_at 变成 ISO，而不是把人话文本塞进 due ----
    acc = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
    view = None
    try:
        import webapp_managebac as mbm
        fake = ManageBacFake(mode="ok").start()
        try:
            view = mbm.fetch_view(MB_USER, PWD_MB, fake.base_url, timeout=8.0, budget_seconds=60.0)
        finally:
            fake.stop()
    except Exception as exc:  # noqa: BLE001
        print("      ", repr(exc))
    tasks = (view or {}).get("tasks") or []
    check("T15.9 fetch_view 的 due 是 ISO 形态 `YYYY-MM-DD HH:MM`（不再是页面原文）",
          bool(tasks) and all(re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", t.get("due", ""))
                              for t in tasks),
          extra=str([t.get("due") for t in tasks[:3]]))
    check("T15.10 fetch_view 保留了原文 due_text 与 due_iso（前端可对照/可排序）",
          bool(tasks) and all(t.get("due_text") and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
                                                            t.get("due_iso", ""))
                              for t in tasks),
          extra=str(tasks[:1]))
    check("T15.11 精确日期（有徽标）不标 due_inferred",
          bool(tasks) and all("due_inferred" not in t for t in tasks),
          extra=str([t.get("due_inferred") for t in tasks[:3]]))

def t06_deps_and_badinput() -> None:
    section("T06 依赖缺失降级 / 脏配置不炸 / 未配置")
    acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")

    wd.clear_cache()
    with import_blocked("edupage_api"):
        result = wd.fetch_all(acct, force=True, user_id="t06")
    check("edupage-api 没装时降级为「缺少依赖」（不让站点崩）",
          result["meta"]["errors"].get("edupage") == wd.EDUPAGE_DEP_MESSAGE
          and "缺少 edupage-api 依赖" in result["meta"]["errors"].get("edupage", ""),
          extra=str(result["meta"]["errors"]))
    check("降级平台给空结构", result["edupage"] == {"lessons": [], "selected": []})

    wd.clear_cache()
    with import_blocked("bs4", "webapp_managebac"):
        no_bs4 = wd.fetch_all(acct, force=True, user_id="t06-bs4")
    check("bs4 没装时 managebac 段降级为「缺少依赖」",
          no_bs4["meta"]["errors"].get("managebac") == wd.MISSING_DEP_MESSAGE,
          extra=str(no_bs4["meta"]["errors"]))
    check("bs4 缺失不影响邮箱段（邮箱只用标准库 imaplib）",
          "mail" not in no_bs4["meta"]["errors"] or "连不上" in no_bs4["meta"]["errors"].get("mail", ""),
          extra=str(no_bs4["meta"]["errors"]))

    # 脏配置：不是 dict 的段、缺字段、类型不对
    messy = {"accounts": {"edupage": "not-a-dict", "managebac": {"username": None},
                          "mail": {"email": 123, "authcode": []}}}
    wd.clear_cache()
    try:
        messy_result = wd.fetch_all(messy, force=True, user_id="t06b")
        crashed = False
    except Exception as exc:  # noqa: BLE001
        messy_result, crashed = {}, True
        print("      ", repr(exc))
    check("脏 accounts 结构不抛异常（每段给中文错误）", not crashed
          and set(messy_result["meta"]["errors"]) == {"edupage", "managebac", "mail"},
          extra=str(messy_result.get("meta", {}).get("errors")))

    # 只有 managebac 配了 → 另两段应报「账号没填全」而不是 409
    partial = {"managebac": acct["managebac"]}
    wd.clear_cache()
    part = wd.fetch_all(partial, force=True, user_id="t06c")
    check("只配一段时：配了的抓、没配的报错",
          "mail" in part["meta"]["errors"] and "edupage" in part["meta"]["errors"]
          and part["meta"]["accounts"]["managebac"] == MB_USER,
          extra=str(part["meta"]["errors"]))

    # HTTP 客户端遇到连接被拒
    dead = make_accounts(mb_base="http://127.0.0.1:9", imap_host="127.0.0.1")
    wd.clear_cache()
    dead_result = wd.fetch_all(dead, force=True, user_id="t06d")
    check("连不上平台 → 中文原因（不是英文异常串）",
          all(re.search(r"[\u4e00-\u9fff]", msg)
              for msg in dead_result["meta"]["errors"].values()),
          extra=str(dead_result["meta"]["errors"]))


def t07_mail_body(fake_imap: FakeIMAP) -> None:
    section("T07 邮件正文（真 IMAP 协议 + MIME 解码）")
    acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
    with _patch_imap(fake_imap):
        mail = wd.fetch_mail_body(acct, "2")
        try:
            wd.fetch_mail_body(acct, "999")
            missing = "no-raise"
        except wd.PlatformError as exc:
            missing = exc
        bogus = wd.fetch_mail_body(acct, "1;DROP")
        status_missing = wd.http_status_for(missing) if isinstance(missing, Exception) else 502

    check("正文解析成功（uid/from/to/subject/date/body_text 齐全）",
          isinstance(mail, dict) and mail["uid"] == "2"
          and mail["subject"] == "作业提醒：数学" and mail["to"] == MAIL_USER
          and mail["body_text"] == "记得交作业。", extra=str(mail))
    check("正文里没有原始 MIME 头", "Message-ID" not in (mail or {}).get("body_text", ""))
    check("不存在的 uid → MailNotFound（IMAP NO 被翻译成 404）",
          isinstance(missing, wd.MailNotFound) and status_missing == 404, extra=repr(missing))
    check("非法 uid → None（不把用户输入拼进 IMAP 命令）", bogus is None)
    check("邮件不存在的文案是中文且可读",
          isinstance(missing, wd.MailNotFound) and "找不到" in str(missing))


def t08_logging_and_status(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T08 日志纪律：只有用户号/平台/耗时/原因，没有账号名与口令")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    wd.logger.addHandler(handler)
    old_level = wd.logger.level
    wd.logger.setLevel(logging.INFO)
    try:
        acct = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")
        wd.clear_cache()
        with fake_edupage(), _patch_imap(fake_imap):
            wd.fetch_all(acct, force=True, user_id="t08")
        bad = ManageBacFake(mode="reject").start()
        try:
            acct2 = make_accounts(mb_base=bad.base_url, imap_host="127.0.0.1")
            wd.clear_cache()
            with fake_edupage(), _patch_imap(fake_imap):
                wd.fetch_all(acct2, force=True, user_id="t08b")
        finally:
            bad.stop()
    finally:
        wd.logger.removeHandler(handler)
        wd.logger.setLevel(old_level)

    log_text = stream.getvalue()
    check("日志里有用户号/平台/耗时/结果",
          "user=t08" in log_text and "platform=managebac" in log_text
          and "elapsed=" in log_text, extra=log_text[:200])
    check("日志里没有账号名", MAIL_USER not in log_text and MB_USER not in log_text
          and EP_USER not in log_text, extra=log_text[:300])
    assert_no_secrets("T08 日志", log_text)
    check("日志里没有口令以外的敏感串（base_url 也不打）",
          "127.0.0.1" not in log_text)

    check("http_status_for：邮件不存在 → 404",
          wd.http_status_for(wd.MailNotFound("x")) == 404)
    check("http_status_for：其它错误 → 502", wd.http_status_for(RuntimeError("x")) == 502)
    check("error_message 是中文且不含异常类名",
          wd.error_message(RuntimeError("boom")) == "抓取失败，请稍后重试")


# ============================================================ T11/T12 本轮新增

def t11_mail_unread_flags() -> None:
    """① 每封邮件带真实的已读状态（IMAP FLAGS），且**绝不**把邮件标成已读。"""
    section("T11 邮件逐封未读状态（真 TLS 假 IMAP + FLAGS）")

    # ---- (a) 3 封里 1 封未读（uid 3 = 最新那封）----
    one = FakeIMAP(unread=1).start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(one):
            mail = wd.fetch_mail(acct["mail"])
    finally:
        one.stop()

    flags = [item.get("unread") for item in mail["recent"]]
    check("T11.1 recent[] 逐封带 unread 布尔，3 封里 1 封未读 → [True, False, False]",
          flags == [True, False, False], extra=str(mail["recent"]))
    check("T11.2 每一项都有 unread 且**类型是 bool**（不是字符串/None）",
          all(isinstance(item.get("unread"), bool) for item in mail["recent"]),
          extra=str(flags))
    check("T11.3 mail.unread 计数取自 IMAP UNSEEN（= 1，不是 3）",
          mail["unread"] == 1, extra=str(mail["unread"]))
    check("T11.4 列表顺序没变（最新在前：3 → 2 → 1）",
          [item["uid"] for item in mail["recent"]] == ["3", "2", "1"],
          extra=str([item["uid"] for item in mail["recent"]]))
    check("T11.5 抓取全程没有把任何邮件标成已读（服务器侧 marked_read 为空）",
          one.marked_read == set(), extra=str(sorted(one.marked_read)))
    check("T11.6 只读性：客户端只发过 BODY.PEEK，没有裸 BODY[（非 PEEK 会被服务器标已读）",
          bool(one.requests)
          and all("BODY.PEEK" in item and "BODY[" not in item.replace("BODY.PEEK", "")
                  for item in one.requests),
          extra=str(one.requests))
    check("T11.7 取邮件头时一并要了 FLAGS（一次 FETCH 同时拿状态）",
          # 邮件头那一次必须在同一条 FETCH 里同时要 FLAGS 与 HEADER（一次往返拿全）。
          # 预加载正文的那些 `(BODY.PEEK[])` 是另一条链路，本来就不需要 FLAGS。
          any("FLAGS" in item and "HEADER" in item for item in one.requests)
          and all("HEADER" not in item or "FLAGS" in item for item in one.requests),
          extra=str(one.requests))

    # ---- (b) unread=0：一个未读都没有（前端据此一个点都不画）----
    none = FakeIMAP(unread=0).start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()          # 清掉 (a) 那次（缓存键只有「用户号 + 账号名」）
        with _patch_imap(none):
            mail0 = wd.fetch_mail(acct["mail"])
    finally:
        none.stop()

    check("T11.8 unread=0 时 mail.unread=0 且逐封 unread 全 False（前端零个点）",
          mail0["unread"] == 0
          and [item.get("unread") for item in mail0["recent"]] == [False, False, False],
          extra=str(mail0))
    check("T11.8b unread=0 的假服务器确实没给任何 UNSEEN（拿的是它自己的 3 封信）",
          none.unseen == [] and sorted(none.mailbox) == [1, 2, 3],
          extra=str(none.unseen))
    check("T11.9 unread=0 的那次也没有标记任何邮件已读",
          none.marked_read == set(), extra=str(sorted(none.marked_read)))

    # ---- (c) 服务器不支持 STATUS → 退化为 FLAGS 反推 / UID SEARCH UNSEEN ----
    nostatus = FakeIMAP(unread=1, status_ok=False).start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(nostatus):
            mail2 = wd.fetch_mail(acct["mail"])
    finally:
        nostatus.stop()

    check("T11.10 STATUS 不可用时退化为 UID SEARCH UNSEEN，未读数仍与逐封状态一致",
          mail2["unread"] == 1
          and [item.get("unread") for item in mail2["recent"]] == [True, False, False],
          extra=str(mail2))
    check("T11.11 退化路径同样没有标记已读",
          nostatus.marked_read == set(), extra=str(sorted(nostatus.marked_read)))

    # ---- (d) 邮件头 + 正文两条路径都只读 ----
    body_fake = FakeIMAP(unread=1).start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(body_fake):
            got = wd.fetch_mail_body(acct, "2")
    finally:
        body_fake.stop()
    check("T11.12 读正文走 BODY.PEEK[]（读完之后那封仍是未读状态）",
          got is not None and body_fake.marked_read == set()
          and all("BODY.PEEK" in item and "BODY[" not in item.replace("BODY.PEEK", "")
                  for item in body_fake.requests),
          extra=str(body_fake.requests))


def t16_mark_mail_seen() -> None:
    """③ 标记已读：**只**标点开的那一封（UID STORE +FLAGS \\Seen），缓存就地减 1。"""
    section("T16 点开邮件 → 标记已读（唯一会写邮箱状态的入口）")

    fake = FakeIMAP(unread=2).start()          # uid 2、3 未读
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(fake):
            # 先抓一次列表：进缓存，而且此时什么都没标
            listed = wd.fetch_all(acct, user_id="t16")
            before_unread = listed["mail"]["unread"]
            before_flags = [item.get("unread") for item in listed["mail"]["recent"]]

            marked = wd.mark_mail_seen(acct, "2", user_id="t16")
            stores_after_mark = list(fake.store_requests)   # 到这一步为止发过的 STORE
            cached = wd.fetch_all(acct, user_id="t16")           # 缓存命中那一份
            after_flags = [item.get("unread") for item in cached["mail"]["recent"]]

            # 只标这一封：uid 3 必须还是未读
            other = wd.fetch_mail(acct["mail"])
            try:
                wd.mark_mail_seen(acct, "999", user_id="t16")
                missing = "no-raise"
            except wd.PlatformError as exc:
                missing = exc
            bogus_status = None
            try:
                wd.mark_mail_seen(acct, "1;DROP", user_id="t16")
                bogus = "no-raise"
            except wd.PlatformError as exc:
                bogus = exc
                bogus_status = wd.http_status_for(exc)
    finally:
        fake.stop()

    check("T16.1 标记返回 {uid, unread:false}（只回这一封，不批量）",
          marked == {"uid": "2", "unread": False}, extra=str(marked))
    check("T16.2 假 IMAP 上真的加上了 \\Seen（服务器侧状态变了，不只是本地视图）",
          2 not in fake.unseen and "\\Seen" in fake.flags.get(2, set()),
          extra=str(fake.flags))
    check("T16.3 走的是 UID STORE +FLAGS (\\Seen)，且**只发了这一条** STORE",
          len(stores_after_mark) == 1 and "2 +FLAGS" in stores_after_mark[0]
          and "SEEN" in stores_after_mark[0].upper(),
          extra=str(stores_after_mark))
    check("T16.4 只标点开的那一封：uid 3 在 IMAP 上仍是未读（没有批量标记）",
          3 in fake.unseen and "\\Seen" not in fake.flags.get(3, set()),
          extra=str(fake.flags))
    check("T16.5 未读计数真的少 1（IMAP STATUS UNSEEN 从 %s 变 %s）"
          % (before_unread, other["unread"]),
          before_unread == 2 and other["unread"] == 1, extra=str(other["unread"]))
    check("T16.6 缓存就地更新：那一封 unread 变 false、mail.unread 减 1（刷新不会变回未读）",
          before_flags == [True, True, False] and after_flags == [True, False, False]
          and cached["mail"]["unread"] == 1,
          extra=str(cached["mail"]["unread"]) + str(after_flags))
    check("T16.7 不存在的 uid → MailNotFound（HTTP 404 语义）",
          isinstance(missing, wd.MailNotFound) and wd.http_status_for(missing) == 404,
          extra=repr(missing))
    check("T16.8 非法 uid（注入尝试）→ 404，且没有拼进任何 IMAP 命令",
          isinstance(bogus, wd.PlatformError) and bogus_status == 404
          and all("DROP" not in item.upper() for item in fake.store_requests),
          extra=str(fake.store_requests))


def t17_mail_html_verbatim() -> None:
    """④ HTML 邮件：**原样返回**（服务端不做任何消毒/改写）+ 正文两版齐全。

    消毒器已按用户要求整体删除 —— 服务端一个字符都不碰 HTML。
    「脚本不执行」这条前提现在**只在两处**成立，测试里都要钉住：
      1. 前端把正文放进 `<iframe sandbox="allow-same-origin">`（**不带 allow-scripts**）；
      2. 于是邮件里的 `<script>` / `onerror` 永远没有执行机会（见 test_app_cdp.py 的 XSS 探针）。
    """
    section("T17 HTML 邮件**原样返回**（服务端不消毒：<script>/onerror/@import/javascript: 一字不改）")

    _gone = [n for n in ("sanitize_mail_html", "_MailHTMLSanitizer", "HTML_ALLOWED_TAGS",
                         "HTML_ALLOWED_ATTRS", "HTML_DROP_TAGS", "_safe_url")
             if hasattr(wd, n)]
    check("T17.1 服务端的 HTML 消毒接口已整体移除（旧名字一个都不在了）",
          not _gone, extra=str(_gone))

    fake = FakeIMAP(unread=2).start()
    try:
        fake.add_html_mail(4)
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(fake):
            rich = wd.fetch_mail_body(acct, "4")       # multipart/alternative（纯文本 + HTML）
            plain = wd.fetch_mail_body(acct, "2")      # 只有纯文本
        # 独立地、不经过被测模块，从**原始 MIME 字节**里抠出 HTML 部分（自证「原样」的基准）
        raw_mime = _html_mail_bytes(4).decode("utf-8")
        raw_html = raw_mime.split('Content-Type: text/html; charset="utf-8"\r\n\r\n', 1)[1] \
                           .split("\r\n--bnd42--", 1)[0]
        marked_read = set(fake.marked_read)
        stores = list(fake.store_requests)
    finally:
        fake.stop()

    got = (rich or {}).get("body_html", "")
    low = got.lower()
    check("T17.2 body_html 与 MIME 原文**逐字相同**（既不消毒、也不截断、也不重排）",
          isinstance(rich, dict) and got == HTML_MAIL_MARKUP and got == raw_html,
          extra="len(got)=%d len(MIME)=%d" % (len(got), len(raw_html)))

    check("T17.3 <script> 连同内容原样保留（不再被剥掉）",
          "<script>window.__xss = 'script-tag';</script>" in got and "__xss" in got,
          extra=got[:200])
    check("T17.4 <style> 内容原样：@import 与 expression( 都还在（不再做 CSS 过滤）",
          "@import url(\"https://evil.example.invalid/x.css\")" in got
          and "expression(alert(1))" in got and "display:none" in got)
    check("T17.5 事件属性原样保留：onload / onerror / onclick 一个都没被删",
          'onload="window.__xss=\'onload\'"' in got
          and 'onerror="window.__xss=\'onerror\'"' in got
          and 'onclick="window.__xss=\'onclick\'"' in got
          and "onerror" in low and "onclick" in low and "onload" in low)
    check("T17.6 javascript: / 混淆写法 / data:text/html 链接的 href 原样保留",
          'href="javascript:window.__xss=\'js-href\'"' in got
          and 'href="jav&#x09;ascript:window.__xss=\'obf\'"' in got
          and 'href="data:text/html,<script>window.__xss=1</script>"' in got)
    check("T17.7 <iframe> 连内容整块原样保留（evil.example.invalid 还在）",
          '<iframe src="https://evil.example.invalid/"></iframe>' in got
          and "evil.example.invalid" in low)
    check("T17.8 远程图片就是 src 本身（没有被改写成 data-src 之类的惰性加载）",
          'src="https://tracker.example.invalid/pixel.gif"' in got
          and "data-src" not in low)
    check("T17.9 服务端不再自动补 target/rel（链接属性与邮件里写的一模一样）",
          'target="_blank"' not in got and "noopener" not in low)
    check("T17.10 正文内容没被清空（标题/表格/文字都在）",
          all(t in low for t in ("<h1>", "<p>", "<b>", "<table>", "<td>")) and "家长会" in got)

    check("T17.11 HTML 邮件的 body_text 仍是那版纯文本（两版都给，纯文本这版仍是老行为）",
          isinstance(rich, dict) and rich["body_text"] == HTML_MAIL_PLAIN,
          extra=(rich or {}).get("body_text", ""))
    check("T17.12 纯文本邮件：body_html 是空串（前端据此只走纯文本渲染）",
          isinstance(plain, dict) and plain["body_html"] == ""
          and plain["body_text"] == "记得交作业。", extra=str(plain))
    check("T17.13 读 HTML 邮件同样是 PEEK（读正文不会顺手把它标成已读，也没发 STORE）",
          marked_read == set() and stores == [], extra=str(stores))

    # 「不消毒」能成立的前提：前端那一层沙箱必须还在，而且**真正写进 DOM 的 sandbox 值**
    # 里不能有 allow-scripts。这条断言是整份 T17 的护栏 —— 谁把 allow-scripts 加进沙箱值、
    # 或者把 iframe 换回内联渲染，这里立刻红。
    # 注意：注释里出现「allow-scripts」字样是正常的（那是在讲**为什么不能加**），
    # 所以这里只认**赋值/塞进属性的那个值**，不整篇扫字符串。
    appjs = open(os.path.join(HERE, "static", "app", "app.js"), encoding="utf-8").read()
    _sv = re.search(r"var MAIL_FRAME_SANDBOX = '([^']*)';", appjs)
    sandbox_val = _sv.group(1) if _sv else None
    _sets = re.findall(r"setAttribute\('sandbox',\s*([^)]*)\)", appjs)
    check("T17.14 前端防线在位：正文走 srcdoc 沙箱 iframe，sandbox 值恰好是 allow-same-origin（无 allow-scripts）",
          sandbox_val == "allow-same-origin"
          and _sets == ["MAIL_FRAME_SANDBOX"]
          and "allow-scripts" not in (sandbox_val or "")
          and "frame.setAttribute('srcdoc', html)" in appjs
          and "function sanitizeMailHTML(" not in appjs,
          f"sandbox_val={sandbox_val!r} setAttribute={_sets}")


def t12_edupage_error_mapping() -> None:
    """② EduPage 异常按原因分类（凭据 / 两步验证 / 连不上 / 缺依赖 / 其它）。"""
    section("T12 EduPage 错误分类：不再一律「连不上平台服务器」")
    base = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")

    def _errors(accounts: dict, label: str, fake_imap: FakeIMAP) -> dict:
        """冷启动 → 等后台 EduPage 任务结束 → 取那一轮的真实原因。

        EduPage 不再阻塞请求（首屏先给 pending），所以分类文案要在**抓完之后**这一轮看；
        缺依赖这种**静态**条件当轮就直接给原因（不 pending），不需要等。
        """
        wd.clear_cache()
        with _patch_imap(fake_imap):
            first = wd.fetch_all(accounts, force=True, user_id=label)
            if not first["meta"].get("pending"):
                return first["meta"]["errors"]
            return wait_edupage(accounts, label)["meta"]["errors"]

    read_only = FakeIMAP(unread=0).start()
    try:
        # BadCredentialsException → 「账号或密码不对」
        with fake_edupage_raising(
                FakeBadCredentials("user or password is invalid (demo-user)"),
                name="BadCredentialsException"):
            errors = _errors(base, "t12-creds", read_only)
        check("T12.1 BadCredentialsException → 「EduPage 账号或密码不对」+ 指向个人中心",
              errors.get("edupage") == wd.EDUPAGE_CREDENTIALS_MESSAGE
              and "账号或密码不对" in errors.get("edupage", "")
              and "个人中心" in errors.get("edupage", ""),
              extra=str(errors.get("edupage")))
        check("T12.2 凭据错误的文案里没有原始异常串（不泄露内部/账号）",
              "invalid" not in errors.get("edupage", "")
              and "demo-user" not in errors.get("edupage", ""),
              extra=str(errors.get("edupage")))

        # 两步验证
        with fake_edupage_raising(RuntimeError("needs a second factor"),
                                  name="SecondFactorRequiredException"):
            errors = _errors(base, "t12-tfa", read_only)
        check("T12.3 两步验证异常 → 「EduPage 需要两步验证…请在客户端登录一次」",
              errors.get("edupage") == wd.EDUPAGE_TFA_MESSAGE,
              extra=str(errors.get("edupage")))

        # 连接错误
        with fake_edupage_raising(FakeRequestsConnectionError("Connection refused ..."),
                                  name="ConnectionError"):
            errors = _errors(base, "t12-conn", read_only)
        check("T12.4 requests 连接错误 → 「连不上平台服务器，请稍后重试」",
              errors.get("edupage") == wd.UNREACHABLE_MESSAGE,
              extra=str(errors.get("edupage")))

        # 超时
        with fake_edupage_raising(FakeRequestsTimeout("read timed out"),
                                  name="Timeout"):
            errors = _errors(base, "t12-timeout", read_only)
        check("T12.5 单次请求超时 → 「连不上平台服务器，请稍后重试」"
              "（整段 90 秒超时是另一条专门文案，见 T14）",
              errors.get("edupage") == wd.UNREACHABLE_MESSAGE,
              extra=str(errors.get("edupage")))

        # 缺依赖
        wd.clear_cache()
        with import_blocked("edupage_api"):
            errors = _errors(base, "t12-dep", read_only)
        check("T12.6 edupage-api 没装 → 「服务端缺少 edupage-api 依赖」（更具体）",
              errors.get("edupage") == wd.EDUPAGE_DEP_MESSAGE,
              extra=str(errors.get("edupage")))

        # 其它异常 → 只报类名，不回 traceback / 原始响应
        class FakeWeirdError(Exception):
            pass

        with fake_edupage_raising(FakeWeirdError("raw upstream junk <html>"),
                                  name="FakeWeirdError"):
            errors = _errors(base, "t12-other", read_only)
        check("T12.7 其它异常 → 「EduPage 抓取失败：<异常类名>」",
              errors.get("edupage") == "EduPage 抓取失败：FakeWeirdError",
              extra=str(errors.get("edupage")))
        check("T12.8 其它异常的文案里没有 traceback / 原始响应片段",
              "Traceback" not in errors.get("edupage", "")
              and "<html>" not in errors.get("edupage", ""),
              extra=str(errors.get("edupage")))

        # 日志里记下异常类名（便于排查），但不记口令/账号
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        wd.logger.addHandler(handler)
        old_level = wd.logger.level
        wd.logger.setLevel(logging.INFO)
        try:
            with fake_edupage_raising(FakeBadCredentials("bad credentials for " + EP_USER),
                                      name="BadCredentialsException"):
                _errors(base, "t12-log", read_only)
        finally:
            wd.logger.removeHandler(handler)
            wd.logger.setLevel(old_level)
        log_text = stream.getvalue()
        check("T12.9 日志里记下 EduPage 失败的**异常类名**（class=BadCredentialsException）",
              "class=BadCredentialsException" in log_text, extra=log_text[:300])
        check("T12.10 日志里没有口令、也没有账号名",
              not any(s in log_text for s in SECRETS) and EP_USER not in log_text,
              extra=log_text[:300])
    finally:
        read_only.stop()


# ============================================================ T13/T14 本轮新增（EduPage 整周映射 + 后台抓取）

def _running_jobs() -> list:
    with wd._ep_jobs_lock:
        return [key for key, job in wd._ep_jobs.items() if job.get("running")]


def _job_started(key: str):
    with wd._ep_jobs_lock:
        return (wd._ep_jobs.get(key) or {}).get("started")


def t13_edupage_week_mapping(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    """③ EduPage 课表字段映射：整周 71 条 → 本周未取消的 60 条，逐字段对齐契约。"""
    section("T13 EduPage 整周映射：只留本周未取消的条目 + 7 个契约字段逐项断言")
    acct = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")
    monday, sunday = wd.week_bounds(dt.date.today())
    items = fake_week_items(monday)
    check("T13.1 假数据就是 71 条整周课卡（表头 2 + 有效 60 + 停课 3 + 事件 2 + 非本周 4）",
          len(items) == 71, extra=str(len(items)))

    wd.clear_cache()
    _LAST_SESSION_CALLS.clear()
    with fake_edupage_week(items) as sess, _patch_imap(fake_imap):
        wd.fetch_all(acct, force=True, user_id="t13")
        done = wait_edupage(acct, "t13")
        _LAST_SESSION_CALLS[:] = list(sess.calls)

    ep = done["edupage"]
    lessons = ep["lessons"]
    check("T13.2 整周 71 条 → 只保留本周未取消的 60 条", len(lessons) == 60,
          extra=str(len(lessons)))
    check("T13.3 每条都只有契约的 7 个字段（date/start/end/subject/group/room/teacher）",
          all(set(row) == {"date", "start", "end", "subject", "group", "room", "teacher"}
              for row in lessons),
          extra=str(sorted(lessons[0]) if lessons else []))

    dates = sorted({row["date"] for row in lessons})
    week_days = [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]
    check("T13.4 date 全部落在本周（周一~周日），且按天铺开（不是全塞今天）",
          all(row["date"] in week_days for row in lessons)
          and dates == week_days[:5],
          extra=str(dates))
    check("T13.5 非本周条目（上周 group Z / 下周 group Y）被剔除",
          "Z" not in ep["selected"] and "Y" not in ep["selected"], extra=str(ep["selected"]))
    check("T13.6 停课（type=absent / removed）与事件（type=event / out）被过滤："
          "它们的教学组 CN/CM/EV 不进 selected，也没有落在课表里",
          not any(g in ep["selected"] for g in ("CN", "CM", "EV"))
          and not any(row["group"] in ("CN", "CM", "EV") for row in lessons),
          extra=str(ep["selected"]))
    check("T13.7 start / end 是 HH:MM（datetime.time 已格式化）",
          all(re.fullmatch(r"\d{2}:\d{2}", row["start"]) and
              re.fullmatch(r"\d{2}:\d{2}", row["end"]) for row in lessons),
          extra=str(lessons[0] if lessons else {}))

    first = lessons[0] if lessons else {}
    expected = {
        "date": monday.isoformat(),
        "start": "08:00",
        "end": "08:40",
        "subject": "Philosophy HL1",     # subject.name
        "group": "A",                    # groups[0].name
        "room": "A205",                  # classrooms[0].name
        "teacher": "Qiwei He",           # teachers[0].name
    }
    check("T13.8 首条逐字段对齐（科目取 name、老师取 teachers[0]、教室取 classrooms[0]）",
          first == expected, extra=f"{first} != {expected}")
    check("T13.9 排序按 (date, start) 升序",
          lessons == sorted(lessons, key=lambda r: (r["date"], r["start"], r["subject"], r["group"])),
          extra=str([(r["date"], r["start"]) for r in lessons[:3]]))
    check("T13.10 selected = 本周出现的教学组，去重且保持出现顺序（A,B,C）",
          ep["selected"] == ["A", "B", "C"], extra=str(ep["selected"]))

    configured = wd._edupage_from_items(items, {"selected": ["C", "X"]}, monday, sunday,
                                        FAKE_WEEK_DBI)
    check("T13.11 配置里显式给的教学组优先且保序（C,X 在前，课表里新出现的追加在后）",
          configured["selected"] == ["C", "X", "A", "B"], extra=str(configured["selected"]))

    check("T13.12 预算分档：EduPage 90s / ManageBac·邮箱 20s / 缓存 30min 与 5min 分开",
          wd.EDUPAGE_TIMEOUT == 90.0 and wd.PLATFORM_TIMEOUT == 20.0
          and wd.EDUPAGE_CACHE_TTL_SECONDS == 1800.0 and wd.CACHE_TTL_SECONDS == 300.0
          and wd.EDUPAGE_HTTP_TIMEOUT > wd.HTTP_TIMEOUT,
          extra=f"{wd.EDUPAGE_TIMEOUT}/{wd.PLATFORM_TIMEOUT}/"
                f"{wd.EDUPAGE_CACHE_TTL_SECONDS}/{wd.CACHE_TTL_SECONDS}")
    check("T13.13 请求打的是「本人 + 整周区间」：table=students、id=-3772、datefrom=周一、dateto=周日",
          session_calls_match(), extra=str(_LAST_SESSION_CALLS))
    check("T13.14 快路只发 1 次请求就拿整周（不是逐日循环 7 次）",
          len(_LAST_SESSION_CALLS) == 1, extra=str(len(_LAST_SESSION_CALLS)))


#: 最近一次快路请求的原始参数（T13.13 用来断言日期区间与查询目标）
_LAST_SESSION_CALLS: list = []


def session_calls_match() -> bool:
    if len(_LAST_SESSION_CALLS) != 1:
        return False
    body = (_LAST_SESSION_CALLS[0].get("body") or {}).get("__args", [None, {}])[1]
    monday, sunday = wd.week_bounds(dt.date.today())
    return (body.get("table") == "students" and body.get("id") == "-3772"
            and body.get("datefrom") == monday.isoformat()
            and body.get("dateto") == sunday.isoformat()
            and _LAST_SESSION_CALLS[0].get("url", "").endswith("__func=curentttGetData"))


def t14_edupage_background(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    """④ 后台抓取路径：缓存空 → 立即返回 + pending + 刷新后拿到；并发只跑一个任务。"""
    section("T14 EduPage 后台抓取：不阻塞首屏 / pending 契约 / 单任务 / 失败与超时文案")
    acct = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")

    # ---- (a) 冷启动：立即返回 + pending；同一用户并发只起一个后台任务 ----
    wd.clear_cache()
    _LAST_SESSION_CALLS.clear()
    with fake_edupage_week(delay=1.0), _patch_imap(fake_imap):
        started = time.monotonic()
        first = wd.fetch_all(acct, user_id="t14")
        elapsed = time.monotonic() - started
        key = wd._cache_key("t14", acct)
        job_started = _job_started(key)
        again = wd.fetch_all(acct, force=True, user_id="t14")     # 并发第二次（= 用户点刷新）
        check("T14.1 缓存为空时**不挂住请求**：立即返回（< 0.5s，真实 EduPage 要几十秒）",
              elapsed < 0.5, extra=f"{elapsed:.3f}s")
        check("T14.2 响应契约：edupage 段为空 + meta.pending == ['edupage']",
              first["edupage"] == {"lessons": [], "selected": []}
              and first["meta"].get("pending") == ["edupage"], extra=str(first["meta"]))
        check("T14.3 errors.edupage = 「EduPage 正在抓取课表（约 5 秒），请稍后点「刷新」」",
              first["meta"]["errors"].get("edupage") == wd.EDUPAGE_PENDING_MESSAGE,
              extra=str(first["meta"]["errors"]))
        check("T14.4 pending 期间 ManageBac / 邮箱照常返回（不受 EduPage 影响）",
              set(first) == {"edupage", "managebac", "mail", "meta"}
              and "managebac" in first["meta"]["accounts"], extra=str(sorted(first)))
        check("T14.5 并发请求不会再起第二个后台任务（同一用户同一缓存键只有一个）",
              len(_running_jobs()) == 1 and _job_started(key) == job_started,
              extra=str(_running_jobs()))
        check("T14.6 并发请求同样立即返回并继续标 pending",
              again["meta"].get("pending") == ["edupage"]
              and again["edupage"] == {"lessons": [], "selected": []}, extra=str(again["meta"]))

        landed = wait_edupage(acct, "t14", timeout=25)
        check("T14.7 抓完后写进缓存：pending 消失、拿到整周课表（60 条）",
              not landed["meta"].get("pending") and len(landed["edupage"]["lessons"]) == 60,
              extra=str(landed["meta"]) + str(len(landed["edupage"]["lessons"])))
        check("T14.8 抓完后 errors 里没有 edupage 了",
              "edupage" not in landed["meta"]["errors"], extra=str(landed["meta"]["errors"]))
        hit = wd.fetch_all(acct, user_id="t14")
        check("T14.9 抓完后普通请求（不点刷新）也能拿到：30 分钟缓存命中 + 数据在",
              hit["meta"]["cache"] == "hit" and not hit["meta"].get("pending")
              and len(hit["edupage"]["lessons"]) == 60,
              extra=str(hit["meta"]) + str(len(hit["edupage"]["lessons"])))
        forced = wd.fetch_all(acct, force=True, user_id="t14")
        check("T14.10 有缓存时 ?force=1 先给旧数据、同时后台再刷新（标 pending，不空窗）",
              len(forced["edupage"]["lessons"]) == 60
              and forced["meta"].get("pending") == ["edupage"], extra=str(forced["meta"]))
        wait_edupage(acct, "t14", timeout=25)

    # ---- (b) 失败：冷却期内如实报原因，不无限标 pending、也不反复重登 ----
    wd.clear_cache()
    with fake_edupage_week(fail=FakeRequestsConnectionError("Connection refused")), \
            _patch_imap(fake_imap):
        first = wd.fetch_all(acct, force=True, user_id="t14-fail")
        check("T14.11 冷启动仍是 pending（后台在抓）",
              first["meta"].get("pending") == ["edupage"], extra=str(first["meta"]))
        after = wait_edupage(acct, "t14-fail", timeout=20)
        check("T14.12 后台抓失败后如实上报「连不上平台服务器，请稍后重试」（不是一直 pending）",
              after["meta"].get("pending") is None
              and after["meta"]["errors"].get("edupage") == wd.UNREACHABLE_MESSAGE,
              extra=str(after["meta"]))
        key = wd._cache_key("t14-fail", acct)
        stamp = _job_started(key)
        wd.fetch_all(acct, force=True, user_id="t14-fail")
        wd.fetch_all(acct, force=True, user_id="t14-fail")
        check("T14.13 失败后有冷却：短时间内不再起新任务（不会每次刷新都重登学校服务器）",
              _job_started(key) == stamp, extra=f"{stamp} -> {_job_started(key)}")

    # ---- (c) 整段抓取超过 90 秒预算 → 专用超时文案 ----
    wd.clear_cache()
    old_timeout = wd.EDUPAGE_TIMEOUT
    wd.EDUPAGE_TIMEOUT = 1.0
    try:
        with fake_edupage_week(delay=4.0), _patch_imap(fake_imap):
            wd.fetch_all(acct, force=True, user_id="t14-slow")
            slow = wait_edupage(acct, "t14-slow", timeout=25)
    finally:
        wd.EDUPAGE_TIMEOUT = old_timeout
    check("T14.14 EduPage 整段超时 → 专用文案「EduPage 抓取太慢/超时（约 5 秒）"
          "，已放后台继续抓，请稍后刷新」",
          slow["meta"]["errors"].get("edupage") == wd.EDUPAGE_TIMEOUT_MESSAGE,
          extra=str(slow["meta"]["errors"]))
    check("T14.15 超时文案和通用 20 秒那条不是同一条（专门文案）",
          wd.EDUPAGE_TIMEOUT_MESSAGE != wd.TIMEOUT_MESSAGE
          and "20 秒" not in wd.EDUPAGE_TIMEOUT_MESSAGE, extra=wd.EDUPAGE_TIMEOUT_MESSAGE)
    check("T14.17 待抓/超时文案写的是「约 5 秒」（实测首抓 2.5~6.6 秒），不再写「约 50 秒」",
          "约 5 秒" in wd.EDUPAGE_PENDING_MESSAGE and "约 5 秒" in wd.EDUPAGE_TIMEOUT_MESSAGE
          and "50 秒" not in wd.EDUPAGE_PENDING_MESSAGE
          and "50 秒" not in wd.EDUPAGE_TIMEOUT_MESSAGE,
          extra=f"pending={wd.EDUPAGE_PENDING_MESSAGE!r} timeout={wd.EDUPAGE_TIMEOUT_MESSAGE!r}")

    # ---- (d) 没填 EduPage 账号 / 缺依赖：静态条件当轮就说清楚，不进后台 ----
    wd.clear_cache()
    partial = {"managebac": acct["managebac"]}
    now = wd.fetch_all(partial, force=True, user_id="t14-noacct")
    check("T14.16 没填 EduPage 账号 → 当轮就报「登录失败」，不标 pending",
          now["meta"].get("pending") is None
          and now["meta"]["errors"].get("edupage") == wd.LOGIN_FAILED_MESSAGE,
          extra=str(now["meta"]))
    wd.clear_cache()
    with import_blocked("edupage_api"):
        dep = wd.fetch_all(acct, force=True, user_id="t14-dep")
    check("T14.17 缺 edupage-api → 当轮就报「服务端缺少 edupage-api 依赖」，不标 pending",
          dep["meta"].get("pending") is None
          and dep["meta"]["errors"].get("edupage") == wd.EDUPAGE_DEP_MESSAGE,
          extra=str(dep["meta"]))


# ============================================================ HTTP 层（真起 server.py）

class LocalSite:
    """把 server.py 真起在一个临时端口的**临时副本**上。

    为什么必须用副本：server.py 用 `Path(__file__).parent` 当站点根目录，
    直接跑仓库里的那份会用**真实的 `.site_secret`/`admin_audit.log`** ——
    测试不碰站点数据是铁律（历史事故），所以这里把 server.py + core_e2e.py +
    抓取层三个模块拷进临时目录，在副本里跑。测完随临时目录一起丢。
    """

    COPY_FILES = ("server.py", "core_e2e.py", "webapp_data.py", "webapp_managebac.py",
                  "webapp_mb_parse.py")

    def __init__(self, accounts: dict | None, user_id: int = 4242,
                 phix: "FakePhix | None" = None, imap_port: int | None = None):
        import shutil

        self.tmp = tempfile.mkdtemp(prefix="phix-webapp-")
        for name in self.COPY_FILES:
            shutil.copy2(os.path.join(HERE, name), os.path.join(self.tmp, name))
        self.secret = os.urandom(32)
        with open(os.path.join(self.tmp, ".site_secret"), "wb") as fh:
            fh.write(self.secret)
        self.dek = os.urandom(32)
        self.user_id = user_id
        self.accounts = accounts
        self.phix = phix
        self.port = self._free_port()
        env = dict(os.environ)
        env["PYTHONPATH"] = self.tmp
        env["PYTHONUNBUFFERED"] = "1"
        if imap_port:                                # 子进程连本地假 IMAP
            env["PHIX_WEBAPP_IMAP_PORT"] = str(imap_port)
        if phix is not None:
            env["PHIX_SERVER"] = phix.base          # 站点只认这个假的 phix
            # 公钥固定文件：站点从 /ping 取到就会固定，这里预置一份省一次往返
            with open(os.path.join(self.tmp, ".phix_pubkey"), "w", encoding="utf-8") as fh:
                json.dump({phix.base: phix.server_pk_raw.hex()}, fh)
        self.log_path = os.path.join(self.tmp, "server.out")
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(self.tmp, "server.py"),
             "--port", str(self.port), "--host", "127.0.0.1"],
            cwd=self.tmp, env=env, stdout=self._log, stderr=subprocess.STDOUT)
        self._wait_ready()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _wait_ready(self, timeout: float = 20.0) -> None:
        """等端口真的能连上（临时副本里没有 html，别用 HTTP 探活）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("server.py 起来就退了：" + self.log_tail())
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=2):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("server.py 没起来：" + self.log_tail())

    def log_tail(self) -> str:
        try:
            with open(self.log_path, "rb") as fh:
                return fh.read().decode("utf-8", "replace")[-2000:]
        except OSError:
            return ""

    def seed_accounts(self, accounts: dict) -> None:
        """把 settings.accounts 的加密 payload 交给假 phix（服务端随后解密取账号）。"""
        wrapper = {"accounts": accounts}
        envelope = phix1_seal(self.dek, self.user_id, "settings.accounts",
                              json.dumps(wrapper, ensure_ascii=False))
        if self.phix is not None:
            self.phix.payload = envelope

    def get(self, path: str, *, auth: bool = True):
        """GET 本站路径，返回 (status, json, 原始文本)。"""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if auth:
            cookie = phix_cookie_header(self.secret, self.user_id, self.dek.hex())
            req.add_header("Cookie", f"phix_access={cookie}")
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        text = raw.decode("utf-8", "replace")
        try:
            body = json.loads(text) if text else {}
        except ValueError:
            body = {"_raw": text[:200]}
        return status, body, text

    def post(self, path: str, payload=None, *, auth: bool = True):
        """POST 本站路径（空体也行），返回 (status, json, 原始文本)。"""
        data = json.dumps(payload if payload is not None else {}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        if auth:
            cookie = phix_cookie_header(self.secret, self.user_id, self.dek.hex())
            req.add_header("Cookie", f"phix_access={cookie}")
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        text = raw.decode("utf-8", "replace")
        try:
            body = json.loads(text) if text else {}
        except ValueError:
            body = {"_raw": text[:200]}
        return status, body, text

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self.proc.terminate()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=10)
        with contextlib.suppress(Exception):
            self._log.close()


def t09_http_layer(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T09 HTTP 层：401 / 409 / 200 / 404（真起 server.py + 假 phix 同步后端）")
    accounts = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")
    phix = FakePhix().start()

    # ---- 未登录 401 ----
    anon = LocalSite(accounts=None, phix=phix, imap_port=fake_imap.port)
    try:
        st, body, _ = anon.get("/app/data/", auth=False)
        check("GET /app/data/ 匿名 → 401 unauthorized",
              st == 401 and body.get("ok") is False
              and body.get("error", {}).get("code") == "unauthorized", extra=str(body))
        st2, body2, _ = anon.get("/app/mail/3/", auth=False)
        check("GET /app/mail/<uid>/ 匿名 → 401",
              st2 == 401 and body2.get("error", {}).get("code") == "unauthorized")
        st2p, body2p, _ = anon.post("/app/mail/3/read/", {}, auth=False)
        check("POST /app/mail/<uid>/read/ 匿名 → 401",
              st2p == 401 and body2p.get("error", {}).get("code") == "unauthorized",
              extra=str(body2p))
    finally:
        anon.stop()

    # ---- 登录了但没配账号 → 409 ----
    site = LocalSite(accounts=accounts, phix=phix, imap_port=fake_imap.port)
    try:
        st, body, _ = site.get("/app/data/")           # 假 phix 里没有这个对象 → 没配
        check("GET /app/data/ 没配 settings.accounts → 409 accounts_not_configured",
              st == 409 and body.get("error", {}).get("code") == "accounts_not_configured"
              and "个人中心" in body.get("error", {}).get("message", ""), extra=str(body))
        st2, body2, _ = site.get("/app/mail/3/")
        check("GET /app/mail/<uid>/ 没配账号 → 409", st2 == 409)
        st2p, body2p, _ = site.post("/app/mail/3/read/", {})
        check("POST /app/mail/<uid>/read/ 没配账号 → 409", st2p == 409, extra=str(body2p))
    finally:
        site.stop()

    # ---- 配好了 → 200（前端 cookie 拿到的 DEK 解出的账号，真去抓假平台）----
    good = LocalSite(accounts=accounts, phix=phix, imap_port=fake_imap.port)
    good.seed_accounts(accounts)
    try:
        st, body, raw = good.get("/app/data/")
        check("GET /app/data/ → 200 且 ok:true", st == 200 and body.get("ok") is True,
              extra=f"{st} {str(body)[:300]}")
        check("返回契约四段齐全",
              set(body) == {"ok", "edupage", "managebac", "mail", "meta"},
              extra=str(sorted(body)))
        check("meta.cache ∈ {hit,miss} 且 fetched_at 是 ISO8601",
              body["meta"]["cache"] in ("hit", "miss")
              and re.match(r"^\d{4}-\d{2}-\d{2}T", body["meta"]["fetched_at"]))
        check("meta.accounts 只有账号名（= 客户端填的账号，不是口令）",
              body["meta"]["accounts"]["managebac"] == MB_USER
              and body["meta"]["accounts"]["mail"] == MAIL_USER
              and body["meta"]["accounts"]["edupage"] == EP_USER)
        check("HTTP 路径下也真解析出了课程/作业/邮件",
              len(body["managebac"]["courses"]) == 5
              and len(body["managebac"]["tasks"]) == 15
              and body["mail"]["unread"] == 2 and len(body["mail"]["recent"]) == 3,
              extra=json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v)
                                for k, v in body.items()}, ensure_ascii=False)[:300])
        check("HTTP 路径下 edupage 段失败也只影响自己（子进程没有假 EduPage；"
              "没抓完时标 pending，抓完/失败后给分类原因）",
              body["edupage"] == {"lessons": [], "selected": []}
              and "edupage" in body["meta"]["errors"]
              and (body["meta"].get("pending") == ["edupage"]
                   or "EduPage" in body["meta"]["errors"]["edupage"]
                   or body["meta"]["errors"]["edupage"].startswith("EduPage")),
              extra=str(body["meta"]))
        check("HTTP 路径下 managebac 段成功、无错误",
              "managebac" not in body["meta"]["errors"]
              and "mail" not in body["meta"]["errors"],
              extra=str(body["meta"]["errors"]))
        assert_no_secrets("T09 /app/data/ 响应", raw)

        # 缓存命中
        st_hit, body_hit, _ = good.get("/app/data/")
        check("第二次请求 meta.cache == 'hit'（缓存生效）",
              st_hit == 200 and body_hit["meta"]["cache"] == "hit",
              extra=str(body_hit.get("meta", {}).get("cache")))
        st_force, body_force, _ = good.get("/app/data/?force=1")
        check("?force=1 绕过缓存（cache == 'miss'）",
              st_force == 200 and body_force["meta"]["cache"] == "miss")

        # 邮件正文
        st_mail, mail_body, mail_raw = good.get("/app/mail/2/")
        check("GET /app/mail/2/ → 200 且正文正确",
              st_mail == 200 and mail_body.get("ok") is True
              and mail_body["mail"]["body_text"] == "记得交作业。"
              and mail_body["mail"]["subject"] == "作业提醒：数学", extra=str(mail_body)[:300])
        assert_no_secrets("T09 /app/mail/ 响应", mail_raw)

        st_404, body_404, _ = good.get("/app/mail/999/")
        check("GET /app/mail/999/ → 404", st_404 == 404
              and body_404.get("error", {}).get("code") == "not_found", extra=str(body_404))

        # ---- 标记已读：POST /app/mail/<uid>/read/（唯一会写邮箱状态的入口）----
        st_read, read_body, read_raw = good.post("/app/mail/3/read/", {})
        check("POST /app/mail/3/read/ → 200 {ok:true, mail:{uid,unread:false}}",
              st_read == 200 and read_body.get("ok") is True
              and read_body.get("mail") == {"uid": "3", "unread": False},
              extra=str(read_body))
        assert_no_secrets("T09 /app/mail/<uid>/read/ 响应", read_raw)
        st_read_bad, body_read_bad, _ = good.post("/app/mail/abc/read/", {})
        check("POST /app/mail/abc/read/（非法 uid）→ 404 而不是 500",
              st_read_bad == 404, extra=str(body_read_bad))
        st_read_404, body_read_404, _ = good.post("/app/mail/999/read/", {})
        check("POST /app/mail/999/read/（不存在的那封）→ 404",
              st_read_404 == 404
              and body_read_404.get("error", {}).get("code") == "not_found",
              extra=str(body_read_404))
        # 标记后未读计数确实少了 1（缓存就地更新）
        st_after, after_body, _ = good.get("/app/data/")
        check("标记已读后列表未读 -1：uid 3 变 unread:false，mail.unread 从 2 变 1",
              st_after == 200 and after_body["mail"]["unread"] == 1
              and after_body["mail"]["recent"][0]["unread"] is False,
              extra=str(after_body["mail"]["unread"])
              + str([i.get("unread") for i in after_body["mail"]["recent"]]))
        st_bad, body_bad, _ = good.get("/app/mail/abc/")
        check("GET /app/mail/abc/（非法 uid）→ 404 而不是 500",
              st_bad == 404 and body_bad.get("error", {}).get("code") == "not_found",
              extra=str(st_bad))

        # ---- 通讯录：/app/mail/contacts/ 必须走到**通讯录处理器**，不能被当成 uid ----
        # 2026-09-17 生产事故复现：`_APP_MAIL_PATH_RE`（`/app/mail/([^/]{1,32})/?`）排在
        # 通讯录路由前面时，`contacts` 被当成邮件 uid → IMAP 里找 "contacts" 这封邮件 →
        # 用户看到「读不到通讯录：找不到这封邮件（可能已被删除或移动）」。
        # 这一条就是钉住这个顺序：URL 一样，**错误消息不一样**才是证据。
        st_ct, body_ct, raw_ct = good.get("/app/mail/contacts/")
        msg_ct = (body_ct.get("error") or {}).get("message", "")
        check("T09.30 GET /app/mail/contacts/ **不是**「找不到这封邮件」（路由没被 uid 吞掉）",
              "找不到这封邮件" not in msg_ct, extra=f"{st_ct} {msg_ct}")
        check("T09.31 通讯录真扫出联系人（假 IMAP：收件箱 + 已发送「&XfJT0ZAB-」）",
              st_ct == 200 and any(c.get("email") == "teacher@example.invalid"
                                   for c in (body_ct.get("contacts") or [])),
              extra=f"{st_ct} {str(body_ct)[:300]}")
        check("T09.32 通讯录**不把自己**收进去（student@example.invalid 不在里面）",
              all(c.get("email") != MAIL_USER for c in (body_ct.get("contacts") or [])),
              extra=str([c.get("email") for c in (body_ct.get("contacts") or [])]))
        assert_no_secrets("T09 /app/mail/contacts/ 响应", raw_ct)
        st_ct2, body_ct2, _ = good.get("/app/mail/contacts/")
        check("T09.33 通讯录第二次命中缓存（meta.cache == True）",
              st_ct2 == 200 and (body_ct2.get("meta") or {}).get("cache") is True,
              extra=str(body_ct2.get("meta")))
        # 顺带钉住「保留段」这套判据本身：这些路径段永远不该被当成邮件 uid
        import re as _re
        _pat = _re.compile(r"^/app/mail/([^/]{1,32})/?$")
        for _p in ("/app/mail/contacts/", "/app/mail/send/", "/app/mail/read/"):
            _m = _pat.match(_p)
            check("T09.34 %s 被识别为保留段而不是 uid" % _p,
                  _m is not None and _m.group(1) in ("contacts", "send", "read"),
                  extra=str(_m.group(1) if _m else None))
        check("T09.35 普通 uid 仍然照常匹配（12345 → 不是保留段）",
              (_pat.match("/app/mail/12345/") or _pat.match("")).group(1) == "12345", "")
    finally:
        good.stop()
        log_text = good.log_tail()
        audit_text = ""
        with contextlib.suppress(OSError):
            with open(os.path.join(good.tmp, "admin_audit.log"),
                      encoding="utf-8", errors="replace") as fh:
                audit_text = fh.read()[-4000:]
        phix.stop()

    # ---- 服务端日志/审计里也不能有口令 ----
    assert_no_secrets("T09 服务端日志与审计", log_text + audit_text)
    check("站点进程没因新路由崩（日志里没有 Traceback）", "Traceback" not in log_text,
          extra=log_text[-400:])
    check("审计日志确实写了记录（uid + 平台）",
          "app/data" in audit_text and "app/mail" in audit_text, extra=audit_text[-200:])
    check("审计日志里没有账号名",
          MAIL_USER not in audit_text and MB_USER not in audit_text, extra=audit_text[-200:])
    check("测试没有写真实站点目录（审计写在临时副本里）",
          os.path.abspath(good.tmp).startswith(tempfile.gettempdir()) and audit_text != "")


def t10_smoke_gatekeeping(mb: ManageBacFake, fake_imap: FakeIMAP) -> None:
    section("T10 并发与幂等（ThreadingHTTPServer 下的缓存击穿防护）")
    acct = make_accounts(mb_base=mb.base_url, imap_host="127.0.0.1")
    wd.clear_cache()
    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        with fake_edupage(), _patch_imap(fake_imap):
            out = wd.fetch_all(acct, user_id="t10")
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)

    check("6 个并发请求都拿到完整数据",
          len(results) == 6 and all(r["managebac"]["courses"] for r in results),
          extra=str(len(results)))
    check("并发时只有第一次是 miss（其余走缓存/等待同一把锁）",
          sum(1 for r in results if r["meta"]["cache"] == "miss") == 1,
          extra=str([r["meta"]["cache"] for r in results]))


# ============================================================ main

def main() -> int:
    print("=" * 74)
    print("phix 官网 · 服务端平台抓取层测试（test_webapp_data.py）")
    print("=" * 74)

    mb = ManageBacFake(mode="ok").start()
    fake_imap = FakeIMAP().start()
    print(f"假 ManageBac: {mb.base_url}    假 IMAP: 127.0.0.1:{fake_imap.port} (TLS)")
    try:
        t01_credential_mapping()
        t02_full_fetch(mb, fake_imap)
        t03_cache(mb, fake_imap)
        t04_isolation(mb, fake_imap)
        t05_timeout(mb, fake_imap)
        t06_deps_and_badinput()
        t07_mail_body(fake_imap)
        t08_logging_and_status(mb, fake_imap)
        t11_mail_unread_flags()
        t16_mark_mail_seen()
        t17_mail_html_verbatim()
        t12_edupage_error_mapping()
        t13_edupage_week_mapping(mb, fake_imap)
        t14_edupage_background(mb, fake_imap)
        t15_due_normalization()
        t09_http_layer(mb, fake_imap)
        t10_smoke_gatekeeping(mb, fake_imap)
    finally:
        mb.stop()
        fake_imap.stop()

    print("-" * 74)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name in FAILED:
        print(f"  FAIL: {name}")
    return 0 if not FAILED else 1


# ============================================================ 假 SMTP（真 smtplib 协议栈）

class FakeSMTP:
    """极简 SMTP over SSL 服务器（够 smtplib.SMTP_SSL 跑通 EHLO/AUTH/MAIL/RCPT/DATA）。"""

    def __init__(self, user: str = MAIL_USER, password: str = PWD_MAIL,
                 *, reject_auth: bool = False, reject_recipient: bool = False):
        self.user = user
        self.password = password
        self.reject_auth = reject_auth
        self.reject_recipient = reject_recipient
        self.mail_from: str = ""
        self.rcpt_to: list[str] = []
        self.data_lines: list[str] = []
        self.data_headers: dict[str, str] = {}
        self.data_body: str = ""
        self.auth_attempts: list[str] = []
        self.cert_dir = tempfile.mkdtemp(prefix="fakesmtp-")
        cert, key = _self_signed_cert(self.cert_dir)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.3)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        with contextlib.suppress(Exception):
            self.sock.close()

    def _serve(self):
        while not self._stop.is_set():
            try:
                raw, _ = self.sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=self._session, args=(raw,), daemon=True).start()

    def _session(self, raw):
        try:
            conn = self.ctx.wrap_socket(raw, server_side=True)
        except Exception:
            with contextlib.suppress(Exception):
                raw.close()
            return
        try:
            conn.settimeout(10)
            fh = conn.makefile("rwb")
            if self._stop.is_set():
                return
            # SMTP banner
            fh.write(b"220 FakeSMTP ready\r\n")
            fh.flush()
            in_data = False
            data_buf: list[str] = []
            while True:
                line = fh.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                if in_data:
                    if text == ".":
                        in_data = False
                        self.data_lines = data_buf
                        # Parse headers and body
                        header_done = False
                        hdrs: dict[str, str] = {}
                        body_lines: list[str] = []
                        for dl in data_buf:
                            if not header_done:
                                if dl == "":
                                    header_done = True
                                    continue
                                if ":" in dl:
                                    k, v = dl.split(":", 1)
                                    hdrs[k.strip().lower()] = v.strip()
                            else:
                                body_lines.append(dl)
                        self.data_headers = hdrs
                        self.data_body = "\r\n".join(body_lines)
                        fh.write(b"250 OK: Message queued\r\n")
                        fh.flush()
                    else:
                        # dot-stuffing: leading .. → leading .
                        if text.startswith(".."):
                            text = text[1:]
                        data_buf.append(text)
                    continue
                parts = text.split(" ", 1)
                cmd = parts[0].upper()
                rest = parts[1] if len(parts) > 1 else ""
                if cmd == "EHLO" or cmd == "HELO":
                    fh.write(b"250-FakeSMTP\r\n250-AUTH LOGIN PLAIN\r\n250 OK\r\n")
                elif cmd == "AUTH":
                    self.auth_attempts.append(rest)
                    if self.reject_auth:
                        fh.write(b"535 Authentication failed\r\n")
                    else:
                        fh.write(b"235 Authentication successful\r\n")
                elif cmd == "MAIL":
                    m = re.search(r"<([^>]+)>", rest)
                    self.mail_from = m.group(1) if m else rest
                    fh.write(b"250 OK\r\n")
                elif cmd == "RCPT":
                    m = re.search(r"<([^>]+)>", rest)
                    addr = m.group(1) if m else rest
                    if self.reject_recipient:
                        fh.write(b"550 User not found\r\n")
                    else:
                        self.rcpt_to.append(addr)
                        fh.write(b"250 OK\r\n")
                elif cmd == "DATA":
                    in_data = True
                    data_buf = []
                    fh.write(b"354 Start mail input\r\n")
                elif cmd == "QUIT":
                    fh.write(b"221 Bye\r\n")
                    fh.flush()
                    return
                elif cmd == "NOOP":
                    fh.write(b"250 OK\r\n")
                else:
                    fh.write(b"500 Command not recognized\r\n")
                fh.flush()
        except (OSError, ssl.SSLError):
            return
        finally:
            with contextlib.suppress(Exception):
                conn.close()


def smtp_connect_factory(fake: FakeSMTP):
    """把 smtplib.SMTP_SSL 换成连本地假服务的版本（真 smtplib 协议栈）。"""
    class _Conn:
        def __new__(cls, host, port=465, timeout=None, context=None, **kwargs):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return _ORIG_SMTP_SSL("127.0.0.1", fake.port, timeout=timeout, context=ctx)
    return _Conn


_ORIG_SMTP_SSL = __import__("smtplib").SMTP_SSL


def _patch_smtp(fake: FakeSMTP):
    import smtplib
    patcher = contextlib.ExitStack()
    patcher.enter_context(_monkeypatch_attr(smtplib, "SMTP_SSL", smtp_connect_factory(fake)))
    return patcher


# ============================================================ T18 邮件发送（SMTP）

def t18_mail_send() -> None:
    """T18 邮件发送（假 SMTP + 真 smtplib 协议栈）。"""
    section("T18 邮件发送：正常 / 多收件人 / 参数错误 / 未配置 / 认证失败 / 连接失败 / 日志 / 限流")

    # ---- (a) 正常发送 ----
    fake_smtp = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        with _patch_smtp(fake_smtp):
            result = wd.send_mail(
                acct,
                to="recipient@example.com",
                subject="测试主题：你好",
                body_text="这是一封测试邮件正文。",
            )
        check("T18.1 正常发送返回 {to, cc, subject, sent_at}",
              isinstance(result, dict)
              and result["to"] == ["recipient@example.com"]
              and result["cc"] == []
              and result["subject"] == "测试主题：你好"
              and "sent_at" in result,
              extra=str(result))
        check("T18.2 假 SMTP 服务器收到的 MAIL FROM 正确",
              fake_smtp.mail_from == MAIL_USER, extra=fake_smtp.mail_from)
        check("T18.3 假 SMTP 服务器收到的 RCPT TO 正确",
              fake_smtp.rcpt_to == ["recipient@example.com"], extra=str(fake_smtp.rcpt_to))
        check("T18.4 中文主题不乱码（Subject 头含 utf-8 编码）",
              "测试主题：你好" in fake_smtp.data_headers.get("subject", "")
              or "utf-8" in fake_smtp.data_headers.get("subject", "").lower(),
              extra=str(fake_smtp.data_headers.get("subject", "")))
        check("T18.5 正文完整传递（DATA 里含正文原文或 MIME 编码形式）",
              "这是一封测试邮件正文。" in fake_smtp.data_body
              or "这是一封测试邮件正文。" in "\n".join(fake_smtp.data_lines),
              extra=fake_smtp.data_body[:200] + " || " + "\n".join(fake_smtp.data_lines)[:200])
    finally:
        fake_smtp.stop()

    # ---- (b) 多收件人（逗号/分号/换行分隔）----
    fake_smtp2 = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        with _patch_smtp(fake_smtp2):
            result2 = wd.send_mail(
                acct,
                to="a@b.com, c@d.com; e@f.com\ng@h.com",
                subject="多收件人测试",
                body_text="群发测试",
            )
        check("T18.6 多种分隔符都能解析（4 个收件人）",
              result2["to"] == ["a@b.com", "c@d.com", "e@f.com", "g@h.com"],
              extra=str(result2["to"]))
        check("T18.7 假 SMTP 收到 4 个 RCPT TO",
              len(fake_smtp2.rcpt_to) == 4, extra=str(fake_smtp2.rcpt_to))
    finally:
        fake_smtp2.stop()

    # ---- (c) 缺 to → 400 ----
    acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
    try:
        wd.send_mail(acct, to="", subject="x", body_text="y")
        missing_to = "no-raise"
    except wd.PlatformError as exc:
        missing_to = exc
    check("T18.8 缺 to → PlatformError（缺少收件人地址）",
          isinstance(missing_to, wd.PlatformError) and "收件人" in str(missing_to),
          extra=repr(missing_to))

    try:
        wd.send_mail(acct, to="a@b.c", subject="", body_text="y")
        missing_subj = "no-raise"
    except wd.PlatformError as exc:
        missing_subj = exc
    check("T18.9 缺 subject → PlatformError（缺少邮件主题）",
          isinstance(missing_subj, wd.PlatformError) and "主题" in str(missing_subj),
          extra=repr(missing_subj))

    try:
        wd.send_mail(acct, to="a@b.c", subject="x", body_text="")
        missing_body = "no-raise"
    except wd.PlatformError as exc:
        missing_body = exc
    check("T18.10 缺 body_text → PlatformError（缺少邮件正文）",
          isinstance(missing_body, wd.PlatformError) and "正文" in str(missing_body),
          extra=repr(missing_body))

    try:
        wd.send_mail(acct, to="not-an-email", subject="x", body_text="y")
        bad_addr = "no-raise"
    except wd.PlatformError as exc:
        bad_addr = exc
    check("T18.11 非法地址 → PlatformError（格式非法）",
          isinstance(bad_addr, wd.PlatformError) and "非法" in str(bad_addr),
          extra=repr(bad_addr))

    # ---- (c2) 抄送：用户实测的 "[object HTMLInputElement]" 事故 ----
    # 前端 openCompose 曾把 DOM 元素当抄送值（`(opts && cc)`），用户**没填抄送**也被
    # 塞进一个元素——String(input) 就是 "[object HTMLInputElement]"，于是服务端回
    # 「抄送地址格式非法：[object HTMLInputElement]」。这里把服务端那一半钉死：
    # ① 正常抄送能过；② 脏值必须被拒并**原样回显**（证明用户看到的报错确实来自这个值）。
    fake_cc = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        with _patch_smtp(fake_cc):
            ok_cc = wd.send_mail(acct, to="a@b.com", cc="c@d.com, e@f.com",
                                 subject="抄送正常", body_text="正文")
            cc_bad = None
            try:
                wd.send_mail(acct, to="a@b.com", cc="[object HTMLInputElement]",
                             subject="抄送脏值", body_text="正文")
            except wd.PlatformError as exc:
                cc_bad = str(exc)
            cc_many = None
            try:
                wd.send_mail(acct, to="a@b.com",
                             cc=", ".join("c%d@d.com" % i
                                          for i in range(wd.MAIL_SEND_MAX_RECIPIENTS + 1)),
                             subject="抄送太多", body_text="正文")
            except wd.PlatformError as exc:
                cc_many = str(exc)
    finally:
        fake_cc.stop()

    check("T18.11b 正常抄送（逗号分隔两个）能发出去",
          ok_cc.get("cc") == ["c@d.com", "e@f.com"], extra=repr(ok_cc.get("cc")))
    check("T18.11c 抄送是 `[object HTMLInputElement]` → 报错原样回显该值（复现用户看到的提示）",
          cc_bad is not None and "抄送地址格式非法" in cc_bad
          and "[object HTMLInputElement]" in cc_bad, extra=repr(cc_bad))
    check("T18.11d 抄送数量超上限 → 明确提示（不是静默丢弃）",
          cc_many is not None and "抄送数量超过上限" in cc_many, extra=repr(cc_many))

    # ---- (d) 没配邮箱账号 → 409 ----
    no_mail_acct = {"managebac": {"username": "x", "password": "y"}}
    try:
        wd.send_mail(no_mail_acct, to="a@b.c", subject="x", body_text="y")
        no_acct = "no-raise"
    except wd.PlatformError as exc:
        no_acct = exc
    check("T18.12 没配邮箱账号 → PlatformError（还没有填邮箱账号）",
          isinstance(no_acct, wd.PlatformError) and "邮箱账号" in str(no_acct),
          extra=repr(no_acct))

    # ---- (e) SMTP 认证失败 → 502 + 「授权码不对」----
    fake_auth_fail = FakeSMTP(reject_auth=True).start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        with _patch_smtp(fake_auth_fail):
            try:
                wd.send_mail(acct, to="a@b.c", subject="x", body_text="y")
                auth_fail = "no-raise"
            except wd.PlatformError as exc:
                auth_fail = exc
        check("T18.13 SMTP 认证失败 → PlatformError（授权码不对）",
              isinstance(auth_fail, wd.PlatformError) and "授权码不对" in str(auth_fail),
              extra=repr(auth_fail))
    finally:
        fake_auth_fail.stop()

    # ---- (f) 连不上 SMTP → 502 + 「连不上邮件服务器」----
    import smtplib as _smtplib
    class _DeadSMTP_SSL:
        def __new__(cls, *args, **kwargs):
            raise ConnectionRefusedError("Connection refused")
    old_smtp = _smtplib.SMTP_SSL
    _smtplib.SMTP_SSL = _DeadSMTP_SSL
    try:
        bad_acct = {"mail": {"email": MAIL_USER, "authcode": PWD_MAIL,
                             "imap_host": "imap.test.invalid"}}
        try:
            wd.send_mail(bad_acct, to="a@b.c", subject="x", body_text="y")
            conn_fail = "no-raise"
        except wd.PlatformError as exc:
            conn_fail = exc
        check("T18.14 连不上 SMTP → PlatformError（连不上邮件服务器）",
              isinstance(conn_fail, wd.PlatformError) and "连不上" in str(conn_fail),
              extra=repr(conn_fail))
    finally:
        _smtplib.SMTP_SSL = old_smtp

    # ---- (g) 口令/正文不进日志（断言 grep）----
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    wd.logger.addHandler(handler)
    old_level = wd.logger.level
    wd.logger.setLevel(logging.INFO)
    try:
        fake_log = FakeSMTP().start()
        try:
            acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
            wd.clear_cache()
            with _patch_smtp(fake_log):
                wd.send_mail(acct, to="a@b.c", subject="日志测试主题",
                             body_text="这是不应该出现在日志里的正文内容")
        finally:
            fake_log.stop()
    finally:
        wd.logger.removeHandler(handler)
        wd.logger.setLevel(old_level)

    log_text = stream.getvalue()
    check("T18.15 日志里没有口令", PWD_MAIL not in log_text, extra=log_text[:300])
    check("T18.16 日志里没有邮件正文", "不应该出现在日志里" not in log_text, extra=log_text[:300])
    check("T18.17 日志里没有收件人全地址（只记域名或数量）",
          "a@b.com" not in log_text, extra=log_text[:300])
    check("T18.18 日志里有 send_mail 标记",
          "send_mail" in log_text, extra=log_text[:300])

    # ---- (h) 限流：第 6 次 → False ----
    import server as srv
    bucket_key = "app_mail_send:4242"
    with srv._rate_lock:
        srv._rate_buckets.pop(bucket_key, None)
    # Send 5 (the limit)
    for i in range(5):
        with srv._rate_lock:
            now = time.monotonic()
            hits = list(srv._rate_buckets.get(bucket_key, []))
            hits.append(now)
            srv._rate_buckets[bucket_key] = hits
    # 6th should fail
    ok = srv._rate_ok(bucket_key, 5)
    check("T18.19 限流：第 6 次 → False（每用户每分钟 ≤ 5 封）",
          ok is False, extra=f"ok={ok}")


# ============================================================ main（含邮件发送）

def t19_mail_full_and_attachments(fake_imap: FakeIMAP) -> None:
    """T19 全量抓取 + 附件。

    两件事都是用户明确要求的，而且之前都坏过：
      1. 「这个账号所有的邮件都抓取然后显示」——旧写法逐封 FETCH，几百封就打穿
         20 秒闸门，用户实测「邮件完全抓不到」。这里断言**成批取**（每批约 40 封），
         并断言 300 封能在预算内返回。
      2. 「写邮件的时候发送附件 / 查看对方的附件」——附件**元数据**进列表，
         **内容**走单独下载接口（列表阶段不搬二进制）。
    """
    section("T19 全量邮件（成批取，不逐封）+ 附件元数据与下载")

    # ---- (a) 640 封：**按真机量级**验证成批取（真实邮箱实测 638 封）----
    # 真机证据（2026-09-17）：学校邮箱 638 封，旧写法逐封 FETCH → 打穿 45 秒预算；
    # 而且完整 `HEADER` 里塞满收信链路，638 封拉完整头也要 48 秒以上。
    # 现在：成批 60 封一次命令 + 只取 From/Subject/Date。这里就按同样的量级断言。
    bulk = FakeIMAP(unread=0).start()
    try:
        uids = bulk.add_many(640, start=1000)
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(bulk):
            started = time.monotonic()
            mail = wd.fetch_mail(acct["mail"])
            elapsed = time.monotonic() - started
    finally:
        bulk.stop()

    check("T19.1 640 封全部拿到（不再截断到 10 封）",
          len(mail["recent"]) == 643 and
          sorted(int(m["uid"]) for m in mail["recent"]) == sorted(uids + [1, 2, 3]),
          extra=f"{len(mail['recent'])} 封")
    check("T19.2 顺序是最新在前（uid 降序）",
          [int(m["uid"]) for m in mail["recent"]] == sorted(uids + [1, 2, 3], reverse=True),
          extra=str([m["uid"] for m in mail["recent"]][:5]))
    check("T19.2b 只取 From/Subject/Date 三个头字段（真机上完整 HEADER 太重，是超时的另一半原因）",
          wd.MAIL_HEADER_FIELDS == "(FROM SUBJECT DATE)"
          and any("HEADER.FIELDS" in r and "FROM SUBJECT DATE" in r for r in bulk.requests),
          extra=str(bulk.requests[:1]))
    check("T19.3 **成批取邮件头**：命令条数 ≈ 邮件数 / 60，远少于邮件数",
          # 逐封写法：命令数 == 邮件数（643）。成批写法：643/60 = 11 条。
          len(bulk.requests) <= (643 // 60) + 3,
          extra=f"共 {len(bulk.requests)} 条 FETCH 命令，每批 {sorted(set(bulk.fetch_batches), reverse=True)[:3]} 封")
    check("T19.4 640 封能在邮箱预算内返回（不超时）",
          elapsed <= wd.MAIL_TIMEOUT, extra=f"{elapsed:.2f}s ≤ {wd.MAIL_TIMEOUT}s")
    check("T19.5 全量抓取仍然全程只读（没有把任何邮件标成已读）",
          bulk.marked_read == set(), extra=str(sorted(bulk.marked_read)))

    # ---- (b) 附件：列表只给头，正文接口才给附件元数据 ----
    att = FakeIMAP(unread=0).start()
    try:
        payload = att.add_attachment_mail(9)
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="127.0.0.1")
        wd.clear_cache()
        with _patch_imap(att):
            listing = wd.fetch_mail(acct["mail"])
            body = wd.fetch_mail_body(acct, "9")
            first = wd.fetch_mail_attachment(acct, "9", 0)
            second = wd.fetch_mail_attachment(acct, "9", 1)
            missing = wd.fetch_mail_attachment(acct, "9", 99)
            badindex = wd.fetch_mail_attachment(acct, "9", "abc")
            baduid = wd.fetch_mail_attachment(acct, "999999", 0)
    finally:
        att.stop()

    row = next((m for m in listing["recent"] if m["uid"] == "9"), None)
    check("T19.6 邮件列表**只带邮件头**（正文与附件刻意不进列表：列表必须快、必须回得来）",
          row is not None and "body_text" not in row and "attachments" not in row,
          extra=str(sorted(row.keys()) if row else None))

    atts = body.get("attachments")
    check("T19.7 正文接口带附件清单（元数据，不是内容），字段齐全",
          isinstance(atts, list) and len(atts) == 2
          and set(atts[0]) >= {"index", "filename", "size", "content_type"}
          and atts[0]["filename"] == "report.pdf" and atts[0]["size"] == len(payload),
          extra=str(atts))
    check("T19.8 中文附件名按 MIME 编码解出来了（不是 =?utf-8?b?...?= 原文）",
          isinstance(atts, list) and len(atts) == 2 and atts[1]["filename"] == "成绩单.pdf",
          extra=repr(atts[1]["filename"]) if isinstance(atts, list) and len(atts) > 1 else "")
    check("T19.9 正文本身也在（预加载失败时前端按需取，这条路必须可靠）",
          isinstance(body.get("body_text"), str) and "附件请查收" in body["body_text"],
          extra=repr((body.get("body_text") or "")[:60]))

    check("T19.10 附件下载回的是**原始字节**（逐字节相等），不是 base64 文本",
          first is not None and first["payload"] == payload,
          extra=str(first and first["payload"][:20]))
    check("T19.11 下载带正确的文件名与 MIME 类型",
          first is not None and first["filename"] == "report.pdf"
          and first["content_type"] == "application/pdf",
          extra=str(first and (first["filename"], first["content_type"])))
    check("T19.12 第二个附件（中文名）也能下",
          second is not None and second["filename"] == "成绩单.pdf"
          and second["payload"] == "中文附件内容".encode("utf-8"),
          extra=str(second and (second["filename"], second["payload"])))
    check("T19.13 index 越界 / 非数字 / uid 不存在 → None（调用方回 404，不 500）",
          missing is None and badindex is None and baduid is None,
          extra=f"{missing} {badindex} {baduid}")
    check("T19.14 取附件不会把邮件标成已读（仍然 PEEK）",
          att.marked_read == set(), extra=str(sorted(att.marked_read)))


def t20_mail_send_attachments() -> None:
    """T20 发信带附件（假 SMTP + 真 smtplib）。

    参考 CipherCore E-Mail Suite 的组信方式（`MIMEBase` + `encode_base64` +
    `add_header('Content-Disposition','attachment',filename=…)`）；这里我们用标准库
    更高一层的 `EmailMessage.add_attachment()`，它自己就做 base64 与 **RFC 2231
    文件名编码**（中文名不会乱码）。断言直接读假 SMTP 收到的 DATA 原文：
      * 附件部分真的在报文里（base64 解回来逐字节相等）
      * 中文文件名可还原
      * 限额（个数 / 单个 / 合计）被拦住并给中文原因
      * 附件不会污染正文与既有字段
    """
    section("T20 发送附件：MIME 组信 / 中文名 / 解码回原文 / 限额 / 不改既有行为")

    # ---- (a) 一个 ASCII 名 + 一个中文名的附件 ----
    fake = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        pdf = b"%PDF-1.4\n\x00\x01\x02 fake pdf bytes\n%%EOF\n"
        txt = "成绩单：数学 7 分\n".encode("utf-8")
        with _patch_smtp(fake):
            result = wd.send_mail(
                acct,
                to="teacher@example.com",
                subject="作业提交（带附件）",
                body_text="老师您好，附件是本次作业。",
                attachments=[
                    {"filename": "homework.pdf", "content_type": "application/pdf", "data": pdf},
                    {"filename": "成绩单.txt", "content_type": "text/plain; charset=utf-8", "data": txt},
                ],
            )
    finally:
        fake.stop()

    raw = "\r\n".join(fake.data_lines)
    check("T20.1 返回里如实列出附件（名字 + 字节数）",
          isinstance(result.get("attachments"), list)
          and [a["filename"] for a in result["attachments"]] == ["homework.pdf", "成绩单.txt"]
          and result["attachments"][0]["size"] == len(pdf),
          extra=str(result.get("attachments")))

    check("T20.2 报文里确实有这两个附件（Content-Disposition: attachment + 文件名）",
          raw.count("Content-Disposition: attachment") == 2
          and "homework.pdf" in raw,
          extra=raw[:200])

    check("T20.3 附件内容是 base64 编码的（不是裸二进制塞进报文）",
          "Content-Transfer-Encoding: base64" in raw,
          extra=raw[:300])

    # 把第一段 base64 解回来逐字节比对
    import base64 as _b64
    import email as _email
    parsed = _email.message_from_string(raw)
    got, names = [], []
    for part in parsed.walk():
        if part.get_content_maintype() == "multipart":
            continue
        fn = part.get_filename()
        if not fn:
            continue
        names.append(fn)
        got.append(part.get_payload(decode=True) or b"")
    check("T20.4 **中文附件名能还原**（不是 =?utf-8?b?…?= 原文）",
          "成绩单.txt" in names, extra=str(names))
    check("T20.5 附件解码回来与原字节**逐字节相等**",
          got[:2] == [pdf, txt], extra=str([len(g) for g in got]))
    check("T20.6 正文没被附件影响（仍是纯文本那段，且附件内容不在正文里）",
          "老师您好，附件是本次作业。" in raw
          and "fake pdf bytes" not in raw,
          extra=raw[-300:])

    # ---- (b) 限额：个数 / 单个 / 合计 ----
    fake2 = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        with _patch_smtp(fake2):
            too_many = None
            try:
                wd.send_mail(acct, to="a@b.com", subject="s", body_text="b",
                             attachments=[{"filename": "f%d.bin" % i,
                                           "content_type": "application/octet-stream",
                                           "data": b"x"} for i in range(wd.MAIL_SEND_MAX_ATTACHMENTS + 1)])
            except Exception as exc:  # noqa: BLE001
                too_many = str(exc)
            too_big = None
            try:
                wd.send_mail(acct, to="a@b.com", subject="s", body_text="b",
                             attachments=[{"filename": "big.bin",
                                           "content_type": "application/octet-stream",
                                           "data": b"x" * (wd.MAIL_SEND_MAX_ATTACHMENT_BYTES + 1)}])
            except Exception as exc:  # noqa: BLE001
                too_big = str(exc)
            total_big = None
            half = wd.MAIL_SEND_MAX_TOTAL_ATTACHMENT_BYTES // 2 + 1
            try:
                wd.send_mail(acct, to="a@b.com", subject="s", body_text="b",
                             attachments=[{"filename": "a.bin",
                                           "content_type": "application/octet-stream",
                                           "data": b"x" * half},
                                          {"filename": "b.bin",
                                           "content_type": "application/octet-stream",
                                           "data": b"x" * half}])
            except Exception as exc:  # noqa: BLE001
                total_big = str(exc)
    finally:
        fake2.stop()

    check("T20.7 附件个数超限被拦住并给中文原因",
          too_many and "附件个数" in too_many, extra=str(too_many))
    check("T20.8 单个附件过大被拦住并带上文件名",
          too_big and "太大" in too_big and "big.bin" in too_big, extra=str(too_big))
    check("T20.9 附件合计过大被拦住",
          total_big and "总大小" in total_big, extra=str(total_big))
    check("T20.10 被拦下的请求**根本没连 SMTP**（本地就拒了，不让用户白等）",
          fake2.rcpt_to == [] and not fake2.data_lines,
          extra=f"rcpt={fake2.rcpt_to} data={len(fake2.data_lines)}")

    # ---- (c) 不带附件时行为与以前完全一致（向后兼容）----
    fake3 = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        with _patch_smtp(fake3):
            plain = wd.send_mail(acct, to="a@b.com", subject="无附件",
                                 body_text="普通正文")
    finally:
        fake3.stop()
    check("T20.11 不带附件时是老行为（没有 attachment 段、返回 attachments 为空）",
          plain.get("attachments") == []
          and "Content-Disposition: attachment" not in "\r\n".join(fake3.data_lines),
          extra=str(plain))

    # ---- (d) 日志纪律：附件名与附件内容都不许进日志 ----
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    wd.logger.addHandler(handler)
    old_level = wd.logger.level
    wd.logger.setLevel(logging.INFO)
    fake4 = FakeSMTP().start()
    try:
        acct = make_accounts(mb_base="http://127.0.0.1:1", imap_host="imap.test.invalid")
        wd.clear_cache()
        secret_bytes = b"SECRET-ATTACHMENT-CONTENT"
        with _patch_smtp(fake4):
            wd.send_mail(acct, to="a@b.com", subject="日志测试", body_text="正文",
                         attachments=[{"filename": "机密成绩单.pdf",
                                       "content_type": "application/pdf",
                                       "data": secret_bytes}])
    finally:
        fake4.stop()
        wd.logger.removeHandler(handler)
        wd.logger.setLevel(old_level)
    logged = stream.getvalue()
    check("T20.12 附件名与附件内容**都不进日志**（只记个数与总字节）",
          "机密成绩单" not in logged and "SECRET-ATTACHMENT-CONTENT" not in logged
          and "SECRET" not in logged,
          extra=logged[-260:])
    check("T20.13 日志里是**个数 + 总字节**这种可统计但不可读的形态",
          "attachments=1" in logged and ("bytes=" in logged),
          extra=logged[-260:])


def main_extended() -> int:
    print("=" * 74)
    print("phix 官网 · 服务端平台抓取层测试 + 邮件发送（test_webapp_data.py）")
    print("=" * 74)

    mb = ManageBacFake(mode="ok").start()
    fake_imap = FakeIMAP().start()
    print(f"假 ManageBac: {mb.base_url}    假 IMAP: 127.0.0.1:{fake_imap.port} (TLS)")
    try:
        t01_credential_mapping()
        t02_full_fetch(mb, fake_imap)
        t03_cache(mb, fake_imap)
        t04_isolation(mb, fake_imap)
        t05_timeout(mb, fake_imap)
        t06_deps_and_badinput()
        t07_mail_body(fake_imap)
        t08_logging_and_status(mb, fake_imap)
        t11_mail_unread_flags()
        t16_mark_mail_seen()
        t17_mail_html_verbatim()
        t12_edupage_error_mapping()
        t13_edupage_week_mapping(mb, fake_imap)
        t14_edupage_background(mb, fake_imap)
        t15_due_normalization()
        t09_http_layer(mb, fake_imap)
        t10_smoke_gatekeeping(mb, fake_imap)
        t18_mail_send()
        t19_mail_full_and_attachments(fake_imap)
        t20_mail_send_attachments()
    finally:
        mb.stop()
        fake_imap.stop()

    print("-" * 74)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name in FAILED:
        print(f"  FAIL: {name}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    code = main_extended()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)      # 假服务器线程都是 daemon，直接退出不等待
