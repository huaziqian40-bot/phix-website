"""phix 官网后端（stdlib ThreadingHTTPServer，零外部框架依赖）。

运行：D:\\phix\\server\\.venv\\Scripts\\python.exe D:\\phix\\website\\server.py [--port 8940]

职责：
- 静态托管 website/ 下 html/css/js/assets
- /app/ 原样返回 app/index.html
- 动态端点：POST /auth/register/ /auth/login/ /auth/logout/、GET /me/、
  POST /proxy/<path>/、GET /download/list/
- cookie：phix_access / phix_refresh，httpOnly+SameSite=Lax，HMAC 签名
- 对 phix 的所有请求走信封（X-Phix-Enc: 1）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 把 website/ 加入 sys.path 以便 import core_e2e
WEBSITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEBSITE_DIR))

import core_e2e as e2e  # noqa: E402
import webapp_data as wd  # noqa: E402  （平台抓取层，纯标准库+可选依赖，绝不抛在 import 期）
import webapp_managebac as mb  # noqa: E402  （ManageBac 活动流层）

# ---- 配置 ----
PHIX_SERVER = os.environ.get("PHIX_SERVER", "http://127.0.0.1:8931").rstrip("/")
SITE_SECRET_PATH = WEBSITE_DIR / ".site_secret"
PUBKEY_PATH = WEBSITE_DIR / ".phix_pubkey"
SERVICE_KEY_PATH = WEBSITE_DIR / ".service_key"
CONTENT_JSON_PATH = WEBSITE_DIR / "content.json"
AUDIT_LOG_PATH = WEBSITE_DIR / "admin_audit.log"
FEEDBACK_DIR = WEBSITE_DIR / "feedback"
LEGACY_USERS_PATH = WEBSITE_DIR / ".legacy_users.json"
MEDIA_DOWNLOADS = WEBSITE_DIR / "media" / "downloads"
COOKIE_ACCESS_TTL = 30 * 24 * 3600  # 30 days (sliding refresh keeps it alive)
COOKIE_REFRESH_TTL = 30 * 24 * 3600  # 30 days

# ---- 跨站免密登录（一次性 SSO 码，见 D:\phix\website\SSO-跨域与子域方案.md）----
#
# 背景：官网与 /app/ 同域同 cookie，天生互通；心履在另一个注册域（xin-lv.com），
# 浏览器不会把 phix.ing 的 cookie 发给它。所以：
#   * 本文件提供 /auth/sso/code/、/auth/sso/redeem/、/auth/sso/enter/、/sso/to-xinlv/
#     四个端点，把"已登录"这件事压成一枚 120 秒、单次、绑站点的一次性码；
#   * 码**不含任何凭据**（没有令牌、没有 DEK、没有用户名），服务端只存它的摘要。
XINLV_SITE_URL = os.environ.get("XINLV_SITE_URL", "https://xin-lv.com").rstrip("/")
#: 登录后写的**非 httpOnly** 提示 cookie（只放 "1" 与用户名，**绝不放令牌**）。
#: 给同域/同父域的页面做"是否已登录 → 要不要直接跳过去自动兑换"的判断。
HINT_COOKIE = "phix_hint"
HINT_USER_COOKIE = "phix_hint_user"
#: cookie 的 Domain 属性。留空 = 只对本域有效（当前生产就是这样）。
#: 若把心履挪到 xinlv.phix.ing，把它设成 `.phix.ing`，两个子域就能共享登录态。
COOKIE_DOMAIN = os.environ.get("PHIX_COOKIE_DOMAIN", "").strip()

# ---- /app/ 平台数据抓取（网页端登录后由服务端代抓 edupage/managebac/邮箱）----
APP_DATA_FETCH_TIMEOUT = 30.0     # 抓取层自带 20 秒平台预算，这里再兜一层
#: 只认 /app/mail/<一段>/ —— uid 的合法性由 webapp_data 再校验（非法直接 404）
_APP_MAIL_PATH_RE = re.compile(r"^/app/mail/([^/]{1,32})/?$")
#: **保留段**：这些不是 uid，而是同一前缀下的固定子路由。`_APP_MAIL_PATH_RE` 会把它们
#: 一起吞掉（`contacts` 完全符合 `[^/]{1,32}`），所以下面每一条都必须先判、且这里列全。
_APP_MAIL_RESERVED = frozenset({"contacts", "send", "read"})
#: 标记已读：POST /app/mail/<uid>/read/ —— 这是**唯一**写邮箱状态的入口
_APP_MAIL_READ_PATH_RE = re.compile(r"^/app/mail/([^/]{1,32})/read/?$")
_APP_MAIL_SEND_PATH_RE = re.compile(r"^/app/mail/send/?$")
#: 附件下载：GET /app/mail/<uid>/attachments/<index>/ —— 回**原始字节**（不是 JSON）
_APP_MAIL_ATTACH_PATH_RE = re.compile(r"^/app/mail/([^/]{1,32})/attachments/(\d{1,3})/?$")
#: 通讯录：GET /app/mail/contacts/ —— 从收件箱+已发送的邮件头收割联系人
_APP_MAIL_CONTACTS_PATH_RE = re.compile(r"^/app/mail/contacts/?$")
#: 通讯录缓存（每用户 30 分钟；收割要扫几百封邮件头，不能每次刷新都做）
CONTACTS_CACHE_TTL = 1800
#: 课程活动流（ManageBac 通知/消息/讨论/详情）
_APP_COURSES_NOTIFICATIONS_RE = re.compile(r"^/app/courses/notifications/?$")
_APP_COURSES_MESSAGES_RE = re.compile(r"^/app/courses/messages/?$")
_APP_COURSES_DISCUSSIONS_RE = re.compile(r"^/app/courses/([^/]+)/discussions/?$")
_APP_COURSES_DETAILS_RE = re.compile(r"^/app/courses/([^/]+)/details/?$")
#: 课程数据缓存（用户隔离，TTL 120秒成功/30秒失败）
COURSE_CACHE_TTL = 120
COURSE_CACHE_FAIL_TTL = 30


# ---- 服务密钥（调 phix admin API 用）----

def parse_multipart_mail(ctype: str, raw: bytes):
    """把一段 `multipart/form-data` 请求体解析成 `(fields, attachments)`。

    `fields` 是 `{字段名: 文本}`；`attachments` 是
    `[{"filename", "content_type", "data"}]`（`data` 是 bytes）。

    做法：给请求体前面补一个 `Content-Type` 头，它就成了一封合法的 MIME 邮件，
    交给标准库解析 —— **不需要 `cgi` 模块**（Python 3.13 已删除它，本站跑在 3.14）。

    **必须显式传 `policy.default`**：默认的 compat32 策略返回老式 `Message`，
    它**没有 `iter_parts()`**；一旦走到那一步就是未捕获异常 → 处理器崩 → 连接被直接
    断开，客户端只看到 `RemoteDisconnected`，拿不到任何可读错误。
    `policy.default` 返回的 `EmailMessage` 才有 `iter_parts()`，而且 `get_filename()`
    会按 RFC 2231/2047 正确还原中文文件名。

    解析不出结构时抛 `ValueError`（调用方回 400）。
    """
    from email import message_from_bytes, policy

    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ValueError("请求体为空")
    enveloped = ("Content-Type: " + str(ctype) + "\r\nMIME-Version: 1.0\r\n\r\n").encode("utf-8")
    try:
        msg = message_from_bytes(enveloped + bytes(raw), policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("上传内容解析失败") from exc
    if not msg.is_multipart():
        raise ValueError("上传格式不正确")

    fields: dict = {}
    attachments: list = []
    for part in msg.iter_parts():
        try:
            name = (part.get_param("name", header="content-disposition") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        try:
            filename = part.get_filename()
        except Exception:  # noqa: BLE001
            filename = None
        payload = part.get_payload(decode=True) or b""
        if filename:
            attachments.append({
                "filename": str(filename),
                "content_type": part.get_content_type() or "application/octet-stream",
                "data": payload,
            })
        elif name:
            charset = part.get_content_charset() or "utf-8"
            try:
                fields[name] = payload.decode(charset, "replace")
            except Exception:  # noqa: BLE001
                fields[name] = ""
    return fields, attachments


def _load_service_key() -> str:
    try:
        return SERVICE_KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


SERVICE_KEY = _load_service_key()


# ---- 静态资源版本化（内容哈希 → ?v= 查询串，破 Cloudflare 固定 URL 缓存）----
#
# HTML 里的静态引用会被改写成 `/static/site.css?v=<sha256[:8]>` 之类。
# 文件名不变、只加查询串；每次部署内容一变版本号自动变。带 ?v= 的响应加
# `immutable` 缓存头，HTML 本身加 `no-cache`，这样固定 URL 被 CF 缓存后也能
# 立即拿到新资源。
#
# 2026-09-13 修：原来这里是**白名单**（只列了 site.js/site.css/app/*/log.css），
# 新增的 about.css、deco-products.css、deco-pages.css 不在名单里 → 固定 URL 被 CF
# 缓存（实测 cf-cache=HIT, age≈1085s），改了样式但公网仍是旧文件，单元格里引用的
# 图片被换掉后直接 404。现在改成**自动覆盖 /static/ 下所有 .css/.js**，新文件不会再漏。

#: 会做内容哈希版本化的静态资源。
#:
#: 2026-09-20 修：原来只覆盖 `.css|.js` —— **图片没有版本串**，于是
#: `/static/app/phl-lite-logo.png` 被浏览器按 `immutable` 缓存一年，
#: 换了新 logo 用户那边死活看不到（用户实测报的就是这个）。
#: 另外这个正则要求「文件名后面立刻是引号」，所以**手写的 `?v=` 会让它跳过整条 URL**
#: —— 一旦手写版本号，后面再改内容浏览器也不会重新取。所以：
#: HTML 里**不要**手写 `?v=`，交给这里按内容哈希生成。
_STATIC_ASSET_RE = re.compile(
    r'(["\'])(/static/[^"\'?]+?\.(?:css|js|png|jpe?g|gif|svg|webp|ico|woff2?|ttf))\1')

# 安装包下载链接（/media/downloads/*.exe|msi|dmg|zip|apk|pkg）也做同样的版本化。
# 为什么必须：官网走 Cloudflare，同一文件名在内容更新后仍会命中旧缓存 ——
# 实测 2026-09-25 有三个包（apk / 两个 dmg）在 CDN 上还是旧版本，客户端下载后
# SHA256 校验失败、更新被拒。给 URL 带上内容指纹即可穿透缓存。
_DOWNLOAD_ASSET_RE = re.compile(
    r'(["\'])(/media/downloads/[^"\'?]+?\.(?:exe|msi|dmg|zip|apk|pkg))\1')

#: 版本串缓存：key=(相对路径, mtime_ns, size) → sha256 前 8 位。
#: 安装包有 171 MB，不能每次渲染 HTML 都重算一遍。
_STATIC_VERSION_CACHE: dict[tuple, str] = {}


def _static_version(rel: str) -> str:
    """返回某静态文件的 sha256 前 8 位；文件缺失返回空串（按 mtime+size 缓存）。"""
    p = WEBSITE_DIR / rel
    try:
        st = p.stat()
    except OSError:
        return ""
    key = (rel, st.st_mtime_ns, st.st_size)
    hit = _STATIC_VERSION_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        ver = hashlib.sha256(p.read_bytes()).hexdigest()[:8]
    except OSError:
        ver = ""
    _STATIC_VERSION_CACHE[key] = ver
    return ver


def _versionize_html(html: str) -> str:
    """给 HTML 里的 `/static/**` 与 `/media/downloads/**` 加上 `?v=<sha256[:8]>`。

    「已带的跳过」由正则保证：URL 里带 `?` 的不会被匹配 —— 所以**别手写 `?v=`**，
    手写就等于把这条 URL 钉死，之后改内容浏览器也不会重新取（immutable 一年）。
    """
    for pattern in (_STATIC_ASSET_RE, _DOWNLOAD_ASSET_RE):
        html = pattern.sub(_versionize_repl, html)
    return html


def _versionize_repl(m: re.Match) -> str:
    quote, url = m.group(1), m.group(2)
    ver = _static_version(url.lstrip("/"))
    if not ver:
        return m.group(0)
    return f"{quote}{url}?v={ver}{quote}"


# ---- 旧账号清单（心履迁移）----

def _load_legacy_users() -> dict:
    """加载旧账号清单 {username: {hash, is_staff, is_superuser}}。
    文件缺失/损坏时返回空 dict，退化为普通登录流程。"""
    try:
        raw = LEGACY_USERS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _django_check_password(password: str, encoded: str) -> bool:
    """Django pbkdf2_sha256 密码校验（不依赖 Django，纯 stdlib + hashlib）。"""
    try:
        parts = encoded.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = parts[2]
        expected_hash = parts[3]
        import hashlib
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt.encode("utf-8"), iterations)
        actual_hash = base64.b64encode(dk).decode("ascii")
        return hmac.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False


LEGACY_USERS = _load_legacy_users()


def _get_legacy_users() -> dict:
    """每次登录都重读旧账号清单。

    文件极小（几 KB）、登录本身有频率限制，重读代价可忽略；换来的是
    「新增旧账号无需重启官网进程」，迁移测试也能在运行中注入临时旧账号再恢复。
    """
    return _load_legacy_users()


def _sync_legacy_flags(uid, legacy_info) -> dict:
    """按旧账号清单给 phix 账号补角色（is_staff / is_superuser）。

    2026-09-12 修的坑：**原实现只在"注册成功"分支补角色**，于是"账号早就存在"
    （例如 hzq 之前已经建过）的人走 `already_registered` 分支，角色永远补不上，
    站长/管理员登录后看不到「管理后台」按钮。现在两条分支都调用这里，幂等。
    superuser 也通过 flags API 设置（该接口原先只认 is_staff/is_active）。
    """
    if not uid or not SERVICE_KEY or not legacy_info:
        return {}
    flags = {}
    if legacy_info.get("is_staff"):
        flags["is_staff"] = True
    if legacy_info.get("is_superuser"):
        flags["is_superuser"] = True
    if not flags:
        return {}
    try:
        st, body, _ = _phix_admin_request("POST", f"admin/user/{int(uid)}/flags", flags)
        _audit_log("legacy_flags_sync", str(uid),
                   f"status={st} want={sorted(flags)} got={json.dumps(body, ensure_ascii=False)[:160]}")
    except Exception as exc:  # noqa: BLE001
        _audit_log("legacy_flags_sync", str(uid), f"error={type(exc).__name__}: {exc}")
    return flags


# ---- CMS（content.json）----

import threading as _threading

_cms_lock = _threading.Lock()


def _cms_load() -> dict:
    """读取 content.json，返回 {defaults: {}, overrides: {}}。"""
    try:
        raw = CONTENT_JSON_PATH.read_text(encoding="utf-8")
        doc = json.loads(raw)
        return {
            "defaults": doc.get("_defaults", {}),
            "overrides": doc.get("_overrides", {}),
        }
    except Exception:
        return {"defaults": {}, "overrides": {}}


def cms_get(key: str) -> str | None:
    """取一个 CMS key 的最终值：override > default > None。"""
    data = _cms_load()
    val = data["overrides"].get(key)
    if val is not None:
        return val
    return data["defaults"].get(key)


def cms_replace(html: str) -> str:
    """把 HTML 里的 {{cms:key}} 替换为最终值；找不到 key 就保留原文中的默认文本。

    约定：HTML 里写成 `{{cms:key|默认文本}}` —— 有 override/default 就用那个，
    没有就回落 `默认文本`。如果只写 `{{cms:key}}`（无竖线），找不到就留空串。
    """
    import re
    data = _cms_load()

    def _repl(m):
        inner = m.group(1)
        if "|" in inner:
            key, fallback = inner.split("|", 1)
        else:
            key, fallback = inner, ""
        key = key.strip()
        val = data["overrides"].get(key)
        if val is None:
            val = data["defaults"].get(key)
        if val is None:
            return fallback
        return str(val)

    return re.sub(r"\{\{cms:([^}]+)\}\}", _repl, html)


def _cms_save_overrides(overrides: dict) -> None:
    """原子写入 content.json 的 _overrides 段。"""
    with _cms_lock:
        data = _cms_load()
        data["overrides"] = overrides
        tmp = CONTENT_JSON_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"_defaults": data["defaults"],
                                    "_overrides": overrides},
                                   ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(CONTENT_JSON_PATH)


# ---- 审计日志 ----

def _audit_log(operator: str, target: str, action: str) -> None:
    """追加一行审计日志（不含任何凭据）。"""
    from datetime import datetime, timezone as _tz
    ts = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts}\t{operator}\t{target}\t{action}\n"
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass

# ---- 简易内存缓存（课程活动流） ----

_course_cache: dict[str, tuple[float, any]] = {}
_course_cache_lock = _threading.Lock()

def _get_cached(key: str) -> any:
    """读缓存，过期/不存在返回 None。"""
    import time
    with _course_cache_lock:
        entry = _course_cache.get(key)
        if entry is None:
            return None
        exp, val = entry
        if time.time() > exp:
            del _course_cache[key]
            return None
        return val

def _set_cache(key: str, val: any, ttl: int) -> None:
    """写缓存，TTL 秒后过期。"""
    import time
    with _course_cache_lock:
        _course_cache[key] = (time.time() + ttl, val)

# ---- 站点密钥（HMAC 签名 cookie）----

def _load_or_create_secret() -> bytes:
    if SITE_SECRET_PATH.exists():
        try:
            raw = SITE_SECRET_PATH.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
        except OSError:
            pass
    secret = secrets.token_bytes(32)
    try:
        SITE_SECRET_PATH.write_bytes(secret)
        os.chmod(SITE_SECRET_PATH, 0o600)
    except OSError:
        pass
    return secret


SITE_SECRET = _load_or_create_secret()

# 临时 DEK 存储（仅用于网站代理加密 payload；生产环境应由客户端加密）
# DEK **不再存进程内存**（旧实现 _DEK_STORE 重启即丢、且让网站长期持钥）。
# 现在 DEK 只放在**签名 cookie** 的 "d" 字段里：每次请求解出即用，
# 网站进程里不留副本，重启也不丢（cookie 在浏览器侧）。
# 信任边界与心履网页端相同：网站作为"客户端代理"短暂接触 DEK，已在 README 记为已知取舍。


def _sign_cookie(payload: str) -> str:
    sig = hmac.new(SITE_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_cookie(raw: str) -> str | None:
    """验证并返回 payload；签名不对返回 None。"""
    if "." not in raw:
        return None
    payload, sig = raw.rsplit(".", 1)
    expected = hmac.new(SITE_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return payload


def _encode_cookie_value(access: str, refresh: str, exp: int, dek_hex: str = "",
                         username: str = "", extra: dict | None = None) -> str:
    doc = {"a": access, "r": refresh, "e": exp}
    if dek_hex:
        doc["d"] = dek_hex
    if username:
        doc["u"] = username          # 只用于 /auth/unlock/ 拉密钥材料；不是秘密
    if extra:
        doc.update(extra)
    raw = json.dumps(doc, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return _sign_cookie(payload)


def _decode_cookie_value(raw: str) -> dict | None:
    payload = _verify_cookie(raw)
    if payload is None:
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        doc = json.loads(base64.urlsafe_b64decode(padded))
        return doc
    except Exception:
        return None


# ---- phix 公钥管理 ----

def _get_server_pk() -> bytes | None:
    """从 .phix_pubkey 加载或从 phix /ping 获取并固定。"""
    pinned = e2e.load_pinned(WEBSITE_DIR)
    key = pinned.get(PHIX_SERVER)
    if key:
        try:
            return bytes.fromhex(key)
        except ValueError:
            pass
    # 尝试从 /ping 获取
    try:
        req = urllib.request.Request(f"{PHIX_SERVER}/api/v1/ping")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pk_b64 = data.get("pk", "")
        if pk_b64:
            pk_bytes = e2e.b64d(pk_b64)
            e2e.pin_key(WEBSITE_DIR, PHIX_SERVER, pk_bytes.hex())
            return pk_bytes
    except Exception:
        pass
    return None


# ---- Payload 加密（用 DEK 把明文包成 PHIX1 信封）----

def _derive_object_key(dek: bytes, object_name: str) -> bytes:
    """从 DEK 派生对象密钥。"""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF as _HKDF
    from cryptography.hazmat.primitives import hashes as _h
    return _HKDF(algorithm=_h.SHA256(), length=32,
                 salt=b"phix/v1/object-keys",
                 info=object_name.encode("utf-8")).derive(dek)


def _aad_object(user_id: int, object_name: str) -> bytes:
    """必须与客户端 phixcrypto.aad_object **逐字节一致**。

    事故记录：这里曾传 None —— 官网自己写的自己能解（smoke 全绿），但客户端
    一定解不开（AAD 不匹配 → GCM 认证失败）。官网↔客户端的黄金链路测试
    `_lab/test_web_client.py` 就是为此而生。
    """
    return f"phix/v1/object|{user_id}|{object_name}".encode("utf-8")


def _user_id_from_access(access: str) -> int:
    """从 access JWT 的 sub 里取 user_id（不验签：这串来自我们自己签的 cookie）。"""
    try:
        payload = access.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        doc = json.loads(base64.urlsafe_b64decode(payload))
        return int(doc.get("sub") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _encrypt_payload(dek: bytes, user_id: int, object_name: str, plaintext: str) -> str:
    """用 DEK 派生对象密钥，AES-GCM 加密，返回 PHIX1 信封字符串。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    obj_key = _derive_object_key(dek, object_name)
    nonce = os.urandom(12)
    ct = _AESGCM(obj_key).encrypt(nonce, plaintext.encode("utf-8"),
                                  _aad_object(user_id, object_name))
    return "PHIX1." + e2e.b64e(nonce) + "." + e2e.b64e(ct)


