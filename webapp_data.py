r"""官网服务端平台抓取层：解密后的 `settings.accounts` → 三个平台的实时数据。

给 Pinghe Launcher 网页端（`/app/`）用。设计要点（逐条对应验收要求）：

* **接口冻结**（别的代理会软引用）：
  - `fetch_all(accounts, *, force=False) -> dict`
  - `fetch_mail_body(accounts, uid) -> dict | None`（mail dict 里带 `body_text` 与**逐字原样**的 `body_html`）
  - `mark_mail_seen(accounts, uid, *, user_id="-") -> dict`（**唯一会写邮箱状态的入口**：
    只把这一封加 `\Seen`，并把缓存里那一封就地改成已读；抓取本身仍然全程 `BODY.PEEK`、只读）
* **邮件正文不消毒**：`body_html` 就是 MIME 里 `text/html` 那一部分的**原样字节**
  （**不做任何改写**：不剥标签、不过滤属性、不动 `<style>`、不重写 URL、不补 target/rel）。
  唯一的结构性防线在**前端**：正文渲染进 `<iframe sandbox="allow-same-origin">`
  —— 该 sandbox **不带 `allow-scripts`**，所以邮件里的脚本永远不会执行。
  **这一条不可删**：去掉它就等于把任意 HTML 直接执行在用户会话里。
* **只读凭据**：本模块只接收一个 accounts dict，**不读文件、不写文件**；
  口令只在本次请求的内存里用，绝不写日志、不进缓存、不回前端。
* **缓存**：**分两档** —— ManageBac / 邮箱每用户 5 分钟（`force=True` 绕过）；
  EduPage 单独 30 分钟（`EDUPAGE_CACHE_TTL_SECONDS`），因为它的抓取成本明显更高
  （一次登录 + 整周课表），按 5 分钟算等于每点一次「刷新」都得等。
  缓存里**只有抓取结果**（无凭据）。
  站点是 ThreadingHTTPServer：缓存用 RLock 保护 + 每用户一把锁，避免并发击穿。
* **超时**：ManageBac / 邮箱沿用整批 20 秒硬预算（它们本来就快）；
  **EduPage 单独 90 秒预算**，绝不和上面共用 20 秒。任一平台异常
  **不影响**其它平台；所有异常都翻译成可读中文，绝不把 traceback / 原始响应抛给前端。
  EduPage 尤其要**按原因分类**（凭据不对 / 需要两步验证 / 连不上 / 抓取超时 / 缺依赖），
  不能把「账号密码错」也笼统说成「连不上平台服务器」——那会把用户带偏。
* **EduPage 不阻塞首屏**：EduPage 没有可用缓存时，请求**立刻返回**
  （`meta.pending == ["edupage"]`、`meta.errors.edupage` = 「正在抓取…请稍后点刷新」、
  `edupage.lessons` 为空），抓取交给**后台线程**；同一用户同一缓存键同时只允许一个
  后台任务。抓完写进 30 分钟缓存，之后点「刷新」（`?force=1`）或前端自动重试即可拿到。
* **降级**：`bs4` / `edupage-api` 没装时只让对应平台报“缺少依赖”，站点照常起。

返回结构（`fetch_all` 不含外层 `ok`，由 server.py 补）::

    {"edupage":   {"lessons": [...], "selected": [...]},
     "managebac": {"courses": [...], "tasks": [...]},
     "mail":      {"unread": 3, "recent": [...]},
     "meta": {"fetched_at": ISO8601, "cache": "hit|miss",
              "accounts": {...账号名...}, "errors": {...中文原因...},
              "pending": ["edupage"]      # 只在有平台在后台抓时出现
              }}
"""
from __future__ import annotations

import concurrent.futures
import email
import email.header
import email.utils
import html as html_mod
import imaplib
import importlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("webapp_data")
if not logger.handlers:                       # 被 server.py 的 logging 一并接管
    logger.addHandler(logging.NullHandler())

# ---- 常量（接口相关的数字都放这里，测试会按名字引用）----

CACHE_TTL_SECONDS = 300.0     # ManageBac / 邮箱：每用户缓存 5 分钟
#: **失败批次的缓存时长**。一次超时/登录失败如果也按 5 分钟缓存，用户点「刷新」
#: 只会拿到刚写进去的空结果 —— 现象就是「邮箱空了五分钟，怎么刷都没用」。
#: 失败档只存 20 秒，下一次刷新就能立刻重试。
SHORT_FAIL_TTL = 20.0
#: EduPage 单独一档缓存：抓一次成本高（登录 + 整周课表），5 分钟太短
EDUPAGE_CACHE_TTL_SECONDS = 1800.0    # 30 分钟
PLATFORM_TIMEOUT = 20.0       # ManageBac / 邮箱单平台抓取总超时（秒）——**不变**
#: EduPage 单独给足预算：登录 + 一次整周课表请求。快路（currenttt 一次拿整周）
#: 实测约 5 秒；慢回退（库自身逐条查 dbi）可能几十秒，所以给它更大的闸门。
EDUPAGE_TIMEOUT = 90.0        # EduPage 单平台抓取总超时（秒）
EDUPAGE_HTTP_TIMEOUT = 30.0   # EduPage 内部单次 HTTP 请求超时
#: 后台抓失败后的冷却：冷却期内不重登（避免每次请求都朝学校服务器打一次登录）
EDUPAGE_RETRY_COOLDOWN = 30.0
HTTP_TIMEOUT = 12.0           # 平台内部单次 HTTP 请求超时（ManageBac）
MB_TOTAL_BUDGET = 16.0        # ManageBac 逐课抓取的总预算（留给平台 20 秒闸门）
IMAP_TIMEOUT = 18.0           # IMAP socket 超时
#: 邮箱整批预算。**比 ManageBac 宽**：用户要求「所有邮件都抓下来」，
#: 邮箱大的账号首轮要拉很多邮件头；拉到之后进 5 分钟缓存，后续都是秒回。
#: ManageBac 自己另有 `MB_TOTAL_BUDGET`（16 秒），不会被这里放大。
#: 前端 `/app/data/` 的超时是 90 秒，所以这里留到 75 秒仍有余量。
MAIL_TIMEOUT = 75.0           # 邮箱单平台抓取总超时（秒）
#: 一次 `UID FETCH` 取多少封的邮件头。**这是「全量抓取」不超时的关键**：
#: 逐封 FETCH（1 封 1 个往返）在几百封的邮箱上必然打穿预算，必须成批取。
MAIL_FETCH_CHUNK = 60
#: 只取这三个头字段。**这是真机实测出来的**：完整 `HEADER` 里塞满了收信链路
#: （Received / DKIM / SPF / X-* 反垃圾头），638 封拉完整头要 48 秒以上，
#: 直接把邮箱段打过预算；我们全程只用 From / Subject / Date，取这三样就够了。
MAIL_HEADER_FIELDS = "(FROM SUBJECT DATE)"
#: 单次回给前端的邮件条数上限（超出只在这里截断，并在 `partial` 里如实说明）。
MAIL_FULL_LIMIT = 1000
#: 前多少封预加载正文 + 附件清单（用户要求「最近十条点进去立刻出来」）。
MAIL_PRELOAD_COUNT = 10
#: 预加载那一小段的**墙钟上限**（秒）。预加载只是锦上添花，
#: 绝不允许它把「邮件头列表」这件事拖过整批预算（实测真机单封 0.5 秒，
#: 10 封约 5 秒；给 8 秒足够，超了就停手，剩下的等用户点开时按需加载）。
MAIL_PRELOAD_BUDGET = 8.0
MAIL_RECENT_LIMIT = 10        # 旧字段：只为兼容既有引用（已不用于截断）
MAIL_BODY_MAX_CHARS = 20000   # 正文长度上限（回前端前截断）
PLATFORMS = ("edupage", "managebac", "mail")
#: 跟着请求同步抓的平台（快）；EduPage 走后台线程，见 `_edupage_result`
BATCH_PLATFORMS = ("managebac", "mail")

DEFAULT_EDUPAGE_SUBDOMAIN = "pingheschool"
DEFAULT_MANAGEBAC_BASE_URL = "https://shph.managebac.cn"
DEFAULT_IMAP_HOST = "imap.qiye.163.com"
DEFAULT_SMTP_HOST = "smtp.qiye.163.com"
IMAP_PORT = 993
SMTP_PORT = 465

#: 邮件发送限制
MAIL_SEND_MAX_RECIPIENTS = 20
MAIL_SUBJECT_MAX_CHARS = 200
MAIL_BODY_TEXT_MAX_CHARS = 100_000
#: 附件限额。数值**对齐 PHL 客户端**（`electron/mail-client.cjs`：
#: MAX_SEND_ATTACHMENTS=20 / MAX_TOTAL_ATTACHMENT_BYTES=20MiB），
#: 免得同一个文件在客户端能发、在网页端却被拒。
MAIL_SEND_MAX_ATTACHMENTS = 20
MAIL_SEND_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAIL_SEND_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
#: 单次请求体上限（multipart 上传用）：附件总限额 + 表单文字 + 编码余量。
MAIL_UPLOAD_MAX_BYTES = MAIL_SEND_MAX_TOTAL_ATTACHMENT_BYTES + 2 * 1024 * 1024

#: IMAP 端口可用环境变量覆盖 —— 只给测试/内网调试用（生产不设这个变量）
#: 账号里的 `imap_host` 仍是主机名；端口没有写在 accounts 结构里，故走环境变量。
IMAP_PORT = int(os.environ.get("PHIX_WEBAPP_IMAP_PORT") or IMAP_PORT)

#: 依赖缺失时的统一口径（前端按这个字符串展示，别改）
MISSING_DEP_MESSAGE = "服务端缺少依赖，暂时无法抓取"
TIMEOUT_MESSAGE = "抓取超时（超过 20 秒），请稍后重试"
LOGIN_FAILED_MESSAGE = "登录失败：账号或密码不对"
UNREACHABLE_MESSAGE = "连不上平台服务器，请稍后重试"
ACCOUNTS_NOT_CONFIGURED_MESSAGE = (
    "还没有填平台账号：请到「个人中心 → 密码管理」补齐后重试")
NOT_FOUND_MESSAGE = "找不到这封邮件（可能已被删除或移动）"
MAIL_NOT_CONFIGURED_MESSAGE = (
    "还没有填邮箱账号：请到「个人中心 → 密码管理」补齐后重试")
MAIL_SEND_AUTH_FAILED = "邮箱授权码不对，请到「个人中心 → 密码管理」更新邮箱授权码"
MAIL_SEND_CONNECT_FAILED = "连不上邮件服务器，请稍后重试"
MAIL_SEND_REJECTED = "收件人地址被邮件服务器拒绝"
MAIL_SEND_UNKNOWN_ERROR = "发送失败，请稍后重试"

#: ---- EduPage 专用口径（**按原因分类**，不要把凭据错误兜成「连不上」）----
#: 缺库时 `_edupage_module()` 先返回 None → 走 MISSING_DEP_MESSAGE；
#: 这里再给一条更具体的说法，让用户看懂「是服务端没装库」而不是自己的网络问题。
EDUPAGE_DEP_MESSAGE = "服务端缺少 edupage-api 依赖"
EDUPAGE_CREDENTIALS_MESSAGE = (
    "EduPage 账号或密码不对：请到「个人中心 → 密码管理」更新 EduPage 的用户名与密码")
EDUPAGE_TFA_MESSAGE = "EduPage 需要两步验证，网页端暂时无法完成，请在客户端登录一次"
#: EduPage **正在后台抓**（首抓实测 2.5~6.6 秒）：这不是错误，前端按中性提示展示 + 给「刷新」
EDUPAGE_PENDING_MESSAGE = "EduPage 正在抓取课表（约 5 秒），请稍后点「刷新」"
#: EduPage **抓取超时**（专门文案，和通用 20 秒那条区分开）
EDUPAGE_TIMEOUT_MESSAGE = "EduPage 抓取太慢/超时（约 5 秒），已放后台继续抓，请稍后刷新"


