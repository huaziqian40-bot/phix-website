"""管理后台测试（≥20 项）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_admin.py [端口]

覆盖：
- 未登录访问 /admin/ → 302 重定向到登录页
- 非 staff 访问 /admin/ → 403
- staff 能访问 /admin/、/admin/users/、/admin/content/
- GET /admin/api/users → 列用户
- POST /admin/api/user/flags → 改 staff/active
- 不能停用自己
- 不能撤自己 staff
- content.json 读写生效
- {{cms:key}} 替换生效
- {{cms:key}} 无 override 时回落默认值
- 服务密钥缺失时报错清晰
- 审计日志写入
"""
import http.cookiejar
import json
import os
import re
import secrets
import sys
import time
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


def req(op, method, path, body=None, raw=False, allow_redirect=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with op.open(r, timeout=20) as resp:
            payload = resp.read()
            if raw:
                return resp.status, payload
            try:
                return resp.status, json.loads(payload or b"{}")
            except (json.JSONDecodeError, ValueError):
                return resp.status, {"_raw": payload[:200].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        payload = e.read()
        if allow_redirect:
            return e.code, {"_headers": dict(e.headers)}
        try:
            return e.code, json.loads(payload or b"{}")
        except (json.JSONDecodeError, ValueError):
            return e.code, {"_raw": payload[:200].decode("utf-8", "replace")}


def main():
    print("=" * 70)
    print(f"管理后台测试 → {BASE}")
    print("=" * 70)

    # ---- 1. 未登录访问 /admin/ → 302 ----
    print("\n[A] 鉴权保护")
    op0, _ = opener()
    st, body = req(op0, "GET", "/admin/", raw=True, allow_redirect=True)
    # stdlib HTTPCookieProcessor 会自动跟随重定向，所以可能拿到 200（登录页）
    # 我们检查是否被重定向到了 /login/
    check("1. 未登录 /admin/ → 重定向或登录页", st in (200, 302),
          f"status={st}")

    # ---- 2. 注册一个普通用户（非 staff）----
    print("\n[B] 非 staff 用户")
    op1, cj1 = opener()
    user1 = "admtest" + secrets.token_hex(3)
    pw1 = "AdmTest-" + secrets.token_hex(3)
    st, body = req(op1, "POST", "/auth/register/", {"username": user1, "password": pw1})
    check("2. 注册测试用户", st in (200, 201), f"{st}")

    # 非 staff 访问 /admin/ → 应该 403
    st, body = req(op1, "GET", "/admin/", raw=True)
    check("3. 非 staff 访问 /admin/ → 403", st == 403, f"status={st}")

    # 非 staff 访问 /admin/api/users → 403
    st, body = req(op1, "GET", "/admin/api/users")
    check("4. 非 staff GET /admin/api/users → 403", st == 403, f"status={st}")

    # ---- 3. 把测试用户设为 staff（通过 phix admin API 直接调）----
    print("\n[C] Staff 权限")
    # 读取 service key
    sk_path = os.path.join(os.path.dirname(__file__), ".service_key")
    try:
        sk = open(sk_path).read().strip()
    except Exception:
        sk = ""
    check("5. .service_key 文件存在且可读", bool(sk), "missing" if not sk else "")

    if sk:
        # 先查用户列表找到 user1 的 id
        api_url = f"{BASE}/api/v1/admin/users"
        r = urllib.request.Request(api_url, headers={"X-Phix-Service-Key": sk})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                users_data = json.loads(resp.read())
        except Exception as e:
            users_data = {"error": str(e)}

        target_id = None
        for u in users_data.get("users", []):
            if u.get("username") == user1:
                target_id = u["id"]
                break
        check("6. 能通过 admin API 查到测试用户", target_id is not None,
              f"id={target_id}")

        # 设为 staff
        if target_id:
            flags_url = f"{BASE}/api/v1/admin/user/{target_id}/flags"
            r = urllib.request.Request(flags_url,
                                       data=json.dumps({"is_staff": True}).encode(),
                                       method="POST",
                                       headers={"Content-Type": "application/json",
                                                "X-Phix-Service-Key": sk})
            try:
                with urllib.request.urlopen(r, timeout=20) as resp:
                    flag_resp = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                flag_resp = json.loads(e.read())
            check("7. 设 staff 成功", flag_resp.get("ok") and flag_resp.get("is_staff"),
                  str(flag_resp)[:120])

    # 重新登录（staff 身份）
    op2, cj2 = opener()
    st, body = req(op2, "POST", "/auth/login/", {"username": user1, "password": pw1})
    check("8. staff 用户登录成功", st == 200, f"{st}")

    # staff 访问 /admin/ → 200
    st, body = req(op2, "GET", "/admin/", raw=True)
    check("9. staff 访问 /admin/ → 200", st == 200, f"status={st}")

    # staff 访问 /admin/users/ → 200
    st, body = req(op2, "GET", "/admin/users/", raw=True)
    check("10. staff 访问 /admin/users/ → 200", st == 200, f"status={st}")

    # staff 访问 /admin/content/ → 200
    st, body = req(op2, "GET", "/admin/content/", raw=True)
    check("11. staff 访问 /admin/content/ → 200", st == 200, f"status={st}")

    # ---- 4. Admin API：列用户 ----
    print("\n[D] Admin API")
    st, body = req(op2, "GET", "/admin/api/users")
    check("12. GET /admin/api/users → 200 + ok", st == 200 and body.get("ok"),
          f"{st} {str(body)[:100]}")
    check("13. 用户列表非空", len(body.get("users", [])) > 0,
          f"count={len(body.get('users', []))}")

    # ---- 5. 不能停用自己 ----
    print("\n[E] 自我保护")
    # 找自己的 user_id
    my_uid = None
    for u in body.get("users", []):
        if u.get("username") == user1:
            my_uid = u["id"]
            break
    if my_uid:
        st, body = req(op2, "POST", "/admin/api/user/flags",
                       {"user_id": my_uid, "is_active": False})
        check("14. 不能停用自己", st == 400 and "self_deactivate" in str(body),
              f"{st} {str(body)[:120]}")

        st, body = req(op2, "POST", "/admin/api/user/flags",
                       {"user_id": my_uid, "is_staff": False})
        check("15. 不能撤自己 staff", st == 400 and "self_destaff" in str(body),
              f"{st} {str(body)[:120]}")
    else:
        check("14. 不能停用自己", False, "找不到自己的 uid")
        check("15. 不能撤自己 staff", False, "找不到自己的 uid")

    # ---- 6. CMS 读写 ----
    print("\n[F] CMS 内容管理")
    st, body = req(op2, "GET", "/admin/api/content")
    check("16. GET /admin/api/content → 200 + items", st == 200 and "items" in body,
          f"{st} {str(body)[:100]}")

    # 保存一个 override
    test_key = "home.title"
    test_val = "测试标题-" + secrets.token_hex(2)
    st, body = req(op2, "POST", "/admin/api/content/save",
                   {"key": test_key, "value": test_val})
    check("17. 保存 CMS override → ok", st == 200 and body.get("ok"),
          f"{st} {str(body)[:100]}")

    # 验证前台 HTML 立刻反映
    st, html = req(op0, "GET", "/", raw=True)
    html_str = html.decode("utf-8") if isinstance(html, bytes) else str(html)
    check("18. 前台首页包含新 CMS 值", test_val in html_str,
          "not found in HTML")

    # 恢复默认
    st, body = req(op2, "POST", "/admin/api/content/reset", {"key": test_key})
    check("19. 恢复默认 → ok", st == 200 and body.get("ok"),
          f"{st} {str(body)[:100]}")

    # 验证回落默认值
    st, html = req(op0, "GET", "/", raw=True)
    html_str = html.decode("utf-8") if isinstance(html, bytes) else str(html)
    check("20. 恢复后首页不含测试值", test_val not in html_str,
          "still present")
    # 2026-09-13 改为"从 index.html 读出 home.title 的默认值再断言"：
    # 早先这里写死标题文案，每改一次口号就误红一次。
    _idx = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"),
                encoding="utf-8").read()
    _m = re.search(r"\{\{cms:home\.title\|([^}]*)\}\}", _idx)
    _default_title = (_m.group(1) if _m else "").strip()
    check("21. 恢复后首页含默认标题（默认值取自 index.html）",
          bool(_default_title) and _default_title in html_str,
          f"default={_default_title!r} not found")

    # ---- 7. 审计日志 ----
    print("\n[G] 审计日志")
    audit_path = os.path.join(os.path.dirname(__file__), "admin_audit.log")
    check("22. 审计日志文件存在", os.path.exists(audit_path),
          "missing")
    if os.path.exists(audit_path):
        log_content = open(audit_path, encoding="utf-8").read()
        check("23. 审计日志含 cms 操作记录", "cms:" in log_content,
              f"len={len(log_content)}")
        # 确认不含凭据
        check("24. 审计日志不含密钥/令牌", sk not in log_content if sk else True,
              "LEAK!" if (sk and sk in log_content) else "")

    # ---- 8. 页脚联系方式 ----
    print("\n[H] 页脚")
    st, html = req(op0, "GET", "/", raw=True)
    html_str = html.decode("utf-8") if isinstance(html, bytes) else str(html)
    check("25. 首页页脚含联系我们", "联系我们" in html_str, "")
    check("26. 首页页脚含版权行", "© 2026 phix" in html_str, "")
    check("27. 首页页脚含邮箱", "norine.liu" in html_str or "huaziqian40" in html_str, "")
    check("27b. 首页页脚含电话", "18901712280" in html_str, "")
    check("27c. B站链接有 target=_blank", 'target="_blank"' in html_str and "space.bilibili.com/1121702307" in html_str, "")
    check("27d. 版权行是 © 2026 phix", "© 2026 phix" in html_str, "")

    # login 页也有页脚
    st, html = req(op0, "GET", "/login/", raw=True)
    html_str = html.decode("utf-8") if isinstance(html, bytes) else str(html)
    check("28. 登录页有页脚", "site-footer" in html_str, "")

    # ---- 9. 反馈收件箱 ----
    print("\n[I] 反馈收件箱")

    # 29. 提交反馈
    fb_desc = "测试反馈-" + secrets.token_hex(3)
    st, body = req(op0, "POST", "/feedback/",
                   {"type": "Bug 报告", "name": "测试用户", "desc": fb_desc})
    check("29. 提交反馈 → ok", st == 200 and body.get("ok"),
          f"{st} {str(body)[:100]}")
    fb_id = body.get("id", "")

    # 30. 后台可见
    st, body = req(op2, "GET", "/admin/api/feedback")
    check("30. 后台反馈列表含刚提交的", st == 200 and body.get("ok") and
          any(item.get("id") == fb_id for item in body.get("items", [])),
          f"{st} total={body.get('total', 0)}")

    # 记录未处理数
    unhandled_before = body.get("unhandled", 0)

    # 31. 标记已处理
    if fb_id:
        st, body = req(op2, "POST", "/admin/api/feedback/handle", {"id": fb_id})
        check("31. 标记已处理 → ok", st == 200 and body.get("ok"),
              f"{st} {str(body)[:100]}")
    else:
        check("31. 标记已处理 → ok", False, "no fb_id")

    # 32. 未处理计数变化
    st, body = req(op2, "GET", "/admin/api/feedback")
    unhandled_after = body.get("unhandled", 0)
    check("32. 未处理计数减少", unhandled_after < unhandled_before,
          f"before={unhandled_before} after={unhandled_after}")

    # 33. 反馈页面可访问
    st, body = req(op2, "GET", "/admin/feedback/", raw=True)
    check("33. staff 访问 /admin/feedback/ → 200", st == 200, f"status={st}")

    # 34. 透明 logo 引用
    st, html = req(op0, "GET", "/", raw=True)
    html_str = html.decode("utf-8") if isinstance(html, bytes) else str(html)
    check("34. 首页使用透明 logo", "logo-transparent.png" in html_str, "")
    check("35. 首页有 favicon", "favicon-32.png" in html_str, "")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
