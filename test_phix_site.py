"""官网四项改造的专项测试（≥10 项）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_phix_site.py [端口]

覆盖：
- 静态资源版本化：HTML 里带 ?v=，版本随内容变化；Cache-Control 头正确
- /me/ 返回 is_staff（非 staff false / staff true）
- site.js 里 staff 才渲染「管理后台」按钮
- /account/ 只留「个人信息 / 密码管理」两块，不含 mood/schedule
- 三项平台账号读改写往返，且不动其它 section
- 改 phix 密码：不一致被拒 / 当前密码错被拒且无改动 / 成功后可新密码登录、旧密码失败
"""
import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8940"
BASE = f"http://127.0.0.1:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
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
        with op.open(r, timeout=30) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload or b"{}")
        except (json.JSONDecodeError, ValueError):
            return e.code, {"_raw": payload[:200].decode("utf-8", "replace")}


def req_headers(op, method, path, body=None):
    """返回 (status, headers_dict, raw_bytes)。"""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with op.open(r, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def sha8(rel):
    p = os.path.join(HERE, rel)
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:8]


def main():
    print("=" * 70)
    print(f"官网四项改造测试 → {BASE}")
    print("=" * 70)

    # ---- [A] 静态资源版本化 ----
    print("\n[A] 静态资源版本化")
    op0, _ = opener()
    st, hdrs, html = req_headers(op0, "GET", "/")
    html_str = html.decode("utf-8", "replace")
    check("A1. 首页 site.js 带 ?v=", f"/static/site.js?v={sha8('static/site.js')}" in html_str, "")
    check("A2. 首页 site.css 带 ?v=", f"/static/site.css?v={sha8('static/site.css')}" in html_str, "")
    check("A3. 版本号 = sha256[:8]（site.js）", f"site.js?v={sha8('static/site.js')}" in html_str, "")
    check("A4. HTML 响应 Cache-Control: no-cache", hdrs.get("Cache-Control") == "no-cache", str(hdrs.get("Cache-Control")))

    st2, hdrs2, html2 = req_headers(op0, "GET", "/app/")
    html2_str = html2.decode("utf-8", "replace")
    check("A5. /app/ 里 app.css 带 ?v=", f"/static/app/app.css?v={sha8('static/app/app.css')}" in html2_str, "")
    check("A6. /app/ 里 app.js 带 ?v=", f"/static/app/app.js?v={sha8('static/app/app.js')}" in html2_str, "")

    st3, hdrs3, _ = req_headers(op0, "GET", f"/static/site.css?v={sha8('static/site.css')}")
    check("A7. 带 ?v= 的静态资源 immutable 缓存头",
          hdrs3.get("Cache-Control") == "public, max-age=31536000, immutable",
          str(hdrs3.get("Cache-Control")))

    # 版本随内容变化：临时改 site.css，版本号应变，最后恢复
    css_path = os.path.join(HERE, "static", "site.css")
    orig_css = open(css_path, "rb").read()
    old_sha = hashlib.sha256(orig_css).hexdigest()[:8]
    try:
        with open(css_path, "ab") as f:
            f.write(b"\n/* version-bump-test */\n")
        new_sha = sha8("static/site.css")
        st4, _, html4 = req_headers(op0, "GET", "/")
        html4_str = html4.decode("utf-8", "replace")
        check("A8. 内容变了版本号自动变",
              new_sha != old_sha and f"/static/site.css?v={new_sha}" in html4_str,
              f"{old_sha} -> {new_sha}")
    finally:
        with open(css_path, "wb") as f:
            f.write(orig_css)

    # ---- [B] /me/ 含 is_staff ----
    print("\n[B] /me/ 含 is_staff")
    op1, _ = opener()
    user1 = "psite" + secrets.token_hex(3)
    pw1 = "Site-Pass-" + secrets.token_hex(3)
    st, body = req(op1, "POST", "/auth/register/", {"username": user1, "password": pw1})
    check("B1. 注册测试用户", st in (200, 201), f"{st}")
    st, body = req(op1, "GET", "/me/")
    check("B2. 非 staff /me/ 返回 is_staff=False",
          st == 200 and body.get("is_staff") is False and "is_superuser" in body,
          f"{st} {str(body)[:120]}")

    # 设成 staff 后 /me/ 应返回 true
    sk = ""
    try:
        sk = open(os.path.join(HERE, ".service_key")).read().strip()
    except Exception:
        pass
    if sk:
        try:
            r = urllib.request.Request(f"{BASE}/api/v1/admin/users",
                                       headers={"X-Phix-Service-Key": sk})
            with urllib.request.urlopen(r, timeout=20) as resp:
                users_data = json.loads(resp.read())
            uid = None
            for u in users_data.get("users", []):
                if u.get("username") == user1:
                    uid = u["id"]
                    break
            if uid:
                r = urllib.request.Request(f"{BASE}/api/v1/admin/user/{uid}/flags",
                                           data=json.dumps({"is_staff": True}).encode(),
                                           method="POST",
                                           headers={"Content-Type": "application/json",
                                                    "X-Phix-Service-Key": sk})
                with urllib.request.urlopen(r, timeout=20) as resp:
                    flag_resp = json.loads(resp.read())
                op1b, _ = opener()
                st, body = req(op1b, "POST", "/auth/login/", {"username": user1, "password": pw1})
                st, body = req(op1b, "GET", "/me/")
                check("B3. staff /me/ 返回 is_staff=True",
                      st == 200 and body.get("is_staff") is True,
                      f"{st} {str(body)[:120]}")
            else:
                check("B3. staff /me/ 返回 is_staff=True", False, "找不到 uid")
        except Exception as e:
            check("B3. staff /me/ 返回 is_staff=True", False, str(e)[:120])
    else:
        check("B3. staff /me/ 返回 is_staff=True", False, "无 service_key")

    # ---- [C] 顶栏「管理后台」按钮（仅 staff）----
    print("\n[C] 顶栏「管理后台」按钮")
    st, js = req(op0, "GET", "/static/site.js", raw=True)
    js_str = js.decode("utf-8", "replace") if isinstance(js, bytes) else str(js)
    check("C1. site.js 含「管理后台」按钮逻辑", "管理后台" in js_str, "")
    check("C2. 按钮仅 staff 可见（u.is_staff 门控）", "u.is_staff" in js_str and "admin-btn" in js_str, "")
    check("C3. 按钮指向 /admin/", "href = '/admin/'" in js_str or "href='/admin/'" in js_str or "'/admin/'" in js_str, "")

    # ---- [D] /account/ 只有两块 tab ----
    print("\n[D] /account/ 只有两块 tab")
    st, acc = req(op0, "GET", "/account/", raw=True)
    acc_str = acc.decode("utf-8", "replace") if isinstance(acc, bytes) else str(acc)
    check("D1. 不含 mood tab", 'data-tab="mood"' not in acc_str, "")
    check("D2. 不含 schedule tab", 'data-tab="schedule"' not in acc_str, "")
    check("D3. 保留 个人信息 tab", 'data-tab="profile"' in acc_str, "")
    check("D4. 保留 密码管理 tab", 'data-tab="credentials"' in acc_str, "")
    check("D5. 含 phix 账号密码表单（只可改）", 'id="pwd-current"' in acc_str and 'id="pwd-confirm"' in acc_str, "")
    check("D6. 个人中心含「改密码请用下面的密码管理」", "改密码请用下面的" in acc_str, "")

    # ---- [E] 三项账号读改写往返 ----
    print("\n[E] 三项账号读改写往返")
    op2, _ = opener()
    user2 = "psite" + secrets.token_hex(3)
    pw2 = "Site-Pass-" + secrets.token_hex(3)
    st, body = req(op2, "POST", "/auth/register/", {"username": user2, "password": pw2})
    check("E1. 注册测试用户", st in (200, 201), f"{st}")
    accounts = {
        "mail": {"email": "me@example.com", "password": "mailpw1"},
        "managebac": {"base_url": "https://shph.managebac.cn", "email": "mb@example.com", "password": "mbpw1"},
        "edupage": {"username": "eduuser", "subdomain": "pingheschool", "password": "edupw1"},
        "xinlv": {"username": "xinlvuser", "token": "tok123"},
    }
    st, body = req(op2, "POST", "/proxy/sync/objects/settings.accounts/",
                   {"payload": json.dumps({"accounts": accounts}), "base_revision": 0})
    check("E2. 写入四段 accounts", st in (200, 201), f"{st} {str(body)[:120]}")

    st, body = req(op2, "GET", "/proxy/sync/objects/settings.accounts/")
    read_back = json.loads(body.get("payload", "{}") or "{}")
    rb = read_back.get("accounts", {})
    ok_roundtrip = (rb.get("mail", {}).get("password") == "mailpw1"
                    and rb.get("managebac", {}).get("password") == "mbpw1"
                    and rb.get("edupage", {}).get("password") == "edupw1"
                    and rb.get("xinlv", {}).get("token") == "tok123")
    check("E3. 读回四段完整（密文传输、内存明文）", st == 200 and ok_roundtrip,
          f"{st} {str(rb)[:140]}")

    # 只改 managebac，写回，其它 section 不动
    accounts["managebac"]["password"] = "mbpw2"
    st, body = req(op2, "POST", "/proxy/sync/objects/settings.accounts/",
                   {"payload": json.dumps({"accounts": accounts}), "base_revision": 1})
    check("E4. 只改 managebac 写回", st in (200, 201), f"{st} {str(body)[:120]}")
    st, body = req(op2, "GET", "/proxy/sync/objects/settings.accounts/")
    rb2 = json.loads(body.get("payload", "{}") or "{}").get("accounts", {})
    check("E5. managebac 已更新", rb2.get("managebac", {}).get("password") == "mbpw2", str(rb2.get("managebac"))[:120])
    check("E6. mail 未动", rb2.get("mail", {}).get("password") == "mailpw1", "")
    check("E7. edupage 未动", rb2.get("edupage", {}).get("password") == "edupw1", "")
    check("E8. xinlv（其它 section）未动", rb2.get("xinlv", {}).get("token") == "tok123", "")

    # ---- [F] 改 phix 密码 ----
    print("\n[F] 改 phix 密码")
    user3 = "psite" + secrets.token_hex(3)
    pw3 = "Site-Pass-" + secrets.token_hex(3)
    st, body = req(op2, "POST", "/auth/register/", {"username": user3, "password": pw3})
    check("F1. 注册测试用户", st in (200, 201), f"{st}")
    op3, _ = opener()
    st, body = req(op3, "POST", "/auth/login/", {"username": user3, "password": pw3})
    check("F2. 登录成功", st == 200, f"{st}")

    newpw = "New-Pass-" + secrets.token_hex(3)
    # 两次新密码不一致 → 被拒
    st, body = req(op3, "POST", "/auth/password/",
                   {"current_password": pw3, "new_password": newpw, "confirm_password": newpw + "x"})
    check("F3. 两次新密码不一致被拒(400)", st == 400 and "不一致" in str(body.get("error", {}).get("message", "")),
          f"{st} {str(body)[:120]}")

    # 当前密码错 → 被拒且无数据改动（原密码仍能登录）
    st, body = req(op3, "POST", "/auth/password/",
                   {"current_password": "Wrong-" + secrets.token_hex(3),
                    "new_password": newpw, "confirm_password": newpw})
    check("F4. 当前密码错被拒(401)", st == 401, f"{st} {str(body)[:120]}")
    op_chk, _ = opener()
    st, body = req(op_chk, "POST", "/auth/login/", {"username": user3, "password": pw3})
    check("F5. 当前密码错后无数据改动（原密码仍能登录）", st == 200, f"{st}")

    # 成功改密码
    st, body = req(op3, "POST", "/auth/password/",
                   {"current_password": pw3, "new_password": newpw, "confirm_password": newpw})
    check("F6. 改密码成功", st == 200 and body.get("ok"), f"{st} {str(body)[:120]}")

    op_old, _ = opener()
    st, body = req(op_old, "POST", "/auth/login/", {"username": user3, "password": pw3})
    check("F7. 旧密码登录失败(401)", st == 401, f"{st}")
    op_new, _ = opener()
    st, body = req(op_new, "POST", "/auth/login/", {"username": user3, "password": newpw})
    check("F8. 新密码登录成功(200)", st == 200, f"{st}")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    print(f"（临时账号 {user1[:8]}…/ {user2[:8]}…/ {user3[:8]}… 请由主代理用 clean_dev_db 清理）")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
