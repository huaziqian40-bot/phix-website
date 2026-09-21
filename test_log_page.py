"""PHIX 日志页专项测试（≥16 项断言）。

    D:\\phix\\server\\.venv\\Scripts\\python.exe -X utf8 D:\\phix\\website\\test_log_page.py [端口]

覆盖：
- /log/ 与 /log.html 均 200；表格五列表头（日期/受理人/受理物/Before/After）；表单字段齐全
- GET /logs/ 匿名可读且 ok:true、logs 为数组
- POST /logs/ 匿名 401；登录后新增 → GET /logs/ 能查到且字段一致
- 图片上传：返回 URL 可取回、Content-Type image/jpeg、字节数与写入文件一致
- 超大图片被拒（4xx）；坏日期被拒（4xx）
- DELETE 匿名 401 / 非 staff 403 / staff 200 且记录消失、图片文件也被删
- logs.json 在 deploy 排除清单里
- 页面与 JS 无敏感串
"""
import base64
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


def get_headers(path):
    r = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main():
    print("=" * 70)
    print(f"PHIX 日志页测试 → {BASE}")
    print("=" * 70)

    # ---- [A] 页面结构 ----
    print("\n[A] 页面结构")
    st, hdrs, html = get_headers("/log/")
    html_str = html.decode("utf-8", "replace")
    check("A1. GET /log/ → 200", st == 200, f"{st}")
    st2, _, html2 = get_headers("/log.html")
    check("A2. GET /log.html → 200", st2 == 200, f"{st2}")

    for h in ("日期", "受理人", "受理物", "Before", "After"):
        check(f"A3. 表格表头含「{h}」", f"<th>{h}</th>" in html_str, h)
    check("A4. 标题「PHIX 日志」", "PHIX 日志" in html_str, "")
    check("A5. 表单字段齐全",
          'id="log-date"' in html_str and 'id="log-handler"' in html_str
          and 'id="log-item"' in html_str and 'id="log-before"' in html_str
          and 'id="log-after"' in html_str, "")
    check("A6. 含 Before/After 图片上传输入（accept=image/*）",
          html_str.count('accept="image/*"') >= 2, "")
    check("A7. 引用 log.css", "/static/log.css" in html_str, "")
    check("A8. title 含校名", "上海民办平和学校" in html_str, "")

    # ---- [B] 敏感串检查 ----
    print("\n[B] 敏感串检查")
    sensitive = ["192.168", "127.0.0.1", ".service_key", "/home/phix",
                 "phix_access", "phix_refresh", "000000"]
    leaked = [s for s in sensitive if s in html_str]
    check("B1. 页面 HTML 无敏感串", not leaked, f"命中:{leaked}")
    for extra in ("/static/site.js", "/static/log.css"):
        s3, _, b3 = get_headers(extra)
        txt = b3.decode("utf-8", "replace")
        l2 = [s for s in sensitive if s in txt]
        check(f"B2. {extra} 无敏感串", s3 == 200 and not l2, f"命中:{l2}")

    # ---- [C] GET /logs/ 匿名可读 ----
    print("\n[C] GET /logs/ 匿名可读")
    op0, _ = opener()
    st, body = req(op0, "GET", "/logs/")
    check("C1. GET /logs/ → 200 且 ok:true", st == 200 and body.get("ok") is True, f"{st}")
    check("C2. logs 是数组", isinstance(body.get("logs"), list), f"{type(body.get('logs'))}")

    # ---- [D] POST /logs/ 匿名 401 ----
    print("\n[D] 匿名写保护")
    st, body = req(op0, "POST", "/logs/", {"date": "2026-01-01", "handler": "x", "item": "y"})
    check("D1. POST /logs/ 匿名 → 401", st == 401, f"{st}")

    # ---- [E] 登录后新增 + 读回 ----
    print("\n[E] 登录后新增")
    op1, _ = opener()
    user = "logtest" + secrets.token_hex(3)
    pw = "Log-Pass-" + secrets.token_hex(3)
    st, body = req(op1, "POST", "/auth/register/", {"username": user, "password": pw})
    check("E1. 注册测试用户", st in (200, 201), f"{st}")
    op2, _ = opener()
    st, body = req(op2, "POST", "/auth/login/", {"username": user, "password": pw})
    check("E2. 登录成功", st == 200, f"{st}")

    text_date = "2026-08-15"
    text_handler = "测试同学"
    text_item = "同学的笔记本（换屏）"
    st, body = req(op2, "POST", "/logs/", {"date": text_date, "handler": text_handler,
                                            "item": text_item, "before": None, "after": None})
    check("E3. POST /logs/（纯文字）→ 200", st == 200 and body.get("ok"), f"{st} {str(body)[:120]}")
    text_id = body.get("id", "")
    check("E4. 返回不可猜 id", bool(text_id) and len(text_id) >= 16, f"id={text_id}")

    st, body = req(op0, "GET", "/logs/")
    logs = body.get("logs", [])
    found = next((r for r in logs if r.get("id") == text_id), None)
    check("E5. GET /logs/ 能查到新记录", found is not None, "")
    ok_fields = found is not None and found.get("date") == text_date \
        and found.get("handler") == text_handler and found.get("item") == text_item \
        and found.get("before") is None and found.get("after") is None \
        and found.get("created_by") == user
    check("E6. 记录字段一致（date/handler/item/created_by）", ok_fields,
          str(found)[:140] if found else "")

    # ---- [F] 图片上传 + 字节一致 ----
    print("\n[F] 图片上传")
    img_bytes = b"\xff\xd8\xff\xe0" + b"phix-log-image-payload" * 80
    data_url = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()
    st, body = req(op2, "POST", "/logs/", {"date": "2026-08-16", "handler": text_handler,
                                            "item": "带图记录", "before": data_url, "after": None})
    check("F1. POST 带 before 图片 → 200", st == 200 and body.get("ok"), f"{st} {str(body)[:120]}")
    img_rec = body.get("record", {})
    img_id = body.get("id", "")
    before_url = img_rec.get("before")
    check("F2. 返回 before 图片 URL", bool(before_url) and before_url.startswith("/media/logs/"),
          f"{before_url}")

    st3, hdrs3, served = get_headers(before_url)
    check("F3. GET 图片 URL → 200 且 Content-Type image/jpeg",
          st3 == 200 and hdrs3.get("Content-Type") == "image/jpeg",
          f"{st3} {hdrs3.get('Content-Type')}")
    check("F4. 图片字节与写入文件一致", served == img_bytes,
          f"served={len(served)} expect={len(img_bytes)}")

    # ---- [G] 校验拒绝 ----
    print("\n[G] 校验拒绝")
    st, body = req(op2, "POST", "/logs/", {"date": "2999-12-31", "handler": "x", "item": "y"})
    check("G1. 未来日期被拒(400)", st == 400, f"{st}")
    st, body = req(op2, "POST", "/logs/", {"date": "not-a-date", "handler": "x", "item": "y"})
    check("G2. 坏日期被拒(400)", st == 400, f"{st}")
    st, body = req(op2, "POST", "/logs/", {"date": "2026-01-01", "handler": "  ", "item": "y"})
    check("G3. 空受理人被拒(400)", st == 400, f"{st}")
    big = "data:image/jpeg;base64," + base64.b64encode(b"x" * (1500_000 + 1)).decode()
    st, body = req(op2, "POST", "/logs/", {"date": "2026-01-01", "handler": "x", "item": "y",
                                            "before": big})
    check("G4. 超大图片被拒(4xx)", 400 <= st < 500, f"{st}")
    st, body = req(op2, "POST", "/logs/", {"date": "2026-01-01", "handler": "x", "item": "y",
                                            "before": "data:image/gif;base64,AAAA"})
    check("G5. 不支持的图片类型被拒(400)", st == 400, f"{st}")

    # ---- [H] DELETE 权限 ----
    print("\n[H] DELETE 权限")
    st, body = req(op0, "DELETE", f"/logs/{text_id}")
    check("H1. DELETE 匿名 → 401", st == 401, f"{st}")
    st, body = req(op2, "DELETE", f"/logs/{text_id}")
    check("H2. DELETE 非 staff → 403", st == 403, f"{st}")

    # 设为 staff（走 phix admin API，用 service key）
    sk_path = os.path.join(HERE, ".service_key")
    sk = ""
    try:
        sk = open(sk_path).read().strip()
    except Exception:
        pass
    check("H3. .service_key 可读", bool(sk), "missing" if not sk else "")

    staff_ok = False
    if sk:
        try:
            r = urllib.request.Request(f"{BASE}/api/v1/admin/users",
                                       headers={"X-Phix-Service-Key": sk})
            with urllib.request.urlopen(r, timeout=20) as resp:
                users_data = json.loads(resp.read())
            uid = None
            for u in users_data.get("users", []):
                if u.get("username") == user:
                    uid = u["id"]
                    break
            if uid:
                r = urllib.request.Request(f"{BASE}/api/v1/admin/user/{uid}/flags",
                                           data=json.dumps({"is_staff": True}).encode(),
                                           method="POST",
                                           headers={"Content-Type": "application/json",
                                                    "X-Phix-Service-Key": sk})
                with urllib.request.urlopen(r, timeout=20) as resp:
                    json.loads(resp.read())
                staff_ok = True
        except Exception as e:
            check("H3b. 设 staff 失败", False, str(e)[:120])
    if staff_ok:
        op3, _ = opener()
        st, body = req(op3, "POST", "/auth/login/", {"username": user, "password": pw})
        check("H4. staff 重新登录成功", st == 200, f"{st}")
        st, body = req(op3, "DELETE", f"/logs/{text_id}")
        check("H5. DELETE staff → 200", st == 200 and body.get("ok"), f"{st} {str(body)[:120]}")
        st, body = req(op0, "GET", "/logs/")
        logs2 = body.get("logs", [])
        check("H6. 删除后记录消失", not any(r.get("id") == text_id for r in logs2), "")
        # 删带图记录 → 图片文件也被删
        st, body = req(op3, "DELETE", f"/logs/{img_id}")
        check("H7. staff 删除带图记录 → 200", st == 200, f"{st}")
        st4, _, _ = get_headers(before_url)
        check("H8. 图片文件被删（GET → 404）", st4 == 404, f"{st4}")
    else:
        check("H4. staff 重新登录成功", False, "无 service_key")
        check("H5. DELETE staff → 200", False, "")
        check("H6. 删除后记录消失", False, "")
        check("H7. staff 删除带图记录 → 200", False, "")
        check("H8. 图片文件被删", False, "")

    # ---- [I] deploy 排除清单 ----
    print("\n[I] deploy 排除清单")
    deploy_src = open(os.path.join(HERE, "deploy.py"), encoding="utf-8").read()
    check("I1. deploy.py EXCLUDE_FILES 含 logs.json", '"logs.json"' in deploy_src, "")
    check("I2. deploy.py EXCLUDE_FILES 含 logs.json.tmp", '"logs.json.tmp"' in deploy_src, "")
    check("I3. deploy.py 排除 media/logs 目录",
          "media/logs" in deploy_src and "EXCLUDE_REL_DIRS" in deploy_src, "")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  - " + f)
    print("=" * 70)
    print(f"（临时账号 {user[:8]}… 请由主代理用 clean_dev_db 清理）")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
