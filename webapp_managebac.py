"""ManageBac 学生端纯 HTTP 客户端（官网抓取层本地副本，无浏览器/无 Selenium）。

来源：`D:\\phl-lite-dev\\hellopinghe\\managebac\\client.py`（用户自己的代码）。
改动只有四处，都是**为了去掉跨包依赖 / 适配官网环境**：
1. `from ..exceptions import ...` → 本文件底部定义同名本地异常类
   （`LoginError` / `LoginRequiredError`）；
2. 删掉官网用不到的“写”接口（发讨论回复）与 CAS/EE 摘要页方法；
3. `get_classes()` 的翻页循环加了一句「本页没有新课程就停手」——
   原版只在遇到 “No classes found” 时停，若站点一直回同一页会白翻 20 次；
4. **2026-09-16：`fetch_view()` 改成「课程列表先完整产出 + 逐课并发抓取（`MB_WORKERS=5`）」**
   —— 以前课程和作业挤在同一个循环里、预算一超就 `break`，还有 `MAX_COURSES=15` 硬顶，
   用户实测只看到 7 门课（客户端 `get_all_tasks()` 是全量遍历、没有上限）。
   现在课程一门不漏；总评/作业并发取，超预算时只把 `partial` 置真，由页面如实说明
   「还有一部分在抓」。其余方法（`login` / `is_logged_in` / `get_class_tasks` /
   `get_class_units` 等）**逐字未改**（已用 AST 比对确认）。

登录原理（已在 shph.managebac.cn/login 实测验证）：
1. GET /login          → Rails 表单 #session_form (action=/sessions, POST)
                         隐藏域 authenticity_token (CSRF) + _managebac_session cookie
2. POST /sessions      → session[login] / session[password] / authenticity_token / remember_me
3. 之后所有数据页只需带 _managebac_session cookie,普通 GET 即可
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from webapp_mb_parse import extract_classes, extract_overall_grade, extract_task_cards

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

#: 登录失败时页面上可能出现的关键词(抄 managebac-mcp 的容错思路)
_FAILURE_PATTERNS = re.compile(r"invalid|incorrect|locked|required", re.IGNORECASE)

#: 逐课抓取的并发度：每门课要打 2 个请求（units + core_tasks），顺序抓 20 门课
#: 必然是 40 个请求、远超预算 —— 这正是以前"课程只出来一部分"的另一半原因。
#: 5 个并发对学校服务器是温和的（客户端是顺序 + 0.3s 间隔，我们靠缓存摊平）。
MB_WORKERS = 3

#: 单门课的作业卡上限（防某一门课作业特别多时把预算吃光）
MAX_TASKS_PER_COURSE = 60


@dataclass
class LoginProbe:
    """对登录页的无害探测结果(不需要账号)."""

    url: str
    status: int
    form_action: str | None
    has_login_field: bool
    has_password_field: bool
    has_csrf: bool
    cookies: list[str]


class ManageBacClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------ 基础
    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _get(self, path: str) -> requests.Response:
        resp = self.session.get(self._url(path), timeout=self.timeout)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------ 登录
    def probe_login(self) -> LoginProbe:
        """探测登录页:验证表单/CSRF 是否为标准 Rails 结构,无需任何凭据."""
        resp = self.session.get(self._url("/login"), timeout=self.timeout)
        soup = _soup(resp.text)
        form = soup.find("form", id="session_form")
        return LoginProbe(
            url=str(resp.url),
            status=resp.status_code,
            form_action=form.get("action") if form else None,
            has_login_field=bool(soup.find(id="session_login")),
            has_password_field=bool(soup.find(id="session_password")),
            has_csrf=bool(soup.find(attrs={"name": "authenticity_token"})),
            cookies=[c.name for c in self.session.cookies],
        )

    def _csrf_token(self, soup) -> str | None:
        tag = soup.find("input", attrs={"name": "authenticity_token"})
        if tag and tag.get("value"):
            return tag["value"]
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        return meta["content"] if meta else None

    def login(self, email: str, password: str, remember: bool = True) -> None:
        """纯 HTTP 账密登录。失败抛 LoginError(绝不重试,防锁号)."""
        resp = self.session.get(self._url("/login"), timeout=self.timeout)
        resp.raise_for_status()
        soup = _soup(resp.text)
        form = soup.find("form", id="session_form")
        if form is None:
            raise LoginError("登录页结构异常: 未找到 #session_form(学校可能启用了 SSO?)")

        # 字段名从表单里读,不硬编码(新版 UI 是 login/password,老版是 session[login]/...)
        login_field = soup.find(id="session_login") or form.find(attrs={"type": "email"})
        password_field = soup.find(id="session_password") or form.find(attrs={"type": "password"})
        login_name = (login_field.get("name") if login_field else None) or "login"
        password_name = (password_field.get("name") if password_field else None) or "password"

        payload: dict[str, str] = {
            "authenticity_token": self._csrf_token(soup) or "",
            login_name: email,
            password_name: password,
            "remember_me": "1" if remember else "0",
        }
        commit = form.find(attrs={"name": "commit"})
        if commit is not None and commit.get("value"):
            payload["commit"] = commit["value"]

        action = form.get("action") or "/sessions"
        resp = self.session.post(
            self._url(action), data=payload, timeout=self.timeout, allow_redirects=True
        )

        final = _soup(resp.text)
        landed_on_login = "/login" in str(resp.url) or final.find(id="session_password")
        if landed_on_login:
            line = next(
                (ln for ln in final.get_text("\n").splitlines() if _FAILURE_PATTERNS.search(ln)),
                None,
            )
            raise LoginError(f"登录被拒绝(密码错误/账号锁定/需要 SSO)。页面提示: {line}")

        if not self.is_logged_in():
            raise LoginError("登录请求已发出,但会话未生效(请检查账号类型是否为学生账号)")

    def is_logged_in(self) -> bool:
        """检查会话:访问 /student,若又出现密码框则已失效."""
        resp = self._get("/student")
        return _soup(resp.text).find(id="session_password") is None

    # ------------------------------------------------------------ 数据
    def get_classes(self) -> dict[str, str]:
        """全部课程 {class_id: 课程名},自动翻页."""
        classes: dict[str, str] = {}
        for page in range(1, 21):
            resp = self._get(f"/student/classes/my?page={page}")
            found = extract_classes(resp.text)
            if not found:
                break
            before = len(classes)
            classes.update(found)
            if len(classes) == before:
                break
        return classes

    def get_overall_grades(self) -> dict[str, str | None]:
        """各科总评 {课程名: 总评文本}(遍历课程, 注意限速)."""
        grades: dict[str, str | None] = {}
        for class_id, name in self.get_classes().items():
            resp = self._get(f"/student/classes/{class_id}/units")
            grades[name] = extract_overall_grade(resp.text)
        return grades

    def get_class_tasks(self, class_id: str, class_name: str = "") -> list:
        """单门课的全部作业卡(含已截止的)."""
        resp = self._get(f"/student/classes/{class_id}/core_tasks")
        return extract_task_cards(resp.text, class_id=class_id, class_name=class_name)

    def get_class_units(self, class_id: str) -> dict:
        """课程 Units 页侧栏总评(官网只要一个文本摘要)."""
        resp = self._get(f"/student/classes/{class_id}/units")
        return {"grade": extract_overall_grade(resp.text)}


# ---------------------------------------------------------------- 官网抓取层入口

def _due_fields(card) -> dict:
    """作业卡 → 契约里的「截止时间」三件套。

    **这里是 DDL 过滤失效的根因所在**：原来只把卡片的可见文本 `card.due_text`
    塞进 `due`（形如 `Thursday at 12:00 PM`，没有年月日），而**把解析器算好的
    `card.due_at` 整个丢掉** → 前端根本没法做「±14 天窗口 / 已过期」判断。

    现在：
      * `due`       = `due_at` 的 ISO 形态 `YYYY-MM-DD HH:MM`（机器可读，排序/比较都对）；
                      真解析不出来时才退回可见文本（前端会把它当「时间未知」）。
      * `due_text`  = 页面上那句给人看的原文（保留，便于用户对照）。
      * `due_inferred` = True 表示 `due` 是按「周几」推算的（不是页面上的精确日期）。
    """
    due_at = getattr(card, "due_at", None)
    text = (getattr(card, "due_text", "") or "").strip()
    out = {"due_text": text}
    if due_at is not None:
        out["due"] = due_at.strftime("%Y-%m-%d %H:%M")
        out["due_iso"] = due_at.strftime("%Y-%m-%dT%H:%M")
        if getattr(card, "due_inferred", False):
            out["due_inferred"] = True
    else:
        out["due"] = text                      # 实在没有日期 → 原样给文本（前端标「时间未知」）
    if getattr(card, "past_due", False):
        out["past_due"] = True
    return out


def fetch_view(email: str, password: str, base_url: str, *,
               timeout: float = 12.0, budget_seconds: float = 16.0) -> dict:
    """登录并抓「课程 + 作业 + 总评」，返回官网契约结构。

    返回 `{"courses": [{"name","grade","units","id"}], "tasks": [...]}`。

    **2026-09-16 修「课程列表不全」**（用户实测只看到 7 门，客户端是全量）：
    1. 课程列表**先完整产出** —— 以前课程和作业挤在同一个 `for` 里，预算一超就 `break`，
       后面的课程**根本不进列表**；现在课程来自 `get_classes()` 的那一页，一门不漏；
    2. 去掉 `MAX_COURSES` 硬顶（客户端 `get_all_tasks()` 也是全量遍历，没有上限）；
    3. 逐课的总评/作业改为**并发抓取**（每门课 2 个请求，顺序抓 20 门 = 40 个请求必超预算，
       这正是"只拿到一部分课程"的另一半原因）；超预算时停止提交新任务，已拿到的照样返回，
       并把 `partial` 置真（调用方据此在页面上如实说明「还在抓另一部分」）。
    """
    started = time.monotonic()
    client = ManageBacClient(base_url, timeout=timeout)
    client.login(email, password)

    classes = client.get_classes()                      # 全部课程（不截断）
    courses: list[dict] = [
        {"name": name, "grade": "", "units": "", "id": str(class_id)}
        for class_id, name in classes.items()
    ]

    tasks: list[dict] = []
    partial = False
    grade_by_id: dict[str, str] = {}
    # 登录后的会话 cookie 在多线程里共用一份快照（每个 worker 各自一个 Session → 不共享可变状态）
    cookie_snapshot = client.session.cookies.get_dict()

    def _work_one(item: tuple[str, str]):
        class_id, name = item
        c = ManageBacClient(base_url, timeout=timeout)
        for k, v in cookie_snapshot.items():
            c.session.cookies.set(k, v)
        grade = ""
        try:
            units = c.get_class_units(class_id)
            grade = str(units.get("grade") or "")
        except Exception:  # noqa: BLE001  单科总评取不到不影响其它信息
            grade = ""
        rows: list[dict] = []
        try:
            for card in c.get_class_tasks(class_id, class_name=name)[:MAX_TASKS_PER_COURSE]:
                task = {
                    "course": name,
                    "title": card.title,
                    "status": card.status or "",
                    "score": card.score_text or "",
                    "id": str(card.task_id or ""),
                }
                task.update(_due_fields(card))
                rows.append(task)
        except Exception:  # noqa: BLE001  单科作业抓不到不影响其它课
            return str(class_id), grade, rows, True
        return str(class_id), grade, rows, False

    items = list(classes.items())
    if items:
        with ThreadPoolExecutor(max_workers=MB_WORKERS) as pool:
            futures: dict = {}
            skipped = False
            for it in items:
                if time.monotonic() - started > budget_seconds:
                    partial = True               # 预算用尽：剩下的课这轮不抓（课程列表仍然完整）
                    skipped = True
                    break
                futures[it] = pool.submit(_work_one, it)
            # 按**课程顺序**取结果（不是完成顺序）：输出稳定、也便于与假页面逐条对照
            for it in items:
                fut = futures.get(it)
                if fut is None:
                    continue
                try:
                    cid, grade, rows, per_course_partial = fut.result()
                except Exception:  # noqa: BLE001
                    partial = True
                    continue
                if per_course_partial:
                    partial = True
                if grade:
                    grade_by_id[cid] = grade
                tasks.extend(rows)
            if skipped:
                partial = True

    for c in courses:                             # 总评回填（拿不到的留空 → 页面显示「未出分」）
        g = grade_by_id.get(c["id"], "")
        if g:
            c["grade"] = g

    return {"courses": courses, "tasks": tasks, "partial": partial}


# ---------------------------------------------------------------- 活动与课程详情入口

def _fetch_activity(email: str, password: str, base_url: str, operation: str, *,
                    timeout: float = 12.0, **kwargs) -> dict:
    """Use one student login per operation; always release its HTTP session.

    Data/cache isolation belongs to the caller, just as with fetch_view. Never
    pass an institution API token in the password field: this is the HTML client.
    """
    import webapp_mb_stream as activity

    handlers = {"notifications": activity.fetch_notifications,
                "messages": activity.fetch_messages,
                "discussions": activity.fetch_discussions,
                "class_details": activity.fetch_class_details}
    client = ManageBacClient(base_url, timeout=timeout)
    try:
        client.login(email, password)
        return handlers[operation](client, **kwargs)
    finally:
        client.session.close()


def fetch_notifications(email: str, password: str, base_url: str, *,
                        timeout: float = 12.0) -> dict:
    """Student notification/announcement feed: notifications/status/partial/sources."""
    return _fetch_activity(email, password, base_url, "notifications", timeout=timeout)


def fetch_messages(email: str, password: str, base_url: str, *,
                   class_id: str | None = None, timeout: float = 12.0,
                   budget_seconds: float = 16.0) -> dict:
    """Personal assignment/exam/grade/discussion cards, at most 100 per result."""
    return _fetch_activity(email, password, base_url, "messages", timeout=timeout,
                           class_id=class_id, budget_seconds=budget_seconds)


def _resolve_class_id(client, class_id: str) -> tuple[str, str]:
    """把调用方给的 `class_id` 归一成（数字 id, 课程名）。

    前端有时只拿得到**课程名**（例如「上次同步的快照」里的旧数据不带编号）。
    以前这种情况直接 `raise ValueError('invalid_class_id')` → 路由回 400，
    用户看到的就是「加载失败：HTTP 400」。现在：数字 id 照旧；不是数字就当成
    课程名去 `get_classes()` 里找同名的那一门 —— 找得到就用它的 id，
    找不到才算真的不合法。**用户点一门课，应该能打开，而不是先撞一个 400。**
    """
    raw = str(class_id or "").strip()
    classes = {str(k): str(v) for k, v in client.get_classes().items()}
    if raw.isdigit():
        if raw not in classes:
            raise PermissionError("class_not_accessible")
        return raw, classes[raw]
    # 不是数字 → 按课程名匹配（去空白、忽略大小写）
    want = "".join(raw.split()).lower()
    if want:
        for cid, name in classes.items():
            if "".join(name.split()).lower() == want:
                return cid, name
    raise ValueError("invalid_class_id")


def fetch_discussions(email: str, password: str, base_url: str, class_id: str, *,
                      timeout: float = 12.0) -> dict:
    """Discussion collection with explicit status and current-course access check."""
    client = ManageBacClient(base_url, timeout=timeout)
    try:
        client.login(email, password)
        resolved, course = _resolve_class_id(client, class_id)
        import webapp_mb_stream as activity
        result = activity.fetch_discussions(client, resolved, course)
        return {"messages": result["items"], "status": result["status"],
                "available": result["available"], "data": result["data"], "note": result["note"],
                "partial": result["partial"], "sources": {"discussions": result["status"]},
                "pages": result["pages"], "classId": resolved, "name": course}
    finally:
        client.session.close()


def fetch_class_details(email: str, password: str, base_url: str, class_id: str, *,
                        timeout: float = 12.0) -> dict:
    """Accessible course metadata, personal grades, members and resources.

    Unknown sections remain unavailable; empty collections do not imply the
    upstream school has no such data. See MANAGEBAC-DATA-CONTRACT.md.
    `class_id` 可以是数字编号，也可以是课程名（见 `_resolve_class_id`）。
    """
    client = ManageBacClient(base_url, timeout=timeout)
    try:
        client.login(email, password)
        resolved, _course = _resolve_class_id(client, class_id)
        import webapp_mb_stream as activity
        return activity.fetch_class_details(client, resolved)
    finally:
        client.session.close()


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


# ---- 本地异常类（原库用 hellopinghe.exceptions，这里自带一份，语义相同）----

class PingheError(Exception):
    """本地异常基类（仅为不再依赖 PLL 包）."""


class LoginError(PingheError):
    pass


class LoginRequiredError(PingheError):
    pass
