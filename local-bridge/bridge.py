#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""phix-local-bridge —— 网页端「本机直连」抓取服务（可选加速/可用性增强）。

为什么有这个东西
----------------
网页端 https://phix.ing/app/ 默认让**服务器**代抓学校平台。如果服务器所在网络
连不上学校平台（例如 EduPage 被拦），网页端就抓不到数据。本服务跑在**用户自己
的电脑**上，用用户自己的网络与 IP 去抓，通常就能连上；凭据由网页端在请求体里
带过来，**只在内存里用一次，不落盘、不写日志**。

接口（冻结，网页端按此调用）
--------------------------
    GET  /ping                       → 200 {"ok":true,"service":"phix-local-bridge","version":1}
    POST /data      {"accounts":{…}} → 与服务器 GET /app/data/ 完全相同的结构（?force=1 绕过缓存）
    POST /mail/<uid> {"accounts":{…}}→ 与服务器 GET /app/mail/<uid>/ 相同的结构
    POST /mail/<uid>/read/ {"accounts":{…}}
                                     → 与服务器 POST /app/mail/<uid>/read/ 相同：
                                       {"ok":true,"mail":{"uid":"…","unread":false}}
                                       **只标这一封**为已读（IMAP UID STORE +FLAGS (\Seen)）

安全
----
* **只绑 127.0.0.1**（绝不 0.0.0.0），默认端口 38123（`--port` / 环境变量 `PHIX_BRIDGE_PORT`）。
* 只允许 phix 官网与本地调试来源的跨域请求（CORS 白名单，见 ALLOWED_ORIGINS）。
* 平台账号口令只出现在请求体里，用完全程即丢；本进程**不写任何日志文件、不写 stdout**，
  请求日志一律关闭（`log_message` 空实现）。
* 抓取代码直接复用服务端同一套 `webapp_data` / `webapp_managebac` / `webapp_mb_parse`，
  函数签名不变 —— 本文件不含任何平台协议实现。

依赖
----
    pip install requests beautifulsoup4 edupage-api
（三个包都在函数内部延迟 import；装不上时对应平台返回「服务端缺少依赖，暂时无法抓取」，
  其余平台照常工作，服务绝不因为在 import 期缺包而崩。）
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:                 # 保证 import 的是本目录随附的那份抓取层
    sys.path.insert(0, HERE)

import webapp_data as wd  # noqa: E402  抓取层（本目录副本，函数签名与服务器一致）

SERVICE = "phix-local-bridge"
VERSION = 1
DEFAULT_PORT = 38123
BIND_HOST = "127.0.0.1"                  # 铁律：只绑本机回环

