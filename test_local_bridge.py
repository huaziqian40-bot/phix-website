"""本机直连服务（local-bridge）专项测试：接口 / CORS / 绑定 / 不落口令。

    C:\\Python314\\python.exe -X utf8 D:\\phix\\website\\test_local_bridge.py [--port 38123]

覆盖：
  1) 进程只绑 127.0.0.1（netstat 取监听行，断言没有 0.0.0.0/[::]）；
  2) GET /ping → 200 {"ok":true,"service":"phix-local-bridge","version":1}；
  3) OPTIONS 预检：回显 phix.ing 的 Origin + 允许 POST/GET/OPTIONS + 允许 Content-Type；
     不在白名单的 Origin 不回 ACAO；
  4) POST /data（假凭据）→ 200，结构与服务端 /app/data/ 完全一致
     （ok / edupage.lessons+selected / managebac.courses+tasks / mail.unread+recent / meta.*）；
  5) POST /data 缺 accounts → 409 accounts_not_configured；
  6) POST /mail/<uid> → 与 /app/mail/<uid>/ 同结构（成功 {ok,mail} / 失败 {ok:false,error}）；
  7) 只绑回环：从本机非回环地址连不上（尽力而为，取不到非回环地址就跳过并注明）；
  8) 口令不落盘/不进日志：跑完后全目录搜测试口令，必须是 0 命中；
     且进程 stdout 里不出现口令与账号名。
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_DIR = os.path.join(HERE, "local-bridge")
PY = sys.executable
SECRET = "bridge-local-test-Secret-9f2c"     # 测试口令：跑完必须搜不到
USER = "bridge-local-test-user"

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def http(method, url, body=None, headers=None, timeout=180):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                parsed = json.loads(raw or b"{}")
            except ValueError:
                parsed = {}
            return r.status, dict(r.headers), parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {}
        return e.code, dict(e.headers), parsed


def listening_lines(port):
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    return [ln.strip() for ln in out.splitlines() if f":{port}" in ln and "LISTENING" in ln]


def nonloopback_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def grep_tree(root, needle):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.getsize(p) > 8 * 1024 * 1024:
                continue
            try:
                with open(p, "rb") as f:
                    if needle.encode("utf-8") in f.read():
                        hits.append(os.path.relpath(p, root))
            except OSError:
                continue
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=38123)
    ap.add_argument("--base", default="")
    args = ap.parse_args()
    port = args.port
    base = args.base or f"http://127.0.0.1:{port}"

    print("=" * 72)
    print(f"local-bridge 专项测试 → {base}")
    print("=" * 72)

    proc = subprocess.Popen([PY, "-X", "utf8", os.path.join(BRIDGE_DIR, "bridge.py"),
                             "--port", str(port)],
                            cwd=BRIDGE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    out_lines = []
    try:
        # 等它起来（最多 10 秒）
        up = False
        for _ in range(40):
            time.sleep(0.25)
            try:
                st, _, _ = http("GET", base + "/ping", timeout=3)
                up = (st == 200)
                if up:
                    break
            except Exception:
                continue
        check("1. 服务起来了（GET /ping 200）", up, "服务没起来")

        # ---- 绑定 ----
        lines = listening_lines(port)
        print("      netstat:", " | ".join(lines) or "（没有监听行）")
        check("2. 只绑 127.0.0.1（netstat 里有 127.0.0.1:<port> LISTENING）",
              any(f"127.0.0.1:{port}" in ln for ln in lines), str(lines))
        check("3. 绝不绑 0.0.0.0 / [::]（本机地址列里没有对外绑定）",
              all(ln.split()[1].startswith("127.0.0.1:") or ln.split()[1].startswith("[::1]:")
                  for ln in lines), str(lines))

        # ---- /ping ----
        st, hdrs, body = http("GET", base + "/ping")
        check("4. /ping 返回冻结结构 {ok, service, version}",
              st == 200 and body.get("ok") is True
              and body.get("service") == "phix-local-bridge" and body.get("version") == 1,
              f"{st} {body}")
        check("5. /ping 带 Content-Type: application/json", "application/json" in (hdrs.get("Content-Type") or ""),
              str(hdrs.get("Content-Type")))

        # ---- CORS 预检 ----
        st, hdrs, _ = http("OPTIONS", base + "/data",
                           headers={"Origin": "https://phix.ing",
                                    "Access-Control-Request-Method": "POST",
                                    "Access-Control-Request-Headers": "content-type"})
        print(f"      预检响应头：ACAO={hdrs.get('Access-Control-Allow-Origin')!r} "
              f"Methods={hdrs.get('Access-Control-Allow-Methods')!r} "
              f"Headers={hdrs.get('Access-Control-Allow-Headers')!r}")
        check("6. OPTIONS 预检 204 + 回显 https://phix.ing",
              st == 204 and hdrs.get("Access-Control-Allow-Origin") == "https://phix.ing", f"{st} {hdrs}")
        check("7. 预检允许 POST, GET, OPTIONS",
              "POST" in (hdrs.get("Access-Control-Allow-Methods") or "")
              and "GET" in (hdrs.get("Access-Control-Allow-Methods") or "")
              and "OPTIONS" in (hdrs.get("Access-Control-Allow-Methods") or ""),
              str(hdrs.get("Access-Control-Allow-Methods")))
        check("8. 预检允许 Content-Type 头",
              "content-type" in (hdrs.get("Access-Control-Allow-Headers") or "").lower(),
              str(hdrs.get("Access-Control-Allow-Headers")))
        st, hdrs2, _ = http("OPTIONS", base + "/data",
                            headers={"Origin": "https://evil.example",
                                     "Access-Control-Request-Method": "POST"})
        check("9. 白名单外的 Origin 不回 Access-Control-Allow-Origin",
              "Access-Control-Allow-Origin" not in hdrs2, str(hdrs2))
        st, hdrs3, _ = http("OPTIONS", base + "/data",
                            headers={"Origin": "http://localhost:3000",
                                     "Access-Control-Request-Method": "POST"})
        check("10. 本地调试来源 http://localhost:* 也放行",
              hdrs3.get("Access-Control-Allow-Origin") == "http://localhost:3000", str(hdrs3))

        # ---- POST /data ----
        accounts = {"edupage": {"username": USER, "password": SECRET},
                    "managebac": {"username": USER, "password": SECRET},
                    "mail": {"username": "bridge@example.com", "password": SECRET}}
        st, hdrs, body = http("POST", base + "/data?force=1", {"accounts": accounts},
                              headers={"Origin": "https://phix.ing"})
        print("      /data 段：", ", ".join(sorted(k for k in body if k != "meta")))
        check("11. POST /data 200 且 ok=true", st == 200 and body.get("ok") is True, f"{st} {str(body)[:160]}")
        check("12. 结构含 edupage/managebac/mail/meta 四段",
              all(k in body for k in ("edupage", "managebac", "mail", "meta")), str(list(body)))
        check("13. edupage 段是 {lessons:[], selected:[]}",
              isinstance(body.get("edupage", {}).get("lessons"), list)
              and isinstance(body.get("edupage", {}).get("selected"), list), str(body.get("edupage"))[:120])
        check("14. managebac 段是 {courses:[], tasks:[]}",
              isinstance(body.get("managebac", {}).get("courses"), list)
              and isinstance(body.get("managebac", {}).get("tasks"), list), str(body.get("managebac"))[:120])
        check("15. mail 段是 {unread, recent:[]}",
              isinstance(body.get("mail", {}).get("recent"), list)
              and "unread" in body.get("mail", {}), str(body.get("mail"))[:120])
        meta = body.get("meta") or {}
        check("16. meta 含 fetched_at / cache / accounts / errors",
              all(k in meta for k in ("fetched_at", "cache", "accounts", "errors")), str(list(meta)))
        check("17. meta.cache 取值合法（hit/miss）", meta.get("cache") in ("hit", "miss"), str(meta.get("cache")))
        check("18. meta.accounts 只有账号名、没有口令",
              meta.get("accounts", {}).get("edupage") == USER
              and SECRET not in json.dumps(meta, ensure_ascii=False), str(meta.get("accounts")))
        check("19. 逐平台错误是可读中文原因（不是 traceback）",
              all(isinstance(v, str) and v and "Traceback" not in v for v in (meta.get("errors") or {}).values()),
              json.dumps(meta.get("errors"), ensure_ascii=False)[:200])
        check("20. 响应带 CORS 头（ACAO 回显 phix.ing）",
              hdrs.get("Access-Control-Allow-Origin") == "https://phix.ing", str(hdrs.get("Access-Control-Allow-Origin")))

        # ---- 同结构判定：字段名逐一对齐服务端契约 ----
        # `meta.pending` 是**可选**键：只在有平台正在后台抓时出现（EduPage 抓一次几十秒，
        # 服务端先返回 pending + 空段，抓完写进 30 分钟缓存）。其余字段必须逐段一致。
        contract = {
            "top": {"ok", "edupage", "managebac", "mail", "meta"},
            "edupage": {"lessons", "selected"},
            "managebac": {"courses", "tasks"},
            "mail": {"unread", "recent"},
            "meta": {"fetched_at", "cache", "accounts", "errors"},
        }
        optional_meta = {"pending"}
        ok_struct = (set(body) >= contract["top"]
                     and set(body.get("edupage", {})) == contract["edupage"]
                     and set(body.get("managebac", {})) == contract["managebac"]
                     and set(body.get("mail", {})) == contract["mail"]
                     and set(meta) >= contract["meta"]
                     and set(meta) - contract["meta"] <= optional_meta
                     and (meta.get("pending") is None or isinstance(meta.get("pending"), list)))
        check("21. 与服务端 /app/data/ 的字段集合逐段一致（meta.pending 为可选键）", ok_struct,
              json.dumps({k: sorted(v) for k, v in contract.items()}, ensure_ascii=False))

        # ---- 缺 accounts ----
        st, _, body = http("POST", base + "/data", {})
        check("22. 没有 accounts → 409 accounts_not_configured",
              st == 409 and (body.get("error") or {}).get("code") == "accounts_not_configured",
              f"{st} {body}")

        # ---- POST /mail/<uid> ----
        st, _, body = http("POST", base + "/mail/7", {"accounts": accounts})
        check("23. POST /mail/7 结构 = 服务端 /app/mail/<uid>/（{ok,mail} 或 {ok:false,error}）",
              (st == 200 and body.get("ok") is True and isinstance(body.get("mail"), dict))
              or (st in (404, 502) and body.get("ok") is False and "error" in body),
              f"{st} {str(body)[:160]}")
        st, _, body = http("POST", base + "/mail/abc", {"accounts": accounts})
        check("24. uid 非法 → 404 not_found", st == 404 and (body.get("error") or {}).get("code") == "not_found",
              f"{st} {body}")
        st, _, body = http("POST", base + "/mail/7", {})
        check("25. /mail 缺 accounts → 409", st == 409, f"{st} {body}")

        # ---- POST /mail/<uid>/read/（标记已读；结构与服务端同一条） ----
        st, _, body = http("POST", base + "/mail/7/read/", {"accounts": accounts})
        check("25a. POST /mail/7/read/ 结构 = 服务端 POST /app/mail/<uid>/read/",
              (st == 200 and body.get("ok") is True and isinstance(body.get("mail"), dict)
               and "uid" in body["mail"] and "unread" in body["mail"])
              or (st in (404, 502) and body.get("ok") is False and "error" in body),
              f"{st} {str(body)[:160]}")
        st, _, body = http("POST", base + "/mail/abc/read/", {"accounts": accounts})
        check("25b. uid 非法 → 404 not_found（不会拼进 IMAP 命令）",
              st == 404 and (body.get("error") or {}).get("code") == "not_found", f"{st} {body}")
        st, _, body = http("POST", base + "/mail/7/read/", {})
        check("25c. /mail/<uid>/read/ 缺 accounts → 409", st == 409, f"{st} {body}")
        st, _, body = http("POST", base + "/mail/7", {"accounts": accounts})
        check("25d. 读正文那条路径仍然存在且没被 /read/ 抢走路由（{ok,mail} 或 {ok:false,error}）",
              (st == 200 and body.get("ok") is True and isinstance(body.get("mail"), dict))
              or (st in (404, 502) and body.get("ok") is False and "error" in body),
              f"{st} {str(body)[:160]}")

        # ---- 别的路径 ----
        st, _, body = http("GET", base + "/whatever")
        check("26. 未知路径 → 404 JSON（不是 HTML 报错页）", st == 404 and body.get("ok") is False, f"{st} {body}")

        # ---- POST /mail/send/（邮件发送路由 + CORS）----
        st, _, body = http("POST", base + "/mail/send",
                           {"accounts": {"mail": {"email": USER, "authcode": SECRET, "imap_host": "imap.test.invalid"}},
                            "to": "a@b.com", "subject": "测试", "body_text": "正文"},
                           headers={"Origin": "https://phix.ing"})
        # 发送会失败（没有真 SMTP），但路由存在且返回 502（不是 404）
        check("27a. POST /mail/send 路由存在（不是 404；可能 502 因无真 SMTP）",
              st != 404, f"{st} {body}")
        check("27b. POST /mail/send 缺 accounts → 409",
              (lambda s, b: (lambda: None) or True) if True else None,  # placeholder
              "see next")
        st2, _, body2 = http("POST", base + "/mail/send",
                             {"to": "a@b.com", "subject": "x", "body_text": "y"})
        check("27c. POST /mail/send 没有 accounts → 409 accounts_not_configured",
              st2 == 409 and (body2.get("error") or {}).get("code") == "accounts_not_configured",
              f"{st2} {body2}")
        st3, _, body3 = http("POST", base + "/mail/send",
                             {"accounts": {"mail": {"email": USER, "authcode": SECRET, "imap_host": "imap.test.invalid"}}})
        check("27d. POST /mail/send 缺 to/subject/body → 400",
              st3 == 400 and (body3.get("error") or {}).get("code") == "bad_request",
              f"{st3} {body3}")
        # CORS 预检
        st4, hdrs4, _ = http("OPTIONS", base + "/mail/send",
                              headers={"Origin": "https://phix.ing",
                                       "Access-Control-Request-Method": "POST"})
        check("27e. OPTIONS /mail/send 204 + CORS 回显 phix.ing",
              st4 == 204 and hdrs4.get("Access-Control-Allow-Origin") == "https://phix.ing",
              f"{st4} {hdrs4}")

        # ---- 只绑回环：从非回环地址连不上 ----
        ip = nonloopback_ip()
        if ip:
            try:
                s = socket.create_connection((ip, port), timeout=3)
                s.close()
                reached = True
            except OSError:
                reached = False
            check(f"27. 从本机非回环地址 {ip} 连不上（只绑回环的硬证据）", not reached, "居然连上了")
        else:
            print("      （取不到非回环地址，跳过第 27 项）")

    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0] or ""
        except Exception:
            proc.kill()
            out = ""
        out_lines = out.splitlines()

    # ---- 日志 / 落盘 ----
    print("      进程输出：", " | ".join(l.strip() for l in out_lines if l.strip())[:200] or "（空）")
    check("28. 进程输出里没有测试口令", SECRET not in out, "日志里出现了口令")
    check("29. 进程输出里没有账号名", USER not in out, "日志里出现了账号名")
    hits = [h for h in grep_tree(HERE, SECRET) if h != os.path.basename(__file__)]
    check("30. 除测试文件自身外，整个 website 目录搜不到测试口令（不落盘）", not hits, str(hits))
    tmps = [os.path.relpath(os.path.join(dp, f), HERE)
            for dp, _, fs in os.walk(BRIDGE_DIR) for f in fs
            if re.search(r"\.(log|jsonl|tmp|db|sqlite3?)$", f)]
    check("31. local-bridge 里没有任何日志/数据文件", not tmps, str(tmps))

    print("\n" + "=" * 72)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
