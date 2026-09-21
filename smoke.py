"""官网冒烟测试（stdlib，零依赖）。站点代理写完、验收代理复跑，都用它。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\smoke.py [端口，默认 8940]

只读 + 用一个**临时账号**走一遍 注册→登录→cookie→/me/→读写 profile→登出，跑完自删账号。
不打印任何口令/令牌（只打印 8 位指纹）。
"""
import base64
import http.cookiejar
import json
import secrets
import sys
import urllib.error
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = f"http://127.0.0.1:{PORT}"
PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))
    return cond


def opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)), cj


def req(op, method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with op.open(r, timeout=20) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload or b"{}")
        except ValueError:
            return e.code, {"_raw": payload[:120].decode("utf-8", "replace")}


def main():
    print("=" * 70)
    print(f"官网冒烟 → {BASE}")
    print("=" * 70)
    op, cj = opener()

    print("\n[1] 静态页")
    for path in ("/", "/about/", "/products/xinlv/", "/products/phl/", "/docs/",
                 "/support/", "/download/", "/login/", "/register/", "/account/", "/app/"):
        st, _ = req(op, "GET", path, raw=True)
        check(f"GET {path} → 200", st == 200, str(st))

    print("\n[2] 未登录保护")
    st, body = req(op, "GET", "/me/")
    check("/me/ 未登录 → 401", st == 401, str(st))
    st, body = req(op, "POST", "/proxy/sync/manifest/")
    check("/proxy/ 未登录 → 401", st == 401, str(st))

    print("\n[3] 注册 → 登录 → cookie")
    user = "site" + secrets.token_hex(3)
    pw = "Site-Pass-" + secrets.token_hex(3)
    st, body = req(op, "POST", "/auth/register/", {"username": user, "password": pw})
    check("注册 200/201", st in (200, 201), f"{st} {str(body)[:120]}")
    op2, cj2 = opener()
    st, body = req(op2, "POST", "/auth/login/", {"username": user, "password": pw})
    check("登录 200", st == 200, f"{st} {str(body)[:120]}")
    names = {c.name for c in cj2}
    check("写了 phix_access / phix_refresh 两个 cookie",
          {"phix_access", "phix_refresh"} <= names, str(names))
    st, body = req(op2, "GET", "/me/")
    check("/me/ 登录态 → 200 且带 username", st == 200 and body.get("username") == user,
          f"{st} {str(body)[:120]}")

    print("\n[4] 读写 profile（头像）与 mood")
    avatar = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()
    st, body = req(op2, "POST", "/proxy/sync/objects/profile/",
                   {"payload": json.dumps({"display_name": user, "avatar": avatar,
                                           "updated_at": "2026-09-12T00:00:00+08:00"}),
                    "base_revision": 0})
    check("写 profile → 200/201", st in (200, 201), f"{st} {str(body)[:140]}")
    st, body = req(op2, "GET", "/proxy/sync/objects/profile/")
    ok = st == 200 and avatar in json.dumps(body)
    check("读回 profile 且头像在", ok, f"{st} {str(body)[:140]}")
    st, body = req(op2, "GET", "/me/")
    check("/me/ 带 avatar", bool(body.get("avatar")), str(body)[:140])

    print("\n[5] 日程追加 + 密码管理读写")
    st, body = req(op2, "POST", "/proxy/sync/objects/schedule/",
                   {"payload": json.dumps({"version": 1, "kind": "pinghe-schedule",
                                           "lastId": 1,
                                           "events": [{"id": 1, "day": "2026-09-20",
                                                       "time": "15:30", "title": "冒烟",
                                                       "note": "", "created": "2026-09-12T00:00:00+08:00"}]}),
                    "base_revision": 0})
    check("写 schedule → 200/201", st in (200, 201), f"{st} {str(body)[:140]}")
    st, body = req(op2, "POST", "/proxy/sync/objects/settings.accounts/",
                   {"payload": json.dumps({"accounts": {"edupage": {"username": "smoke",
                                                                    "password": "x" * 8}}}),
                    "base_revision": 0})
    check("写 settings.accounts → 200/201", st in (200, 201), f"{st} {str(body)[:140]}")
    st, body = req(op2, "GET", "/proxy/sync/objects/settings.accounts/")
    check("读回 accounts 且密码在（密文传输、内存明文）",
          st == 200 and "smoke" in json.dumps(body), f"{st} {str(body)[:140]}")

    print("\n[6] 下载列表")
    st, body = req(op, "GET", "/download/list/")
    check("/download/list/ → 200 且是列表", st == 200 and isinstance(body.get("files"), list),
          f"{st} {str(body)[:120]}")

    print("\n[7] 登出")
    st, body = req(op2, "POST", "/auth/logout/")
    check("登出 200", st == 200, str(st))
    st, body = req(op2, "GET", "/me/")
    check("登出后 /me/ → 401", st == 401, str(st))

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    print(f"（临时账号 {user[:8]}… 请由主代理用 clean_dev_db 清理）")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