#: 允许跨域的来源：phix 官网（HTTPS 页面调 http://127.0.0.1 属"可信来源"，浏览器放行）+ 本地调试
ALLOWED_ORIGINS = ("https://phix.ing", "https://www.phix.ing")
ALLOWED_ORIGIN_RE = re.compile(
    r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$")
ALLOWED_METHODS = "GET, POST, OPTIONS"
ALLOWED_HEADERS = "Content-Type"

MAX_BODY = 256 * 1024                    # 请求体上限（凭据 JSON 远小于此）
MAIL_UID_RE = re.compile(r"^[0-9]{1,12}$")

#: 每用户缓存键 = 三平台账号名（与 webapp_data 一致；**不含口令**）
_cache_lock = threading.RLock()


def origin_allowed(origin: str | None) -> str:
    """回显可用的 Origin；不在白名单里返回 ''（此时不发 CORS 头）。"""
    if not origin:
        return ""
    o = origin.strip()
    if o in ALLOWED_ORIGINS or ALLOWED_ORIGIN_RE.match(o):
        return o
    return ""


def _accounts_of(body: dict | None):
    """请求体 → accounts 映射（非 dict 一律当没配）。"""
    if not isinstance(body, dict):
        return None
    acc = body.get("accounts")
    return acc if isinstance(acc, dict) else None


class Handler(BaseHTTPRequestHandler):
    server_version = "phix-local-bridge/" + str(VERSION)
    sys_version = ""
    # HTTP/1.0：每条请求一个连接、响应完就关。默认的 keep-alive 会在浏览器主动关掉
    # 空闲连接时给标准库抛 ConnectionResetError（会在控制台打一段 traceback），
    # 这里干脆不用长连接 —— 本机服务的请求量极小，代价可以忽略。
    protocol_version = "HTTP/1.0"

    # ---- 日志：一律关闭（口令绝不进日志；也不在控制台刷账号）----

    def log_message(self, fmt, *args):        # noqa: D102  （故意空实现）
        return

    def log_error(self, fmt, *args):          # noqa: D102
        return

    def handle_one_request(self):
        """标准库默认会把「浏览器提前关掉连接」当成异常打一整段 traceback 到控制台。
        这里把这类连接中断吞掉：本机服务不写日志、也不该吓到用户。"""
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # ---- CORS ----

    def _cors(self, origin: str) -> None:
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", ALLOWED_METHODS)
        self.send_header("Access-Control-Allow-Headers", ALLOWED_HEADERS)
        self.send_header("Access-Control-Max-Age", "600")

    def _json(self, status: int, body: dict, origin: str = "") -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors(origin)
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, status: int, code: str, message: str, origin: str = "") -> None:
        self._json(status, {"ok": False, "error": {"code": code, "message": message}}, origin)

    def _read_body(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    # ---- 路由 ----

    def do_OPTIONS(self):                      # noqa: N802
        path = (self.path or "/").split("?", 1)[0]
        origin = origin_allowed(self.headers.get("Origin"))
        if path not in ("/ping", "/data") and not path.startswith("/mail/"):
            return self._err(404, "not_found", "没有这个接口", origin)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors(origin)
        self.end_headers()
    def do_GET(self):                          # noqa: N802
        path = (self.path or "/").split("?", 1)[0].rstrip("/") or "/"
        origin = origin_allowed(self.headers.get("Origin"))
        if path in ("/ping", "/"):
            return self._json(200, {"ok": True, "service": SERVICE, "version": VERSION}, origin)
        return self._err(404, "not_found", "没有这个接口（本机桥只有 /ping、/data、/mail/<uid>、/mail/<uid>/read/）", origin)

    def do_POST(self):                         # noqa: N802
        raw_path = self.path or "/"
        path, _, query = raw_path.partition("?")
        path = path.rstrip("/") or "/"
        origin = origin_allowed(self.headers.get("Origin"))
        body = self._read_body()

        if path == "/data":
            return self._data(body, "force=1" in query, origin)
        if path == "/mail/send":
            return self._mail_send(body, origin)
        if path.startswith("/mail/"):
            rest = path[len("/mail/"):]
            # 先认 /mail/<uid>/read/（标记已读），再认 /mail/<uid>（读正文）。
            if rest.endswith("/read"):
                return self._mail_read(body, rest[:-len("/read")], origin)
            return self._mail(body, rest, origin)
        return self._err(404, "not_found", "没有这个接口", origin)

    # ---- 业务 ----

    def _data(self, body: dict | None, force: bool, origin: str) -> None:
        accounts = _accounts_of(body)
        if not accounts:
            return self._err(409, "accounts_not_configured",
                             wd.ACCOUNTS_NOT_CONFIGURED_MESSAGE, origin)
        try:
            payload = wd.fetch_all(accounts, force=force, user_id="local-bridge")
        except Exception:                      # noqa: BLE001  抓取层兜底：绝不让本机桥 500
            return self._err(502, "upstream_error", "抓取失败，请稍后重试", origin)
        out = {"ok": True}
        out.update(payload)
        return self._json(200, out, origin)

    def _mail(self, body: dict | None, uid: str, origin: str) -> None:
        accounts = _accounts_of(body)
        if not accounts:
            return self._err(409, "accounts_not_configured",
                             wd.ACCOUNTS_NOT_CONFIGURED_MESSAGE, origin)
        if not MAIL_UID_RE.match(uid or ""):
            return self._err(404, "not_found", wd.NOT_FOUND_MESSAGE, origin)
        try:
            mail = wd.fetch_mail_body(accounts, uid)
        except Exception as exc:               # noqa: BLE001
            code = wd.http_status_for(exc)
            return self._err(code, "not_found" if code == 404 else "upstream_error",
                             wd.error_message(exc), origin)
        if mail is None:
            return self._err(404, "not_found", wd.NOT_FOUND_MESSAGE, origin)
        return self._json(200, {"ok": True, "mail": mail}, origin)

    def _mail_read(self, body: dict | None, uid: str, origin: str) -> None:
        """POST /mail/<uid>/read/ —— 把**这一封**标成已读（唯一写邮箱状态的入口）。

        结构与服务器 `POST /app/mail/<uid>/read/` 一致：
        成功 200 `{"ok":true,"mail":{"uid":…,"unread":false}}`；
        uid 非法 / 这封不在了 → 404；没给账号 → 409；上游失败 → 502。
        """
        accounts = _accounts_of(body)
        if not accounts:
            return self._err(409, "accounts_not_configured",
                             wd.ACCOUNTS_NOT_CONFIGURED_MESSAGE, origin)
        if not MAIL_UID_RE.match(uid or ""):
            return self._err(404, "not_found", wd.NOT_FOUND_MESSAGE, origin)
        try:
            marked = wd.mark_mail_seen(accounts, uid, user_id="local-bridge")
        except Exception as exc:               # noqa: BLE001
            code = wd.http_status_for(exc)
            return self._err(code, "not_found" if code == 404 else "upstream_error",
                             wd.error_message(exc), origin)
        return self._json(200, {"ok": True, "mail": marked}, origin)

    def _mail_send(self, body: dict | None, origin: str) -> None:
        """POST /mail/send/ —— 通过 SMTP 发送邮件（与服务器 POST /app/mail/send/ 同构）。"""
        accounts = _accounts_of(body)
        if not accounts:
            return self._err(409, "accounts_not_configured",
                             wd.ACCOUNTS_NOT_CONFIGURED_MESSAGE, origin)
        if not isinstance(body, dict):
            return self._err(400, "bad_request", "请求格式错误", origin)
        to_raw = body.get("to") or ""
        subject = body.get("subject") or ""
        body_text = body.get("body_text") or ""
        if not isinstance(to_raw, str) or not to_raw.strip():
            return self._err(400, "bad_request", "缺少收件人地址", origin)
        if not isinstance(subject, str) or not subject.strip():
            return self._err(400, "bad_request", "缺少邮件主题", origin)
        if not isinstance(body_text, str) or not body_text.strip():
            return self._err(400, "bad_request", "缺少邮件正文", origin)
        try:
            result = wd.send_mail(
                accounts,
                to=to_raw,
                cc=str(body.get("cc") or ""),
                subject=str(subject),
                body_text=str(body_text),
                in_reply_to=str(body.get("in_reply_to") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, wd.PlatformError):
                msg = wd._clean_message(str(exc))
                text = str(exc).lower()
                if "缺少" in text or "非法" in text or "超过" in text or "太长" in text:
                    return self._err(400, "bad_request", msg, origin)
                return self._err(502, "upstream_error",
                                 "发送失败：" + msg, origin)
            return self._err(502, "upstream_error",
                             "发送失败：请稍后重试", origin)
        return self._json(200, {"ok": True, "mail": result}, origin)


def resolve_port(argv_port: int | None = None, env: str | None = None) -> int:
    """端口优先级：--port > 环境变量 PHIX_BRIDGE_PORT > 38123。非法值回退默认。"""
    for raw in (argv_port, env if env is not None else os.environ.get("PHIX_BRIDGE_PORT")):
        if raw in (None, ""):
            continue
        try:
            p = int(str(raw).strip())
        except ValueError:
            continue
        if 1 <= p <= 65535:
            return p
    return DEFAULT_PORT


def build_server(port: int, host: str = BIND_HOST) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port_arg = None
    if "--port" in argv:
        i = argv.index("--port")
        if i + 1 < len(argv):
            port_arg = argv[i + 1]
    if "--help" in argv or "-h" in argv:
        print("用法: python bridge.py [--port 38123]   （只绑 127.0.0.1；端口也可用 PHIX_BRIDGE_PORT）")
        return 0

    port = resolve_port(port_arg)
    try:
        srv = build_server(port)
    except OSError as exc:
        print(f"启动失败：{BIND_HOST}:{port} 起不来（{exc}）。"
              f"换端口：python bridge.py --port 38124", file=sys.stderr)
        return 2

    print(f"phix-local-bridge v{VERSION} 已启动：http://{BIND_HOST}:{port}")
    print(f"  仅监听本机回环（{BIND_HOST}），不对外网开放；凭据只在内存里用，不落盘、不写日志。")
    print(f"  接口：GET /ping · POST /data · POST /mail/<uid> · POST /mail/<uid>/read/")
    print(f"  关掉这个窗口即停止服务（网页端会自动回到「服务器抓取」）。")
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