def edupage_unknown_message(exc: Exception | None) -> str:
    """其它 EduPage 异常：只报**类名**（不回 traceback、不回原始响应）。"""
    name = type(exc).__name__ if exc is not None else "UnknownError"
    return f"EduPage 抓取失败：{_clean_message(name) or 'UnknownError'}"


_UID_RE = re.compile(r"^[0-9]{1,12}$")

#: imaplib 的错误类（`IMAP4.error`，IMAP4_SSL 是它的子类）
_IMAP_ERROR = getattr(imaplib, "IMAP4", imaplib.IMAP4_SSL).error

#: 「这封邮件不在了」的 IMAP NO 文本特征（各家服务器措辞不同，都收进来）
_IMAP_MISSING_HINTS = ("no such message", "not found", "does not exist",
                       "no matching messages", "invalid uidl", "message not found")


class PlatformError(Exception):
    """平台级失败：message 直接面向用户（中文、不含凭据、不含 traceback）。"""


class DependencyMissing(PlatformError):
    pass


class LoginFailed(PlatformError):
    pass


def _mail_fetch_error(exc: Exception) -> "PlatformError":
    """单封邮件 FETCH 失败：确实没有 → 404（MailNotFound），否则当上游故障。"""
    text = str(exc).lower()
    if any(hint in text for hint in _IMAP_MISSING_HINTS):
        return MailNotFound(NOT_FOUND_MESSAGE)
    return _classify(exc)


class Unreachable(PlatformError):
    pass


class MailNotFound(PlatformError):
    pass


def _language_error(kind: str, exc: Exception | None = None) -> str:
    """把内部异常翻译成给用户看的中文原因。"""
    if kind == "timeout":
        return TIMEOUT_MESSAGE
    if kind == "dependency":
        return MISSING_DEP_MESSAGE
    if kind == "login":
        return LOGIN_FAILED_MESSAGE
    if kind == "unreachable":
        return UNREACHABLE_MESSAGE
    if isinstance(exc, PlatformError):
        return _clean_message(str(exc)) or "抓取失败，请稍后重试"
    return "抓取失败，请稍后重试"