def _decrypt_payload(dek: bytes, user_id: int, object_name: str, envelope: str) -> str | None:
    """解密 PHIX1 信封，返回明文；失败返回 None。"""
    if not envelope.startswith("PHIX1."):
        return None
    try:
        parts = envelope[6:].split(".")
        if len(parts) != 2:
            return None
        nonce = e2e.b64d(parts[0])
        ct = e2e.b64d(parts[1])
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
        obj_key = _derive_object_key(dek, object_name)
        plain = _AESGCM(obj_key).decrypt(nonce, ct, _aad_object(user_id, object_name))
        return plain.decode("utf-8")
    except Exception:
        return None


# ---- phix 代理调用 ----

def _phix_request(method: str, path: str, body=None, access_token: str = "",
                  retry_on_expired: bool = True, refresh_token: str = "",
                  service_key: bool = False) -> tuple[int, dict, str | None]:
    """向 phix 发请求（走信封）。返回 (status, body_dict, new_access_if_refreshed)。

    path 不含 /api/v1 前缀。
    ``service_key=True`` 额外带上 ``X-Phix-Service-Key``（服务端到服务端的端点要它，
    例如 /auth/sso/redeem —— 光有码还换不走令牌）。
    """
    api_path = f"/api/v1/{path.lstrip('/')}"
    pk = _get_server_pk()
    if pk is None:
        return 502, {"error": {"code": "no_server_key", "message": "无法连接 phix 服务器"}}, None

    headers = {"Content-Type": "application/json", "X-Phix-Enc": "1"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if service_key and SERVICE_KEY:
        headers["X-Phix-Service-Key"] = SERVICE_KEY

    envelope, sk = e2e.make_envelope(pk, method, api_path, body)
    data = json.dumps(envelope).encode("utf-8")

    try:
        req = urllib.request.Request(f"{PHIX_SERVER}{api_path}", data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except Exception as exc:
        return 502, {"error": {"code": "proxy_error", "message": str(exc)}}, None

    # 解密响应
    try:
        env_resp = json.loads(raw.decode("utf-8"))
        if isinstance(env_resp, dict) and "iv" in env_resp and "ct" in env_resp:
            plain = e2e.open_envelope_response(sk, method, api_path, env_resp)
            resp_body = json.loads(plain.decode("utf-8"))
        else:
            resp_body = env_resp
    except Exception:
        try:
            resp_body = json.loads(raw.decode("utf-8"))
        except Exception:
            resp_body = {"_raw": raw[:200].decode("utf-8", "replace")}

    # token_expired → 用 refresh 续一次再重试
    err_code = ""
    if isinstance(resp_body, dict):
        err_obj = resp_body.get("error", {})
        if isinstance(err_obj, dict):
            err_code = err_obj.get("code", "")
    if err_code == "token_expired" and retry_on_expired and refresh_token:
        new_access = _refresh_access(refresh_token)
        if new_access:
            return _phix_request(method, path, body, access_token=new_access,
                                 retry_on_expired=False, refresh_token="")
    return status, resp_body, None


def _refresh_access(refresh_token: str) -> str | None:
    """用 refresh 令牌换新 access。"""
    pk = _get_server_pk()
    if pk is None:
        return None
    api_path = "/api/v1/auth/refresh"
    body = {"refresh_token": refresh_token}
    envelope, sk = e2e.make_envelope(pk, "POST", api_path, body)
    data = json.dumps(envelope).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Phix-Enc": "1"}
    try:
        req = urllib.request.Request(f"{PHIX_SERVER}{api_path}", data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    except Exception:
        return None
    try:
        env_resp = json.loads(raw.decode("utf-8"))
        if isinstance(env_resp, dict) and "iv" in env_resp and "ct" in env_resp:
            plain = e2e.open_envelope_response(sk, "POST", api_path, env_resp)
            resp_body = json.loads(plain.decode("utf-8"))
        else:
            resp_body = env_resp
    except Exception:
        return None
    if isinstance(resp_body, dict) and resp_body.get("ok"):
        return resp_body.get("access_token")
    return None


# ---- phix admin API 调用（服务密钥，不走信封）----

def _phix_admin_request(method: str, path: str, body=None) -> tuple[int, dict]:
    """调 phix /api/v1/admin/* 端点。用 X-Phix-Service-Key 鉴权，不走信封加密。"""
    if not SERVICE_KEY:
        return 500, {"error": {"code": "no_service_key",
                                "message": "官网缺少 .service_key 文件"}}
    url = f"{PHIX_SERVER}/api/v1/{path.lstrip('/')}"
    headers = {
        "Content-Type": "application/json",
        "X-Phix-Service-Key": SERVICE_KEY,
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except Exception as exc:
        return 502, {"error": {"code": "proxy_error", "message": str(exc)}}
    try:
        resp_body = json.loads(raw.decode("utf-8"))
    except Exception:
        resp_body = {"_raw": raw[:200].decode("utf-8", "replace")}
    return status, resp_body


# ---- 客户端密钥材料生成（注册用）----
# 复制 phl-lite-dev/hellopinghe/phixcrypto.py 的最小实现

def _new_client_material(username: str, passphrase: str) -> dict:
    """生成注册所需的密钥材料（与 phixcrypto.new_material 兼容）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes as _hashes

    DEK_BYTES = 32
    SALT_BYTES = 16
    NONCE_BYTES = 12
    ENVELOPE_PREFIX = "PHIX1."
    KDF_ALGO_V2 = "scrypt-hkdf-v2"
    AUTH_INFO = b"phix/v1/auth"
    ENC_INFO = b"phix/v1/enc"
    SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 1
    SCRYPT_MAXMEM = 64 * 1024 * 1024

    def _b64e(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
        return HKDF(algorithm=_hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)

    def _aad_identity(uname: str) -> bytes:
        return f"phix/v1/identity|{uname}".encode("utf-8")

    def _seal(kek: bytes, plaintext: bytes, aad: bytes) -> str:
        nonce = os.urandom(NONCE_BYTES)
        ct = AESGCM(kek).encrypt(nonce, plaintext, aad)
        return ENVELOPE_PREFIX + _b64e(nonce) + "." + _b64e(ct)

    def _derive_kek(passphrase_str: str, salt_hex: str, algo: str) -> bytes:
        pw = passphrase_str.encode("utf-8")
        mk = hashlib.scrypt(pw, salt=bytes.fromhex(salt_hex), n=SCRYPT_N, r=SCRYPT_R,
                            p=SCRYPT_P, dklen=32, maxmem=SCRYPT_MAXMEM)
        if algo == KDF_ALGO_V2:
            return _hkdf(mk, bytes.fromhex(salt_hex), ENC_INFO, 32)
        return mk

    def _wrap_dek(dek: bytes, passphrase_str: str, salt_hex: str, uname: str, algo: str) -> str:
        kek = _derive_kek(passphrase_str, salt_hex, algo)
        return _seal(kek, dek, _aad_identity(uname))

    def _make_key_check(dek: bytes, uname: str, plain: str) -> str:
        # 与 phixcrypto.make_key_check **逐字节一致**：用 `derive_object_key(dek, "__keycheck__")`
        # 派生的对象密钥 + 身份族 AAD，加密 `bytes.fromhex(plain)`。
        # （旧实现误用 sha256(dek+aad) 与 keycheck 族 AAD，客户端 prove_dek 解不开。）
        aad = f"phix/v1/identity|{uname}".encode("utf-8")
        obj_key = _derive_object_key(dek, "__keycheck__")
        return _seal(obj_key, bytes.fromhex(plain), aad)

    def _new_key_check_plain() -> str:
        return secrets.token_hex(16)

    def _new_recovery_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        chars = "".join(secrets.choice(alphabet) for _ in range(24))
        return "-".join(chars[i:i+4] for i in range(0, 24, 4))

    def _wrap_dek_with_recovery(dek: bytes, code: str, salt_hex: str, uname: str, algo: str) -> str:
        norm = code.replace("-", "").upper()
        kek = hashlib.scrypt(norm.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                             n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=SCRYPT_MAXMEM)
        if algo == KDF_ALGO_V2:
            kek = _hkdf(kek, bytes.fromhex(salt_hex), ENC_INFO, 32)
        return _seal(kek, dek, _aad_identity(uname))

    kdf_algo = KDF_ALGO_V2
    dek = os.urandom(DEK_BYTES)
    salt = secrets.token_hex(SALT_BYTES)
    asalt = secrets.token_hex(SALT_BYTES)
    rsalt = secrets.token_hex(SALT_BYTES)
    plain = _new_key_check_plain()
    code = _new_recovery_code()

    # AuthHash
    mk = hashlib.scrypt(passphrase.encode("utf-8"), salt=bytes.fromhex(asalt),
                        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=SCRYPT_MAXMEM)
    auth_hash = _hkdf(mk, bytes.fromhex(asalt), AUTH_INFO, 32).hex()

    return {
        "dek": dek,  # 返回 DEK 供网站代理加密 payload（存 cookie，不暴露给前端）
        "kdf_algo": kdf_algo,
        "kdf_salt": salt,
        "auth_salt": asalt,
        "auth_hash": auth_hash,
        "key_wrap": _wrap_dek(dek, passphrase, salt, username, kdf_algo),
        "key_mode": "password",
        "key_check": _make_key_check(dek, username, plain),
        "key_check_plain": plain,
        "recovery_salt": rsalt,
        "recovery_wrap": _wrap_dek_with_recovery(dek, code, rsalt, username, kdf_algo),
    }


def _safe_next(raw: str, default: str = "") -> str:
    """把 ``next`` 参数收敛成**站内相对路径**，堵住开放重定向。

    SSO 的入口 URL 会带 ``next=``，不校验就成了"任意站点都能借我们的域名跳转"。
    只接受以单个 ``/`` 开头、且不含 ``\\`` 与控制字符的路径。
    """
    if not isinstance(raw, str):
        return default
    url = raw.strip()
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        return default
    if any(c in url for c in "\r\n\t"):
        return default
    return url


# ---- HTTP Handler ----

def _unwrap_dek(key_wrap: str, passphrase: str, kdf_salt: str, username: str,
                kdf_algo: str):
    """用口令解出 DEK（与 phixcrypto.unwrap_dek 同构）。失败返回 None。"""
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    try:
        mk = hashlib.scrypt(passphrase.encode("utf-8"), salt=bytes.fromhex(kdf_salt),
                            n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
        if kdf_algo == "scrypt-hkdf-v2":
            kek = HKDF(algorithm=_hashes.SHA256(), length=32,
                       salt=bytes.fromhex(kdf_salt), info=b"phix/v1/enc").derive(mk)
        else:
            kek = mk
        rest = key_wrap[len("PHIX1."):]
        nonce_s, ct_s = rest.split(".", 1)
        pad = lambda s: s + "=" * (-len(s) % 4)
        nonce = base64.urlsafe_b64decode(pad(nonce_s))
        ct = base64.urlsafe_b64decode(pad(ct_s))
        aad = f"phix/v1/identity|{username}".encode("utf-8")
        return AESGCM(kek).decrypt(nonce, ct, aad)
    except Exception:  # noqa: BLE001  口令不对/材料不齐都当"没有 DEK"
        return None


# ---- 改 phix 登录密码（网页端只发派生值，绝不发口令原文）----

def _wrap_dek(dek: bytes, passphrase: str, kdf_salt: str, username: str,
              kdf_algo: str) -> str:
    """用口令派生的 KEK 重新包裹 DEK（与 _unwrap_dek 互逆）。"""
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    mk = hashlib.scrypt(passphrase.encode("utf-8"), salt=bytes.fromhex(kdf_salt),
                        n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    if kdf_algo == "scrypt-hkdf-v2":
        kek = HKDF(algorithm=_hashes.SHA256(), length=32,
                   salt=bytes.fromhex(kdf_salt), info=b"phix/v1/enc").derive(mk)
    else:
        kek = mk
    nonce = os.urandom(12)
    ct = AESGCM(kek).encrypt(nonce, dek, f"phix/v1/identity|{username}".encode("utf-8"))
    return "PHIX1." + e2e.b64e(nonce) + "." + e2e.b64e(ct)


def _prove_dek(dek: bytes, username: str, key_check: str) -> str | None:
    """解开 key_check 自检块，返回里面的明文 hex（= key_check_plain）；失败返回 None。

    兼容两种格式：标准格式（与 phixcrypto 一致）与旧网站注册格式。
    """
    if not key_check.startswith("PHIX1."):
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        rest = key_check[len("PHIX1."):]
        nonce_s, ct_s = rest.split(".", 1)
        nonce = e2e.b64d(nonce_s)
        ct = e2e.b64d(ct_s)
    except Exception:  # noqa: BLE001
        return None
    # 1) 标准格式（与 phixcrypto.prove_dek 一致）
    try:
        obj_key = _derive_object_key(dek, "__keycheck__")
        plain = AESGCM(obj_key).decrypt(nonce, ct,
                                        f"phix/v1/identity|{username}".encode("utf-8"))
        return plain.hex()
    except Exception:  # noqa: BLE001
        pass
    # 2) 旧网站注册格式（sha256(dek + aad) 作 KEK，AAD 为 keycheck 族）
    try:
        aad = f"phix/v1/keycheck|{username}".encode("utf-8")
        kek = hashlib.sha256(dek + aad).digest()
        plain = AESGCM(kek).decrypt(nonce, ct, aad)
        return plain.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


# ---- 维修日志（PHIX 日志页：logs.json + media/logs/，均为运行期数据，绝不 deploy 覆盖）----

LOGS_JSON_PATH = WEBSITE_DIR / "logs.json"
MEDIA_LOGS = WEBSITE_DIR / "media" / "logs"
LOG_IMAGE_MAX_BYTES = 1_500_000       # 单张图片（解码后）上限 1.5 MB
LOG_BODY_MAX_BYTES = 8 * 1024 * 1024  # POST /logs/ 请求体上限（防超大 base64 body）
LOG_ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_logs_lock = _threading.RLock()
_rate_lock = _threading.Lock()
_rate_buckets: dict = {}
_RATE_WINDOW = 60.0
_RATE_LIMITS = {"logs_post": 30, "logs_delete": 30}


def _rate_ok(bucket: str, limit: int) -> bool:
    """进程内滑动窗口限流（写操作的基本健壮性，不引入外部依赖）。"""
    now = time.monotonic()
    with _rate_lock:
        hits = [t for t in _rate_buckets.get(bucket, []) if now - t < _RATE_WINDOW]
        if len(hits) >= limit:
            _rate_buckets[bucket] = hits
            return False
        hits.append(now)
        _rate_buckets[bucket] = hits
        return True


def _logs_read_unlocked() -> list:
    """读 logs.json（调用方需持有 _logs_lock）。损坏/缺失返回空列表。"""
    try:
        data = json.loads(LOGS_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _logs_load() -> list:
    with _logs_lock:
        return _logs_read_unlocked()


def _logs_write_unlocked(logs: list) -> None:
    """原子写 logs.json（先写 .tmp 再 replace）。"""
    tmp = LOGS_JSON_PATH.with_name("logs.json.tmp")
    tmp.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LOGS_JSON_PATH)


def _logs_append(record: dict) -> None:
    with _logs_lock:
        logs = _logs_read_unlocked()
        logs.append(record)
        _logs_write_unlocked(logs)


def _logs_remove(rec_id: str) -> bool:
    """删除记录；找到并删除返回 True，否则 False。"""
    with _logs_lock:
        logs = _logs_read_unlocked()
        new = [r for r in logs if r.get("id") != rec_id]
        if len(new) == len(logs):
            return False
        _logs_write_unlocked(new)
        return True


def _valid_log_date(date_str: str) -> bool:
    """YYYY-MM-DD 且不是未来日期。"""
    from datetime import datetime, date as _date
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    return d <= _date.today()


def _decode_data_url(raw: str) -> tuple[str | None, bytes | None]:
    """解析 data URL → (mime, bytes)；非法返回 (None, None)。"""
    try:
        head, b64 = raw.split(",", 1)
        mime = head[5:].split(";")[0].strip().lower()
        b64 = b64.strip()
        b64 += "=" * (-len(b64) % 4)
        return mime, base64.b64decode(b64)
    except Exception:
        return None, None


def _delete_log_images(record: dict) -> None:
    """删除记录关联的图片文件（只删 media/logs 下我们自己命名的文件）。"""
    rec_id = record.get("id") or ""
    if not rec_id:
        return
    for key in ("before", "after"):
        for ext in (".jpg", ".png", ".webp"):
            p = MEDIA_LOGS / f"{rec_id}-{key}{ext}"
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass


# ---- Pinghe Launcher 网页端 AI 接口（POST /app/ai/chat/）----
#
# 与前端 app/ 的**冻结契约**（原字段名一个都没改；`provider`/`protocol` 是新增的附加字段）：
#   请求：{"question": "...", "history": [{"role": "user|assistant", "content": "..."}]}  # history 可选
#   200 ：{"ok": true, "answer": "...", "model": "...",
#          "provider": "DeepSeek", "protocol": "openai|anthropic",   # 新增：便于显示"由 XXX 回答"
#          "context": {"objects": ["school", ...], "chars": 1234, "truncated": false}}
#   401 unauthorized / 400 bad_request / 409 ai_not_configured / 429 rate_limited / 502 upstream_error
#
# 设计要点（评审重点）：
# 1. **只查询**：模型拿到的是「只读文本快照」，网页端不给任何写接口、不给工具调用。
#    系统提示里写死"不能写、没有工作区/文件概念、数据缺失就直说不知道"。
# 2. **密钥不硬编码**：用用户自己在客户端配的 API Key（存在端到端加密的同步对象
#    settings.ai 里）。服务端只在**这一次请求**里临时解密使用 —— 不落盘、不记日志、
#    不回传；异常/上游报错文本也一律脱敏（见 _ai_redact）。
# 3. **审计日志只记元信息**：谁问了、用了哪个模型、上下文多少字符、是否截断、
#    各平台走的是实时还是快照，**绝不记问题原文与回答**（避免把用户隐私写进 admin_audit.log）。
# 4. 同步对象解密失败/不存在 → 当成该对象为空，绝不 500。
#
# 数据来源：**实时抓取优先，抓不到的平台回退到客户端同步快照**（服务端拿解密后的平台账号
# 直接登录 EduPage / ManageBac / 邮箱，见 webapp_data.py）。这一段是**可注入**的：
#   _ai_build_context(docs, accounts=..., fetch_school=...)   # fetch_school(accounts) -> dict|None
# 默认回调 `_ai_live_fetch` 用 `try: import webapp_data` + hasattr 软引用它的 fetch_all，
# 模块不在/没有该函数/抛错/超墙钟（AI_LIVE_FETCH_TIMEOUT）→ 返回 None → 纯同步快照路径。
# 所以抓取层可以晚一步上线，两边互不阻塞；它挂了也只是退化成"用快照回答"。
#
# settings.ai 的真实形态（①②③ 顺序探测，历史版本都认）：
#   ① 数组 + 默认项（**现在的权威结构**，与客户端 ai_providers 对齐，不限数量）：
#      {"providers": [{"name","protocol","base_url","model","api_key"}, …], "default_index": 0}
#      也兼容 Lite 写的老变体：条目用 `models:[…]`、顶层用 `active_provider_id`/`active_model`
#   ② 早期单个：{"provider":"api","base_url":…,"model":…,"api_key":…}
#      （也认 PH Launcher 渲染层的 apiEndpoint/apiModel/apiKey，apiKey 常被抹成 apiKeySaved=true）
#   ③ 桌面客户端的本地模型：{"provider":"local","localModel":…} → 网页端够不着，直接给明确提示
#
# 协议：真的支持两种 —— openai（/chat/completions + Bearer）与 anthropic（/v1/messages + x-api-key）。
# 认不出的协议**明确报错**，绝不拿 openai 格式硬打过去。

AI_QUESTION_MAX = 2000            # 单次问题长度上限（字符）
AI_HISTORY_MAX_ITEMS = 12         # 历史最多 12 条（只保留最近的）
AI_HISTORY_MAX_ITEM_CHARS = 1000  # 单条历史长度上限
AI_CONTEXT_MAX_CHARS = 24000      # 交给模型的用户数据快照总量上限
AI_ANSWER_MAX_CHARS = 32000       # 回传前端前再兜一层
AI_RATE_LIMIT_PER_MIN = 10        # 每个用户每分钟最多 10 次
MAIL_SEND_RATE_LIMIT_PER_MIN = 5  # 每个用户每分钟最多 5 封邮件
AI_EDUPAGE_MAX_ROWS = 300
AI_TASK_MAX_ROWS = 200
AI_SCHEDULE_MAX_ROWS = 300
AI_TIMETABLE_MAX_DAYS = 14
AI_CONTEXT_OBJECTS = ("school", "settings.lessons", "schedule", "timetable")
AI_SECTION_BUDGET = {             # 每个小节的字符上限（总量上限之外的第二道闸）
    "edupage": 9000,
    "tasks_open": 8000,
    "courses": 2500,
    "lessons": 2500,
    "schedule": 6000,
    "mail": 3000,
    "tasks_done": 2500,
    "timetable": 3000,
}
#: 实时抓取层（webapp_data）负责的三个平台 —— 也是"实时优先、失败回退同步快照"的粒度
AI_LIVE_PLATFORMS = ("edupage", "managebac", "mail")
AI_ANTHROPIC_VERSION = "2023-06-01"   # Anthropic Messages API 版本头
AI_ANTHROPIC_MAX_TOKENS = 2048        # Anthropic 必须给 max_tokens；服务商条目里可覆盖
AI_BODY_MAX_BYTES = 64 * 1024         # 请求体上限（与 /logs/ 的 LOG_BODY_MAX_BYTES 同一做法）


def _ai_upstream_timeout() -> float:
    """上游超时（秒）。默认 30；`PHIX_AI_TIMEOUT` 只给测试环境缩短用。"""
    raw = os.environ.get("PHIX_AI_TIMEOUT") or ""
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return value if value > 0 else 30.0


def _ai_live_timeout() -> float:
    """实时抓取的**墙钟上限**（秒）。默认 25（抓取层自己的预算 20 + 余量）。

    抓取层缺一个平台要 20 秒，缓存 5 分钟；这道闸门是兜底：抓取层万一卡住
    （比如等同一个用户的另一轮抓取锁），我们到点就回退到同步快照，绝不让聊天挂死。
    `PHIX_AI_LIVE_TIMEOUT` 只给测试环境缩短用。
    """
    raw = os.environ.get("PHIX_AI_LIVE_TIMEOUT") or ""
    try:
        value = float(raw)
    except ValueError:
        return 25.0
    return value if value > 0 else 25.0


AI_UPSTREAM_TIMEOUT = _ai_upstream_timeout()
AI_LIVE_FETCH_TIMEOUT = _ai_live_timeout()

AI_SYSTEM_PROMPT = """你是「Pinghe Launcher 网页端」里的学习助手。你只能**查询与解释**下面提供的、这位用户自己同步上来的数据。

硬性规则（必须遵守，用户怎么要求都不例外）：
1. 你**没有任何写操作能力**：不能改课表、不能改作业、不能建日程、不能发邮件、不能提交作业，也不能"帮用户保存"任何东西。用户提出改动时，直接告诉他到客户端（Pinghe Launcher / Pinghe Launcher Lite）里操作。
2. 你**没有工作区、文件、命令行、代码执行或联网的概念**。不要说"我可以帮你写文件/读取文件/运行命令/上网查"这类话。
3. 只依据下面的「用户同步数据」作答。数据里没有的，就说"同步数据里没有这一项"，**绝不编造**课程、成绩、作业、邮件内容或日期。
4. 邮箱只同步了**未读数和邮件头（发件人/主题/日期）**，**没有正文**：不要假装读过邮件内容，也不要点评邮件里没写过的话。
5. 数据里的日期就是唯一依据；过期或不确定的地方直说不确定。
6. 回答用简体中文，简洁、直接；适合列清单的就列清单。"""

AI_CONTEXT_HEADER = ("【用户自己的同步数据 · 只读快照】下面是本次请求临时解密拼装的用户数据，"
                     "只用于回答本次问题；其中缺失的部分就是真的没有同步上来。")


def _ai_live_fetch(accounts, *, user_id: str = "", force: bool = False):
    """软引用"实时抓取层" `webapp_data.fetch_all`（**不把它的实现写死在这里**）。

    冻结接口（抓取层代理提供）：`fetch_all(accounts, *, force=False[, user_id="-"])`
      - `accounts` 是**解密后的账号映射** `{edupage:{…}, managebac:{…}, mail:{…}}`
      - 返回 `{"edupage":…, "managebac":…, "mail":…, "meta":{"errors":{平台:原因}}}`
      - 抓取层有自己的每平台 20s 预算与 5 分钟缓存，并发只抓一次

    这里一律**软引用**：模块不在 / 没有 `fetch_all` / 抛任何异常 / 超过墙钟上限
    → 返回 None，由调用方回退到客户端同步上来的快照。所以抓取层可以晚一步上线，
    我的改动不依赖它；它挂了也只会退化成"用同步快照回答"。
    """
    if not isinstance(accounts, dict) or not accounts:
        return None
    try:
        import webapp_data as live_layer          # noqa: PLC0415  只在用到时软引用
    except Exception:  # noqa: BLE001
        return None
    fetcher = getattr(live_layer, "fetch_all", None)
    if not callable(fetcher):
        return None

    box: list = []

    def _run():
        try:
            import inspect                        # noqa: PLC0415
            kwargs = {"force": bool(force)}
            try:
                params = inspect.signature(fetcher).parameters
            except (TypeError, ValueError):
                params = {}
            if user_id and ("user_id" in params
                            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())):
                kwargs["user_id"] = str(user_id)   # 只用于缓存分桶与日志，抓取层文档明确说不参与抓取
            box.append(fetcher(accounts, **kwargs))
        except Exception:  # noqa: BLE001  抓取层任何异常都不该影响回答
            box.append(None)

    worker = _threading.Thread(target=_run, name="app-ai-live-fetch", daemon=True)
    worker.start()
    worker.join(AI_LIVE_FETCH_TIMEOUT)
    if worker.is_alive():                          # 超墙钟 → 直接回退，不等它
        return None
    if not box or not isinstance(box[0], dict):
        return None
    return box[0]


def _ai_school_with_live(school, accounts, fetch_school) -> tuple:
    """把"实时抓取"叠到"同步快照"上，返回 (school_doc, sources)。

    **粒度是平台**：抓成功的平台用实时数据，抓失败（`meta.errors` 里有它）或压根没抓到的
    平台，保留客户端同步上来的那一段。`fetch_school` 是**可注入**的回调，契约就一句：
        fetch_school(accounts) -> {"edupage":…, "managebac":…, "mail":…, "meta":{…}} 或 None
    传 None 就是不注入（纯同步快照路径）。
    """
    merged = dict(school) if isinstance(school, dict) else {}
    sources = {platform: ("sync" if isinstance(merged.get(platform), dict) and merged.get(platform)
                          else "none") for platform in AI_LIVE_PLATFORMS}
    if not callable(fetch_school) or not isinstance(accounts, dict) or not accounts:
        return merged, sources

    try:
        live = fetch_school(accounts)
    except Exception:  # noqa: BLE001  注入的抓取实现抛错 = 这条链路没有实时数据
        live = None
    if not isinstance(live, dict):
        return merged, sources

    meta = live.get("meta") if isinstance(live.get("meta"), dict) else {}
    errors = meta.get("errors") if isinstance(meta.get("errors"), dict) else {}
    for platform in AI_LIVE_PLATFORMS:
        section = live.get(platform)
        if platform in errors:                     # 该平台抓失败 → 保留同步快照那一段
            continue
        if isinstance(section, dict) and section:
            merged[platform] = section
            sources[platform] = "live"
    return merged, sources


def _ai_clean(value, limit: int = 200) -> str:
    """压成单行并限长（同步对象里的字段可能带换行/超长）。"""
    return " ".join(str(value if value is not None else "").split())[:limit]


def _ai_as_list(value) -> list:
    return value if isinstance(value, list) else []


def _ai_redact(text, *hide) -> str:
    """脱敏：抹掉 API Key / Bearer 令牌 / `sk-` 形态的串。**保留换行**。

    用在三条路径上：上游报错文本、上游返回的正文、任何要写进响应或日志的字符串。
    """
    out = str(text if text is not None else "")
    for secret in hide:
        secret = str(secret or "")
        if len(secret) >= 6:
            out = out.replace(secret, "***")
    out = re.sub(r"(?i)\b(bearer|api[_-]?key|token)\b\s*[:=]?\s*[A-Za-z0-9._\-]{6,}", r"\1 ***", out)
    out = re.sub(r"\bsk-[A-Za-z0-9._\-]{4,}", "sk-***", out)
    return out


def _ai_day(value) -> str:
    """从 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM' / '2026/9/14' 里取规范日期；取不到返回 ''。"""
    match = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", _ai_clean(value, 40))
    if not match:
        return ""
    return "%04d-%02d-%02d" % (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _ai_day_distance(day: str, today) -> int:
    """离今天的绝对天数；解析不了给一个大数（于是排最后）。"""
    from datetime import datetime as _dt
    try:
        parsed = _dt.strptime(day, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 10 ** 6
    return abs((parsed - today).days)


def _ai_recent_first(rows: list, day_of, today) -> list:
    """「最近优先」排序：按离今天的距离升序，距离相同按日期升序。

    截断策略就建立在这个顺序上——先被砍掉的一定是最远的那些。
    """
    return sorted(rows, key=lambda row: (_ai_day_distance(day_of(row), today), day_of(row)))


# ---- settings.ai 解析 ----

def _ai_config_from_doc(doc) -> dict:
    """从 settings.ai 同步对象里挑出一个接入点。

    返回 `{name, protocol, base_url, model, api_key, index, local_mode}`；认不出/没配置返回 {}。
    **绝不打印 api_key**。

    按 ①②③ 顺序探测（历史形态都认）：
      ① 数组 + 默认项（**现在的权威结构**，与客户端 ai_providers 对齐，不限数量）：
         {"providers":[{"name","protocol","base_url","model","api_key"}, …], "default_index":0}
         也兼容 Lite 写的老变体：条目里用 `models:[…]`、顶层用 `active_provider_id`/`active_model`
      ② 早期单个：{"provider":"api","base_url":…,"model":…,"api_key":…}
         （也认 PH Launcher 渲染层的 apiEndpoint/apiModel/apiKey）
      ③ 桌面客户端的本地模型：{"provider":"local","localModel":…, …}
         → `local_mode=True`：**网页端用不了**（服务器够不着用户本机的 Ollama），
           上层据此回明确的中文提示"请用桌面客户端"，而不是拿 openai 格式硬打过去。

    选谁：优先 `default_index` 指的那个；它字段不全/密钥为空就按数组顺序取第一个能用的；
    一个能用的都没有时，把 default_index（或第一个）原样返回，让上层给出准确原因。

    探测顺序（与网页端 account.html 的解析保持一致）：
      `providers[]` → `ai.providers[]` → `base_url/api_key` 扁平 → `apiModel/apiKey` 扁平。
    也就是说 `ai` 键要先拆一层再找 `providers`（PLL cloudsync 会把整个 ai 段塞进对象）。
    """
    if not isinstance(doc, dict):
        return {}

    # 允许"先看顶层，再看被 ai 包起来的那一层"
    layers = [doc]
    inner = doc.get("ai") if isinstance(doc.get("ai"), dict) else None
    if inner is not None and inner is not doc:
        layers.append(inner)

    # ---------------- ① / ② providers 数组（先顶层，再 ai.providers[]）----------------
    for layer in layers:
        providers = [item for item in _ai_as_list(layer.get("providers")) if isinstance(item, dict)]
        if providers:
            return _ai_pick_provider(layer, providers)

    # ---------------- ③ / ④ 扁平单服务商（先顶层，再 ai 里那一层）----------------
    for layer in layers:
        cfg = _ai_flat_config(layer)
        if cfg is not None:
            return cfg
    return {}


def _ai_pick_provider(layer: dict, providers: list) -> dict:
    """从 providers 数组里挑一个：default_index 优先 → active_provider_id → 第一个可用的。"""
    def _as_config(entry: dict, index: int) -> dict:
        models = [_ai_clean(m, 120) for m in _ai_as_list(entry.get("models")) if _ai_clean(m, 120)]
        protocol = (_ai_clean(entry.get("protocol"), 20) or "openai").lower()
        return {
            "name": _ai_clean(entry.get("name"), 60) or f"供应商 {index + 1}",
            "protocol": protocol,
            "base_url": _ai_clean(entry.get("base_url") or entry.get("apiEndpoint"), 300),
            # 模型名：本条的 model → apiModel → 顶层 active_model → 本条 models[0]（字段别名都认）
            "model": (_ai_clean(entry.get("model"), 120)
                      or _ai_clean(entry.get("apiModel"), 120)
                      or _ai_clean(layer.get("active_model"), 120)
                      or (models[0] if models else "")),
            # 密钥别名：api_key（规范）→ apiKey（PHL）→ key（旧写法）
            "api_key": str(entry.get("api_key") or entry.get("apiKey") or entry.get("key") or "").strip(),
            "max_tokens": entry.get("max_tokens"),
            "index": index,
            "local_mode": protocol in ("local", "ollama", "local-ai"),
        }

    order: list = []
    default_index = layer.get("default_index")
    if isinstance(default_index, str) and default_index.strip().lstrip("-").isdigit():
        default_index = int(default_index.strip())
    if isinstance(default_index, int) and not isinstance(default_index, bool) \
            and 0 <= default_index < len(providers):
        order.append(default_index)                  # 优先 default_index 指的那个
    wanted = _ai_clean(layer.get("active_provider_id"), 80)   # Lite 老变体：按 id 选
    if wanted:
        for index, item in enumerate(providers):
            if _ai_clean(item.get("id"), 80) == wanted and index not in order:
                order.insert(0, index)
    order += [index for index in range(len(providers)) if index not in order]

    first = order[0]
    for index in order:                              # 取第一个「能用的」
        cfg = _as_config(providers[index], index)
        if not _ai_config_problem(cfg):
            return cfg
    return _as_config(providers[first], first)       # 都不行 → 原样返回，由上层判定原因


#: 扁平形态的字段名（认得出来才算"这一层写了服务商"）
_AI_FLAT_KEYS = ("provider", "base_url", "apiEndpoint", "api_url",
                 "model", "apiModel", "localModel", "api_key", "apiKey", "key")


def _ai_flat_config(layer: dict):
    """扁平单服务商形态 → config；这一层没写服务商相关字段就返回 None（继续找下一层）。

    认得两种：
      - `{"provider":"api","base_url":…,"model":…,"api_key":…}`（早期网页端/密码管理）
      - **PHL 渲染层**：`{"provider":"api","apiModel":…,"apiKey":…,"localModel":…}`
        → 转成一个 provider：`{protocol:"openai", base_url:(可能为空), model: apiModel||localModel, api_key: apiKey}`
        **base_url 为空时绝不猜默认地址**，交给上层回"请补上服务商地址"。
      - `{"provider":"local", …}`（桌面客户端的本地模型）→ 单独一条，网页端明确说用不了。
    """
    if not any(key in layer for key in _AI_FLAT_KEYS):
        return None
    kind = _ai_clean(layer.get("provider"), 30).lower()
    if kind in ("local", "ollama", "local-ai"):
        return {
            "name": _ai_clean(layer.get("name"), 60) or "本地模型",
            "protocol": "local",
            "base_url": _ai_clean(layer.get("localEndpoint") or layer.get("base_url"), 300),
            "model": _ai_clean(layer.get("localModel") or layer.get("model"), 120),
            "api_key": "",
            "index": None,
            "local_mode": True,
        }
    endpoint = layer.get("base_url") or layer.get("apiEndpoint") or layer.get("api_url") or ""
    model = (layer.get("model") or layer.get("apiModel") or layer.get("localModel") or "")
    key = layer.get("api_key") or layer.get("apiKey") or layer.get("key") or ""
    name = _ai_clean(layer.get("name"), 60)
    if not name:
        name = kind if kind not in ("", "api", "openai") else "自定义"
    return {
        "name": name,
        "protocol": (_ai_clean(layer.get("protocol"), 20) or "openai").lower(),
        "base_url": _ai_clean(endpoint, 300),
        "model": _ai_clean(model, 120),
        "api_key": key.strip() if isinstance(key, str) else "",
        "index": None,
        "local_mode": False,
    }


def _ai_is_local_url(url: str) -> bool:
    """是不是「服务器够不着」的本机/内网地址。

    注意：**不是拒绝**，只是失败时好给一句人话（本机联调时 127.0.0.1 的假服务也能用）。
    """
    host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".localhost"):
        return True
    if re.match(r"^127\.", host) or re.match(r"^10\.", host) or re.match(r"^192\.168\.", host):
        return True
    return bool(re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host))


#: 网页端真的实现了的两种协议（见 _ai_call_upstream）
AI_PROTOCOLS = ("openai", "openai-compatible", "openai_compatible", "anthropic")


def _ai_config_problem(cfg: dict) -> str:
    """'' 表示可用；否则 'not_configured' / 'no_base_url' / 'local' / 'protocol'。"""
    if not cfg:
        return "not_configured"
    if cfg.get("local_mode") or (cfg.get("protocol") or "") in ("local", "ollama", "local-ai"):
        return "local"           # 桌面客户端的本地模型，网页端够不着 → 明确提示用客户端
    base_url = cfg.get("base_url") or ""
    if not base_url or not base_url.startswith(("http://", "https://")):
        # **绝不猜默认地址**（PHL 的扁平形态常常只写了 apiModel/apiKey）→ 让用户去补
        return "no_base_url"
    if not cfg.get("model"):
        return "not_configured"
    protocol = (cfg.get("protocol") or "openai").lower()
    if protocol not in AI_PROTOCOLS:
        return "protocol"
    if not cfg.get("api_key"):
        # 没密钥就是"没配好"（会被跳过、去找下一个可用的服务商）。
        # 本机/内网地址不再例外：本地模型请用 provider:"local"，那是另一条明确提示。
        return "not_configured"
    return ""


def _ai_endpoint_url(base_url: str) -> str:
    """base_url → chat/completions 完整地址（已经是完整地址就不动）。"""
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _ai_clean_history(raw) -> list:
    """历史消息：只认 user/assistant + 字符串内容，只保留**最近** 12 条。"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content.strip()[:AI_HISTORY_MAX_ITEM_CHARS]})
    return out[-AI_HISTORY_MAX_ITEMS:]


# ---- 上下文构造（只用查询：把用户自己的同步对象拼成紧凑文本）----

def _ai_lesson_rows(section: dict) -> list:
    """把课表段归一成「每行都带 date」的课卡列表。

    两个客户端写的形状不一样，**都要认**（2026-09-13 实测：网页端种的数据是 days 形状，
    只读 lessons 会把整周课表静默漏掉）：
      - `lessons`: [{"date":"YYYY-MM-DD","start","end","subject",…}]（Lite/PHL 共用 School 段）
      - `days`:    {"YYYY-MM-DD": [{"start","end","subject",…}]}（按天存的形状）
    """
    rows = [row for row in _ai_as_list(section.get("lessons")) if isinstance(row, dict)]
    days = section.get("days")
    if isinstance(days, dict):
        for day, items in days.items():
            for item in _ai_as_list(items):
                if isinstance(item, dict):
                    rows.append({**item, "date": item.get("date") or day})
    return rows


def _ai_lines_edupage(school: dict, today) -> list:
    """EduPage 课表：日期/时间/科目/教室/老师/教学组。"""
    section = school.get("edupage") if isinstance(school, dict) else None
    if not isinstance(section, dict):
        return []
    rows = _ai_lesson_rows(section)
    if not rows:
        return []
    rows = _ai_recent_first(rows, lambda row: _ai_day(row.get("date")), today)[:AI_EDUPAGE_MAX_ROWS]
    rows.sort(key=lambda row: (_ai_day(row.get("date")), _ai_clean(row.get("start"), 5)))
    lines = []
    for row in rows:
        subject = _ai_clean(row.get("subject"), 60)
        if not subject:
            continue
        start = _ai_clean(row.get("start"), 5)
        end = _ai_clean(row.get("end"), 5)
        bits = [subject]
        for label, value in (("组", row.get("group")), ("老师", row.get("teacher")), ("教室", row.get("room"))):
            text = _ai_clean(value, 80)
            if text:
                bits.append(f"{label}:{text}")
        if row.get("cancelled"):
            bits.append("（已取消）")
        lines.append(f"- {_ai_day(row.get('date'))} {start}{('-' + end) if end else ''}  | "
                     + " | ".join(bits))
    return lines


def _ai_task_done(task: dict) -> bool:
    """作业是否算「已完成」：有分数，或状态词命中。"""
    if _ai_clean(task.get("score"), 40):
        return True
    status = _ai_clean(task.get("status"), 60).lower()
    if not status:
        return False
    open_words = ("未提交", "未交", "待提交", "未完成", "进行中", "逾期", "not submitted", "pending", "in progress", "todo")
    if any(word in status for word in open_words):
        return False
    done_words = ("已提交", "已完成", "已交", "已评", "已批改", "已打分", "submitted", "graded", "complete", "done")
    return any(word in status for word in done_words)


def _ai_lines_tasks(school: dict, today, want_done: bool) -> list:
    """ManageBac 作业：课程 / 标题 / 截止时间 / 状态 / 成绩。"""
    section = school.get("managebac") if isinstance(school, dict) else None
    if not isinstance(section, dict):
        return []
    rows = [row for row in _ai_as_list(section.get("tasks")) if isinstance(row, dict)]
    rows = [row for row in rows if _ai_task_done(row) == want_done]
    if not rows:
        return []
    rows = _ai_recent_first(
        rows,
        lambda row: _ai_day(row.get("due_at") or row.get("due") or row.get("due_text")),
        today)[:AI_TASK_MAX_ROWS]
    lines = []
    for row in rows:
        title = _ai_clean(row.get("title"), 120)
        if not title:
            continue
        bits = [title]
        for label, value, limit in (("课程", row.get("course") or row.get("class_name"), 60),
                                    ("截止", row.get("due_at") or row.get("due") or row.get("due_text"), 40),
                                    ("状态", row.get("status"), 60),
                                    ("成绩", row.get("score"), 40)):
            text = _ai_clean(value, limit)
            if text:
                bits.append(f"{label}:{text}")
        lines.append("- " + " | ".join(bits))
    return lines


def _ai_lines_courses(school: dict) -> list:
    """ManageBac 课程与总评。"""
    section = school.get("managebac") if isinstance(school, dict) else None
    if not isinstance(section, dict):
        return []
    lines = []
    for row in _ai_as_list(section.get("courses"))[:80]:
        if not isinstance(row, dict):
            continue
        name = _ai_clean(row.get("name") or row.get("class_name"), 80)
        if not name:
            continue
        grade = _ai_clean(row.get("grade"), 40)
        lines.append(f"- {name}" + (f" | 总评:{grade}" if grade else ""))
    return lines


def _ai_lines_mail(school: dict) -> list:
    """邮箱摘要：**只有未读数与邮件头（发件人/主题/日期）**，正文没有同步。

    字段白名单是刻意的：客户端没同步正文，这里就绝不从别的字段里"顺手"取。
    """
    section = school.get("mail") if isinstance(school, dict) else None
    if not isinstance(section, dict):
        return []
    lines = []
    unread = section.get("unread")
    if isinstance(unread, (int, float)) and not isinstance(unread, bool):
        lines.append(f"- 未读邮件：{int(unread)} 封")
    for row in _ai_as_list(section.get("recent"))[:30]:
        if not isinstance(row, dict):
            continue
        date = _ai_clean(row.get("date"), 40)
        sender = _ai_clean(row.get("from"), 120)
        subject = _ai_clean(row.get("subject"), 160)
        parts = [p for p in (date, sender, subject) if p]
        if not parts:
            continue
        lines.append("- " + " | ".join(parts) + ("（未读）" if row.get("unread") else ""))
    return lines


def _ai_lines_schedule(doc: dict, today) -> list:
    """日程：日期 / 时间 / 标题 / 备注（最近优先）。"""
    if not isinstance(doc, dict):
        return []
    rows = [row for row in _ai_as_list(doc.get("events")) if isinstance(row, dict)]
    if not rows:
        return []
    rows = _ai_recent_first(rows, lambda row: _ai_day(row.get("day")), today)[:AI_SCHEDULE_MAX_ROWS]
    lines = []
    for row in rows:
        title = _ai_clean(row.get("title"), 120)
        if not title:
            continue
        time_text = _ai_clean(row.get("time"), 5)
        note = _ai_clean(row.get("note"), 120)
        lines.append(f"- {_ai_day(row.get('day'))} {time_text}  | {title}"
                     + (f" | 备注:{note}" if note else ""))
    return lines


def _ai_lines_lessons(doc: dict) -> list:
    """选课（settings.lessons）：Lite 写 {lessons:[{subject,group,teacher}]}，也兼容 {groups:[...]}。"""
    if not isinstance(doc, dict):
        return []
    lines = []
    for row in _ai_as_list(doc.get("lessons"))[:80]:
        if not isinstance(row, dict):
            continue
        subject = _ai_clean(row.get("subject"), 60)
        if not subject:
            continue
        bits = [subject]
        for label, value, limit in (("组", row.get("group"), 80), ("老师", row.get("teacher"), 60)):
            text = _ai_clean(value, limit)
            if text:
                bits.append(f"{label}:{text}")
        lines.append("- " + " | ".join(bits))
    groups = [_ai_clean(item, 120) for item in _ai_as_list(doc.get("groups")) if _ai_clean(item, 120)]
    if groups:
        lines.append("- 教学组：" + "、".join(groups[:60]))
    return lines


def _ai_lines_timetable(doc: dict, today) -> list:
    """课表明细（timetable 对象：{days: {"YYYY-MM-DD": [课卡…]}}），只带最近两周。"""
    if not isinstance(doc, dict) or not isinstance(doc.get("days"), dict):
        return []
    days = [day for day in doc["days"].keys() if _ai_day(day)]
    days = _ai_recent_first(days, lambda day: _ai_day(day), today)[:AI_TIMETABLE_MAX_DAYS]
    lines = []
    for day in sorted(days):
        for row in _ai_as_list(doc["days"].get(day)):
            if not isinstance(row, dict):
                continue
            subject = _ai_clean(row.get("subject"), 60)
            if not subject:
                continue
            start = _ai_clean(row.get("start"), 5)
            end = _ai_clean(row.get("end"), 5)
            bits = [f"- {_ai_day(day)} {start}{('-' + end) if end else ''}  | {subject}"]
            for label, value, limit in (("组", row.get("group"), 80), ("老师", row.get("teacher"), 60),
                                        ("教室", row.get("room"), 60)):
                text = _ai_clean(value, limit)
                if text:
                    bits.append(f" | {label}:{text}")
            lines.append("".join(bits))
    return lines


def _ai_build_context(docs: dict, *, accounts=None, fetch_school=None) -> dict:
    """把用户数据拼成紧凑文本快照，返回 {"text","objects","chars","truncated","sources"}。

    **数据来源是可注入的**：`docs` 里给的是客户端同步上来的对象（回退用），
    `fetch_school` 是可选的"实时抓取"回调（契约：`fetch_school(accounts) -> dict|None`，
    见 `_ai_school_with_live`）。**实时优先、按平台回退**：
      - 抓成功的平台（edupage / managebac / mail）用实时数据；
      - `meta.errors` 里报错、或压根没抓到的平台，保留同步快照里那一段；
      - 不注入（fetch_school=None）就是原来的纯同步快照路径。
    实际用了哪一路记在返回值的 `sources`（只进审计日志，不回传前端）。

    优先级（也是截断顺序，先放的一定最"近"）：
      课表 → 未完成作业 → 课程与总评 → 选课 → 日程 → 邮箱摘要 → 已完成作业 → 课表明细。
    两级闸门：每节 AI_SECTION_BUDGET 字符 + 全量 AI_CONTEXT_MAX_CHARS 字符；
    任何一节被砍或整节放不下 → truncated=True。
    """
    from datetime import date as _date

    today = _date.today()
    school = docs.get("school") if isinstance(docs.get("school"), dict) else {}
    school, sources = _ai_school_with_live(school, accounts, fetch_school)
    schedule_doc = docs.get("schedule") if isinstance(docs.get("schedule"), dict) else {}
    lessons_doc = docs.get("settings.lessons") if isinstance(docs.get("settings.lessons"), dict) else {}
    timetable_doc = docs.get("timetable") if isinstance(docs.get("timetable"), dict) else {}

    plan = (
        ("school", "### 最近课表（EduPage，日期/时间/科目/教室/老师/教学组）",
         _ai_lines_edupage(school, today), "edupage"),
        ("school", "### 未完成的作业（ManageBac，最近的截止日期在前）",
         _ai_lines_tasks(school, today, False), "tasks_open"),
        ("school", "### 课程与总评（ManageBac）", _ai_lines_courses(school), "courses"),
        ("settings.lessons", "### 选课（教学组）", _ai_lines_lessons(lessons_doc), "lessons"),
        ("schedule", "### 日程（最近优先）", _ai_lines_schedule(schedule_doc, today), "schedule"),
        ("school", "### 邮箱摘要（只有未读数与邮件头：发件人/主题/日期，**没有正文**）",
         _ai_lines_mail(school), "mail"),
        ("school", "### 已完成的作业与成绩（ManageBac）", _ai_lines_tasks(school, today, True), "tasks_done"),
        ("timetable", "### 课表明细（最近两周）", _ai_lines_timetable(timetable_doc, today), "timetable"),
    )

    parts: list = []
    objects: list = []
    # 表头与小节之间的分隔符也算进总量，保证最终 text 真的 ≤ AI_CONTEXT_MAX_CHARS
    used = len(AI_CONTEXT_HEADER) + 1
    truncated = False
    for name, title, lines, budget_key in plan:
        if not lines:
            continue
        text = title + "\n" + "\n".join(lines)
        budget = AI_SECTION_BUDGET.get(budget_key, AI_CONTEXT_MAX_CHARS)
        if len(text) > budget:
            # 这一节自己太胖：按本节预算砍掉尾巴，**后面的小节还有机会**（总量还没满）
            text = text[:budget]
            truncated = True
        room = AI_CONTEXT_MAX_CHARS - used
        if room <= 0:
            truncated = True
            break
        if len(text) > room:
            # 总量满了：从这里截断，更低优先级的小节一律不放
            text = text[:room]
            truncated = True
            parts.append(text)
            used += len(text) + 2
            if name not in objects:
                objects.append(name)
            break
        if not text.strip():
            truncated = True
            break
        parts.append(text)
        used += len(text) + 2
        if name not in objects:
            objects.append(name)

    text = (AI_CONTEXT_HEADER + "\n" + "\n\n".join(parts)) if parts else ""
    return {"text": text, "objects": objects, "chars": len(text), "truncated": truncated,
            "sources": sources}


def _ai_anthropic_url(base_url: str) -> str:
    """base_url → `/v1/messages`（已经是完整地址就不动）。"""
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/messages"):
        return url
    if url.endswith("/v1"):
        return url + "/messages"
    return url + "/v1/messages"


def _ai_anthropic_conversation(messages: list) -> tuple:
    """把内部 messages 转成 Anthropic 的形状 → (system 文本, messages)。

    Anthropic 的 system 是**顶层字段**、messages 必须 user 开头且角色交替，
    所以这里合并连续同角色、丢掉开头的 assistant。
    """
    system_parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    convo: list = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        role = "assistant" if role == "assistant" else "user"
        content = str(message.get("content") or "")
        if convo and convo[-1]["role"] == role:
            convo[-1]["content"] += "\n\n" + content
        else:
            convo.append({"role": role, "content": content})
    while convo and convo[0]["role"] != "user":
        convo.pop(0)
    return "\n\n".join(part for part in system_parts if part), convo


def _ai_post_json(url: str, headers: dict, payload: dict, key: str) -> tuple:
    """POST 一个 JSON 请求 → (ok, parsed_dict|None, error_code, error_message)。

    错误一律脱敏：不回显 API Key、不留上游原文里的敏感串。
    """
    try:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         method="POST", headers=headers)
        with urllib.request.urlopen(request, timeout=AI_UPSTREAM_TIMEOUT) as resp:
            raw = resp.read(4_000_000)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(4000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = ""
        detail = _ai_clean(_ai_redact(detail, key), 200)
        message = f"AI 服务返回 HTTP {exc.code}" + (f"：{detail}" if detail else "")
        return False, None, "upstream_error", message
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if _ai_is_local_url(url):
            return False, None, "upstream_error", (
                "无法连接 AI 服务：网页端只能使用公网可访问的 AI 服务商"
                "（本机/内网地址如 localhost、127.0.0.1 服务器够不着）")
        reason = _ai_clean(_ai_redact(getattr(exc, "reason", exc) or exc, key), 160)
        return False, None, "upstream_error", ("AI 服务连接失败：" + reason) if reason else "AI 服务连接失败"

    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return False, None, "upstream_error", "AI 服务返回的不是 JSON"
    if not isinstance(body, dict):
        return False, None, "upstream_error", "AI 服务返回的不是 JSON 对象"
    if body.get("error") or body.get("type") == "error":
        err = body.get("error")
        detail = err.get("message") if isinstance(err, dict) else (err or body.get("message"))
        return False, None, "upstream_error", (
            "AI 服务报错：" + _ai_clean(_ai_redact(detail, key), 200)).strip("：")
    return True, body, "", ""


def _ai_call_openai(cfg: dict, messages: list) -> tuple:
    """OpenAI 兼容：POST {base_url}/chat/completions，`Authorization: Bearer <key>`。"""
    key = str(cfg.get("api_key") or "")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": cfg.get("model"), "messages": messages, "stream": False}
    ok, body, code, message = _ai_post_json(_ai_endpoint_url(cfg.get("base_url") or ""),
                                            headers, payload, key)
    if not ok:
        return False, "", code, message
    answer = ""
    choices = _ai_as_list(body.get("choices"))
    if choices and isinstance(choices[0], dict):
        message_obj = choices[0].get("message")
        if isinstance(message_obj, dict):
            answer = message_obj.get("content") or ""
        answer = answer or choices[0].get("text") or ""
    if not answer:
        # 少数非标实现会把正文直接放顶层；**只认字符串**——
        # 顶层 content 是列表时那是 Anthropic 的块结构，不能 str() 成"[{'type': 'text'…}]"糊弄过去
        fallback = body.get("output_text") or body.get("content") or ""
        if isinstance(fallback, str):
            answer = fallback
    answer = _ai_redact(str(answer or "").strip(), key)
    if not answer:
        # 反向的常见坑：协议写 openai，地址其实是 Anthropic 端点（回的是 content[] 块）
        blocks = _ai_as_list(body.get("content"))
        if any(isinstance(b, dict) and isinstance(b.get("text"), str) for b in blocks):
            return False, "", "upstream_error", (
                "这个服务商地址返回的是 Anthropic 形状（content[]），不是 OpenAI 的 choices[]："
                "请检查「个人中心 → 密码管理」里该服务商的协议是否该填 anthropic")
        return False, "", "upstream_error", "AI 服务没有返回内容"
    return True, answer[:AI_ANSWER_MAX_CHARS], "", ""


def _ai_call_anthropic(cfg: dict, messages: list) -> tuple:
    """Anthropic Messages：POST {base_url}/v1/messages（已是 /v1 就拼 /messages）。

    头 `x-api-key: <key>` + `anthropic-version: 2023-06-01` + `Content-Type: application/json`；
    body `{model, max_tokens, system, messages}` —— **system 是顶层字段**，不是 messages 里的一条；
    回答取 `content[0].text`（取不到再退化成拼接所有 text 块）。
    超时/脱敏与 openai 分支共用同一套（`_ai_post_json` + AI_UPSTREAM_TIMEOUT）。
    """
    key = str(cfg.get("api_key") or "")
    system_text, convo = _ai_anthropic_conversation(messages)
    if not convo:
        return False, "", "upstream_error", "没有可发送的用户消息"
    max_tokens = cfg.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        max_tokens = AI_ANTHROPIC_MAX_TOKENS
    payload = {"model": cfg.get("model"), "max_tokens": max_tokens, "messages": convo}
    if system_text:
        payload["system"] = system_text
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "x-api-key": key, "anthropic-version": AI_ANTHROPIC_VERSION}
    ok, body, code, message = _ai_post_json(_ai_anthropic_url(cfg.get("base_url") or ""),
                                            headers, payload, key)
    if not ok:
        return False, "", code, message
    texts = [block["text"] for block in _ai_as_list(body.get("content"))
             if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip()]
    # 取 content[0].text；有多个 text 块就把它们拼起来（单块时与 content[0].text 等价）
    answer = _ai_redact("\n".join(texts).strip(), key)
    if not answer:
        # 常见坑：地址填成了 OpenAI 兼容端点（它照样 200，但回的是 choices[]）→ 说清楚
        if body.get("choices"):
            return False, "", "upstream_error", (
                "这个服务商地址返回的是 OpenAI 形状（choices[]），不是 Anthropic 的 content[]："
                "请检查「个人中心 → 密码管理」里该服务商的协议/地址是否匹配"
                "（anthropic 要打 /v1/messages）")
        return False, "", "upstream_error", "AI 服务没有返回内容"
    return True, answer[:AI_ANSWER_MAX_CHARS], "", ""


def _ai_call_upstream(cfg: dict, messages: list) -> tuple:
    """按服务商协议选一条路：openai → /chat/completions；anthropic → /v1/messages。

    返回 (ok, answer, error_code, error_message)。**error_message 一律脱敏**，
    绝不回显 API Key 或上游原文里的敏感内容。协议不认识 → 明确报错，不硬打。
    """
    protocol = str(cfg.get("protocol") or "openai").lower()
    if protocol == "anthropic":
        return _ai_call_anthropic(cfg, messages)
    if protocol in ("openai", "openai-compatible", "openai_compatible"):
        return _ai_call_openai(cfg, messages)
    return False, "", "upstream_error", (
        f"这个服务商的协议是 {protocol}，网页端暂时不支持（请在桌面客户端使用）")


class SiteHandler(BaseHTTPRequestHandler):
    server_version = "phix-site/1.0"

    def log_message(self, format, *args):
        # 静默日志（smoke 测试时不刷屏）
        pass

    # ---- 单端口分流：/api/* 与 /healthz 字节透传给 phix ----
    #
    # 为什么要这样：Cloudflare（或任何反代）**一个域名通常只映射一个源站端口**。
    # 与其对外暴露 8931（API）+ 8940（官网）两个端口，不如只开一个：
    #   /api/v1/*  → phix 服务（127.0.0.1:8931）
    #   其它一切   → 官网静态页 / 本站 /proxy/ 代理
    # 客户端（PHL/PLL/心履）只要把服务器地址填成同一个域名即可。
    #
    # **透传不解析**：请求体连密文信封一起原样转发，响应也原样回。
    # 也就是说这一层**看不到任何明文**（E2E 加密对它是透明的），
    # 它只做字节搬运 —— 不碰 DEK、不碰令牌语义。

    API_PREFIXES = ("/api/", "/healthz")

    def _is_phix_path(self, path: str) -> bool:
        return any(path == p or path.startswith(p) for p in self.API_PREFIXES)

    def _proxy_to_phix(self):
        import http.client

        upstream = PHIX_SERVER
        u = urllib.parse.urlparse(upstream)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        # 只转发我们认识的业务头；拒绝把 Host/Connection 这类原样带过去
        fwd = {}
        for h in ("X-Phix-Enc", "Authorization", "Content-Type", "Accept",
                  "X-Phix-Service-Key", "DPoP", "User-Agent"):
            v = self.headers.get(h)
            if v:
                fwd[h] = v
        try:
            conn = http.client.HTTPConnection(host, port, timeout=60)
            conn.request(self.command, self.path, body=body, headers=fwd)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
            rheaders = resp.getheaders()
        except Exception as exc:  # noqa: BLE001  上游不可达：如实回 502，不伪造
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return self._json_response(502, {"ok": False, "error": {
                "code": "upstream_down", "message": f"phix 服务不可达：{exc}"}})
        conn.close()
        self.send_response(status)
        for k, v in rheaders:
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- 路由 ----

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if self._is_phix_path(parsed.path):        # /api/* 透传给 phix
            return self._proxy_to_phix()
        if path == "/me":
            return self._handle_me()
        # ---- 跨站免密登录（一次性码）----
        if path == "/auth/sso/enter":
            return self._handle_sso_enter(parsed.query)
        if path == "/sso/to-xinlv":
            return self._handle_to_xinlv(parsed.query)
        if path == "/download/list":
            return self._handle_download_list()
        if path == "/logs":
            return self._handle_logs_get()
        # ---- PHL 网页端的平台数据（服务端代抓，见 webapp_data.py）----
        if path in ("/app/data", "/app/data/index"):
            return self._handle_app_data(parsed.query)
        # ---- 邮件附件下载（回原始字节，不走 JSON）----
        att_match = _APP_MAIL_ATTACH_PATH_RE.match(parsed.path)
        if att_match:
            return self._handle_app_mail_attachment(att_match.group(1), att_match.group(2))
        # ---- 通讯录 / 发信 / 标记已读：**必须排在 /app/mail/<uid>/ 前面** ----
        # `_APP_MAIL_PATH_RE` 是 `/app/mail/([^/]{1,32})/?`，它会连 `contacts` 这种
        # **固定路径段**一起吞掉（当成邮件 uid）。2026-09-17 实测：网页端点「通讯录」
        # 走到的是 `_handle_app_mail('contacts')` → 拿真正的 uid 去 IMAP 找 "contacts"
        # 这封邮件 → 404「找不到这封邮件」，前端只能显示「读不到通讯录」。
        # 顺序错了不会报任何服务端错误，所以这里留一段话说明为什么它必须在上面。
        if _APP_MAIL_CONTACTS_PATH_RE.match(parsed.path):
            return self._handle_app_mail_contacts(parsed.query)
        # ---- 课程活动流（ManageBac）----
        if _APP_COURSES_NOTIFICATIONS_RE.match(parsed.path):
            return self._handle_courses_notifications(parsed.query)
        if _APP_COURSES_MESSAGES_RE.match(parsed.path):
            return self._handle_courses_messages(parsed.query)
        disc_match = _APP_COURSES_DISCUSSIONS_RE.match(parsed.path)
        if disc_match:
            # URL 路径段是**百分号编码**的，课程名里带空格/括号时必须解码，
            # 否则按名字找课程永远匹配不上（只会得到一个 400）。
            return self._handle_courses_discussions(
                urllib.parse.unquote(disc_match.group(1)), parsed.query)
        det_match = _APP_COURSES_DETAILS_RE.match(parsed.path)
        if det_match:
            return self._handle_courses_details(
                urllib.parse.unquote(det_match.group(1)), parsed.query)
        # 兜底放最后：`/app/mail/<uid>/` 读一封正文（uid 合法性由 webapp_data 再校验）
        mail_match = _APP_MAIL_PATH_RE.match(parsed.path)
        if mail_match and mail_match.group(1) not in _APP_MAIL_RESERVED:
            return self._handle_app_mail(mail_match.group(1))
        if path.startswith("/proxy/"):
            sub = path[len("/proxy/"):]
            return self._handle_proxy(sub, method="GET")
        # ---- admin 路由 ----
        if path == "/admin" or path.startswith("/admin/"):
            return self._handle_admin_get(path)
        # 静态文件 / HTML 页面
        return self._serve_static(path, parsed.query)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if self._is_phix_path(parsed.path):
            return self._proxy_to_phix()
        if path == "/auth/register":
            return self._handle_register()
        if path == "/auth/login":
            return self._handle_login()
        if path == "/auth/logout":
            return self._handle_logout()
        # ---- 跨站免密登录（一次性码）----
        if path == "/auth/sso/code":
            return self._handle_sso_code()
        if path == "/auth/sso/redeem":
            return self._handle_sso_redeem()
        if path == "/auth/unlock":
            return self._handle_unlock()
        if path == "/auth/password":
            return self._handle_password_change()
        if path == "/feedback":
            return self._handle_feedback()
        if path == "/logs":
            return self._handle_logs_post()
        if path == "/app/ai/chat":                 # Pinghe Launcher 网页端 AI 接口
            return self._handle_app_ai_chat()
        # ---- 标记一封邮件为已读（**唯一**会写邮箱状态的入口；/app/data/ 与 /app/mail/<uid>/ 仍只读）----
        mail_read_match = _APP_MAIL_READ_PATH_RE.match(parsed.path)
        if mail_read_match:
            return self._handle_app_mail_read(mail_read_match.group(1))
        # ---- 发送邮件 ----
        mail_send_match = _APP_MAIL_SEND_PATH_RE.match(parsed.path)
        if mail_send_match:
            return self._handle_app_mail_send()
        if path.startswith("/proxy/"):
            sub = path[len("/proxy/"):]
            return self._handle_proxy(sub, method="POST")
        # ---- admin API 路由 ----
        if path.startswith("/admin/api/"):
            return self._handle_admin_api_post(path)
        return self._json_response(404, {"ok": False, "error": {"code": "not_found", "message": "Not found"}})

    def do_PUT(self):
        """phix 的同步对象写入用 PUT —— 单端口模式下必须透传。"""
        parsed = urllib.parse.urlparse(self.path)
        if self._is_phix_path(parsed.path):
            return self._proxy_to_phix()
        return self._json_response(404, {"ok": False, "error": {"code": "not_found", "message": "Not found"}})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if self._is_phix_path(parsed.path):
            return self._proxy_to_phix()
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/logs/"):
            rec_id = path[len("/logs/"):]
            if rec_id and "/" not in rec_id:
                return self._handle_logs_delete(rec_id)
        return self._json_response(404, {"ok": False, "error": {"code": "not_found", "message": "Not found"}})

    # ---- 静态文件服务 ----

    def _serve_static(self, path: str, query: str = ""):
        # /app/ → app/index.html
        if path == "/app" or path.startswith("/app/"):
            file_path = WEBSITE_DIR / "app" / "index.html"
            if not file_path.exists():
                # fallback: 返回占位（产品全称，不用简称）
                return self._html_response(200, "<!DOCTYPE html><html><body><h1>Pinghe Launcher Web App</h1><p>Coming soon</p></body></html>")
            return self._serve_file(file_path, "text/html")

        # 路由映射
        route_map = {
            "/": "index.html",
            "/about": "about.html",
            "/products/xinlv": "products/xinlv.html",
            "/products/phl": "products/phl.html",
            "/products/hardware": "products/hardware.html",
            "/docs": "docs.html",
            "/support": "support.html",
            "/download": "download.html",
            "/log": "log.html",
            "/login": "login.html",
            "/register": "register.html",
            "/account": "account.html",
        }

        filename = route_map.get(path)
        if filename:
            file_path = WEBSITE_DIR / filename
            if file_path.exists():
                return self._serve_file(file_path, "text/html")
            # 如果文件不存在，返回 404
            return self._html_response(404, "<h1>404 Not Found</h1>")

        # 其他静态资源（css/js/assets）
        safe = path.lstrip("/")
        if ".." in safe:
            return self._html_response(403, "Forbidden")
        file_path = WEBSITE_DIR / safe
        if file_path.is_file():
            ct, _ = mimetypes.guess_type(str(file_path))
            return self._serve_file(file_path, ct or "application/octet-stream",
                                    immutable=("v=" in query))

        return self._html_response(404, "<h1>404 Not Found</h1>")

    def _serve_file(self, file_path: Path, content_type: str, immutable: bool = False):
        """**流式**发送文件。

        旧实现用 `read_bytes()` 把整个文件读进内存 —— 安装包是 171MB，
        几个人同时点下载就把网站内存吃爆。改成 64KB 分块发送。
        另外补 `do_HEAD`：部分下载器/链接检查器先发 HEAD，stdlib 默认回 501。

        **HTML 文件特殊处理**：先读全文做 CMS 替换 + 静态资源版本化再发送
        （HTML 通常 < 100KB，内存开销可忽略），响应带 `Cache-Control: no-cache`。
        非 HTML 仍走流式；带 `?v=` 的静态资源加 `immutable` 长缓存头。
        """
        is_html = "html" in content_type
        if is_html:
            try:
                raw = file_path.read_text(encoding="utf-8")
            except OSError:
                return self._html_response(500, "Internal Server Error")
            rendered = _versionize_html(cms_replace(raw))
            data = rendered.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "none")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if getattr(self, "_head_only", False):
                return
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        try:
            size = file_path.stat().st_size
        except OSError:
            return self._html_response(500, "Internal Server Error")
        self.send_response(200)
        ctype = f"{content_type}; charset=utf-8" if "text" in content_type else content_type
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        # stdlib 的 http.server 不支持 Range；明确告知，免得下载器傻等断点续传
        self.send_header("Accept-Ranges", "none")
        if immutable:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        if getattr(self, "_head_only", False):
            return                      # HEAD：只给头，不给体
        try:
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return                      # 客户端中途取消下载：正常现象，别刷栈

    def do_HEAD(self):
        """只回响应头（下载器与链接检查器会用到）。"""
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    # ---- JSON 响应辅助 ----

    def _json_response(self, status: int, body: dict, extra_headers: dict | None = None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        # 2026-09-12：API 响应一律 no-store。否则浏览器对 /me/ 这类 GET 可能
        # 启发式缓存，登录状态/角色（is_staff）变了页面也不刷新。
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        self.wfile.write(data)

    def _html_response(self, status: int, html: str, extra_headers: dict | None = None):
        data = _versionize_html(html).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        self.wfile.write(data)

    def _bytes_response(self, status: int, data: bytes, content_type: str,
                        filename: str = "", extra_headers: dict | None = None):
        """原样回二进制（附件下载用）。

        `filename` 走 RFC 5987 双写法：`filename=` 给老浏览器（纯 ASCII 兜底），
        `filename*=UTF-8''…` 给现代浏览器（中文附件名不会变成乱码）。
        内容一律 `no-store`：附件正文是私密数据，不留在任何缓存里。
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
            quoted = urllib.parse.quote(filename, safe="")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"; filename*=UTF-8\'\'%s'
                             % (ascii_fallback, quoted))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        self.wfile.write(data)

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _get_cookies(self) -> dict[str, str]:
        """解析 Cookie 头。"""
        raw = self.headers.get("Cookie", "")
        result = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    def _make_cookie_header(self, name: str, value: str, max_age: int,
                            http_only: bool = True, domain: str = "") -> str:
        dom = f"; Domain={domain}" if domain else ""
        hh = "; HttpOnly" if http_only else ""
        return f"{name}={value}; Path=/{dom}{hh}; SameSite=Lax; Max-Age={max_age}"

    def _make_clear_cookie_header(self, name: str, http_only: bool = True,
                                  domain: str = "") -> str:
        dom = f"; Domain={domain}" if domain else ""
        hh = "; HttpOnly" if http_only else ""
        return f"{name}=; Path=/{dom}{hh}; SameSite=Lax; Max-Age=0"

    # ---- 登录提示 cookie（非 httpOnly，**绝不放令牌**）----
    #
    # 只有两个可能的值：HINT_COOKIE=1、HINT_USER_COOKIE=<用户名>。
    # 它的唯一用途是让页面（同域，或将来同父域的 xinlv.phix.ing）在**不请求 /me/**
    # 的情况下判断"这台浏览器当前是登录状态"，从而决定要不要走"跳过去自动兑换"。
    # 令牌、DEK、任何签名材料都**不在这里** —— 它们只在 httpOnly 的 phix_access 里。

    def _hint_headers(self, username: str) -> list[tuple[str, str]]:
        user = (username or "").strip()[:150]
        return [
            ("Set-Cookie", self._make_cookie_header(
                HINT_COOKIE, "1", COOKIE_REFRESH_TTL, http_only=False,
                domain=COOKIE_DOMAIN)),
            ("Set-Cookie", self._make_cookie_header(
                HINT_USER_COOKIE, urllib.parse.quote(user), COOKIE_REFRESH_TTL,
                http_only=False, domain=COOKIE_DOMAIN)),
        ]

    def _hint_clear_headers(self) -> list[tuple[str, str]]:
        return [
            ("Set-Cookie", self._make_clear_cookie_header(
                HINT_COOKIE, http_only=False, domain=COOKIE_DOMAIN)),
            ("Set-Cookie", self._make_clear_cookie_header(
                HINT_USER_COOKIE, http_only=False, domain=COOKIE_DOMAIN)),
        ]

    def _send_hint_headers(self, username: str) -> None:
        for k, v in self._hint_headers(username):
            self.send_header(k, v)

    def _maybe_refresh_session(self) -> tuple[dict | None, dict | None]:
        """读 cookie 会话 + 滑动续期 + 静默刷新。

        返回 (session, extra_headers)：
          session: 与 _get_session 一致（{a,r,e,…} 或 None）
          extra_headers: 需要回写给客户端的额外 Set-Cookie 头（可能为 None）

        滑动续期：只要 access cookie 里 e（exp）在未来 6 小时内过期，
        就重新编码 cookie（refresh TTL 不变）。
        静默刷新：access JWT 过期但 refresh 有效 → 调 phix /auth/refresh 换新 access，
        DEK 从旧 cookie 保留。
        """
        cookies = self._get_cookies()
        raw = cookies.get("phix_access")
        if not raw:
            return None, None
        sess = _decode_cookie_value(raw)
        if not sess:
            return None, None

        now = int(time.time())
        access = sess.get("a", "")
        refresh = sess.get("r", "")
        exp = sess.get("e", 0)
        dek_hex = sess.get("d", "")
        username = sess.get("u", "")

        # --- 静默刷新：access JWT 过期 → 用 refresh 换新的 ---
        if access and exp and now > exp and refresh:
            new_access = _refresh_access(refresh)
            if new_access:
                access = new_access
                sess["a"] = access
                sess["e"] = now + COOKIE_ACCESS_TTL
            else:
                # refresh 也无效 → 真正未登录：清 cookie
                return None, {
                    "Set-Cookie": self._make_clear_cookie_header("phix_access"),
                    "Set-Cookie2": self._make_clear_cookie_header("phix_refresh"),
                }

        # --- 滑动续期：exp 在未来 6 小时内 → 重新编码 cookie ---
        SLIDING_THRESHOLD = 6 * 3600
        new_cookie = None
        if access and refresh and (not exp or (exp - now) < SLIDING_THRESHOLD):
            new_exp = now + COOKIE_ACCESS_TTL
            val = _encode_cookie_value(access, refresh, new_exp, dek_hex, username)
            sess["e"] = new_exp
            new_cookie = f"phix_access={val}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_ACCESS_TTL}"

        return sess, ({"Set-Cookie": new_cookie} if new_cookie else None)

    def _get_session(self) -> dict | None:
        """从 cookie 取会话。返回 {a, r, e} 或 None。"""
        cookies = self._get_cookies()
        raw = cookies.get("phix_access")
        if not raw:
            return None
        return _decode_cookie_value(raw)

    # ---- /me/ ----

    def _handle_me(self):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}})
        access = sess.get("a", "")
        refresh = sess.get("r", "")
        dek_hex = sess.get("d", "")
        status, body, new_access = _phix_request("GET", "auth/me", access_token=access, refresh_token=refresh,
                                                  retry_on_expired=False)
        if new_access:
            # 续期成功，回写 cookie（**保留 DEK**，否则头像/代理全瞎）
            exp = int(time.time()) + COOKIE_ACCESS_TTL
            val = _encode_cookie_value(new_access, refresh, exp, dek_hex)
            hdrs = {"Set-Cookie": f"phix_access={val}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_ACCESS_TTL}"}
            if extra_h: hdrs.update(extra_h)
            return self._json_response(status, body, extra_headers=hdrs)
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            uname = body.get("username", "")
            out = {"username": uname, "avatar": "", "has_session": True,
                   "is_staff": bool(body.get("is_staff")),
                   "is_superuser": bool(body.get("is_superuser")),
                   # 免密（SSO）来的会话一开始没有 DEK：端到端加密的对象读不出明文。
                   # 页面据此提示"输一次密码解锁"（POST /auth/unlock/）。
                   "dek_unlocked": bool(dek_hex)}
            # 尝试读 profile 对象拿头像（DEK 从会话 cookie 取，进程里不留副本）
            ps, pb, _ = _phix_request("GET", "sync/objects/profile", access_token=access, refresh_token=refresh)
            if ps == 200 and isinstance(pb, dict) and pb.get("ok"):
                payload_str = pb.get("payload", "")
                uid = int(body.get("user_id") or _user_id_from_access(access))
                if payload_str and payload_str.startswith("PHIX1.") and dek_hex:
                    payload_str = _decrypt_payload(bytes.fromhex(dek_hex), uid, "profile",
                                                   payload_str) or payload_str
                if payload_str:
                    try:
                        prof = json.loads(payload_str)
                        out["avatar"] = prof.get("avatar", "")
                    except Exception:
                        pass
            return self._json_response(200, out, extra_h)
        return self._json_response(status, body, extra_h)

    # ---- /auth/register/ ----

    def _handle_register(self):
        data = self._read_json_body()
        if not data:
            return self._json_response(400, {"ok": False, "error": {"code": "bad_request", "message": "请求格式错误"}})
        username = data.get("username", "")
        password = data.get("password", "")
        if not username or not password:
            return self._json_response(400, {"ok": False, "error": {"code": "bad_request", "message": "用户名和密码都要填"}})

        mat = _new_client_material(username, password)
        dek = mat.pop("dek")  # 取出 DEK，不发给 phix
        reg_body = {
            "username": username,
            "agree": True,
            "device": "website",
            **mat,
        }
        status, body, _ = _phix_request("POST", "auth/register", body=reg_body)
        if status in (200, 201) and isinstance(body, dict) and body.get("ok"):
            # 注册即视为登录：DEK 只进**签名 cookie**，不落进程内存
            access = body.get("access_token", "")
            refresh = body.get("refresh_token", "")
            exp = int(time.time()) + COOKIE_ACCESS_TTL
            val = _encode_cookie_value(access, refresh, exp, dek.hex())
            ref_val = _encode_cookie_value(access, refresh,
                                           int(time.time()) + COOKIE_REFRESH_TTL, dek.hex())
            data = json.dumps({"ok": True, "username": username}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", self._make_cookie_header("phix_access", val, COOKIE_ACCESS_TTL))
            self.send_header("Set-Cookie", self._make_cookie_header("phix_refresh", ref_val, COOKIE_REFRESH_TTL))
            self._send_hint_headers(username)
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json_response(status, body)

    # ---- /auth/login/ ----

    def _handle_login(self):
        data = self._read_json_body()
        if not data:
            return self._json_response(400, {"ok": False, "error": {"code": "bad_request", "message": "请求格式错误"}})
        username = data.get("username", "")
        password = data.get("password", "")
        if not username or not password:
            return self._json_response(400, {"ok": False, "error": {"code": "bad_request", "message": "用户名和密码都要填"}})

        # 获取用户的密钥材料以确定 kdf_algo 和 auth_salt
        km_status, km_body, _ = _phix_request("POST", "auth/keymaterial", body={"username": username})

        # 检测 phix 是否可达（keymaterial 返回 502 说明上游不可达）
        phix_unreachable = (km_status == 502)

        login_body = {"username": username, "device": "website"}
        if not phix_unreachable and km_status == 200 and isinstance(km_body, dict) and km_body.get("ok"):
            kdf_algo = km_body.get("kdf_algo", "")
            auth_salt = km_body.get("auth_salt", "")
            if kdf_algo == "scrypt-hkdf-v2" and auth_salt:
                ah = e2e.auth_hash_hex(password, auth_salt, kdf_algo)
                login_body["auth_hash"] = ah
            else:
                login_body["password"] = password
        elif not phix_unreachable:
            login_body["password"] = password

        if phix_unreachable:
            return self._json_response(502, {"ok": False, "error": {
                "code": "service_unavailable", "message": "服务暂时不可用，请稍后重试"}})

        status, body, _ = _phix_request("POST", "auth/login", body=login_body)

        # ---- 旧账号迁移分支 ----
        # phix 明确拒绝（401 bad_credentials）且用户名在旧清单里 → 尝试旧密码比对
        err_code = ""
        if isinstance(body, dict):
            err_obj = body.get("error", {})
            if isinstance(err_obj, dict):
                err_code = err_obj.get("code", "")

        legacy_users = _get_legacy_users()
        if status == 401 and err_code == "bad_credentials" and username in legacy_users:
            legacy_info = legacy_users[username]
            old_hash = legacy_info.get("hash", "")
            if _django_check_password(password, old_hash):
                # 旧密码匹配 → 用该口令在 phix 注册新账号
                _audit_log("legacy_migration", username, "attempt")
                mat = _new_client_material(username, password)
                dek = mat.pop("dek")
                reg_body = {
                    "username": username,
                    "agree": True,
                    "device": "官网迁移",
                    **mat,
                }
                reg_status, reg_body_resp, _ = _phix_request("POST", "auth/register", body=reg_body)

                if reg_status in (200, 201) and isinstance(reg_body_resp, dict) and reg_body_resp.get("ok"):
                    # 注册成功 → 同步角色
                    new_uid = reg_body_resp.get("user_id")
                    _sync_legacy_flags(new_uid, legacy_info)
                    _audit_log("legacy_migration", username, f"success uid={new_uid}")

                    # 用注册返回的令牌直接登录
                    access = reg_body_resp.get("access_token", "")
                    refresh = reg_body_resp.get("refresh_token", "")
                    exp = int(time.time()) + COOKIE_ACCESS_TTL
                    val = _encode_cookie_value(access, refresh, exp, dek.hex())
                    ref_val = _encode_cookie_value(access, refresh,
                                                   int(time.time()) + COOKIE_REFRESH_TTL, dek.hex())
                    resp_data = json.dumps({"ok": True, "username": username}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(resp_data)))
                    self.send_header("Set-Cookie", self._make_cookie_header("phix_access", val, COOKIE_ACCESS_TTL))
                    self.send_header("Set-Cookie", self._make_cookie_header("phix_refresh", ref_val, COOKIE_REFRESH_TTL))
                    self._send_hint_headers(username)
                    self.end_headers()
                    self.wfile.write(resp_data)
                    return

                elif reg_status in (200, 201, 400) and isinstance(reg_body_resp, dict):
                    # "已被注册" → 已迁过，用口令算 auth_hash 再登录
                    err2 = reg_body_resp.get("error", {})
                    if isinstance(err2, dict) and "已经被注册" in err2.get("message", ""):
                        _audit_log("legacy_migration", username, "already_registered_retry_login")
                        # 重新获取 keymaterial 并登录
                        km2_status, km2_body, _ = _phix_request("POST", "auth/keymaterial", body={"username": username})
                        login2 = {"username": username, "device": "website"}
                        if km2_status == 200 and isinstance(km2_body, dict) and km2_body.get("ok"):
                            ka = km2_body.get("kdf_algo", "")
                            sa = km2_body.get("auth_salt", "")
                            if ka == "scrypt-hkdf-v2" and sa:
                                login2["auth_hash"] = e2e.auth_hash_hex(password, sa, ka)
                            else:
                                login2["password"] = password
                        else:
                            login2["password"] = password
                        st2, bd2, _ = _phix_request("POST", "auth/login", body=login2)
                        if st2 == 200 and isinstance(bd2, dict) and bd2.get("ok"):
                            access = bd2.get("access_token", "")
                            refresh = bd2.get("refresh_token", "")
                            # 2026-09-12：账号早就存在的人也要补角色（站长/管理员的
                            # 「管理后台」按钮靠这个），否则他们永远是普通用户。
                            _sync_legacy_flags(bd2.get("user_id") or _user_id_from_access(access),
                                               legacy_info)
                            dek_hex = ""
                            km_src2 = km2_body if (km2_status == 200 and isinstance(km2_body, dict) and km2_body.get("ok")) else bd2
                            if isinstance(km_src2, dict) and km_src2.get("key_wrap"):
                                dek2 = _unwrap_dek(km_src2.get("key_wrap", ""), password,
                                                    km_src2.get("kdf_salt", ""), username,
                                                    km_src2.get("kdf_algo", ""))
                                dek_hex = dek2.hex() if dek2 else ""
                            exp = int(time.time()) + COOKIE_ACCESS_TTL
                            val = _encode_cookie_value(access, refresh, exp, dek_hex)
                            ref_val = _encode_cookie_value(access, refresh, int(time.time()) + COOKIE_REFRESH_TTL, dek_hex)
                            resp_data = json.dumps({"ok": True, "username": bd2.get("username", username)}, ensure_ascii=False).encode("utf-8")
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.send_header("Content-Length", str(len(resp_data)))
                            self.send_header("Set-Cookie", self._make_cookie_header("phix_access", val, COOKIE_ACCESS_TTL))
                            self.send_header("Set-Cookie", self._make_cookie_header("phix_refresh", ref_val, COOKIE_REFRESH_TTL))
                            self._send_hint_headers(bd2.get("username", username))
                            self.end_headers()
                            self.wfile.write(resp_data)
                            return
                    # 注册失败但不是"已注册" →  fall through 到原始错误
                # 注册失败 → 返回密码错误（不泄露细节）
                _audit_log("legacy_migration", username, f"register_failed status={reg_status}")

            # 旧密码不匹配或迁移失败 → 返回统一的密码错误
            return self._json_response(401, {"ok": False, "error": {
                "code": "bad_credentials", "message": "账号或密码不对"}})

        # ---- 正常登录成功路径 ----
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            access = body.get("access_token", "")
            refresh = body.get("refresh_token", "")
            # 2026-09-12：**这是最常见的一条路** —— 账号在 phix 里已存在且密码就是原密码，
            # 登录直接在 phix 成功，根本不会走上面两条迁移分支。此时若该用户名在旧账号
            # 清单里（站长/管理员），必须在这里补角色，否则他们永远是普通用户，
            # 「管理后台」按钮不出现。
            _sync_legacy_flags(body.get("user_id") or _user_id_from_access(access),
                               _get_legacy_users().get(username))
            # 解出 DEK：登录响应里有 key_wrap/kdf_salt；v1 账号 kdf_salt 也能解（KEK=MK）。
            # 解不开（异常材料）就放空 —— 网页端降级为"不能读写同步对象"，登录本身不受影响。
            dek_hex = ""
            km_src = km_body if (km_status == 200 and isinstance(km_body, dict)
                                 and km_body.get("ok")) else body
            if isinstance(km_src, dict) and km_src.get("key_wrap"):
                dek = _unwrap_dek(km_src.get("key_wrap", ""), password,
                                  km_src.get("kdf_salt", ""), username,
                                  km_src.get("kdf_algo", ""))
                dek_hex = dek.hex() if dek else ""
            exp = int(time.time()) + COOKIE_ACCESS_TTL
            val = _encode_cookie_value(access, refresh, exp, dek_hex)
            ref_val = _encode_cookie_value(access, refresh, int(time.time()) + COOKIE_REFRESH_TTL, dek_hex)
            cookie_headers = {
                "Set-Cookie": self._make_cookie_header("phix_access", val, COOKIE_ACCESS_TTL),
                "X-Set-Cookie-2": self._make_cookie_header("phix_refresh", ref_val, COOKIE_REFRESH_TTL),
            }
            # Use multi-cookie response
            data = json.dumps({"ok": True, "username": body.get("username", username)}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", self._make_cookie_header("phix_access", val, COOKIE_ACCESS_TTL))
            self.send_header("Set-Cookie", self._make_cookie_header("phix_refresh", ref_val, COOKIE_REFRESH_TTL))
            self._send_hint_headers(body.get("username", username))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json_response(status, body)

    # ---- /auth/logout/ ----

    def _handle_logout(self):
        sess = self._get_session()
        if sess:
            access = sess.get("a", "")
            refresh = sess.get("r", "")
            _phix_request("POST", "auth/logout", access_token=access, refresh_token=refresh)
        data = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Set-Cookie", self._make_clear_cookie_header("phix_access"))
        self.send_header("Set-Cookie", self._make_clear_cookie_header("phix_refresh"))
        for k, v in self._hint_clear_headers():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # ---- 跨站免密登录：一次性 SSO 码 ----
    #
    # 需求原文：「登录心履、Pinghe Launcher 网页端、phix 官网中的任何一个，其它两个都
    # 自动登录」。官网与 /app/ 同域同 cookie，本来就满足；难的是**跨注册域**的心履
    # （xin-lv.com）。跨域只有两条路：一次性码（本节），或把心履挪到 xinlv.phix.ing
    # 让 cookie 域设成 `.phix.ing`（部署方案，见 SSO-跨域与子域方案.md）。
    #
    # 这里的四个端点都只做"搬运"，密码学上的事全在 phix API（api/sso.py）里：
    #   POST /auth/sso/code/    已登录（cookie 会话）→ 换一枚一次性码
    #   POST /auth/sso/redeem/  码 → 本站会话（写 cookie，JSON 返回，与 /auth/login/ 同构）
    #   GET  /auth/sso/enter/   浏览器入口：兑换成功 302 回 next（心履/官网互跳用）
    #   GET  /sso/to-xinlv/     浏览器入口：本已登录 → 带的码 302 跳到心履兑换页
    #
    # **码里没有任何凭据**（无令牌、无 DEK、无用户名），120 秒、单次、绑站点，
    # 且兑换端点要求服务密钥 —— 光捡到码换不走东西。

    def _sso_mint(self, sess: dict, audience: str, next_url: str = "",
                  device: str = "官网") -> tuple[int, dict]:
        """用当前 cookie 会话换一枚一次性码。"""
        status, body, _ = _phix_request(
            "POST", "auth/sso/code",
            body={"audience": audience, "device": device, "next": (next_url or "")[:400]},
            access_token=sess.get("a", ""), refresh_token=sess.get("r", ""))
        return status, body if isinstance(body, dict) else {}

    def _sso_redeem(self, code: str, device: str = "官网 SSO") -> tuple[int, dict]:
        """用码换令牌（服务密钥 + 码，两者缺一不可）。"""
        status, body, _ = _phix_request(
            "POST", "auth/sso/redeem",
            body={"code": code, "site": "phix-site", "device": device},
            service_key=True)
        return status, body if isinstance(body, dict) else {}

    def _write_session_cookies(self, access: str, refresh: str, dek_hex: str,
                               username: str, extra: dict | None = None) -> None:
        """把会话写进两个 cookie（access 短命、refresh 长命），并带上提示 cookie。"""
        exp = int(time.time()) + COOKIE_ACCESS_TTL
        val = _encode_cookie_value(access, refresh, exp, dek_hex, username, extra)
        ref_val = _encode_cookie_value(access, refresh,
                                       int(time.time()) + COOKIE_REFRESH_TTL,
                                       dek_hex, username, extra)
        self.send_header("Set-Cookie",
                         self._make_cookie_header("phix_access", val, COOKIE_ACCESS_TTL))
        self.send_header("Set-Cookie",
                         self._make_cookie_header("phix_refresh", ref_val, COOKIE_REFRESH_TTL))
        self._send_hint_headers(username)

    def _respond_with_session(self, status: int, body: dict, access: str, refresh: str,
                              dek_hex: str, username: str, location: str = "",
                              extra: dict | None = None):
        """写 cookie + 返回 JSON（不是 redirect）。"""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        self._write_session_cookies(access, refresh, dek_hex, username, extra)
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        self.wfile.write(data)

    def _redirect(self, location: str, extra_cookies=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_cookies or []):
            self.send_header(k, v)
        self.end_headers()

    # POST /auth/sso/code/ —— 已登录（cookie）→ 一次性码
    def _handle_sso_code(self):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})
        data = self._read_json_body() or {}
        audience = str(data.get("audience") or "xinlv").strip()
        status, body = self._sso_mint(sess, audience,
                                      str(data.get("next") or ""),
                                      str(data.get("device") or "官网"))
        if status == 200 and body.get("ok"):
            out = dict(body)
            out["redeem_url"] = "/auth/sso/redeem/"
            out["hint"] = "码只在本站后端与目标站后端之间传递，不含任何凭据"
            return self._json_response(200, out, extra_h)
        return self._json_response(status, body or {"ok": False, "error": {
            "code": "sso_failed", "message": "换码失败"}}, extra_h)

    # POST /auth/sso/redeem/ —— 码 → 本站会话（JSON + Set-Cookie）
    def _handle_sso_redeem(self):
        if not _rate_ok("sso_redeem", 30):
            return self._json_response(429, {"ok": False, "error": {
                "code": "rate_limited", "message": "兑换太频繁了，请稍后再试"}})
        data = self._read_json_body() or {}
        code = data.get("code")
        if not isinstance(code, str) or len(code) < 16:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少 code"}})
        status, body = self._sso_redeem(code)
        if status != 200 or not body.get("ok"):
            err_obj = body.get("error") if isinstance(body.get("error"), dict) else {}
            return self._json_response(status if status >= 400 else 401, {
                "ok": False, "error": err_obj or {
                    "code": "sso_failed", "message": "兑换失败"}})
        username = body.get("username", "")
        # **DEK 留空**：码里刻意不携带 DEK，本站也没有口令，所以这是一次
        # "身份会话"——能登、能看个人中心，但读不了端到端加密对象；
        # 要解数据需 POST /auth/unlock/ 输一次口令（口令只用来解 key_wrap）。
        self._respond_with_session(200, {
            "ok": True, "username": username, "user_id": body.get("user_id"),
            "via": "sso", "dek_unlocked": False,
            "sso": body.get("sso", {}),
        }, body.get("access_token", ""), body.get("refresh_token", ""), "", username,
            extra={"sso": 1})

    # GET /auth/sso/enter/?code=...&next=... —— 浏览器入口，收尾是 302
    def _handle_sso_enter(self, query: str):
        qs = urllib.parse.parse_qs(query or "")
        code = (qs.get("code") or [""])[0]
        nxt = (qs.get("next") or [""])[0]
        if not code:
            return self._redirect("/login/?sso_error=missing_code")
        status, body = self._sso_redeem(code, "官网 SSO 跳转")
        if status != 200 or not body.get("ok"):
            return self._redirect("/login/?sso_error=redeem_failed")
        username = body.get("username", "")
        target = _safe_next(nxt) or "/account/"
        cookies = []
        exp = int(time.time()) + COOKIE_ACCESS_TTL
        cookies.append(("Set-Cookie", self._make_cookie_header(
            "phix_access",
            _encode_cookie_value(body.get("access_token", ""),
                                 body.get("refresh_token", ""), exp, "", username),
            COOKIE_ACCESS_TTL)))
        cookies.append(("Set-Cookie", self._make_cookie_header(
            "phix_refresh",
            _encode_cookie_value(body.get("access_token", ""),
                                 body.get("refresh_token", ""),
                                 int(time.time()) + COOKIE_REFRESH_TTL, "", username),
            COOKIE_REFRESH_TTL)))
        cookies.extend(self._hint_headers(username))
        sep = "&" if "?" in target else "?"
        return self._redirect(f"{target}{sep}sso=phix", extra_cookies=cookies)

    # GET /sso/to-xinlv/?next=... —— 官网跳心履：签码 → 302 到心履兑换页
    def _handle_to_xinlv(self, query: str):
        sess = self._get_session()
        qs = urllib.parse.parse_qs(query or "")
        nxt = (qs.get("next") or [""])[0]
        # soft=1：没登录就**照常打开心履**（不把人拦到登录页）——
        # 产品页上的"打开网页端"按钮用这个，免密只是顺带的好处。
        soft = (qs.get("soft") or [""])[0] in ("1", "true", "yes")
        back = _safe_next(nxt) or "/"
        if not sess:
            if soft:
                return self._redirect(XINLV_SITE_URL + "/")
            return self._redirect("/login/?next=" +
                                  urllib.parse.quote("/sso/to-xinlv/"))
        status, body = self._sso_mint(sess, "xinlv", next_url=back,
                                      device="官网 → 心履")
        if status != 200 or not body.get("ok"):
            return self._redirect(f"{XINLV_SITE_URL}/?sso_error=1")
        code = body.get("code", "")
        return self._redirect(
            f"{XINLV_SITE_URL}/phix/sso/?sso={urllib.parse.quote(code)}"
            f"&next={urllib.parse.quote(back)}")

    # ---- POST /auth/unlock/ —— 给"免密登录"来的会话补上 DEK ----
    #
    # 为什么需要：DEK 只能由**口令**解开（KEK ← scrypt(口令)），而一次性码刻意不带任何
    # 凭据，所以 SSO 过来的会话一开始没有 DEK。没有 DEK 时，端到端加密的对象
    # （头像 / 心情 / 日程 / 四平台凭据）读出来还是密文，写进去也不会被加密。
    # 这里让用户**输一次口令**把 DEK 补上：口令只用于本地解 key_wrap，
    # 解出来的 DEK 写回签名 cookie，**进程里不留副本** —— 与正常登录同一套机制。

    def _handle_unlock(self):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})
        if sess.get("d"):
            return self._json_response(200, {"ok": True, "dek_unlocked": True,
                                             "already": True})
        if not _rate_ok("unlock", 10):
            return self._json_response(429, {"ok": False, "error": {
                "code": "rate_limited", "message": "尝试太频繁了，请稍后再试"}})
        username = (sess.get("u") or "").strip()
        if not username:
            # 老 cookie 里没有 "u"：问一次 phix 当前用户是谁（令牌在手，问得着）
            st, me, _ = _phix_request("GET", "auth/me", access_token=sess.get("a", ""),
                                      refresh_token=sess.get("r", ""))
            if st == 200 and isinstance(me, dict):
                username = (me.get("username") or "").strip()
        if not username:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "这个会话里没有用户名，请重新登录后再解锁"}})
        data = self._read_json_body() or {}
        password = data.get("password") or ""
        if not password:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "请输入密码"}})

        km_status, km, _ = _phix_request("POST", "auth/keymaterial",
                                         body={"username": username})
        if km_status != 200 or not isinstance(km, dict) or not km.get("key_wrap"):
            return self._json_response(km_status if km_status >= 400 else 502, {
                "ok": False, "error": {"code": "unreachable",
                                       "message": "暂时拿不到密钥材料，请稍后再试"}})
        dek = _unwrap_dek(km.get("key_wrap", ""), password, km.get("kdf_salt", ""),
                          username, km.get("kdf_algo", ""))
        if dek is None:
            return self._json_response(401, {"ok": False, "error": {
                "code": "bad_credentials", "message": "密码不对"}})
        # 持有性证明：密钥材料里的 key_check 只有真持有 DEK 才解得开
        proof = _prove_dek(dek, username, km.get("key_check", ""))
        if km.get("key_check", "").startswith("PHIX1.") and not proof:
            return self._json_response(401, {"ok": False, "error": {
                "code": "bad_credentials", "message": "密码不对"}})

        access = sess.get("a", "")
        refresh = sess.get("r", "")
        exp = int(time.time()) + COOKIE_ACCESS_TTL
        val = _encode_cookie_value(access, refresh, exp, dek.hex(), username)
        ref_val = _encode_cookie_value(access, refresh,
                                       int(time.time()) + COOKIE_REFRESH_TTL,
                                       dek.hex(), username)
        data_out = json.dumps({"ok": True, "dek_unlocked": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data_out)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", self._make_cookie_header(
            "phix_access", val, COOKIE_ACCESS_TTL))
        self.send_header("Set-Cookie", self._make_cookie_header(
            "phix_refresh", ref_val, COOKIE_REFRESH_TTL))
        self.end_headers()
        self.wfile.write(data_out)

    # ---- /auth/password/（改 phix 登录密码）----

    def _handle_password_change(self):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})
        access = sess.get("a", "")
        refresh = sess.get("r", "")
        dek_hex = sess.get("d", "")

        data = self._read_json_body()
        if not data:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "请求格式错误"}})
        current = data.get("current_password") or ""
        newpw = data.get("new_password") or ""
        confirm = data.get("confirm_password") or ""
        if not current or not newpw or not confirm:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "请填写完整"}})
        if newpw != confirm:
            return self._json_response(400, {"ok": False, "error": {
                "code": "password_mismatch", "message": "两次新密码不一致"}})
        if len(newpw) < 6:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "新密码至少 6 位"}})

        # 取当前用户名
        ms, mbody, _ = _phix_request("GET", "auth/me", access_token=access,
                                     refresh_token=refresh)
        if ms != 200 or not isinstance(mbody, dict) or not mbody.get("ok"):
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "会话无效"}})
        username = mbody.get("username", "")

        # 取公开密钥材料
        km_status, km_body, _ = _phix_request("POST", "auth/keymaterial",
                                              body={"username": username})
        if km_status != 200 or not isinstance(km_body, dict) or not km_body.get("ok"):
            return self._json_response(502, {"ok": False, "error": {
                "code": "proxy_error", "message": "无法获取密钥材料，请稍后重试"}})
        kdf_algo = km_body.get("kdf_algo", "")
        auth_salt = km_body.get("auth_salt", "")
        old_key_wrap = km_body.get("key_wrap", "")
        old_kdf_salt = km_body.get("kdf_salt", "")
        key_check = km_body.get("key_check", "")

        dek = bytes.fromhex(dek_hex) if dek_hex else None
        if dek is None:
            return self._json_response(400, {"ok": False, "error": {
                "code": "no_dek", "message": "网页端拿不到数据密钥，请到客户端修改密码"}})

        # 校验当前密码：用旧 key_wrap 解出的 DEK 必须等于会话里那把
        cur_dek = _unwrap_dek(old_key_wrap, current, old_kdf_salt, username, kdf_algo)
        if cur_dek is None or cur_dek != dek:
            return self._json_response(401, {"ok": False, "error": {
                "code": "bad_credentials", "message": "当前密码不正确"}})

        # 派生 old/new 凭证（v2 账号发 AuthHash，绝不发口令原文）
        if kdf_algo == "scrypt-hkdf-v2" and auth_salt:
            old_cred = {"old_auth_hash": e2e.auth_hash_hex(current, auth_salt, kdf_algo)}
            new_cred = {"new_auth_hash": e2e.auth_hash_hex(newpw, auth_salt, kdf_algo)}
        else:
            old_cred = {"old_password": current}
            new_cred = {"new_password": newpw}

        # 用新密码重新包裹 DEK（新的 kdf_salt；key_check 沿用原 key_check_plain）
        new_kdf_salt = secrets.token_hex(16)
        new_key_wrap = _wrap_dek(dek, newpw, new_kdf_salt, username, kdf_algo)
        dek_proof = _prove_dek(dek, username, key_check)
        if dek_proof is None:
            return self._json_response(400, {"ok": False, "error": {
                "code": "dek_proof_failed", "message": "无法证明数据密钥，请到客户端修改密码"}})

        chg = {
            "kdf_algo": kdf_algo,
            "kdf_salt": new_kdf_salt,
            "key_wrap": new_key_wrap,
            "key_check": key_check,
            "key_mode": km_body.get("key_mode", "password"),
            "dek_proof": dek_proof,
        }
        chg.update(old_cred)
        chg.update(new_cred)

        status, body, new_access = _phix_request("POST", "auth/password", body=chg,
                                                 access_token=access,
                                                 refresh_token=refresh)
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            return self._json_response(200, {"ok": True,
                                             "message": "密码已更新，其它设备需重新登录"})

        # 失败：给中文可读提示，不裸抛异常
        msg = "修改失败，请稍后重试"
        if isinstance(body, dict):
            err_obj = body.get("error", {})
            if isinstance(err_obj, dict):
                code = err_obj.get("code", "")
                raw_msg = err_obj.get("message", "")
                if status == 401 or code in ("unauthorized", "bad_credentials"):
                    msg = "当前密码不正确"
                elif "DEK" in raw_msg:
                    msg = "密码验证失败，请确认当前密码正确"
                elif raw_msg:
                    msg = raw_msg
        return self._json_response(status, {"ok": False, "error": {
            "code": "password_change_failed", "message": msg}})

    # ---- /proxy/<path>/ ----

    def _handle_proxy(self, sub_path: str, method: str = "POST"):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}})
        access = sess.get("a", "")
        refresh = sess.get("r", "")

        body = self._read_json_body() if method == "POST" else None
        # phix sync objects use PUT for writes; translate POST → PUT for sync/objects paths
        phix_method = method
        if method == "POST" and sub_path.startswith("sync/objects/") and not sub_path.endswith("/batch"):
            phix_method = "PUT"
        dek_hex = sess.get("d", "") if sess else ""
        dek = bytes.fromhex(dek_hex) if dek_hex else None
        uid = _user_id_from_access(access) if access else 0
        # 如果写同步对象且 payload 是明文，用会话里的 DEK 加密成 PHIX1 信封
        if phix_method == "PUT" and isinstance(body, dict) and "payload" in body:
            payload_val = body.get("payload", "")
            if isinstance(payload_val, str) and not payload_val.startswith("PHIX1."):
                if dek:
                    obj_name = sub_path.replace("sync/objects/", "").rstrip("/")
                    body["payload"] = _encrypt_payload(dek, uid, obj_name, payload_val)
                elif sess.get("sso"):
                    # **免密（SSO）会话且还没解锁**：手里没有 DEK。此时若照原样写下去，
                    # 这条数据会以**明文**落到云端（而其它端期待的是 PHIX1 密文信封）。
                    # 宁可明确报错，也别把明文悄悄写进端到端加密的对象里。
                    return self._json_response(409, {"ok": False, "error": {
                        "code": "dek_locked",
                        "message": "这是免密登录的会话，端到端加密还没解锁："
                                   "请先在个人中心输入一次密码（POST /auth/unlock/）再修改数据。"}})
        status, resp, new_access = _phix_request(phix_method, sub_path, body=body,
                                                  access_token=access, refresh_token=refresh)
        # 解密 GET 响应的 payload（如果有的话）
        if method == "GET" and sub_path.startswith("sync/objects/") and status == 200:
            if isinstance(resp, dict) and resp.get("ok") and isinstance(resp.get("payload"), str):
                if dek:
                    obj_name = sub_path.replace("sync/objects/", "").rstrip("/")
                    decrypted = _decrypt_payload(dek, uid, obj_name, resp["payload"])
                    if decrypted is not None:
                        resp["payload"] = decrypted
        if new_access:
            exp = int(time.time()) + COOKIE_ACCESS_TTL
            val = _encode_cookie_value(new_access, refresh, exp, dek_hex)
            extra = {"Set-Cookie": f"phix_access={val}; Path=/; HttpOnly; SameSite=Lax; Max-Age={COOKIE_ACCESS_TTL}"}
            return self._json_response(status, resp, extra_headers=extra)
        return self._json_response(status, resp, extra_h)

    # ---- POST /app/ai/chat/（Pinghe Launcher 网页端 AI）----

    def _ai_load_object(self, name: str, sess: dict):
        """读一个同步对象并解密成本地 dict → (status, doc|None)。

        对象不存在 / 解密失败 / 内容不是 JSON 对象 → doc 为 None（**当成空**，绝不 500）。
        DEK 只用这一次：来自签名 cookie，进程里不留副本。
        """
        access = sess.get("a", "")
        refresh = sess.get("r", "")
        dek_hex = sess.get("d", "")
        status, body, _ = _phix_request("GET", f"sync/objects/{name}",
                                        access_token=access, refresh_token=refresh)
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            return status, None
        payload = body.get("payload")
        if not isinstance(payload, str) or not payload:
            return status, None
        if payload.startswith("PHIX1."):
            if not dek_hex:
                return status, None
            try:
                dek = bytes.fromhex(dek_hex)
            except ValueError:
                return status, None
            plain = _decrypt_payload(dek, _user_id_from_access(access), name, payload)
            if plain is None:
                return status, None       # 解密失败（口令不符/对象损坏）→ 当空
            payload = plain
        try:
            doc = json.loads(payload)
        except Exception:  # noqa: BLE001
            return status, None
        return status, doc if isinstance(doc, dict) else None

    def _ai_me(self, access: str, refresh: str):
        """校验会话并取用户名（用户名只进审计日志）。返回 (username, status)。"""
        status, body, _ = _phix_request("GET", "auth/me", access_token=access, refresh_token=refresh)
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            return str(body.get("username") or ""), 200
        return "", status

    def _handle_app_ai_chat(self):
        """给 Pinghe Launcher 网页端回答一个问题：只读用户自己的同步数据。

        顺序：413（超大请求体）→ 401（未登录）→ 400（参数）→ 429（限流）→ 401（会话失效）
              → 409（没配 AI）→ 502/200。
        """
        # 请求体上限：与 /logs/ 一样先看 Content-Length 再读体，别把超大 body 读进内存
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > AI_BODY_MAX_BYTES:
            return self._json_response(413, {"ok": False, "error": {
                "code": "too_large",
                "message": f"请求体过大（上限 {AI_BODY_MAX_BYTES // 1024} KB）"}})

        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})

        data = self._read_json_body()
        if not isinstance(data, dict):
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "请求格式错误"}})
        question = data.get("question")
        question = question.strip() if isinstance(question, str) else ""
        if not question:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "问题不能为空"}})
        if len(question) > AI_QUESTION_MAX:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request",
                "message": f"问题太长（最多 {AI_QUESTION_MAX} 字）"}})

        access = sess.get("a", "")
        refresh = sess.get("r", "")
        uid = _user_id_from_access(access) if access else 0

        # 限流：每个用户每分钟 ≤ 10 次（沿用站点既有的进程内滑动窗口，不引入新依赖）
        if not _rate_ok(f"app_ai:{uid or 'anon'}", AI_RATE_LIMIT_PER_MIN):
            return self._json_response(429, {"ok": False, "error": {
                "code": "rate_limited", "message": "太频繁了，请稍后再试"}})

        username, me_status = self._ai_me(access, refresh)
        if me_status in (401, 403):
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "会话已失效，请重新登录"}})
        if me_status != 200:
            return self._json_response(502, {"ok": False, "error": {
                "code": "upstream_error", "message": "暂时读不到你的账号信息，请稍后重试"}})

        # 用户自己的 AI 配置（端到端加密的同步对象；只在本次请求里解密使用）
        ai_status, ai_doc = self._ai_load_object("settings.ai", sess)
        if ai_status in (401, 403):
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "会话已失效，请重新登录"}})
        cfg = _ai_config_from_doc(ai_doc)
        problem = _ai_config_problem(cfg)
        if problem == "not_configured":
            return self._json_response(409, {"ok": False, "error": {
                "code": "ai_not_configured",
                "message": "还没有可用的 AI 服务商：请到「个人中心 → 密码管理」添加一个"
                           "（服务商 + 密钥 + 模型，随账号同步过来）"}})
        if problem == "no_base_url":
            return self._json_response(409, {"ok": False, "error": {
                "code": "ai_not_configured",
                "message": "这个服务商还没有填服务商地址（base_url）：请到「个人中心 → 密码管理」补上，再回来提问"}})
        if problem == "local":
            # 桌面客户端的本地模型：服务器够不着用户本机
            return self._json_response(502, {"ok": False, "error": {
                "code": "upstream_error",
                "message": "这个服务商是本地模型（provider: local），网页端够不着："
                           "请用桌面客户端；或到「个人中心 → 密码管理」添加一个公网可访问的服务商"}})
        if problem == "protocol":
            return self._json_response(502, {"ok": False, "error": {
                "code": "upstream_error",
                "message": f"这个服务商的协议是 {cfg.get('protocol')}，网页端暂时不支持"
                           "（目前支持 openai 与 anthropic；请换一个或在桌面客户端使用）"}})

        # 用户数据：**实时抓取优先，抓不到的平台回退同步快照**。
        # 同步对象先解出来当回退底稿（对象取不到/解不开一律当空，不 500）；
        # 再把 settings.accounts（解密后的平台账号）交给可注入的抓取回调。
        docs = {}
        for name in AI_CONTEXT_OBJECTS:
            _, doc = self._ai_load_object(name, sess)
            if doc is not None:
                docs[name] = doc

        _, accounts_doc = self._ai_load_object("settings.accounts", sess)
        accounts = accounts_doc.get("accounts") if isinstance(accounts_doc, dict) else None
        if not isinstance(accounts, dict) or not accounts:
            accounts = None
        fetch_school = (lambda accts: _ai_live_fetch(accts, user_id=str(uid))) if accounts else None
        ctx = _ai_build_context(docs, accounts=accounts, fetch_school=fetch_school)

        messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}]
        if ctx["text"]:
            messages.append({"role": "system", "content": ctx["text"]})
        messages.extend(_ai_clean_history(data.get("history")))
        messages.append({"role": "user", "content": question})

        ok, answer, code, message = _ai_call_upstream(cfg, messages)

        # 审计：只记「谁问了 / 哪个服务商与模型 / 上下文多少字符 / 是否截断 / 各平台走的是实时还是快照」——
        # **不记问题原文，也不记回答**（用户隐私不进日志）。
        sources = ctx.get("sources") or {}
        _audit_log(username or f"uid:{uid}", "app/ai/chat",
                   f"provider={_ai_clean(cfg.get('name'), 60)}"
                   f" protocol={_ai_clean(cfg.get('protocol'), 20)}"
                   f" model={_ai_clean(cfg.get('model'), 80)}"
                   f" objects={','.join(ctx['objects']) or '-'}"
                   f" chars={ctx['chars']} truncated={int(bool(ctx['truncated']))}"
                   f" live={','.join(p for p in AI_LIVE_PLATFORMS if sources.get(p) == 'live') or '-'}"
                   f" sync={','.join(p for p in AI_LIVE_PLATFORMS if sources.get(p) == 'sync') or '-'}"
                   f" ok={int(bool(ok))}")

        if not ok:
            return self._json_response(502, {"ok": False, "error": {
                "code": code or "upstream_error", "message": message or "AI 服务暂时不可用"}})
        return self._json_response(200, {
            "ok": True,
            "answer": answer,
            "model": cfg.get("model") or "",
            # 前端可以显示"由 XXX 回答"（名字；用户没起名时是「供应商 N」）
            "provider": cfg.get("name") or "",
            "protocol": cfg.get("protocol") or "openai",
            "context": {"objects": ctx["objects"], "chars": ctx["chars"],
                        "truncated": bool(ctx["truncated"])},
        })

    # ---- /app/data/ 与 /app/mail/<uid>/（Pinghe Launcher 网页端的平台数据）----
    #
    # 数据流：会话 cookie 里的 DEK → 解密同步对象 settings.accounts（只读这一个对象）
    #        → webapp_data 用**解密出来的平台账号**去登录并抓取
    #        → 返回给前端（口令绝不回前端、绝不进日志/缓存）。
    # 状态码：401 未登录/会话失效；409 没配平台账号；502 抓取失败（errors 里有中文原因）。

    def _app_load_accounts(self, sess: dict):
        """解密 `settings.accounts` → (accounts, status, error_body)。

        与 `_ai_load_object` 同一条路径（同一个 DEK、同一个对象名）。
        会话失效 → 401；对象不存在/解不开/里面没 accounts 段 → 409。
        """
        status, doc = self._ai_load_object("settings.accounts", sess)
        if status in (401, 403):
            return None, 401, {"ok": False, "error": {
                "code": "unauthorized", "message": "会话已失效，请重新登录"}}
        accounts = doc.get("accounts") if isinstance(doc, dict) else None
        if not isinstance(accounts, dict):
            return None, 409, {"ok": False, "error": {
                "code": "accounts_not_configured",
                "message": wd.ACCOUNTS_NOT_CONFIGURED_MESSAGE}}
        return accounts, 200, None

    def _handle_app_data(self, query: str = ""):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})

        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return self._json_response(status, error_body, extra_h)

        force = "force=1" in (query or "")
        uid = _user_id_from_access(sess.get("a", ""))

        started = time.monotonic()
        try:
            payload = wd.fetch_all(accounts, force=force, user_id=str(uid))
        except Exception:  # noqa: BLE001  抓取层兜底，绝不让站点 500
            _audit_log(f"uid:{uid}", "app/data", "fetch_failed")
            return self._json_response(502, {"ok": False, "error": {
                "code": "upstream_error", "message": "抓取失败，请稍后重试"}})

        # 审计只记「谁、命中缓存与否、哪些平台失败」——不记账号名与口令
        errors = payload.get("meta", {}).get("errors", {})
        _audit_log(f"uid:{uid}", "app/data",
                   f"cache={payload.get('meta', {}).get('cache')}"
                   f" elapsed={time.monotonic() - started:.2f}s"
                   f" errors={','.join(errors) or '-'}")
        body = {"ok": True}
        body.update(payload)
        return self._json_response(200, body, extra_h)

    def _handle_app_mail(self, uid: str):
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})

        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return self._json_response(status, error_body, extra_h)

        user_id = _user_id_from_access(sess.get("a", ""))
        try:
            mail = wd.fetch_mail_body(accounts, uid)
        except Exception as exc:  # noqa: BLE001
            code = wd.http_status_for(exc)
            message = wd.error_message(exc)
            _audit_log(f"uid:{user_id}", "app/mail", f"status={code}")
            return self._json_response(code, {"ok": False, "error": {
                "code": "not_found" if code == 404 else "upstream_error",
                "message": message}}, extra_h)

        if mail is None:
            _audit_log(f"uid:{user_id}", "app/mail", "status=404")
            return self._json_response(404, {"ok": False, "error": {
                "code": "not_found", "message": wd.NOT_FOUND_MESSAGE}}, extra_h)
        _audit_log(f"uid:{user_id}", "app/mail", "status=200")
        return self._json_response(200, {"ok": True, "mail": mail}, extra_h)

    def _handle_app_mail_attachment(self, uid: str, index: str):
        """GET /app/mail/<uid>/attachments/<index>/ —— 下载某一封邮件里的一个附件。

        回**原始字节**（不是 JSON），带 `Content-Disposition: attachment`；
        中文文件名走 RFC 5987。404 语义与 `/app/mail/<uid>/` 一致
        （uid/序号不对 = 找不到这封或这个附件）。
        """
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}}, extra_h)

        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return self._json_response(status, error_body, extra_h)

        user_id = _user_id_from_access(sess.get("a", ""))
        try:
            got = wd.fetch_mail_attachment(accounts, uid, index)
        except Exception as exc:  # noqa: BLE001
            code = wd.http_status_for(exc)
            message = wd.error_message(exc)
            _audit_log(f"uid:{user_id}", "app/mail/attachment", f"status={code}")
            return self._json_response(code, {"ok": False, "error": {
                "code": "not_found" if code == 404 else "upstream_error",
                "message": message}}, extra_h)

        if got is None:
            _audit_log(f"uid:{user_id}", "app/mail/attachment", "status=404")
            return self._json_response(404, {"ok": False, "error": {
                "code": "not_found",
                "message": "找不到这个附件（可能已被删除，或附件太大）"}}, extra_h)

        _audit_log(f"uid:{user_id}", "app/mail/attachment",
                   f"status=200 bytes={got.get('size')}")
        return self._bytes_response(200, got["payload"],
                                    got.get("content_type") or "application/octet-stream",
                                    got.get("filename") or "attachment", extra_h)

    def _handle_app_mail_contacts(self, query: str = ""):
        """GET /app/mail/contacts/ —— 通讯录（从收件箱 + 已发送的邮件头收割）。

        返回 `{"ok": true, "contacts": [{"name","email","count"}], "meta": {...}}`。
        只读：全程 `select(readonly=True)` + `BODY.PEEK`，不动任何邮件的已读状态。
        收割一次要扫几百封邮件头，所以按用户缓存 30 分钟（`?force=1` 绕过）。
        """
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}}, extra_h)

        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return self._json_response(status, error_body, extra_h)

        user_id = _user_id_from_access(sess.get("a", ""))
        force = "force=1" in (query or "")
        cache_key = f"mail_contacts:{user_id}"
        if not force:
            cached = _get_cached(cache_key)
            if cached is not None:
                return self._json_response(200, {"ok": True, "contacts": cached,
                                                 "meta": {"cache": True}}, extra_h)
        try:
            contacts = wd.fetch_contacts(accounts)
        except Exception as exc:  # noqa: BLE001
            code = wd.http_status_for(exc)
            message = wd.error_message(exc)
            _audit_log(f"uid:{user_id}", "app/mail/contacts", f"status={code}")
            return self._json_response(code, {"ok": False, "error": {
                "code": "not_found" if code == 404 else "upstream_error",
                "message": message}}, extra_h)
        _set_cache(cache_key, contacts, CONTACTS_CACHE_TTL)
        _audit_log(f"uid:{user_id}", "app/mail/contacts", f"ok=1 count={len(contacts)}")
        return self._json_response(200, {"ok": True, "contacts": contacts,
                                         "meta": {"cache": False}}, extra_h)

    def _handle_app_mail_read(self, uid: str):
        """POST /app/mail/<uid>/read/ —— 把**这一封**邮件标记为已读。

        唯一的写操作入口（`/app/data/` 与 `/app/mail/<uid>/` 仍然是只读的）。
        成功：`200 {"ok": true, "mail": {"uid": "...", "unread": false}}`；
        失败语义与 `/app/mail/<uid>/` 完全一致：401 未登录 / 404 找不到这封 / 502 抓取失败。
        """
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})

        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return self._json_response(status, error_body, extra_h)

        user_id = _user_id_from_access(sess.get("a", ""))
        try:
            marked = wd.mark_mail_seen(accounts, uid, user_id=str(user_id))
        except Exception as exc:  # noqa: BLE001
            code = wd.http_status_for(exc)
            message = wd.error_message(exc)
            _audit_log(f"uid:{user_id}", "app/mail/read", f"status={code}")
            return self._json_response(code, {"ok": False, "error": {
                "code": "not_found" if code == 404 else "upstream_error",
                "message": message}}, extra_h)

        _audit_log(f"uid:{user_id}", "app/mail/read", "status=200")
        return self._json_response(200, {"ok": True, "mail": marked}, extra_h)

    def _parse_mail_send_body(self):
        """解析发信请求体：**同时支持 JSON 与 multipart/form-data**。

        为什么两种都收：
          * JSON —— 老调用方（本机直连桥、既有脚本/测试）还在用，必须继续能跑；
          * multipart —— 浏览器上传附件走它。`<input type="file">` + `FormData`
            是原生能力：文件**不进 JS 内存**，中文名按 RFC 7578 自带编码。
        解析 multipart 用标准库 `email.message_from_bytes`（把请求体前面补一个
        Content-Type 头就能当邮件解析），**不用 `cgi` 模块** —— 它在 Python 3.13
        已经被删除，而本站跑在 3.14 上。

        返回 `(fields, attachments)`；附件是 `[{"filename","content_type","data"}]`。
        解析失败抛 `ValueError`（调用方回 400）。
        """
        from email import message_from_bytes, policy

        length = int(self.headers.get("Content-Length") or 0)
        ctype = (self.headers.get("Content-Type") or "").strip()

        if ctype.lower().startswith("multipart/"):
            if length <= 0:
                raise ValueError("请求体为空")
            if length > wd.MAIL_UPLOAD_MAX_BYTES:
                raise ValueError(
                    "附件总大小超过上限（最多 %d MB）"
                    % (wd.MAIL_SEND_MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)))
            # 解析交给模块级纯函数（可单测），见 parse_multipart_mail 的说明
            return parse_multipart_mail(ctype, self.rfile.read(length))

        data = self._read_json_body()
        if not isinstance(data, dict):
            raise ValueError("请求格式错误")
        return data, []

    def _handle_app_mail_send(self):
        """POST /app/mail/send/ —— 通过 SMTP 发送邮件。

        参数（JSON 或 multipart/form-data 都行）：
        `to` / `cc` / `subject` / `body_text` / `in_reply_to`，外加**多个附件文件**
        （multipart 里字段名不限，带 filename 的部分即附件）。
        成功 200：`{"ok": true, "mail": {"to": [...], "subject": "...",
                    "sent_at": "...", "attachments": [{"filename","size"}]}}`
        400 参数错 / 401 未登录 / 409 没配邮箱 / 413 太大 / 429 限流 / 502 发送失败
        """
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})

        # 限流：每个用户每分钟 ≤ 5 封
        access = sess.get("a", "")
        uid = _user_id_from_access(access) if access else 0
        if not _rate_ok(f"app_mail_send:{uid or 'anon'}", MAIL_SEND_RATE_LIMIT_PER_MIN):
            return self._json_response(429, {"ok": False, "error": {
                "code": "rate_limited", "message": "发送太频繁了，请稍后再试"}})

        try:
            data, attachments = self._parse_mail_send_body()
        except ValueError as exc:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": str(exc)}})

        to_raw = data.get("to") or ""
        subject = data.get("subject") or ""
        body_text = data.get("body_text") or ""
        cc = data.get("cc") or ""
        in_reply_to = data.get("in_reply_to") or ""

        if not isinstance(to_raw, str) or not to_raw.strip():
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少收件人地址"}})
        if not isinstance(subject, str) or not subject.strip():
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少邮件主题"}})
        if not isinstance(body_text, str) or not body_text.strip():
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少邮件正文"}})

        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return self._json_response(status, error_body, extra_h)

        # 检查邮箱账号是否真的配了（_app_load_accounts 只检查三平台之一有凭据即可）
        mail_section = accounts.get("mail") if isinstance(accounts, dict) else None
        mail_user = wd.account_username(mail_section) if isinstance(mail_section, dict) else ""
        mail_secret = wd.account_secret(mail_section, prefer_authcode=True) if isinstance(mail_section, dict) else ""
        if not mail_user or not mail_secret:
            return self._json_response(409, {"ok": False, "error": {
                "code": "accounts_not_configured",
                "message": wd.MAIL_NOT_CONFIGURED_MESSAGE}}, extra_h)

        try:
            result = wd.send_mail(
                accounts,
                to=to_raw,
                cc=str(cc),
                subject=str(subject),
                body_text=str(body_text),
                in_reply_to=str(in_reply_to),
                attachments=attachments,
            )
        except Exception as exc:  # noqa: BLE001
            # PlatformError → 502 + 中文消息（认证失败/连接失败/参数错/其他）
            if isinstance(exc, wd.PlatformError):
                msg = wd._clean_message(str(exc))
                # 按错误类型分类状态码
                text = str(exc).lower()
                if "太大" in text or "总大小" in text:
                    return self._json_response(413, {"ok": False, "error": {
                        "code": "too_large", "message": msg}}, extra_h)
                if "缺少" in text or "非法" in text or "超过" in text or "太长" in text:
                    return self._json_response(400, {"ok": False, "error": {
                        "code": "bad_request", "message": msg}}, extra_h)
                return self._json_response(502, {"ok": False, "error": {
                    "code": "upstream_error",
                    "message": "发送失败：" + msg}}, extra_h)
            return self._json_response(502, {"ok": False, "error": {
                "code": "upstream_error",
                "message": "发送失败：请稍后重试"}}, extra_h)

        _audit_log(f"uid:{uid}", "app/mail/send",
                   f"ok=1 recipients={len(result.get('to', [])) + len(result.get('cc', []))}"
                   f" attachments={len(result.get('attachments') or [])}")
        return self._json_response(200, {"ok": True, "mail": result}, extra_h)

    # ---- 课程活动流（ManageBac 通知 / 消息 / 讨论 / 详情）----
    #
    # 这四个端点的公共前置（登录态 + 账号就位）抽成 _courses_preflight()；
    # 错误映射统一在 _courses_error()：登录失败 → 502 中文提示、课程 ID 非法 → 400、
    # 课程不属于当前学生 → 403。ManageBac 侧的状态（partial / unavailable /
    # authentication_required …）**原样透传**给前端，绝不把「读不出来」画成「没有数据」。

    def _courses_preflight(self, session_holder):
        """返回 (accounts, mb_section, extra_h) 或 (None, response, None)。"""
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            return None, self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}}, extra_h or {}), None
        accounts, status, error_body = self._app_load_accounts(sess)
        if error_body is not None:
            return None, self._json_response(status, error_body, extra_h), None
        mb_section = accounts.get("managebac") if isinstance(accounts, dict) else None
        if not isinstance(mb_section, dict):
            return None, self._json_response(409, {"ok": False, "error": {
                "code": "accounts_not_configured",
                "message": "还没有填 ManageBac 账号：请到「个人中心 → 密码管理」补齐后重试"}}, extra_h), None
        return sess, mb_section, extra_h

    def _courses_fail(self, exc, user_id: str, target: str, extra_h):
        """课程类接口的统一错误映射（凭据 / 参数 / 权限 / 上游）。"""
        import webapp_managebac as _mb
        code, http = "upstream_error", 502
        message = "抓取失败，请稍后重试"
        if isinstance(exc, ValueError) and "invalid_class_id" in str(exc):
            code, http, message = "bad_request", 400, "课程编号不合法"
        elif isinstance(exc, PermissionError):
            code, http, message = "forbidden", 403, "这门课程不在你的课程列表里"
        elif isinstance(exc, _mb.LoginError):
            code, http, message = "login_failed", 502, "ManageBac 登录失败：请到「个人中心 → 密码管理」更新账号密码"
        else:
            text = str(exc).lower()
            if "timeout" in text or "timed out" in text:
                message = "连 ManageBac 超时了，请稍后重试"
            elif "connection" in text or "max retries" in text:
                message = "连不上 ManageBac，请稍后重试"
        _audit_log(f"uid:{user_id}", target, f"status={http} {type(exc).__name__}")
        return self._json_response(http, {"ok": False, "error": {"code": code, "message": message}}, extra_h)

    def _handle_courses_notifications(self, query: str = ""):
        """GET /app/courses/notifications/ —— 通知 / 公告流（最多 100 条）。"""
        sess, mb_section, extra_h = self._courses_preflight(None)
        if sess is None:
            return mb_section
        user_id = _user_id_from_access(sess.get("a", ""))
        force = "force=1" in (query or "")
        cache_key = f"courses_notif:{user_id}"
        if not force:
            cached = _get_cached(cache_key)
            if cached is not None:
                body = {"ok": True, "meta": {"cache": True}}
                body.update(cached)
                return self._json_response(200, body, extra_h)
        try:
            email, password, base_url = wd.managebac_config(mb_section)
            result = mb.fetch_notifications(email, password, base_url)
        except Exception as exc:  # noqa: BLE001
            return self._courses_fail(exc, user_id, "app/courses/notifications", extra_h)
        _set_cache(cache_key, result, COURSE_CACHE_TTL)
        count = len(result.get("notifications") or [])
        _audit_log(f"uid:{user_id}", "app/courses/notifications",
                   f"ok=1 count={count} status={result.get('status')}")
        body = {"ok": True, "meta": {"cache": False}}
        body.update(result)
        return self._json_response(200, body, extra_h)

    def _handle_courses_messages(self, query: str = ""):
        """GET /app/courses/messages/ —— 作业 / 考试 / 成绩 / 讨论聚合流（最多 100 条）。"""
        sess, mb_section, extra_h = self._courses_preflight(None)
        if sess is None:
            return mb_section
        user_id = _user_id_from_access(sess.get("a", ""))
        force = "force=1" in (query or "")
        cache_key = f"courses_msg:{user_id}"
        if not force:
            cached = _get_cached(cache_key)
            if cached is not None:
                body = {"ok": True, "meta": {"cache": True}}
                body.update(cached)
                return self._json_response(200, body, extra_h)
        try:
            email, password, base_url = wd.managebac_config(mb_section)
            result = mb.fetch_messages(email, password, base_url)
        except Exception as exc:  # noqa: BLE001
            return self._courses_fail(exc, user_id, "app/courses/messages", extra_h)
        _set_cache(cache_key, result, COURSE_CACHE_TTL)
        count = len(result.get("messages") or [])
        _audit_log(f"uid:{user_id}", "app/courses/messages",
                   f"ok=1 count={count} status={result.get('status')}")
        body = {"ok": True, "meta": {"cache": False}}
        body.update(result)
        return self._json_response(200, body, extra_h)

    def _handle_courses_discussions(self, course_id: str, query: str = ""):
        """GET /app/courses/<course_id>/discussions/ —— 某课程的讨论列表。"""
        sess, mb_section, extra_h = self._courses_preflight(None)
        if sess is None:
            return mb_section
        user_id = _user_id_from_access(sess.get("a", ""))
        force = "force=1" in (query or "")
        cache_key = f"courses_disc:{user_id}:{course_id}"
        if not force:
            cached = _get_cached(cache_key)
            if cached is not None:
                body = {"ok": True, "meta": {"cache": True}}
                body.update(cached)
                return self._json_response(200, body, extra_h)
        try:
            email, password, base_url = wd.managebac_config(mb_section)
            result = mb.fetch_discussions(email, password, base_url, course_id)
        except Exception as exc:  # noqa: BLE001
            return self._courses_fail(exc, user_id, "app/courses/discussions", extra_h)
        _set_cache(cache_key, result, COURSE_CACHE_TTL)
        count = len(result.get("messages") or [])
        _audit_log(f"uid:{user_id}", "app/courses/discussions",
                   f"ok=1 course={course_id} count={count} status={result.get('status')}")
        body = {"ok": True, "meta": {"cache": False}}
        body.update(result)
        return self._json_response(200, body, extra_h)

    def _handle_courses_details(self, course_id: str, query: str = ""):
        """GET /app/courses/<course_id>/details/ —— 课程详情（成员 / 成绩 / 资源 / 统计）。"""
        sess, mb_section, extra_h = self._courses_preflight(None)
        if sess is None:
            return mb_section
        user_id = _user_id_from_access(sess.get("a", ""))
        force = "force=1" in (query or "")
        cache_key = f"courses_det:{user_id}:{course_id}"
        if not force:
            cached = _get_cached(cache_key)
            if cached is not None:
                return self._json_response(200, {"ok": True, "details": cached,
                                                 "meta": {"cache": True}}, extra_h)
        try:
            email, password, base_url = wd.managebac_config(mb_section)
            details = mb.fetch_class_details(email, password, base_url, course_id)
        except Exception as exc:  # noqa: BLE001
            return self._courses_fail(exc, user_id, "app/courses/details", extra_h)
        _set_cache(cache_key, details, COURSE_CACHE_TTL)
        _audit_log(f"uid:{user_id}", "app/courses/details",
                   f"ok=1 course={course_id} status={details.get('status')}")
        return self._json_response(200, {"ok": True, "details": details,
                                         "meta": {"cache": False}}, extra_h)


    # ---- /download/list/ ----

    def _handle_download_list(self):
        EXPECTED = [
            "xinlv-windows.zip", "xinlv-windows-setup.exe", "xinlv-macos.dmg",
            "xinlv-android.apk",
            "phl-windows-setup.exe", "phl-macos.dmg",
            # PLL Windows 自 2026-09-21 起改用 WiX 构建的 **MSI 安装包**：
            # 只出安装版，不再提供便携版（绿色版 exe）。
            "phllite-windows-setup.msi", "phllite-macos.dmg",
        ]
        MEDIA_DOWNLOADS.mkdir(parents=True, exist_ok=True)
        existing = {f.name for f in MEDIA_DOWNLOADS.iterdir() if f.is_file()}
        files = []
        for name in EXPECTED:
            files.append({
                "name": name,
                "available": name in existing,
                "url": f"/media/downloads/{name}" if name in existing else None,
            })
        return self._json_response(200, {"files": files})

    # ---- /admin/ 鉴权与页面 ----

    def _admin_check_staff(self, json_mode: bool = False) -> tuple[str | None, dict | None]:
        """检查当前请求是否有 staff 权限。

        返回 (username, me_body) 表示通过；否则已发 HTTP 响应，调用方应 return。
        json_mode=True 时返回 JSON 错误而非 HTML/重定向。
        """
        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if json_mode:
                self._json_response(401, {"ok": False, "error": {"code": "unauthorized",
                                          "message": "未登录"}}, extra_h)
            else:
                self.send_response(302)
                self.send_header("Location", "/login/?next=/admin/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return None, None
        access = sess.get("a", "")
        refresh = sess.get("r", "")
        status, body, new_access = _phix_request("GET", "auth/me",
                                                  access_token=access,
                                                  refresh_token=refresh)
        if new_access:
            access = new_access
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            if json_mode:
                self._json_response(401, {"ok": False, "error": {"code": "unauthorized",
                                          "message": "会话无效"}})
            else:
                self.send_response(302)
                self.send_header("Location", "/login/?next=/admin/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return None, None
        if not body.get("is_staff"):
            if json_mode:
                self._json_response(403, {"ok": False, "error": {"code": "forbidden",
                                          "message": "需要 staff 权限"}})
            else:
                self._html_response(403, _ADMIN_403_HTML)
            return None, None
        return body.get("username", ""), body

    def _handle_admin_get(self, path: str):
        """GET /admin/* —— 页面或 API（均需 staff）。"""
        is_api = path.startswith("/admin/api/")
        result = self._admin_check_staff(json_mode=is_api)
        if result[0] is None:
            return
        username = result[0]

        # API 子路由
        if path == "/admin/api/users":
            return self._admin_api_users()
        if path == "/admin/api/content":
            return self._admin_api_content_get()
        if path == "/admin/api/feedback":
            return self._admin_api_feedback_list()

        # 页面路由

        if path == "/admin" or path == "/admin/":
            return self._serve_admin_page("admin/index.html", username)
        if path == "/admin/users":
            return self._serve_admin_page("admin/users.html", username)
        if path == "/admin/content":
            return self._serve_admin_page("admin/content.html", username)
        if path == "/admin/feedback":
            return self._serve_admin_page("admin/feedback.html", username)
        return self._html_response(404, "<h1>404 Not Found</h1>")

    def _serve_admin_page(self, rel_path: str, username: str):
        """渲染 admin 页面（注入用户名）。"""
        file_path = WEBSITE_DIR / rel_path
        if not file_path.exists():
            return self._html_response(500, "<h1>Admin page missing</h1>")
        raw = file_path.read_text(encoding="utf-8")
        rendered = cms_replace(raw).replace("{{admin_username}}", username)
        return self._html_response(200, rendered)

    def _handle_admin_api_post(self, path: str):
        """POST /admin/api/* —— JSON API（需 staff）。"""
        result = self._admin_check_staff(json_mode=True)
        if result[0] is None:
            return
        operator = result[0]

        if path == "/admin/api/user/flags":
            return self._admin_api_user_flags(operator)
        if path == "/admin/api/content/save":
            return self._admin_api_content_save(operator)
        if path == "/admin/api/content/reset":
            return self._admin_api_content_reset(operator)
        if path == "/admin/api/feedback/handle":
            return self._admin_api_feedback_handle(operator)
        return self._json_response(404, {"ok": False, "error": {"code": "not_found"}})

    # ---- admin API 实现 ----

    def _admin_api_users(self):
        """GET /admin/api/users —— 列用户（从 phix 拉）。"""
        status, body = _phix_admin_request("GET", "admin/users")
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            return self._json_response(status, body)
        return self._json_response(200, {"ok": True, "users": body.get("users", [])})

    def _admin_api_user_flags(self, operator: str):
        """POST /admin/api/user/flags —— 改 staff/active。"""
        data = self._read_json_body()
        if not data or "user_id" not in data:
            return self._json_response(400, {"ok": False, "error": {"code": "bad_request",
                                              "message": "缺少 user_id"}})
        target_id = int(data["user_id"])
        # 获取自己的 user_id（从会话 JWT）
        sess = self._get_session()
        my_uid = _user_id_from_access(sess.get("a", "")) if sess else 0

        # 不允许停用自己或撤自己 staff
        if target_id == my_uid:
            if "is_active" in data and not data["is_active"]:
                return self._json_response(400, {"ok": False, "error": {
                    "code": "self_deactivate", "message": "不能停用自己的账号"}})
            if "is_staff" in data and not data["is_staff"]:
                return self._json_response(400, {"ok": False, "error": {
                    "code": "self_destaff", "message": "不能撤销自己的 staff 权限"}})

        # 不允许动 superuser（除非操作者也是 superuser）
        # 先查目标用户信息
        st, users_resp = _phix_admin_request("GET", "admin/users")
        if st == 200 and isinstance(users_resp, dict) and users_resp.get("ok"):
            target_info = None
            my_info = None
            for u in users_resp.get("users", []):
                if u.get("id") == target_id:
                    target_info = u
                if u.get("id") == my_uid:
                    my_info = u
            if target_info and target_info.get("is_superuser") and not (my_info and my_info.get("is_superuser")):
                return self._json_response(403, {"ok": False, "error": {
                    "code": "superuser_protected",
                    "message": "只有 superuser 才能修改其他 superuser"}})

        flags = {}
        if "is_staff" in data:
            flags["is_staff"] = bool(data["is_staff"])
        if "is_active" in data:
            flags["is_active"] = bool(data["is_active"])
        if not flags:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "没有要改的字段"}})

        status, body = _phix_admin_request("POST", f"admin/user/{target_id}/flags", flags)
        if status == 200 and isinstance(body, dict) and body.get("ok"):
            action_parts = [f"{k}={v}" for k, v in flags.items()]
            _audit_log(operator, f"user:{target_id}", " ".join(action_parts))
        return self._json_response(status, body)

    def _admin_api_content_get(self):
        """GET /admin/api/content —— 返回所有 key + defaults + overrides。"""
        data = _cms_load()
        all_keys = set(data["defaults"].keys()) | set(data["overrides"].keys())
        items = []
        for k in sorted(all_keys):
            items.append({
                "key": k,
                "default": data["defaults"].get(k, ""),
                "override": data["overrides"].get(k),
                "effective": data["overrides"].get(k, data["defaults"].get(k, "")),
            })
        return self._json_response(200, {"ok": True, "items": items})

    def _admin_api_content_save(self, operator: str):
        """POST /admin/api/content/save —— 保存 override。"""
        data = self._read_json_body()
        if not data or "key" not in data or "value" not in data:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少 key/value"}})
        key = data["key"]
        value = data["value"]
        cms_data = _cms_load()
        cms_data["overrides"][key] = value
        _cms_save_overrides(cms_data["overrides"])
        _audit_log(operator, f"cms:{key}", "save")
        return self._json_response(200, {"ok": True})

    def _admin_api_content_reset(self, operator: str):
        """POST /admin/api/content/reset —— 清掉某个 key 的 override。"""
        data = self._read_json_body()
        if not data or "key" not in data:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少 key"}})
        key = data["key"]
        cms_data = _cms_load()
        cms_data["overrides"].pop(key, None)
        _cms_save_overrides(cms_data["overrides"])
        _audit_log(operator, f"cms:{key}", "reset")
        return self._json_response(200, {"ok": True})

    # ---- 反馈（/feedback/）----

    def _handle_feedback(self):
        """POST /feedback/ —— 公开端点，任何人可提交反馈。"""
        data = self._read_json_body()
        if not data or not data.get("desc", "").strip():
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "请填写描述"}})
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone as _tz
        ts = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%S")
        fid = f"{ts}-{secrets.token_hex(3)}"
        record = {
            "id": fid,
            "type": data.get("type", "其他"),
            "name": data.get("name", ""),
            "desc": data["desc"].strip(),
            "submitted_at": datetime.now(_tz.utc).isoformat(),
            "handled": False,
            "handled_at": None,
        }
        fpath = FEEDBACK_DIR / f"{fid}.json"
        fpath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._json_response(200, {"ok": True, "id": fid})

    def _admin_api_feedback_list(self):
        """GET /admin/api/feedback —— 列出所有反馈（最近 200 条）。"""
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(FEEDBACK_DIR.glob("*.json"), reverse=True)[:200]
        items = []
        unhandled = 0
        for f in files:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                items.append(rec)
                if not rec.get("handled"):
                    unhandled += 1
            except Exception:
                pass
        return self._json_response(200, {"ok": True, "items": items,
                                          "unhandled": unhandled, "total": len(items)})

    def _admin_api_feedback_handle(self, operator: str):
        """POST /admin/api/feedback/handle —— 标记已处理。"""
        data = self._read_json_body()
        if not data or "id" not in data:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "缺少 id"}})
        fid = data["id"]
        fpath = FEEDBACK_DIR / f"{fid}.json"
        if not fpath.exists():
            return self._json_response(404, {"ok": False, "error": {
                "code": "not_found", "message": "反馈不存在"}})
        try:
            rec = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            return self._json_response(500, {"ok": False, "error": {
                "code": "corrupt", "message": "反馈文件损坏"}})
        from datetime import datetime, timezone as _tz
        rec["handled"] = True
        rec["handled_at"] = datetime.now(_tz.utc).isoformat()
        fpath.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        _audit_log(operator, f"feedback:{fid}", "handle")
        return self._json_response(200, {"ok": True})

    # ---- 维修日志 /logs/ ----

    def _handle_logs_get(self):
        """GET /logs/ —— 公开可读，按日期倒序。"""
        logs = _logs_load()
        logs.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
        return self._json_response(200, {"ok": True, "logs": logs})

    def _handle_logs_post(self):
        """POST /logs/ —— 需登录；新增一条维修记录（before/after 为 data URL 或 null）。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length > LOG_BODY_MAX_BYTES:
            return self._json_response(413, {"ok": False, "error": {
                "code": "too_large", "message": "请求体过大"}})

        sess, extra_h = self._maybe_refresh_session()
        if not sess:
            if extra_h:
                return self._json_response(401, {"ok": False, "error": {"code": "unauthorized", "message": "未登录"}}, extra_h)
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "未登录"}})
        access = sess.get("a", "")
        refresh = sess.get("r", "")
        ms, mbody, _ = _phix_request("GET", "auth/me", access_token=access,
                                     refresh_token=refresh)
        if ms != 200 or not isinstance(mbody, dict) or not mbody.get("ok"):
            return self._json_response(401, {"ok": False, "error": {
                "code": "unauthorized", "message": "会话无效"}})
        username = str(mbody.get("username") or "未知用户")

        ip = self.client_address[0] if self.client_address else "?"
        if not _rate_ok(f"logs_post:{ip}", _RATE_LIMITS["logs_post"]):
            return self._json_response(429, {"ok": False, "error": {
                "code": "rate_limited", "message": "操作太频繁，请稍后再试"}})

        data = self._read_json_body()
        if data is None:
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_request", "message": "请求格式错误"}})

        date_str = str(data.get("date") or "").strip()
        handler = str(data.get("handler") or "").strip()
        item = str(data.get("item") or "").strip()
        if not _valid_log_date(date_str):
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_date", "message": "日期格式应为 YYYY-MM-DD 且不能是未来日期"}})
        if not (1 <= len(handler) <= 60):
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_handler", "message": "受理人需为 1–60 字"}})
        if not (1 <= len(item) <= 60):
            return self._json_response(400, {"ok": False, "error": {
                "code": "bad_item", "message": "受理物需为 1–60 字"}})

        rec_id = secrets.token_hex(10)
        before_url = after_url = None
        MEDIA_LOGS.mkdir(parents=True, exist_ok=True)
        for key in ("before", "after"):
            raw = data.get(key)
            if raw in (None, ""):
                continue
            if not isinstance(raw, str) or not raw.startswith("data:"):
                return self._json_response(400, {"ok": False, "error": {
                    "code": "bad_image", "message": "图片需为 data URL"}})
            mime, blob = _decode_data_url(raw)
            if mime not in LOG_ALLOWED_IMAGE_TYPES:
                return self._json_response(400, {"ok": False, "error": {
                    "code": "bad_image_type", "message": "仅支持 jpeg/png/webp 图片"}})
            if blob is None or len(blob) > LOG_IMAGE_MAX_BYTES:
                return self._json_response(413, {"ok": False, "error": {
                    "code": "image_too_large", "message": "单张图片不能超过 1.5 MB"}})
            ext = LOG_ALLOWED_IMAGE_TYPES[mime]
            fname = f"{rec_id}-{key}{ext}"
            try:
                (MEDIA_LOGS / fname).write_bytes(blob)
            except OSError:
                return self._json_response(500, {"ok": False, "error": {
                    "code": "write_failed", "message": "图片保存失败"}})
            url = f"/media/logs/{fname}"
            if key == "before":
                before_url = url
            else:
                after_url = url

        from datetime import datetime, timezone
        record = {
            "id": rec_id,
            "date": date_str,
            "handler": handler,
            "item": item,
            "before": before_url,
            "after": after_url,
            "created_by": username,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _logs_append(record)
        _audit_log(username, f"log:{rec_id}", "create")
        return self._json_response(200, {"ok": True, "id": rec_id, "record": record})

    def _handle_logs_delete(self, rec_id: str):
        """DELETE /logs/<id> —— 仅 staff；删除记录并清理图片文件。"""
        result = self._admin_check_staff(json_mode=True)
        if result[0] is None:
            return
        operator = result[0]

        ip = self.client_address[0] if self.client_address else "?"
        if not _rate_ok(f"logs_delete:{ip}", _RATE_LIMITS["logs_delete"]):
            return self._json_response(429, {"ok": False, "error": {
                "code": "rate_limited", "message": "操作太频繁，请稍后再试"}})

        with _logs_lock:
            target = next((r for r in _logs_read_unlocked() if r.get("id") == rec_id), None)
        if target is None:
            return self._json_response(404, {"ok": False, "error": {
                "code": "not_found", "message": "记录不存在"}})
        _delete_log_images(target)
        if not _logs_remove(rec_id):
            return self._json_response(404, {"ok": False, "error": {
                "code": "not_found", "message": "记录不存在"}})
        _audit_log(operator, f"log:{rec_id}", "delete")
        return self._json_response(200, {"ok": True})


# ---- Admin 403 页面 ----

_ADMIN_403_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>403 · phix</title>
<link rel="stylesheet" href="/static/site.css"></head>
<body style="display:grid;place-items:center;min-height:80vh;background:var(--phix-mist)">
<div style="text-align:center;padding:40px">
<h1 style="color:var(--phix-violet);font-size:2rem;margin:0 0 16px">无权访问</h1>
<p style="color:var(--ink-2)">此区域仅限管理人员访问。</p>
<a href="/" class="btn btn--primary" style="margin-top:24px;display:inline-flex">返回首页</a>
</div></body></html>"""


def main():
    parser = argparse.ArgumentParser(description="phix 官网服务器")
    parser.add_argument("--port", type=int, default=8940)
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址。默认 127.0.0.1（只本机）；"
                             "要让局域网/反代访问就传 0.0.0.0")
    args = parser.parse_args()

    # 确保 media/downloads 目录存在
    MEDIA_DOWNLOADS.mkdir(parents=True, exist_ok=True)
    # 维修日志图片目录
    MEDIA_LOGS.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), SiteHandler)
    print(f"phix 官网已启动 → http://{args.host}:{args.port}"
          + ("（局域网可访问）" if args.host == "0.0.0.0" else "（仅本机）"))
    print(f"  phix API 透传： http://{args.host}:{args.port}/api/v1/ping")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