def _clean_message(text: str) -> str:
    """错误摘要：压空白、去控制字符、截断（绝不带回原始响应/栈）。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = "".join(ch for ch in text if ch >= " " or ch == "\t")
    return text[:200]


# ============================================================ 凭据取值（兼容两套字段名）

def _sect(accounts: dict | None, key: str) -> dict:
    if not isinstance(accounts, dict):
        return {}
    value = accounts.get(key)
    return value if isinstance(value, dict) else {}


def account_username(section: dict | None) -> str:
    """账号名：`username` 优先，其次 `email`（两套变体都兼容）。"""
    if not isinstance(section, dict):
        return ""
    for key in ("username", "email"):
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def account_secret(section: dict | None, *, prefer_authcode: bool = False) -> str:
    """口令：邮箱优先 `authcode`，其它平台用 `password`。"""
    if not isinstance(section, dict):
        return ""
    keys = ("authcode", "password") if prefer_authcode else ("password", "authcode")
    for key in keys:
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def edupage_config(section: dict | None) -> tuple[str, str, str]:
    user = account_username(section)
    secret = account_secret(section)
    sub = ""
    if isinstance(section, dict):
        raw = section.get("subdomain")
        if isinstance(raw, str) and raw.strip():
            sub = raw.strip()
    return user, secret, sub or DEFAULT_EDUPAGE_SUBDOMAIN


def managebac_config(section: dict | None) -> tuple[str, str, str]:
    user = account_username(section)
    secret = account_secret(section)
    base = ""
    if isinstance(section, dict):
        raw = section.get("base_url")
        if isinstance(raw, str) and raw.strip():
            base = raw.strip().rstrip("/")
    return user, secret, base or DEFAULT_MANAGEBAC_BASE_URL


def mail_config(section: dict | None) -> tuple[str, str, str]:
    """邮箱：账号 = 完整邮箱，口令优先客户端授权码。"""
    user = account_username(section)
    secret = account_secret(section, prefer_authcode=True)
    host = ""
    if isinstance(section, dict):
        raw = section.get("imap_host")
        if isinstance(raw, str) and raw.strip():
            host = raw.strip()
    return user, secret, host or DEFAULT_IMAP_HOST


def accounts_configured(accounts: dict | None) -> bool:
    """三平台**一个都没配**才算没配（返回 409 的条件）。"""
    for key, cfg in (("edupage", edupage_config), ("managebac", managebac_config),
                     ("mail", mail_config)):
        user, secret, _ = cfg(_sect(accounts, key))
        if user and secret:
            return True
    return False


def account_names(accounts: dict | None) -> dict:
    """`meta.accounts`：**只有账号名**，用于前端显示「已连接」。"""
    ep_user, _, _ = edupage_config(_sect(accounts, "edupage"))
    mb_user, _, _ = managebac_config(_sect(accounts, "managebac"))
    ml_user, _, _ = mail_config(_sect(accounts, "mail"))
    return {"edupage": ep_user, "managebac": mb_user, "mail": ml_user}


def _empty_sections() -> dict:
    return {
        "edupage": {"lessons": [], "selected": []},
        "managebac": {"courses": [], "tasks": []},
        "mail": {"unread": 0, "recent": []},
    }


# ============================================================ 依赖探测（便于降级与测试）

def _module_attr(module, name: str):
    """取模块里的属性（异常类等）；没有就返回 None（**不在这里 import 库**）。"""
    try:
        return getattr(module, name, None)
    except Exception:  # noqa: BLE001
        return None


def _bs4_available() -> bool:
    try:
        importlib.import_module("bs4")
        return True
    except Exception:  # noqa: BLE001
        return False


def _edupage_module():
    """拿 edupage_api 模块；没装或坏掉返回 None（调用方转“缺少依赖”）。"""
    try:
        return importlib.import_module("edupage_api")
    except Exception:  # noqa: BLE001
        return None


def _require_bs4() -> None:
    if not _bs4_available():
        raise DependencyMissing(MISSING_DEP_MESSAGE)


# ============================================================ 抓取：EduPage（课表）

def fetch_edupage(section: dict, lib=None) -> dict:
    """EduPage **本周**课表。用的第三方库 edupage_api（非官方），失败只影响本段。

    异常**逐类映射**（不要把凭据错误说成「连不上」）：

    ==========================================  ============================================
    异常                                         给用户看的中文
    ==========================================  ============================================
    依赖 import 失败（缺 edupage-api）            「服务端缺少 edupage-api 依赖」
    ``BadCredentialsException``                   「EduPage 账号或密码不对：请到个人中心…」
    库要两步验证（`second_factor` / 2FA 异常）     「EduPage 需要两步验证…请在客户端登录一次」
    ``requests`` 的连接/超时（ConnectionError/Timeout/TLS） 「连不上平台服务器，请稍后重试」（超时另说）
    EduPage 整段超过 90 秒预算                     「EduPage 抓取太慢/超时…已放后台继续抓…」
    其它                                          「EduPage 抓取失败：<异常类名>」（无 traceback）
    ==========================================  ============================================

    取数走两条路（快路在前，见 `_edupage_week_items`）：

    1. **快路**：`currenttt.js?__func=curentttGetData` 带 `datefrom=周一 & dateto=周日`
       —— 和库的 `get_timetable(本人, 某天)` 是**同一个接口**，只是把日期区间放宽到整周、
       由服务端按登录人过滤，实测**一次请求 0.5~1.1 秒**就拿到整周（库的
       `get_my_timetable` 逐条重建 dbi 对象，实测 37~49 秒；逐日 `get_timetable`
       7 天实测 122 秒）。课卡自带 `date` 字段，所以「按天分组」不必猜。
    2. **慢回退**：快路不可用（老版本库没有 `session`/`gsec_hash`、接口改版、返回空）
       时退回库的公开接口 `get_my_timetable(今天)` —— 一天的数据，
       `date` 直接取请求的那一天。

    `lib` 由调用方**事先解析好**再传进来：后台线程里绝不重新 import，
    免得测试替身（`sys.modules` 打桩）在别的线程里被换掉。
    """
    user, secret, subdomain = edupage_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)

    if lib is None:
        lib = _edupage_module()
    if lib is None:
        raise DependencyMissing(EDUPAGE_DEP_MESSAGE)

    client = _edupage_login(lib, user, secret, subdomain)

    today = datetime.now().date()
    monday, sunday = week_bounds(today)

    items = _edupage_week_items(client, subdomain, monday, sunday)
    if items is not None:
        dbi = getattr(client, "data", None)
        dbi = dbi.get("dbi") if isinstance(dbi, dict) else None
        return _edupage_from_items(items, section, monday, sunday,
                                   dbi if isinstance(dbi, dict) else {})

    # ---- 慢回退：库的公开接口，只能拿一天 ----
    try:
        timetable = client.get_my_timetable(today)
    except Exception as exc:  # noqa: BLE001
        raise _ep_error(exc, lib) from exc
    return _edupage_from_lessons(timetable, section, today)


def _edupage_login(lib, user: str, secret: str, subdomain: str):
    """登录 EduPage；异常逐类映射成中文（凭据/2FA/连接/超时/其它）。"""
    try:
        client = lib.Edupage(request_timeout=EDUPAGE_HTTP_TIMEOUT)
        second_factor = client.login(user, secret, subdomain)
    except TypeError:
        # 老版本 Edupage() 不接受 request_timeout（用 TypeError 重试一次无参构造）
        try:
            client = lib.Edupage()
            second_factor = client.login(user, secret, subdomain)
        except Exception as exc:  # noqa: BLE001
            raise _edupage_login_error(exc, lib, user, secret) from exc
    except Exception as exc:  # noqa: BLE001
        raise _edupage_login_error(exc, lib, user, secret) from exc

    if second_factor is not None:
        raise PlatformError(EDUPAGE_TFA_MESSAGE)
    return client


#: ---- 本周：周一 ~ 周日（周一为一周第一天，与客户端周课表一致）----

def week_bounds(day) -> tuple:
    """`day` 所在自然周的 (周一, 周日)。"""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _edupage_week_items(client, subdomain: str, monday, sunday):
    """**快路**：一次请求拿下整周课卡（原始 JSON 条目）。

    成功（哪怕是空列表）返回 list；不可用 / 出错返回 None（调用方走慢回退）。
    只读 `client.session` / `subdomain` / `gsec_hash` / `get_school_year()` ——
    都是库的公开属性，不碰私有方法、也不自己实现登录协议。
    """
    try:
        year = client.get_school_year()
        gsh = getattr(client, "gsec_hash", None)
        session = getattr(client, "session", None)
        if session is None or not gsh:
            return None
        for table, target_id in _edupage_targets(client):
            request_data = {
                "__args": [None, {
                    "year": year,
                    "datefrom": monday.isoformat(),
                    "dateto": sunday.isoformat(),
                    "table": table,
                    "id": str(target_id),
                    "showColors": True,
                    "showIgroupsInClasses": True,
                    "showOrig": True,
                    "log_module": "CurrentTTView",
                }],
                "__gsh": gsh,
            }
            url = (f"https://{subdomain}.edupage.org/"
                   "timetable/server/currenttt.js?__func=curentttGetData")
            response = session.post(url, json=request_data, timeout=EDUPAGE_HTTP_TIMEOUT)
            payload = json.loads(response.content.decode())
            body = payload.get("r")
            if not isinstance(body, dict):
                continue
            if body.get("error"):
                # 权限/参数不对：换下一个候选身份再试（不把原文回给前端）
                logger.info("edupage currenttt rejected: table=%s", table)
                continue
            items = body.get("ttitems") or []
            if items:
                return items
        return []
    except Exception as exc:  # noqa: BLE001  快路任何异常都退慢回退，不影响可用性
        logger.info("edupage fast path unavailable: class=%s", type(exc).__name__)
        return None


def _edupage_targets(client) -> list:
    """课表的查询目标候选 [(table, id), …]：优先「本人」。

    学校账号的 person_id 在 dbi 里是**负数**（如 `-3772`），而 `get_user_id()`
    给的是 `Student-3772`，所以两种写法都试；学生查 students，老师查 teachers。
    """
    data = getattr(client, "data", None)
    row = (data or {}).get("userrow") if isinstance(data, dict) else None
    row = row if isinstance(row, dict) else {}
    uid = ""
    try:
        uid = str(client.get_user_id() or "")
    except Exception:  # noqa: BLE001
        uid = ""

    table = "students"
    low = uid.lower() + str(row.get("UserID") or "").lower()
    if "teacher" in low:
        table = "teachers"

    raw_ids = [row.get("StudentID"), row.get("TeacherID")]
    tail = uid.split("-", 1)[1] if "-" in uid else ""
    if tail:
        raw_ids.append(f"-{tail}")
        raw_ids.append(tail)

    out: list = []
    for raw in raw_ids:
        text = str(raw or "").strip()
        if not text or not re.fullmatch(r"-?\d+", text):
            continue
        for candidate in (text, text.lstrip("-"), f"-{text.lstrip('-')}"):
            pair = (table, candidate)
            if pair not in out:
                out.append(pair)
    return out


#: ---- 原始课卡 → 契约条目 -------------------------------------------------
#:
#: 字段来源（两条路的键不一样，取值时都兼容）：
#:   currenttt 课卡：date / starttime / endtime / subjectid / groupnames /
#:                   teacherids / classroomids / classids / type 都在**顶层**
#:   gcall 课卡    ：同样的键藏在 `flags.dp0` 里
#: 名字（科目/老师/教室）一律查本地 `client.data["dbi"]`，**不再发请求**——
#: 库慢就慢在每张卡都重建一遍全校列表。

#: 与 edupage_api 的 `__parse_timetable` 同一套 type 词表（保持判定一致）
_EP_CANCELLED_TYPES = ("absent", "")
_EP_EVENT_TYPES = ("event", "out")


def _ep_field(item: dict, key: str):
    """课卡字段：顶层优先，其次 `flags.dp0`（gcall 形态）。"""
    if not isinstance(item, dict):
        return None
    value = item.get(key)
    if value not in (None, "", [], {}):
        return value
    flags = item.get("flags")
    dp0 = flags.get("dp0") if isinstance(flags, dict) else None
    if isinstance(dp0, dict):
        value = dp0.get(key)
        if value not in (None, ""):
            return value
    return None


def _ep_is_skippable(item: dict) -> bool:
    """表头/空行（gcall 的时段行）不算课。"""
    if not isinstance(item, dict):
        return True
    if not _ep_field(item, "starttime"):
        return True
    if "header" in item:
        header = item.get("header")
        if not header or (isinstance(header, list) and header
                          and isinstance(header[0], dict)
                          and header[0].get("cmd") == "addlesson_t"):
            return True
    return False


def _ep_is_cancelled(item: dict) -> bool:
    """停课判定：`removed` 为真，或 `type` 落在库同款词表里（absent / 空）。"""
    if _ep_field(item, "removed") or _ep_field(item, "is_cancelled"):
        return True
    if "type" not in item and not isinstance((item.get("flags") or {}).get("dp0"), dict):
        return False
    raw_type = _ep_field(item, "type")
    if raw_type is None:
        return False
    return str(raw_type).strip().lower() in _EP_CANCELLED_TYPES


def _ep_is_event(item: dict) -> bool:
    """事件/外出（`type` = event/out，或 `main` 为真）——官网只列真正要上的课。"""
    raw_type = _ep_field(item, "type")
    if raw_type is not None and str(raw_type).strip().lower() in _EP_EVENT_TYPES:
        return True
    return bool(_ep_field(item, "main"))


def _ep_time(value) -> str:
    """`08:00` / `08:00:00` / `time(8, 0)` → `08:00`。"""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%H:%M")
        except Exception:  # noqa: BLE001
            return ""
    text = str(value).strip()
    match = re.match(r"^(\d{1,2}):(\d{2})", text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""


def _dbi_entry(dbi, group: str, ident) -> dict:
    if not isinstance(dbi, dict) or ident in (None, ""):
        return {}
    bucket = dbi.get(group)
    if not isinstance(bucket, dict):
        return {}
    entry = bucket.get(str(ident))
    return entry if isinstance(entry, dict) else {}


def _dbi_subject(dbi, ident) -> str:
    entry = _dbi_entry(dbi, "subjects", ident)
    return _clean_message(entry.get("name") or entry.get("short") or "")


def _dbi_teacher(dbi, ident) -> str:
    entry = _dbi_entry(dbi, "teachers", ident)
    full = " ".join(part for part in (str(entry.get("firstname") or "").strip(),
                                      str(entry.get("lastname") or "").strip()) if part)
    return _clean_message(full or entry.get("short") or entry.get("name") or "")


def _dbi_classroom(dbi, ident) -> str:
    entry = _dbi_entry(dbi, "classrooms", ident)
    return _clean_message(entry.get("name") or entry.get("short") or "")


def _ep_groups(item: dict) -> list:
    raw = _ep_field(item, "groupnames") or []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [str(g).strip() for g in raw if str(g or "").strip()]


def _ep_id_list(item: dict, key: str) -> list:
    raw = _ep_field(item, key) or []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [x for x in raw if x not in (None, "")]


def _first_where(values: list, pick) -> str:
    for value in values:
        name = pick(value)
        if name:
            return name
    return ""


def _edupage_from_items(items, section: dict, monday, sunday, dbi=None) -> dict:
    """原始课卡 → 契约条目（**只保留本周、未停课、非事件**的条目）。"""
    return _collect_lessons(items, section, monday, sunday, None, dbi)


def _edupage_from_lessons(timetable, section: dict, day) -> dict:
    """慢回退：库的 `Lesson` 对象（只有那一天）→ 契约条目。"""
    items = []
    for lesson in list(timetable or []):
        items.append({
            "date": day.isoformat(),
            "starttime": getattr(lesson, "start_time", None),
            "endtime": getattr(lesson, "end_time", None),
            "subject": getattr(getattr(lesson, "subject", None), "name", None)
                       or getattr(getattr(lesson, "subject", None), "short", None),
            "groupnames": [str(g) for g in (getattr(lesson, "groups", None) or []) if g],
            "teachers": [getattr(t, "name", "") for t in (getattr(lesson, "teachers", None) or [])],
            "classrooms": [getattr(c, "name", "") for c in (getattr(lesson, "classrooms", None) or [])],
            "is_cancelled": bool(getattr(lesson, "is_cancelled", False)),
            "is_event": bool(getattr(lesson, "is_event", False)),
        })
    return _collect_lessons(items, section, day, day, day, None)


def _collect_lessons(items, section: dict, monday, sunday, fallback_day, dbi=None) -> dict:
    """把课卡整理成契约结构：`{lessons: [...], selected: [...]}`。

    **date 怎么来的**：快路的课卡自带权威 `date` 字段（`currenttt` 顶层 /
    `gcall` 的 `flags.dp0.date`），直接用它；慢回退只有一天，用请求的那天。
    外层只保留 [周一, 周日] 区间内的条目（学校偶尔会带出相邻天）。
    """
    lessons: list = []
    groups_seen: list = []

    for item in list(items or []):
        if not isinstance(item, dict) or _ep_is_skippable(item):
            continue
        if item.get("is_cancelled") or item.get("is_event"):
            continue                              # 慢回退形态：库已判定过

        date_text = str(_ep_field(item, "date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            date_text = fallback_day.isoformat() if fallback_day else ""
        if not date_text:
            continue
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
        if not (monday <= day <= sunday):
            continue                              # 只保留本周

        if _ep_is_cancelled(item) or _ep_is_event(item):
            continue

        groups = _ep_groups(item)
        for group in groups:
            if group not in groups_seen:
                groups_seen.append(group)

        subject = _clean_message(item.get("subject") or "") or _dbi_subject(
            dbi, _ep_field(item, "subjectid"))
        teacher = _first_where(_ep_id_list(item, "teachers"), lambda t: _clean_message(t)) \
            or _dbi_teacher(dbi, (_ep_id_list(item, "teacherids") or [None])[0])
        room = _first_where(_ep_id_list(item, "classrooms"), lambda c: _clean_message(c)) \
            or _dbi_classroom(dbi, (_ep_id_list(item, "classroomids") or [None])[0])

        lessons.append({
            "date": date_text,
            "start": _ep_time(_ep_field(item, "starttime")),
            "end": _ep_time(_ep_field(item, "endtime")),
            "subject": subject or "(无科目)",
            "group": groups[0] if groups else "",     # 契约：取 groups[0]
            "room": room,
            "teacher": teacher,
        })

    lessons.sort(key=lambda row: (row["date"], row["start"], row["subject"], row["group"]))

    # 「教学组」：账号配置里显式写的优先，其次是本周课表里出现的组名（保持出现顺序）
    configured = (section or {}).get("selected") if isinstance(section, dict) else None
    selected = list(groups_seen)
    if isinstance(configured, list):
        from_cfg = [str(g) for g in configured if str(g or "").strip()]
        selected = from_cfg + [g for g in selected if g not in from_cfg]
    return {"lessons": lessons, "selected": selected}


def _hhmm(value) -> str:
    try:
        return value.strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _redact(text: str, *needles: str) -> str:
    """把日志里可能出现的账号名/口令替换成 `***`（我们的日志只许有无害信息）。"""
    out = _clean_message(text)
    for needle in needles:
        needle = (needle or "").strip()
        if len(needle) >= 3:
            out = out.replace(needle, "***")
    return out


def _edupage_login_error(exc: Exception, lib=None, *secrets: str) -> PlatformError:
    """EduPage 登录失败：分类成中文 + **在日志里记下异常类名**（不记口令/账号）。

    第三方库的异常文案经常带账号名甚至请求 URL，所以 detail 先过一遍
    `_redact`（账号名/口令替换成 `***`、压平空白、截断 200 字）。
    类名是排查的关键（BadCredentialsException / ConnectionError / 2FA …），
    它不敏感，照记。
    """
    error = _ep_error(exc, lib)
    logger.warning("edupage login failed: class=%s reason=%s detail=%s",
                   type(exc).__name__, error_message(error),
                   _redact(_exc_text(exc), *secrets))
    return error


def _name(obj) -> str:
    if obj is None:
        return ""
    name = getattr(obj, "name", None)
    if isinstance(name, str):
        return name.strip()
    return str(obj).strip() if not isinstance(obj, (list, tuple)) else ""


def _exc_names(exc: Exception) -> str:
    """异常**类名链**（小写）：`BadCredentialsException` → "badcredentialsexception"。

    只看类名、不看 `str(exc)`：第三方库的异常文案里经常带账号名/URL，
    不能拿去做关键词匹配，也不能回给前端。
    """
    names: list[str] = []
    for cls in type(exc).__mro__:
        name = getattr(cls, "__name__", "")
        if name:
            names.append(name.lower())
    return " ".join(names)


def _exc_text(exc: Exception) -> str:
    """异常文案（只用于**本进程内**分类判断，绝不回给前端）。"""
    try:
        return str(exc).lower()
    except Exception:  # noqa: BLE001
        return ""


#: 连接/网络类（requests 的 ConnectionError / Timeout 都算）
_CONNECT_HINTS = ("connection", "connect", "timeout", "timed out", "unreachable",
                  "network", "ssl", "resolve")

#: 凭据类（第三方库的类名）
_CREDENTIAL_HINTS = ("badcredentials", "credentials", "credential",
                     "invalidpassword", "wrongpassword", "authfailed")

#: 两步验证 / 需要验证码
_TFA_HINTS = ("secondfactor", "twofactor", "2fa", "otp", "verificationcode",
              "needstwofactor", "tfa")


def _ep_kind(exc: Exception, lib) -> str:
    """EduPage 异常分类：'credentials' / 'tfa' / 'connect' / 'timeout' / 'other'。

    先认第三方库的异常类（有就用 isinstance，最准），再退回类名/文案关键词。
    """
    cred = _module_attr(lib, "BadCredentialsException")
    if isinstance(cred, type) and isinstance(exc, cred):
        return "credentials"

    names = _exc_names(exc)
    text = _exc_text(exc)

    for hint in _TFA_HINTS:
        if hint in names or hint in text:
            return "tfa"
    for hint in _CREDENTIAL_HINTS:
        if hint in names or hint in text:
            return "credentials"
    if "timeout" in names or "timed out" in text or "timeout" in text:
        return "timeout"
    for hint in _CONNECT_HINTS:
        if hint in names or hint in text:
            return "connect"
    return "other"


def _ep_error(exc: Exception, lib=None) -> PlatformError:
    """EduPage 异常 → 面向用户的中文 PlatformError（分类映射，见 fetch_edupage）。"""
    if isinstance(exc, PlatformError):
        return exc
    if lib is None:
        lib = _edupage_module()
    missing = _module_attr(lib, "EdupageNotInstalledError")
    if isinstance(missing, type) and isinstance(exc, missing):
        return DependencyMissing(EDUPAGE_DEP_MESSAGE)

    kind = _ep_kind(exc, lib)
    if kind == "credentials":
        return LoginFailed(EDUPAGE_CREDENTIALS_MESSAGE)
    if kind == "tfa":
        return PlatformError(EDUPAGE_TFA_MESSAGE)
    if kind in ("timeout", "connect"):
        # 连接 / 单次请求超时都归「连不上平台服务器」；**整段抓取超时（90 秒）**
        # 是另一条专门文案（EDUPAGE_TIMEOUT_MESSAGE，见 _call_with_timeout）。
        return Unreachable(UNREACHABLE_MESSAGE)
    return PlatformError(edupage_unknown_message(exc))


def _classify(exc: Exception) -> PlatformError:
    """把第三方库的异常分类成 PlatformError（信息不外泄）。

    通用口径（ManageBac / 邮箱 / 未分类异常都走这里）；EduPage 登录与课表
    抓取走 `_ep_error`，那里对「凭据不对」有更明确的说法。
    """
    if isinstance(exc, PlatformError):
        return exc
    name = type(exc).__name__.lower()
    text = _exc_text(exc)
    if "timeout" in name or "timed out" in text or "timeout" in text:
        return Unreachable(TIMEOUT_MESSAGE)
    if "login" in name or "password" in text or "credential" in text:
        return LoginFailed(LOGIN_FAILED_MESSAGE)
    return Unreachable(UNREACHABLE_MESSAGE)


# ============================================================ 抓取：ManageBac

def fetch_managebac(section: dict) -> dict:
    """ManageBac 课程 + 作业卡（客户端与解析器从 PLL 搬来，见对应模块）。"""
    user, secret, base_url = managebac_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    _require_bs4()

    try:
        import webapp_managebac as mb
    except Exception as exc:  # noqa: BLE001  依赖缺失/import 出错 → 本段降级
        raise DependencyMissing(MISSING_DEP_MESSAGE) from exc

    try:
        view = mb.fetch_view(user, secret, base_url, timeout=HTTP_TIMEOUT,
                             budget_seconds=MB_TOTAL_BUDGET)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc

    return {"courses": view.get("courses", []), "tasks": view.get("tasks", [])}


# ============================================================ 抓取：邮箱（IMAP）

#: 邮箱抓取的**最后一段进度**（只放阶段名与秒数，不含任何账号/正文）。
#: 抓取超时时 `fetch_mail` 不会返回结果，分段数据也就跟着丢了 ——
#: 这个模块级快照就是为了让「超时也能看出卡在哪一段」，
#: 由 `_compose` 挂到 `meta.mail_diag` 上一并回给排障的人。
_MAIL_DIAG: dict = {}


def _mail_diag(stage: str, **extra) -> None:
    _MAIL_DIAG.clear()
    _MAIL_DIAG.update(stage=stage, at=round(time.monotonic(), 2), **extra)


def fetch_mail(section: dict) -> dict:
    """瓿和邮箱：未读数 + 所有邮件（**全量**抓取，前10条预加载正文）。

    **2026升级**：
    - 抓取**所有**邮件元数据（不限数量）
    - 前10条并行预加载正文（`body_text` 字段），10条外只有元数据
    - `recent[]` 的每一项都带 **`unread`: true|false** —— 逐封的真实已读状态

    **只读**设计不变：
    - 与邮件头同一次 `UID FETCH` 里取 `FLAGS`（含 `\\Seen` 判定），不用第二次
      往返、也不会碰 `\\Seen`。**`BODY.PEEK[HEADER]` 绝不能改成 `BODY[HEADER]`**：
      后者会让服务器给邮件打上 `\\Seen`，等于替用户「读」了邮件。

    未读数 `unread`：优先 `STATUS (UNSEEN)`；其次由上面这份 FLAGS 推出；
    最后才退回 `UID SEARCH UNSEEN` —— 三条路都拿不到才算 0。
    """
    user, secret, host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)

    conn = None
    try:
        conn = _imap_connect(host, user, secret)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc

    try:
        mail_started = time.monotonic()      # 邮箱整批预算从「连上之后」开始算
        _mail_diag("connected")
        try:
            status, data = conn.status("INBOX", "(UNSEEN)")
            unread = _parse_unseen(data)
        except Exception:  # noqa: BLE001  STATUS 不支持就退化用 SEARCH
            unread = None
        _mail_diag("status", elapsed=round(time.monotonic() - mail_started, 2))

        recent: list[dict] = []
        uids: list[str] = []
        partial = False
        timing: dict = {}            # 分段耗时（秒），随邮件段回给排障用
        try:
            _t0 = time.monotonic()
            status, data = conn.uid("search", None, "ALL")
            uids = [u.decode("ascii", "replace") if isinstance(u, bytes) else str(u)
                    for u in (data[0] or b"").split()]
            timing["search"] = round(time.monotonic() - _t0, 2)

            # ---- 只做「邮件头」这一件事：**列表是必须成功的部分** ----
            #
            # 正文预加载**不在这一层做**（2026-09-17 实测教训）：把最新 10 封的整封
            # 正文塞进这条链路后，真实邮箱上会让整个邮箱段冲过 45 秒预算 ——
            # 结果是「连列表都拿不到」（用户最初报的正是这个症状）。
            # 列表与正文必须解耦：
            #   * 这里只成批取邮件头（几百封也只几条命令），保证列表一定回得来；
            #   * 正文改由**前端在列表画出来之后**逐封后台预取，走
            #     `GET /app/mail/<uid>/`（实测 0.4 秒/封，每次一条独立连接），
            #     拿到的正文先存在浏览器里 —— 用户点开时已经是「立马出来」。
            ordered = list(reversed(uids))
            if len(ordered) > MAIL_FULL_LIMIT:
                ordered = ordered[:MAIL_FULL_LIMIT]
                partial = True

            _mail_diag("headers_start", n=len(ordered))
            _t2 = time.monotonic()
            recent = _fetch_headers_bulk(conn, ordered)
            timing["headers"] = round(time.monotonic() - _t2, 2)
            timing["headers_n"] = len(recent)
            timing["fetched"] = len(uids)
            _mail_diag("headers_done", n=len(recent), elapsed=timing["headers"])

        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc

        if unread is None or unread < 0:
            # STATUS 不支持 → 退化：优先用「每一封都拿到了真实 FLAGS」的那批反推，
            # 否则只能 `UID SEARCH UNSEEN` 数一遍。
            unread = _derived_unread(uids, recent)
        if unread is None or unread < 0:
            unread = _count_unseen_fallback(conn, uids)
        result = {"unread": int(max(0, unread or 0)), "recent": recent}
        if timing:
            timing["total"] = round(time.monotonic() - mail_started, 2)
            result["timing"] = timing
        if partial:
            result["partial"] = True
            result["total"] = len(uids)
        return result
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def _imap_connect(host: str, user: str, secret: str, port: int = IMAP_PORT):
    conn = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT)
    try:
        conn.login(user, secret)
        conn.select("INBOX", readonly=True)
    except Exception:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
        raise
    return conn


def _parse_unseen(data) -> int | None:
    try:
        raw = data[0]
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "replace")
        match = re.search(r"UNSEEN\s+(\d+)", raw or "")
        return int(match.group(1)) if match else None
    except Exception:  # noqa: BLE001
        return None


def _derived_unread(uids: list[str], recent: list[dict]) -> int:
    """STATUS 不可用时：由最近这批邮件的真实 FLAGS 反推未读总数。

    **只有「INBOX 一封都没漏下、且每一封都拿到了 FLAGS」时才敢用**：
    `len(recent) == len(uids)` 且每项都有 bool 型的 `unread`。
    否则返回 -1（= 不知道），调用方再退回 `UID SEARCH UNSEEN`。
    宁可少数一次，也不给用户凭空的数字。
    """
    if not recent or len(recent) != len(uids):
        return -1
    flags = [item.get("unread") for item in recent]
    if not all(isinstance(value, bool) for value in flags):
        return -1
    return sum(1 for value in flags if value)


def _count_unseen_fallback(conn, uids: list[str]) -> int:
    """未读数兜底：`UID SEARCH UNSEEN` 数一遍；失败返回 -1（不知道）。"""
    try:
        status, data = conn.uid("search", None, "UNSEEN")
        return len((data[0] or b"").split())
    except Exception:  # noqa: BLE001
        return -1


def _parse_flags(data) -> str:
    """从一次 `UID FETCH` 的响应里抽出 FLAGS 原文（拿不到回空串）。

    `imaplib` 把 `(FLAGS (\\Seen) ...)` 里的 flags 解析成 list[str]，
    位于 `data[0][0]`；少数实现给的是裸字节，也一并兜住。
    """
    try:
        head = data[0][0]
        if isinstance(head, (bytes, bytearray)):
            head = head.decode("utf-8", "replace")
        return head if isinstance(head, str) else ""
    except Exception:  # noqa: BLE001
        return ""


#: IMAP 的「已读」标记（RFC 3501 系统 flag，不区分大小写；各家服务器措辞一致）
_SEEN_FLAG_RE = re.compile(r"\\SEEN\b", re.I)


def _is_unread(flags: str) -> bool | None:
    """FLAGS 原文 → `unread` 布尔；**拿不到 FLAGS 时回 None（未知，不猜）**。

    前端只在 `unread === true` 时画蓝点：未知一律**不画点**，
    绝不因为「读不出来」就给用户画一个假未读。
    """
    if "FLAGS" not in (flags or "").upper():
        return None
    return not _SEEN_FLAG_RE.search(flags)


def _fetch_headers(conn, uid: str) -> dict | None:
    """一封邮件的邮件头 + **真实已读状态**（一次 FETCH 同时取，只读不改）。
    `(UID FLAGS BODY.PEEK[HEADER])`：`FLAGS` 用来判定 `unread`，
    `BODY.PEEK[HEADER]` 只读邮件头 —— **PEEK 是硬要求**：
    写成 `BODY[HEADER]` 会让服务器把邮件标成已读（用户会看到「我的未读没了」）。
    """
    try:
        typ, data = conn.uid("fetch", uid, "(UID FLAGS BODY.PEEK[HEADER])")
    except Exception:  # noqa: BLE001  imaplib 对 NO 直接抛异常（邮件可能刚好被删）
        return None
    if typ != "OK" or not data or not isinstance(data[0], tuple):
        return None
    msg = email.message_from_bytes(data[0][1])
    meta = {
        "uid": str(uid),
        "from": _header(msg, "From"),
        "subject": _header(msg, "Subject") or "(无主题)",
        "date": _header(msg, "Date"),
    }
    unread = _is_unread(_parse_flags(data))
    if unread is not None:
        meta["unread"] = unread
    return meta


def _fetch_headers_bulk(conn, uids: list) -> list:
    """**成批**取邮件头：一次 `UID FETCH` 带上 `MAIL_FETCH_CHUNK` 个 uid。

    这是「全量抓取」能活下来的关键。旧写法是逐封 `_fetch_headers()`——
    1 封 1 个 IMAP 往返，邮箱里几百封就是几百次往返，必然打穿抓取预算，
    用户看到的就是「抓取超时（超过 20 秒）」（2026-09-17 实测就是这个原因）。

    只读语义完全不变：`BODY.PEEK[HEADER.FIELDS …]` + 同一次响应里读 `FLAGS`，
    **绝不写 `\\Seen`**。单批失败只丢那一批（不整批报废），其余照常返回。
    返回顺序与传入的 `uids` 顺序一致（按 uid 归位，不依赖服务器回包顺序）。
    """
    if not uids:
        return []
    by_uid: dict = {}
    for start in range(0, len(uids), MAIL_FETCH_CHUNK):
        chunk = [str(u) for u in uids[start:start + MAIL_FETCH_CHUNK]]
        try:
            typ, data = conn.uid("fetch", ",".join(chunk),
                                 "(UID FLAGS BODY.PEEK[HEADER.FIELDS %s])"
                                 % MAIL_HEADER_FIELDS)
        except Exception:  # noqa: BLE001  这一批有问题（某一封被删/连接抖动）→ 跳过这批
            continue
        if typ != "OK" or not data:
            continue
        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            head = item[0]
            if isinstance(head, (bytes, bytearray)):
                head = head.decode("utf-8", "replace")
            match = re.search(r"UID\s+(\d+)", head or "")
            if not match:
                continue
            uid = match.group(1)
            try:
                msg = email.message_from_bytes(item[1])
            except Exception:  # noqa: BLE001
                continue
            meta = {
                "uid": uid,
                "from": _header(msg, "From"),
                "subject": _header(msg, "Subject") or "(无主题)",
                "date": _header(msg, "Date"),
            }
            unread = _is_unread(head)
            if unread is not None:
                meta["unread"] = unread
            by_uid[uid] = meta
    return [by_uid[str(u)] for u in uids if str(u) in by_uid]


def _fetch_mail_body(section: dict, uid: str) -> dict:
    """单封邮件正文的实现（section = mail 段，已含账号与口令）。"""
    user, secret, host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    uid = str(uid or "").strip()
    if not _UID_RE.match(uid):
        raise MailNotFound(NOT_FOUND_MESSAGE)

    try:
        conn = _imap_connect(host, user, secret)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc

    try:
        try:
            typ, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        except _IMAP_ERROR as exc:
            # imaplib 对 NO 直接抛异常：这一封不在了 → 404；其余（连接断了）→ 502
            raise _mail_fetch_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            raise MailNotFound(NOT_FOUND_MESSAGE)
        msg = email.message_from_bytes(data[0][1])
        body_text, body_html = _body_parts(msg)
        return {
            "uid": uid,
            "from": _header(msg, "From"),
            "to": _header(msg, "To"),
            "subject": _header(msg, "Subject") or "(无主题)",
            "date": _header(msg, "Date"),
            "body_text": body_text,
            "body_html": body_html,     # MIME text/html 原样，未改写；没有 HTML 部分时为空串
            "attachments": _mail_attachments(msg),   # 只有元数据；内容走附件下载接口
        }
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def _mark_mail_seen(section: dict, uid: str) -> bool:
    r"""把**一封**邮件标记为已读（IMAP `UID STORE <uid> +FLAGS (\Seen)`）。
    这是抓取层里**唯一会写邮箱状态**的函数，而且只做这一件事：
    抓列表（`fetch_mail`）与读正文（`_fetch_mail_body`）依然全程 `BODY.PEEK`、绝不改状态。

    注意 `select(readonly=False)`：只读打开的邮箱不允许 STORE，必须读写打开才能加 flag。
    """
    user, secret, host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    uid = str(uid or "").strip()
    if not _UID_RE.match(uid):
        raise MailNotFound(NOT_FOUND_MESSAGE)

    try:
        conn = imaplib.IMAP4_SSL(host, IMAP_PORT, timeout=IMAP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc
    try:
        try:
            conn.login(user, secret)
            conn.select("INBOX", readonly=False)     # STORE 需要读写会话
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        try:
            typ, data = conn.uid("store", uid, "+FLAGS", "(\\Seen)")
        except _IMAP_ERROR as exc:
            raise _mail_fetch_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        if typ != "OK":
            raise MailNotFound(NOT_FOUND_MESSAGE)
        return True
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def _header(msg, key: str) -> str:
    raw = msg.get(key)
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
        out: list[str] = []
        for text, charset in parts:
            if isinstance(text, bytes):
                out.append(text.decode(charset or "utf-8", "replace"))
            else:
                out.append(text)
        value = "".join(out)
    except Exception:  # noqa: BLE001
        value = str(raw)
    return re.sub(r"\s+", " ", value).strip()[:300]


def _body_parts(msg) -> tuple[str, str]:
    """一封邮件的两版正文：`(纯文本, 原样 HTML)`。

    * 纯文本优先 `text/plain`；没有就用 HTML 去标签**退回**（老行为不变）。
    * HTML 为 **MIME 里 `text/html` 那一部分的原样内容**（**逐字不改写**：不剥标签、
      不过滤属性、不动 `<style>`、不重写 URL、不补 target/rel）；没有 HTML 部分 = 空串
      （前端据此决定「只走纯文本渲染」还是「沙箱 iframe 渲染」）。
    * 脚本不执行这件事**不由这里负责** —— 前端把它放进
      `<iframe sandbox="allow-same-origin">`（不带 `allow-scripts`）。
    """
    plain, html_part = None, None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if (part.get("Content-Disposition") or "").startswith("attachment"):
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True) or b""
                text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html_part is None:
                html_part = text
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        if msg.get_content_type() == "text/html":
            html_part = text
        else:
            plain = text

    if plain is None and html_part is not None:
        plain = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_part)
        plain = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", plain)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = html_mod.unescape(plain)

    text = (plain or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # HTML 正文**逐字原样**回前端：一个字符都不改（消毒已按用户要求整体移除；
    # 唯一防线是前端那颗不带 allow-scripts 的沙箱 iframe）。
    html_raw = html_part if html_part else ""
    return text[:MAIL_BODY_MAX_CHARS], html_raw


def _body_text(msg) -> str:
    """纯文本正文（保留这个函数名：既有测试与脚本都在用它）。"""
    return _body_parts(msg)[0]


#: 附件名最长保留字符数（超长截断，避免畸形头部把页面撑破）
ATTACH_NAME_MAX_CHARS = 200
#: 单个附件回前端的字节上限（超过只在列表里标「过大」，不给下载按钮）
ATTACH_MAX_BYTES = 25 * 1024 * 1024


def _attachment_parts(msg) -> list:
    """按 MIME 顺序列出附件部分（**不含内联图片**，那些属于正文）。

    判据与 `_body_parts` 一致：`Content-Disposition: attachment`，
    外加「有 filename 的非文本部分」（很多邮件不带 disposition 却带 filename）。
    """
    parts = []
    if not msg.is_multipart():
        return parts
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        # 内联资源（`Content-Disposition: inline` 或带 `Content-ID` 的正文图片）不是附件：
        # 它们是正文的一部分，列进附件栏反而会让用户以为多收了几个文件。
        if disposition.startswith("inline") or part.get("Content-ID"):
            continue
        if not disposition.startswith("attachment") and not filename:
            continue
        if part.get_content_maintype() == "text" and not disposition.startswith("attachment"):
            continue
        parts.append(part)
    return parts


def _decode_header_text(value) -> str:
    """MIME 编码的头部（`=?utf-8?B?...?=`）→ 可读文本；失败回原样。"""
    if not value:
        return ""
    try:
        chunks = email.header.decode_header(value)
    except Exception:  # noqa: BLE001
        return str(value)[:ATTACH_NAME_MAX_CHARS]
    out = []
    for text, charset in chunks:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", "replace"))
        else:
            out.append(str(text))
    return "".join(out)[:ATTACH_NAME_MAX_CHARS]


def _mail_attachments(msg) -> list:
    """附件清单（**只列元数据，不搬内容**）：`[{index, filename, size, content_type}]`。

    `index` 是 `_attachment_parts()` 里的下标 —— 下载接口用它定位同一封邮件的
    同一个附件（每次下载都在服务端重新 FETCH 一次，不在内存里留二进制）。
    """
    rows = []
    for index, part in enumerate(_attachment_parts(msg)):
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        name = _decode_header_text(part.get_filename()) or ("附件 %d" % (index + 1))
        rows.append({
            "index": index,
            "filename": name,
            "size": len(payload),
            "content_type": part.get_content_type() or "application/octet-stream",
        })
    return rows


def _preload_mail_bodies(conn, mails: list[dict], deadline: float = 0.0) -> None:
    """预加载前 N 封邮件的正文与附件清单（写进 mail dict 的 `body_text` / `attachments`）。

    **必须串行、并且复用同一个 `conn`**：`imaplib` 的连接**不是线程安全的**——
    三个线程同时对同一个 socket 发 `UID FETCH` 会把应答流交叉读错，
    轻则数据错乱，重则整个解释器 access violation 崩掉（2026-09-17 实测到了）。

    打开独立连接虽然能并行，但每封都要重新 LOGIN + SELECT，在 10 封的量级上
    并不比串行快，却把「一次登录」变成「十次登录」（邮箱服务商更容易限流）。
    所以这里就老老实实串行：实测 10 封正文约 1~3 秒，用户点开第一封时早已就位。

    `deadline` 是单调时钟的截止点（0 = 不限）：到点就停手，剩下的邮件正文/附件
    保持缺省值，等用户点开时再按需加载 —— **绝不为了预加载把整批抓取拖超时**。
    """
    if not mails:
        return
    for mail in mails:
        if deadline and time.monotonic() >= deadline:
            return
        uid = mail.get("uid")
        if not uid:
            continue
        try:
            typ, data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                mail.setdefault("body_text", "")
                mail.setdefault("attachments", [])
                continue
            msg = email.message_from_bytes(data[0][1])
            body_text, _ = _body_parts(msg)
            mail["body_text"] = body_text
            mail["attachments"] = _mail_attachments(msg)
        except Exception:  # noqa: BLE001  单封失败不影响其它
            mail.setdefault("body_text", "")
            mail.setdefault("attachments", [])


# ============================================================ 并发抓取（全程不落日志）

_FETCHERS = {
    "edupage": fetch_edupage,
    "managebac": fetch_managebac,
    "mail": fetch_mail,
}


def _gather(accounts: dict, platform_timeout: float,
            user_id: str = "-", platforms=None,
            budgets: dict | None = None) -> tuple[dict, dict, dict]:
    """平台并发抓取（默认三平台；`platforms` 可收窄）。返回 (sections, errors, timings)。

    - 任一平台异常/超时**只影响自己**；
    - 超时是硬预算：到点就收工，没回来的平台写超时原因（不阻塞响应）；
    - `budgets` 给某个平台**单独的一档预算**（没给就用 `platform_timeout`）。
      邮箱走的就是这条路：用户要求「所有邮件都抓」，首轮成本明显高于 ManageBac，
      但绝不因此把 ManageBac 也拖成 45 秒 —— 各平台按自己的闸门收工。
    - 日志只有 用户号/平台名/耗时/成败，**没有账号名与口令**。

    EduPage 不在这里：它走后台线程（`_edupage_result`），预算单独 90 秒，
    绝不和 ManageBac / 邮箱共用一个闸门。
    """
    names = tuple(platforms) if platforms else tuple(PLATFORMS)
    per_platform = dict(budgets or {})
    sections = _empty_sections()
    errors: dict[str, str] = {}
    timings: dict[str, float] = {}

    def budget_of(platform: str) -> float:
        try:
            return max(1.0, float(per_platform.get(platform, platform_timeout)))
        except (TypeError, ValueError):
            return max(1.0, float(platform_timeout))

    runner = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(names), thread_name_prefix="webapp-fetch")

    def _run(platform: str, section: dict):
        started = time.monotonic()
        try:
            return platform, _FETCHERS[platform](section), time.monotonic() - started, None
        except BaseException as exc:  # noqa: BLE001  连 KeyboardInterrupt 都收成一行错误
            return platform, None, time.monotonic() - started, exc

    futures: dict = {}
    started_at = time.monotonic()
    try:
        for platform in names:
            futures[runner.submit(_run, platform, _sect(accounts, platform))] = platform

        # **每个平台按自己的闸门独立收工**，整批只等到「最后一个到期的平台」。
        # 不能简单地 `as_completed(timeout=max(预算))`：那样一个慢平台会把
        # 早已超时的小预算平台一直挂着，响应被拖到最宽那档（实测：ManageBac
        # 2 秒闸门被邮箱 45 秒闸门拖成 24 秒）。
        pending = dict(futures)          # future -> platform
        while pending:
            now = time.monotonic()
            expired = [f for f, p in pending.items() if started_at + budget_of(p) <= now]
            if expired:
                for future in expired:
                    platform = pending.pop(future)
                    future.cancel()
                    timings[platform] = round(budget_of(platform), 2)
                    errors[platform] = TIMEOUT_MESSAGE
                    _log_platform(user_id, platform, budget_of(platform),
                                  TimeoutError(TIMEOUT_MESSAGE))
                continue
            soonest = min(started_at + budget_of(p) for p in pending.values())
            try:
                done, _ = concurrent.futures.wait(
                    list(pending), timeout=max(0.0, soonest - now),
                    return_when=concurrent.futures.FIRST_COMPLETED)
            except Exception:  # noqa: BLE001
                break
            if not done:
                continue                 # 下一轮循环处理刚到期的那一档
            for future in done:
                platform = pending.pop(future)
                try:
                    name, data, elapsed, error = future.result()
                except Exception as exc:  # noqa: BLE001
                    name, data, elapsed, error = platform, None, 0.0, exc
                timings[name] = round(elapsed, 2)
                if error is None and isinstance(data, dict):
                    sections[name] = data
                    _log_platform(user_id, name, elapsed, None)
                else:
                    errors[name] = _language_error("error", error)
                    _log_platform(user_id, name, elapsed, error)
    finally:
        runner.shutdown(wait=False)     # 不阻塞响应；超时线程自然跑完即回收
    return sections, errors, timings


def _log_platform(user_id, platform: str, elapsed: float, error: Exception | None) -> None:
    """日志只记 用户号 / 平台 / 耗时 / 成败原因摘要。"""
    if error is None:
        logger.info("fetch user=%s platform=%s elapsed=%.2fs ok=1",
                    user_id, platform, elapsed)
    else:
        logger.warning("fetch user=%s platform=%s elapsed=%.2fs ok=0 reason=%s",
                       user_id, platform, elapsed, _language_error("error", error))


# ============================================================ 缓存（分两档 TTL）

_cache: dict[str, tuple[float, dict]] = {}     # 批次缓存：ManageBac + 邮箱，5 分钟
_ep_cache: dict[str, tuple[float, dict]] = {}  # EduPage 缓存：单独 30 分钟
_cache_lock = threading.RLock()
_key_locks: dict[str, threading.Lock] = {}
_ep_jobs: dict[str, dict] = {}                 # 缓存键 → 后台抓取任务状态
_ep_jobs_lock = threading.RLock()


def _key_lock(user_id: str) -> threading.Lock:
    with _cache_lock:
        lock = _key_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _key_locks[user_id] = lock
        return lock


def _cache_get(user_id: str):
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry is None:
            return None
        stamped, result, ttl = entry
        if now - stamped > ttl:
            _cache.pop(user_id, None)
            return None
        return result


def _cache_put(user_id: str, result: dict, ttl: float | None = None) -> None:
    """写缓存。`ttl=None` 用默认 5 分钟；失败批次传更短的 `SHORT_FAIL_TTL`。"""
    with _cache_lock:
        _cache[user_id] = (time.monotonic(), result,
                           float(ttl) if ttl is not None else CACHE_TTL_SECONDS)


def clear_cache(user_id: str | None = None) -> None:
    """测试/运维用：清掉某用户或全部缓存（**两档一起清**，含后台任务状态）。"""
    with _cache_lock:
        if user_id is None:
            _cache.clear()
            _ep_cache.clear()
        else:
            _cache.pop(user_id, None)
            _ep_cache.pop(user_id, None)
    with _ep_jobs_lock:
        if user_id is None:
            _ep_jobs.clear()
        else:
            _ep_jobs.pop(user_id, None)


def _cache_key(user_id: str, accounts: dict) -> str:
    """缓存键 = 用户号 + 三平台账号名。

    **用户号必须进键**：否则两个共用同一平台账号的用户会互相看到对方缓存。
    键里只有账号名，**没有口令**（换口令不换键，到期后自然刷新）。
    """
    names = account_names(accounts)
    return f"{user_id or '-'}|" + "|".join(names.get(p, "") for p in PLATFORMS)


# ============================================================ EduPage：缓存 + 后台抓取

def _ep_cache_peek(key: str):
    """EduPage 缓存 → (是否仍在 30 分钟 TTL 内, 数据|None)。

    过期也把数据带出来：过期的那份可以**先给用户看**，同时后台去刷新，
    比「先清空再等」友好（冷启动才真的没有数据）。
    """
    with _cache_lock:
        entry = _ep_cache.get(key)
    if entry is None:
        return False, None
    stamped, data = entry
    return (time.monotonic() - stamped <= EDUPAGE_CACHE_TTL_SECONDS), data


def _ep_cache_put(key: str, data: dict) -> None:
    with _cache_lock:
        _ep_cache[key] = (time.monotonic(), data)


def _ep_job_state(key: str) -> dict:
    with _ep_jobs_lock:
        return dict(_ep_jobs.get(key) or {})


def _ep_recent_error(key: str):
    """后台任务**刚刚失败**（还在冷却期内）→ 返回那个异常，供本次请求如实上报。"""
    job = _ep_job_state(key)
    if job.get("running") or job.get("error") is None:
        return None
    finished = job.get("finished")
    if finished is None:
        return None
    if time.monotonic() - finished > EDUPAGE_RETRY_COOLDOWN:
        return None
    return job["error"]


def _ep_start_job(key: str, section: dict, user_id: str, lib) -> bool:
    """起一个后台抓取线程。返回 True = 本次真的起了新线程。

    幂等：**同一缓存键同时只有一个任务**；上一次刚失败（冷却期内）也不重起
    ——避免每次点刷新都朝学校服务器打一次登录。
    """
    now = time.monotonic()
    with _ep_jobs_lock:
        job = _ep_jobs.get(key)
        if job:
            if job.get("running"):
                return False
            finished = job.get("finished")
            if (job.get("error") is not None and finished is not None
                    and now - finished < EDUPAGE_RETRY_COOLDOWN):
                return False
        _ep_jobs[key] = {"running": True, "started": now, "finished": None,
                         "error": None, "data": None}
    thread = threading.Thread(
        target=_ep_job_run,
        args=(key, dict(section if isinstance(section, dict) else {}), user_id, lib),
        name="webapp-edupage", daemon=True)
    thread.start()
    return True


def _ep_job_run(key: str, section: dict, user_id: str, lib) -> None:
    """后台线程体：抓 EduPage（**单独 90 秒预算**），成功写进 30 分钟缓存。"""
    started = time.monotonic()
    data, error = None, None
    try:
        data = _call_with_timeout(fetch_edupage, (section, lib), EDUPAGE_TIMEOUT)
    except BaseException as exc:  # noqa: BLE001  后台线程绝不让异常冒出去
        error = exc
    elapsed = time.monotonic() - started
    if data is not None:
        _ep_cache_put(key, data)
        _log_platform(user_id, "edupage", elapsed, None)
    else:
        _log_platform(user_id, "edupage", elapsed, error)
    with _ep_jobs_lock:
        job = _ep_jobs.get(key) or {}
        job.update({"running": False, "finished": time.monotonic(),
                    "error": error, "data": data})
        _ep_jobs[key] = job


def _call_with_timeout(func, args: tuple, timeout: float):
    """在独立线程里跑 `func(*args)` 并限时；超时抛 Unreachable(超时专门文案)。"""
    runner = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="webapp-edupage-run")
    try:
        future = runner.submit(func, *args)
        try:
            return future.result(timeout=max(1.0, float(timeout)))
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise Unreachable(EDUPAGE_TIMEOUT_MESSAGE) from exc
    finally:
        runner.shutdown(wait=False)      # 超时那次线程留在后台自生自灭


def _edupage_result(key: str, section: dict, user_id: str, *, force: bool, lib):
    """本次请求里 EduPage 段怎么给：返回 (数据|None, 错误文案|None, 是否 pending)。

    规则（对应验收①3）：
      * 30 分钟内命中且不是 force        → 直接给缓存，无 pending；
      * 命中但 force / 有过期数据在手上   → **先给旧数据**，同时后台刷新，标 pending；
      * 缓存为空且要现抓                  → **立刻返回**（空段 + pending 文案），后台去抓；
      * 缺依赖 / 后台刚失败（冷却期内）    → 如实上报，不标 pending。
    """
    fresh, data = _ep_cache_peek(key)
    if fresh and not force:
        return data, None, False

    if lib is None:
        # 缺 edupage-api 是**静态**条件，没必要让用户等一次后台抓才知道
        return None, EDUPAGE_DEP_MESSAGE, False

    started = _ep_start_job(key, section, user_id, lib)
    if data is not None:
        return data, None, True
    if not started:
        recent = _ep_recent_error(key)
        if recent is not None:
            return None, _language_error("error", recent), False
    return None, EDUPAGE_PENDING_MESSAGE, True


def fetch_all(accounts: dict, *, force: bool = False, user_id: str = "-") -> dict:
    """抓取三平台数据并返回契约结构（**不含外层 ok 字段**，由 server.py 补）。

    * ManageBac / 邮箱：跟着请求同步抓，5 分钟缓存，`force=True` 绕过；
    * EduPage：30 分钟单独缓存；没有可用缓存时**不阻塞本次请求**，
      立刻返回空段 + `meta.pending == ["edupage"]` + 「正在抓取…请稍后点刷新」，
      抓取交给后台线程（同一用户同时只跑一个，见 `_ep_start_job`）。

    `user_id` 只用来隔离缓存与写日志（**不参与抓取**）。
    账号名只以「账号名」形式出现在 `meta.accounts`；口令绝不进返回值与日志。
    """
    key = _cache_key(user_id, accounts)
    if not force:
        cached = _cache_get(key)
        if cached is not None:
            return _compose(key, cached, accounts, user_id, cache_tag="hit", force=False)

    lock = _key_lock(key)
    with lock:                                   # 同一用户并发只抓一次（防击穿）
        if not force:
            cached = _cache_get(key)
            if cached is not None:
                return _compose(key, cached, accounts, user_id, cache_tag="hit", force=False)

        sections, errors, timings = _gather(accounts, PLATFORM_TIMEOUT, user_id,
                                            platforms=BATCH_PLATFORMS,
                                            budgets={"mail": MAIL_TIMEOUT})
        batch = {
            "sections": sections,
            "errors": errors,
            "timings": timings,
            "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        # **失败的批次只短存一会儿**。原来的写法把「这一轮某平台超时 / 登录失败」
        # 的空结果也按 5 分钟缓存，结果是：网络抖一下 → 邮箱（或课程）就空着 5 分钟，
        # 用户点「刷新」也还是空的（因为命中的是刚写进去的空缓存）。
        # 失败档只存 SHORT_FAIL_TTL，让下一次刷新能立刻重试。
        _cache_put(key, batch,
                   ttl=(SHORT_FAIL_TTL if errors else None))
    return _compose(key, batch, accounts, user_id, cache_tag="miss", force=force)


def _compose(key: str, batch: dict, accounts: dict, user_id: str, *,
             cache_tag: str, force: bool) -> dict:
    """把「批次结果（ManageBac/邮箱）」与「EduPage 那一段」拼成一次响应。

    EduPage 单独拼装，是因为它有自己的 30 分钟 TTL 与后台抓取状态；
    批次缓存命中时也要现算一次，否则后台抓完的课表会被 5 分钟的旧响应盖住。
    """
    sections = _empty_sections()
    for name, value in (batch.get("sections") or {}).items():
        if name in BATCH_PLATFORMS:
            sections[name] = value
    errors = dict(batch.get("errors") or {})
    pending: list = []

    section = _sect(accounts, "edupage")
    ep_user, ep_secret, _sub = edupage_config(section)
    if not (ep_user and ep_secret):
        errors["edupage"] = LOGIN_FAILED_MESSAGE      # 没填账号：立刻说清楚，不进后台
    else:
        # 依赖只在这里解析一次，交给后台线程（线程里不重新 import，见 fetch_edupage）
        data, error, is_pending = _edupage_result(key, section, user_id,
                                                  force=force, lib=_edupage_module())
        if isinstance(data, dict):
            sections["edupage"] = data
        if error:
            errors["edupage"] = error
        else:
            errors.pop("edupage", None)
        if is_pending:
            pending.append("edupage")

    result = dict(sections)
    meta = {
        "fetched_at": batch.get("fetched_at")
                      or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cache": cache_tag,
        "accounts": account_names(accounts),
        "errors": errors,
        # 逐平台耗时（秒）。**只是数字**，不含账号名/口令/正文：
        # 出「某个平台怎么这么慢」时，这是唯一能一眼看出是哪一段的证据。
        "timings": batch.get("timings") or {},
        # 邮箱最后一段进度（超时也看得到卡在哪）
        "mail_diag": dict(_MAIL_DIAG),
    }
    if pending:
        meta["pending"] = pending
    result["meta"] = meta
    return result


# ============================================================ 通讯录（从邮件头收割）

#: 自动补全/通讯录里**不要**出现的机器地址（照抄桌面客户端 `MailService._CONTACTS_SKIP`）
_CONTACTS_SKIP = re.compile(
    r"noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster"
    r"|bounce|notification|notice|system", re.I)

#: 每个文件夹最多扫多少封（取最新的一批；通讯录不需要翻遍整箱）
CONTACTS_SCAN_LIMIT = 400
#: 一次 FETCH 取多少封的头（与邮件列表同样的成批思路，别逐封往返）
CONTACTS_FETCH_CHUNK = 100


def _mutf7_decode(name: str) -> str:
    """IMAP modified UTF-7 → UTF-8。

    Coremail 的文件夹名是这种编码（`&XfJT0ZAB-` = 已发送），
    不解码就找不到「已发送」文件夹，通讯录会缺一半（收件人只在自己发出去的邮件里）。
    """
    import base64 as _b64
    out, i = [], 0
    while i < len(name):
        ch = name[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        j = name.find("-", i + 1)
        if j < 0:
            out.append(name[i:])
            break
        b64 = name[i + 1:j].replace(",", "/")
        if not b64:
            out.append("&")
        else:
            try:
                out.append(_b64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be"))
            except Exception:  # noqa: BLE001
                out.append(name[i:j + 1])
        i = j + 1
    return "".join(out)


def _sent_folders(conn) -> list:
    """探测「已发送」文件夹（Coremail 命名各异，中文名是 modified UTF-7）。"""
    try:
        typ, data = conn.list()
        if typ != "OK":
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for line in data or []:
        if not line:
            continue
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
        m = re.search(r'\s"([^"]+)"\s*$', text)
        name = m.group(1) if m else text.rsplit(" ", 1)[-1].strip('"')
        decoded = _mutf7_decode(name)
        if "sent" in (name + " " + decoded).lower() or "已发送" in decoded:
            out.append('"%s"' % name if " " in name else name)
    return out


def _envelope_addresses(value: str) -> list:
    """解析 Coremail 的**信封式**地址头（它有时不回标准 RFC5322 头）。

    形如 `(("显示名" NIL "local" "domain"))`；`email.utils.getaddresses` 解析不了，
    这里按括号 + 引号做一个小分词器。**照抄桌面客户端的实现**。
    """
    stack: list = [[]]
    buf, in_quote, esc = "", False, False

    def push_atom() -> None:
        nonlocal buf
        t = buf.strip()
        if t:
            stack[-1].append(None if t.upper() == "NIL" else t.strip('"'))
        buf = ""

    for ch in value:
        if esc:
            buf += ch
            esc = False
        elif in_quote:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_quote = False
            else:
                buf += ch
        elif ch == '"':
            in_quote = True
        elif ch == "(":
            push_atom()
            stack.append([])
        elif ch == ")":
            push_atom()
            node = stack.pop()
            stack[-1].append(node)
        elif ch in " \t":
            push_atom()
        else:
            buf += ch
    push_atom()

    addrs = []

    def walk(node) -> None:
        if not isinstance(node, list):
            return
        if len(node) >= 4 and isinstance(node[2], str) and isinstance(node[3], str):
            name = node[0] if isinstance(node[0], str) else ""
            addrs.append((name, "%s@%s" % (node[2], node[3])))
            return
        for child in node:
            walk(child)

    walk(stack[0])
    return addrs


def _harvest_addresses(header_bytes: bytes, me: str, agg: dict) -> None:
    """把一封邮件的 From / To / Cc 里的地址收进 `agg`（地址 → {names, count}）。"""
    try:
        msg = email.message_from_bytes(header_bytes)
    except Exception:  # noqa: BLE001
        return
    for key, value in msg.items():
        if key.lower() not in ("from", "to", "cc"):
            continue
        decoded = _decode_header_text(str(value))
        pairs = [(n, a) for n, a in email.utils.getaddresses([decoded]) if a and "@" in a]
        if not pairs and decoded.lstrip().startswith("("):
            pairs = _envelope_addresses(decoded)
        for name, addr in pairs:
            addr = (addr or "").strip().strip("<>").lower()
            if "@" not in addr or addr == me or not addr.partition("@")[0]:
                continue
            if _CONTACTS_SKIP.search(addr):
                continue
            entry = agg.setdefault(addr, {"names": {}, "count": 0})
            entry["count"] += 1
            if name:
                clean = name.strip().strip("\"'").strip()
                if clean:
                    entry["names"][clean] = entry["names"].get(clean, 0) + 1


def fetch_contacts(accounts: dict, *, limit: int = 300) -> list:
    """【通讯录】从「收件箱 + 已发送」的邮件头收割联系人，按往来次数排序。

    为什么这么做（照抄桌面客户端的结论）：网易企业邮的**个人**账号没有 CardDAV /
    通讯录 API（那是管理员端能力）。mutt/lbdb、Gmail 也都是这么干的 ——
    解析 From/To/Cc，(地址 → 姓名, 出现次数) 聚合，按频率排序，
    用来做收件人自动补全。

    全程**只读**：每个文件夹 `select(readonly=True)` + `BODY.PEEK[HEADER.FIELDS …]`，
    绝不改任何邮件的已读状态。
    """
    section = _sect(accounts, "mail")
    user, secret, host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    me = (user or "").strip().lower()
    agg: dict = {}

    try:
        conn = _imap_connect(host, user, secret)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc
    try:
        for folder in ["INBOX"] + _sent_folders(conn):
            try:
                typ, data = conn.select(folder, readonly=True)
                if typ != "OK" or not data or not data[0]:
                    continue
                typ, data = conn.uid("search", None, "ALL")
                if typ != "OK":
                    continue
                uids = (data[0] or b"").split()[-CONTACTS_SCAN_LIMIT:]
                for i in range(0, len(uids), CONTACTS_FETCH_CHUNK):
                    batch = b",".join(uids[i:i + CONTACTS_FETCH_CHUNK])
                    try:
                        typ, md = conn.uid(
                            "fetch", batch.decode("ascii"),
                            "(BODY.PEEK[HEADER.FIELDS (FROM TO CC)])")
                    except Exception:  # noqa: BLE001
                        continue
                    if typ != "OK":
                        continue
                    for part in md or []:
                        if isinstance(part, tuple) and part[1]:
                            _harvest_addresses(part[1], me, agg)
            except Exception:  # noqa: BLE001  某个文件夹读不了不影响别的
                continue
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass

    out = []
    for addr, entry in sorted(agg.items(), key=lambda kv: -kv[1]["count"])[:limit]:
        best = max(entry["names"], key=entry["names"].get) if entry["names"] else ""
        out.append({"name": best, "email": addr, "count": entry["count"]})
    return out


def _cache_note_mail_read(user_id: str, accounts: dict, uid: str) -> bool:
    """把「刚标成已读」反映到**已有缓存**里（就地改，不整批清缓存）。

    找到这一轮邮件段的那条 `recent` → `unread = false`，并把 `mail.unread` 计数减 1。
    找不到（缓存过期 / 这封不在最近列表里）就什么都不做 —— 下次抓取自然是对的。
    """
    key = _cache_key(user_id, accounts)
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return False
    batch = entry[1]
    sections = batch.get("sections") if isinstance(batch, dict) else None
    mail = sections.get("mail") if isinstance(sections, dict) else None
    if not isinstance(mail, dict):
        return False
    want = str(uid)
    changed = False
    for item in (mail.get("recent") or []):
        if isinstance(item, dict) and str(item.get("uid")) == want and item.get("unread") is True:
            item["unread"] = False
            changed = True
            break
    if changed:
        count = mail.get("unread")
        if isinstance(count, int) and count > 0:
            mail["unread"] = count - 1
    return changed


def fetch_mail_body(accounts: dict, uid: str) -> dict | None:
    """【冻结接口】取单封邮件正文（accounts = 解密后的 accounts 映射）。

    返回的 mail dict 里有两个正文：`body_text`（纯文本，老字段）与
    **`body_html`（原始 HTML 正文，**逐字原样、不做任何消毒/改写**）**；没有 HTML 部分时 `body_html` 是空串。
    脚本不执行由前端那颗 `sandbox="allow-same-origin"`（**不含 `allow-scripts`**）的 iframe 保证。
    取正文**不会**改变已读状态（仍然 `BODY.PEEK[]`）。

    成功返回邮件 dict；uid 格式不对返回 None；失败抛 PlatformError
    （调用方翻译成 502 / 404）。
    """
    section = _sect(accounts, "mail")
    user, secret, _host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    if not _UID_RE.match(str(uid or "").strip()):
        return None
    return _fetch_mail_body(section, uid)


def fetch_mail_attachment(accounts: dict, uid: str, index) -> dict | None:
    """【附件下载】取某一封邮件里第 `index` 个附件的**原始字节**。

    返回 `{"filename":…, "content_type":…, "size":…, "payload": bytes}`；
    邮件的 `uid` 非法 / 找不到这封 / 这个 index 不存在 → `None`（调用方回 404）。
    失败抛 PlatformError（调用方回 502）。

    每次下载都在这封邮件上重新 `BODY.PEEK[]` 一次：**不在服务端留二进制缓存**，
    也不改已读状态（仍然 PEEK）。
    """
    section = _sect(accounts, "mail")
    user, secret, host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    clean = str(uid or "").strip()
    if not _UID_RE.match(clean):
        return None
    try:
        wanted = int(index)
    except (TypeError, ValueError):
        return None
    if wanted < 0:
        return None

    try:
        conn = _imap_connect(host, user, secret)
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc
    try:
        try:
            typ, data = conn.uid("fetch", clean, "(BODY.PEEK[])")
        except _IMAP_ERROR as exc:
            raise _mail_fetch_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        msg = email.message_from_bytes(data[0][1])
        parts = _attachment_parts(msg)
        if wanted >= len(parts):
            return None
        part = parts[wanted]
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        if len(payload) > ATTACH_MAX_BYTES:
            return None
        return {
            "filename": _decode_header_text(part.get_filename()) or ("附件 %d" % (wanted + 1)),
            "content_type": part.get_content_type() or "application/octet-stream",
            "size": len(payload),
            "payload": payload,
        }
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def mark_mail_seen(accounts: dict, uid: str, *, user_id: str = "-") -> dict:
    """【冻结接口】把**一封**邮件标记为已读，并把缓存里那一封就地改成已读。

    返回 `{"uid": uid, "unread": False}`。只做这一件事：不批量标记、不动别的邮件。
    失败抛 PlatformError（HTTP 语义与 `/app/mail/<uid>/` 一致：404 找不到 / 502 抓取失败）。
    """
    section = _sect(accounts, "mail")
    user, secret, _host = mail_config(section)
    if not user or not secret:
        raise LoginFailed(LOGIN_FAILED_MESSAGE)
    clean = str(uid or "").strip()
    if not _UID_RE.match(clean):
        raise MailNotFound(NOT_FOUND_MESSAGE)
    _mark_mail_seen(section, clean)
    _cache_note_mail_read(user_id, accounts, clean)
    return {"uid": clean, "unread": False}


# ============================================================ 邮件发送（SMTP）

def _smtp_host_from_imap(imap_host: str) -> str:
    """从 IMAP 主机推导 SMTP 主机：`imap.qiye.163.com` → `smtp.qiye.163.com`。"""
    host = (imap_host or "").strip().lower()
    if host.startswith("imap."):
        return "smtp." + host[5:]
    return DEFAULT_SMTP_HOST


def _parse_recipients(raw: str) -> list[str]:
    """把逗号/分号/换行分隔的收件人拆成列表，每项去空白。"""
    import re as _re
    parts = _re.split(r"[,;\n\r]+", str(raw or ""))
    return [p.strip() for p in parts if p.strip()]


def _validate_email(addr: str) -> bool:
    """极简邮箱格式校验：`x@y.z` 且不含空白。"""
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", addr.strip()))


def _classify_smtp(exc: Exception) -> PlatformError:
    """把 smtplib 异常分类成面向用户的中文 PlatformError（口令不外泄）。"""
    names = " ".join(cls.__name__.lower() for cls in type(exc).__mro__)
    text = str(exc).lower()

    # 认证失败：SMTPAuthenticationError / BAD AUTH / 535 / authentication
    if ("smtpauthenticationerror" in names
            or "authentication" in names
            or "535" in text
            or "bad auth" in text
            or "login failed" in text
            or "用户名或密码" in text
            or "用户名或授权码" in text):
        return PlatformError(MAIL_SEND_AUTH_FAILED)

    # 连接失败
    if ("connectionrefused" in names
            or "timeout" in names
            or "timed out" in text
            or "connect" in names
            or "connection" in names
            or "ssl" in names
            or "errno" in text
            or "network" in text):
        return PlatformError(MAIL_SEND_CONNECT_FAILED)

    # 收件人被拒
    if ("recipient" in text and "reject" in text) or "550" in text or "551" in text:
        return PlatformError(MAIL_SEND_REJECTED)

    return PlatformError(MAIL_SEND_UNKNOWN_ERROR)


def _is_smtp_auth_error(exc: Exception) -> bool:
    """判断是否是认证错误（用于更精准的 502 消息）。"""
    names = " ".join(cls.__name__.lower() for cls in type(exc).__mro__)
    text = str(exc).lower()
    return ("smtpauthenticationerror" in names
            or "authentication" in names
            or "535" in text
            or "bad auth" in text)


def _is_smtp_connect_error(exc: Exception) -> bool:
    """判断是否是连接错误。"""
    names = " ".join(cls.__name__.lower() for cls in type(exc).__mro__)
    text = str(exc).lower()
    return ("connectionrefused" in names
            or "timeout" in names
            or "timed out" in text
            or "connect" in names
            or "errno" in text
            or "network" in names)


def send_mail(accounts: dict, *, to: str, cc: str = "",
              subject: str = "", body_text: str = "",
              in_reply_to: str = "",
              attachments: list | None = None) -> dict:
    """【冻结接口】通过 SMTP 发送邮件。

    参数:
        accounts: 解密后的 accounts 映射（与 fetch_mail 同构）
        to: 收件人（逗号/分号/换行分隔，必填）
        cc: 抄送（可选，同上格式）
        subject: 主题（必填）
        body_text: 纯文本正文（必填）
        in_reply_to: 回复某封邮件的 Message-ID（可选，用于 In-Reply-To / References 头）
        attachments: 附件列表（可选），每项 `{"filename": str, "content_type": str, "data": bytes}`。
            组信用标准库的 `EmailMessage.add_attachment()` —— 它自己会做 base64 与
            **RFC 2231 文件名编码**（中文名不会变成乱码），不用手写 MIME。

    返回:
        {"to": [...], "cc": [...], "subject": "...", "sent_at": "ISO8601",
         "attachments": [{"filename":..., "size":...}, ...]}

    异常:
        PlatformError: 含面向用户的中文消息（认证失败/连接失败/参数错误/附件超限）
    """
    import smtplib
    from email.message import EmailMessage
    from datetime import datetime, timezone

    # ---- 1. 参数校验 ----
    to_list = _parse_recipients(to)
    if not to_list:
        raise PlatformError("缺少收件人地址")
    if len(to_list) > MAIL_SEND_MAX_RECIPIENTS:
        raise PlatformError(f"收件人数量超过上限（最多 {MAIL_SEND_MAX_RECIPIENTS} 个）")
    for addr in to_list:
        if not _validate_email(addr):
            raise PlatformError(f"收件人地址格式非法：{addr}")

    cc_list = _parse_recipients(cc)
    if len(cc_list) > MAIL_SEND_MAX_RECIPIENTS:
        raise PlatformError(f"抄送数量超过上限（最多 {MAIL_SEND_MAX_RECIPIENTS} 个）")
    for addr in cc_list:
        if not _validate_email(addr):
            raise PlatformError(f"抄送地址格式非法：{addr}")

    subject = str(subject or "").strip()
    if not subject:
        raise PlatformError("缺少邮件主题")
    if len(subject) > MAIL_SUBJECT_MAX_CHARS:
        raise PlatformError(f"邮件主题太长（最多 {MAIL_SUBJECT_MAX_CHARS} 字）")

    body_text = str(body_text or "")
    if not body_text.strip():
        raise PlatformError("缺少邮件正文")
    if len(body_text) > MAIL_BODY_TEXT_MAX_CHARS:
        raise PlatformError(f"邮件正文太长（最多 {MAIL_BODY_TEXT_MAX_CHARS} 字）")

    # ---- 1b. 附件校验（先全部验完再连 SMTP：别让用户等半天才被告知超限）----
    parts: list[tuple[str, str, bytes]] = []
    total_bytes = 0
    for raw in (attachments or []):
        item = raw if isinstance(raw, dict) else {}
        data = item.get("data")
        if not isinstance(data, (bytes, bytearray)):
            raise PlatformError("附件内容读不出来，请重新选择文件")
        payload = bytes(data)
        name = str(item.get("filename") or "").strip() or "附件"
        name = name.replace("\r", " ").replace("\n", " ")[:200]
        ctype = str(item.get("content_type") or "").strip() or "application/octet-stream"
        if len(payload) > MAIL_SEND_MAX_ATTACHMENT_BYTES:
            raise PlatformError(
                f"附件「{name}」太大（单个最多 {MAIL_SEND_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB）")
        total_bytes += len(payload)
        parts.append((name, ctype, payload))
    if len(parts) > MAIL_SEND_MAX_ATTACHMENTS:
        raise PlatformError(f"附件个数超过上限（最多 {MAIL_SEND_MAX_ATTACHMENTS} 个）")
    if total_bytes > MAIL_SEND_MAX_TOTAL_ATTACHMENT_BYTES:
        raise PlatformError(
            f"附件总大小超过上限（最多 {MAIL_SEND_MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MB）")

    # ---- 2. 取凭据 ----
    section = _sect(accounts, "mail")
    user = account_username(section)
    secret = account_secret(section, prefer_authcode=True)
    if not user or not secret:
        raise PlatformError(MAIL_NOT_CONFIGURED_MESSAGE)

    imap_host = ""
    if isinstance(section, dict):
        raw_host = section.get("imap_host")
        if isinstance(raw_host, str) and raw_host.strip():
            imap_host = raw_host.strip()
    smtp_host = _smtp_host_from_imap(imap_host)

    # ---- 3. 构造邮件 ----
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.set_content(body_text, charset="utf-8")

    if in_reply_to:
        clean_reply = str(in_reply_to).strip()
        if clean_reply.startswith("<") and clean_reply.endswith(">"):
            msg["In-Reply-To"] = clean_reply
            msg["References"] = clean_reply

    for name, ctype, payload in parts:
        maintype, _, subtype = ctype.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        # add_attachment 自己处理 base64 与 RFC 2231 文件名编码（中文名安全）
        msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=name)

    # ---- 4. SMTP 发送 ----
    started = time.monotonic()
    all_recipients = to_list + cc_list
    try:
        with smtplib.SMTP_SSL(smtp_host, SMTP_PORT, timeout=20) as smtp:
            smtp.login(user, secret)
            smtp.send_message(msg, from_addr=user, to_addrs=all_recipients)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        err = _classify_smtp(exc)
        # 日志：只记 平台/耗时/成败原因/收件人数，**不记地址全文、不记口令**
        domain_counts = {}
        for addr in all_recipients:
            domain = addr.split("@")[-1] if "@" in addr else "?"
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
        logger.warning("send_mail user=%s elapsed=%.2fs ok=0 recipients=%d attachments=%d reason=%s",
                       user, elapsed, len(all_recipients), len(parts),
                       ",".join(f"{d}×{c}" for d, c in domain_counts.items()),
                       error_message(err))
        raise err from exc

    elapsed = time.monotonic() - started
    # 日志：只记 平台/耗时/成功/收件人数/附件数与总字节/域名（**不记口令与正文**）
    domain_counts = {}
    for addr in all_recipients:
        domain = addr.split("@")[-1] if "@" in addr else "?"
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    logger.info("send_mail user=%s elapsed=%.2fs ok=1 recipients=%d attachments=%d bytes=%d domains=%s",
                user, elapsed, len(all_recipients), len(parts), total_bytes,
                ",".join(f"{d}×{c}" for d, c in domain_counts.items()))

    sent_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "to": to_list,
        "cc": cc_list,
        "subject": subject,
        "sent_at": sent_at,
        "attachments": [{"filename": n, "size": len(d)} for n, _c, d in parts],
    }


# ---- 给 server.py 的错误映射（HTTP 状态码只在这里决定）----

def error_message(error: Exception | str) -> str:
    """异常 → 可读中文原因（绝不回 traceback / 原始响应）。"""
    if isinstance(error, str):
        return _clean_message(error) or "抓取失败，请稍后重试"
    return _language_error("error", error)


def http_status_for(error: Exception | str) -> int:
    """平台错误 → HTTP 状态码：邮件不存在 = 404，其余（缺依赖/连不上/登录失败）= 502。

    登录失败也归 502：账号是用户自己填错的，但契约只给了 502 兜底，
    前端按 `errors` / `error.message` 的中文文案展示。
    """
    if isinstance(error, MailNotFound):
        return 404
    return 502
