"""Read-only, capability-aware ManageBac student activity adapter.

The existing client authenticates with a student cookie, not a Public API token.
Only same-origin student pages and links discovered on those pages are followed.
HTML is reduced to text; upstream HTML never becomes trusted browser markup.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

MAX_PAGES = 5
MAX_ITEMS = 100
KINDS = ("discussion", "assignment", "exam", "grade", "announcement", "resource")


def safe_link(base_url, value):
    """Return only same-origin HTTP(S) URLs, without credential-bearing URLs."""
    if not isinstance(value, str) or not value.strip():
        return ""
    target = urlsplit(urljoin(base_url.rstrip('/') + '/', value))
    base = urlsplit(base_url)
    if target.scheme not in ("https", "http") or target.netloc != base.netloc:
        return ""
    if target.username or target.password:
        return ""
    return target.geturl()


def _text(node, limit=4000):
    if node is None:
        return ""
    # Do not leak scripts into card text, even though callers also escape text.
    for tag in node.select("script, style"):
        tag.decompose()
    return node.get_text(" ", strip=True)[:limit]


def message_type(value):
    text = str(value or "").lower()
    for pattern, kind in ((r"discussion|thread|讨论", "discussion"),
                          (r"announcement|公告", "announcement"),
                          (r"exam|quiz|test|考试|测验", "exam"),
                          (r"grade|score|成绩", "grade"),
                          (r"resource|attachment|file|资源", "resource"),
                          (r"assignment|task|coursework|作业", "assignment")):
        if re.search(pattern, text):
            return kind
    return "other"


def _date(node):
    tag = node.select_one("time, [data-timestamp], .date, .timestamp")
    if tag:
        return str(tag.get("datetime") or tag.get("data-timestamp") or _text(tag, 100))[:100]
    match = re.search(r"Posted on\s+(.+? (?:AM|PM))", _text(node))
    return match.group(1)[:100] if match else ""


def parse_messages(html, base_url, *, class_id="", course="", default_type="other"):
    """Parse explicit activity nodes, never arbitrary dashboard links as events.

    Discussion selectors are established by the desktop client. Notification
    selectors are conservative adapters and require a visible recognized node.
    """
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select("div.discussion[id^=discussion_], [data-notification-id], "
                        ".notification-item, .announcement-item, [data-announcement-id]")
    result, seen = [], set()
    for node in nodes:
        title = node.select_one(".h4.title a, .h4.title, .title, .notification-title, h3, h4")
        anchor = (title if title and title.name == "a" else title.find("a") if title else None)
        anchor = anchor or node.select_one("a[href]")
        title_text = _text(title or anchor, 300)
        if not title_text:
            continue
        link = safe_link(base_url, anchor.get("href", "") if anchor else "")
        source_type = node.get("data-type") or " ".join(node.get("class", []))
        kind = message_type(source_type)
        if kind == "other":
            kind = message_type(link) if link else default_type
        if kind == "other" and default_type != "other":
            kind = default_type
        identity = str(node.get("data-notification-id") or node.get("data-announcement-id") or
                       node.get("id") or hashlib.sha256((link + title_text).encode()).hexdigest()[:20])
        if identity in seen:
            continue
        seen.add(identity)
        class_match = re.search(r"/classes/(\d+)(?:/|$)", link)
        cid = str(class_id or (class_match.group(1) if class_match else ""))
        course_node = node.select_one(".course, .class-name, [data-course]")
        content = node.select_one(".fr-view, .discussion-body, .body, .content, .description")
        classes = node.get("class", [])
        read_attr = node.get("data-read")
        read = (read_attr.lower() in ("true", "1")) if isinstance(read_attr, str) else (
            False if "unread" in classes else True if "read" in classes else None)
        result.append({"id": identity, "type": kind, "title": title_text,
                       "content": _text(content), "course": course or _text(course_node, 200),
                       "class_id": cid, "classId": cid, "date": _date(node),
                       "status": _text(node.select_one(".status"), 100), "read": read,
                       "link": link, "author": _text(node.select_one(".author"), 200)})
    return result


def _page(client, path):
    response = client._get(path)
    soup = BeautifulSoup(response.text, "html.parser")
    if "/login" in str(response.url) or soup.select_one("#session_password, input[type=password]"):
        raise PermissionError("student_session_expired")
    _dump_html("page-" + urlsplit(str(response.url)).path.replace("/", "_"), response.text)
    return response.text


#: 诊断转储目录 / 开关。**默认关闭**：只在排障时 `export PHIX_MB_DUMP=1`，
#: 把抓到的页面原样存到 /tmp 供照着真实结构写选择器（不进仓库、不回前端）。
#: 2026-09-17 就是靠它拿到真实页面，才发现「通知」根本不在 HTML 里
#: （走 mnn-hub 微服务），从而把一版永远不可能命中的选择器换掉。
_DUMP_DIR = os.environ.get("PHIX_MB_DUMP_DIR") or "/tmp/phix-mb-dump"
_DUMP_ENABLED = (os.environ.get("PHIX_MB_DUMP") or "").strip() not in ("", "0", "false", "no")
_DUMP_MAX_BYTES = 600_000


def _dump_html(tag, html):
    """把一段 HTML 存到 /tmp 供排障（默认关闭；失败就算了，绝不影响抓取）。"""
    if not _DUMP_ENABLED:
        return
    try:
        os.makedirs(_DUMP_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(tag))[:60]
        with open(os.path.join(_DUMP_DIR, safe + ".html"), "w", encoding="utf-8") as f:
            f.write((html or "")[:_DUMP_MAX_BYTES])
    except Exception:  # noqa: BLE001
        pass


def _failure(exc):
    if isinstance(exc, PermissionError):
        return "authentication_required"
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
        return {401: "authentication_required", 403: "forbidden", 404: "unavailable", 429: "rate_limited"}.get(status, "upstream_error")
    if isinstance(exc, requests.Timeout):
        return "timeout"
    return "upstream_error"


def _discovered_link(html, base_url, pattern):
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        link = safe_link(base_url, anchor.get("href", ""))
        if link and re.search(pattern, urlsplit(link).path):
            return link
    return ""


def _collection(client, path, *, class_id="", course="", kind="other", first_html=None):
    result, ids, pages, visited = [], set(), 0, set()
    current = safe_link(client.base_url, path)
    if not current:
        return {"items": [], "status": "unavailable", "partial": False, "pages": 0}
    try:
        while current and pages < MAX_PAGES:
            if current in visited:
                return {"items": result, "status": "partial", "partial": True, "pages": pages}
            visited.add(current)
            html = first_html if pages == 0 and first_html is not None else _page(client, current)
            pages += 1
            rows = parse_messages(html, client.base_url, class_id=class_id, course=course, default_type=kind)
            for row in rows:
                key = row["id"]
                if key not in ids:
                    ids.add(key)
                    result.append(row)
                if len(result) >= MAX_ITEMS:
                    return {"items": result, "status": "partial", "partial": True, "pages": pages}
            soup = BeautifulSoup(html, "html.parser")
            nxt = soup.select_one("a[rel~=next][href], .pagination a.next[href]")
            current = safe_link(client.base_url, nxt.get("href", "")) if nxt else ""
            # Unknown markup must not be presented as an authoritative empty feed.
            if not rows and not soup.select_one(".empty-state, .no-records, [data-empty=true]"):
                _dump_html("unrecognized-" + urlsplit(current).path.replace("/", "_"),
                           first_html if pages == 1 and first_html is not None else html)
                return {"items": result, "status": "unrecognized" if not result else "partial", "partial": True, "pages": pages}
        return {"items": result, "status": "partial" if current else "ok", "partial": bool(current), "pages": pages}
    except (requests.RequestException, PermissionError) as exc:
        return {"items": result, "status": _failure(exc), "partial": True, "pages": pages}


def _capability(payload, data):
    """Add the stable capability envelope without removing legacy fields."""
    status = payload.get("status", "unavailable")
    notes = {"ok": "已读取学生页面；结果范围仅限当前账号可见内容。",
             "partial": "仅取得部分数据，受分页上限、请求预算或数据源可用性限制。",
             "unavailable": "学生页面未提供可用入口或该来源不存在。",
             "unrecognized": "学生页面结构尚未识别，无法确认是否存在数据。",
             "authentication_required": "学生会话失效，需要重新登录。",
             "forbidden": "当前学生账号无权读取此来源。",
             "rate_limited": "上游限流，请保留缓存并稍后重试。",
             "timeout": "上游请求超时。",
             "not_loaded": "本轮请求预算不足，尚未读取。",
             "upstream_error": "上游读取失败。"}
    available = status == "ok" or bool(data)
    payload.update(available=available, data=data, note=notes.get(status, notes["upstream_error"]))
    return payload


def _student_client(session):
    """Accept an authenticated client, or requests.Session with base_url set.

    The caller owns and closes its session. No host is inferred from cookies.
    """
    if not isinstance(session, requests.Session):
        return session
    base_url = getattr(session, "base_url", "")
    if not isinstance(base_url, str) or urlsplit(base_url).scheme not in ("http", "https"):
        raise ValueError("session.base_url must be the configured student website URL")
    from webapp_managebac import ManageBacClient
    client = ManageBacClient(base_url)
    client.session.close()
    client.session = session
    return client


def fetch_announcements(session):
    """Notification + announcement feed with available/data/note envelope."""
    return fetch_notifications(_student_client(session))


def fetch_notifications(client):
    """「通知 + 待办」这一张卡的数据。

    **2026-09-17 照着真实页面重写（原来这套 HTML 选择器永远不可能命中）**：
    真实 ManageBac 的「通知」**不在学校站点的 HTML 里** —— 仪表盘上只有一个
    触发器元素，带这些属性：
        data-mnn-hub-endpoint="https://mnn-hub.prod.faria.cn"
        data-token="<JWT>"   data-namespace="student"   data-count="0"
        <div id="mnn-sidebar-content"></div>
    也就是说通知由**独立的 mnn-hub 服务**用那个 JWT 下发，页面里只有壳
    （实测：这个账号 data-count 就是 0，确实一条通知都没有）。
    上一版在这里扫 `.notification-item` 之类的类名 → 永远扫不到 → 卡片显示
    「页面结构没认出来」，把「其实是 0 条」说成了「读不出来」。

    现在如实分成两块：
      * `unread_count` —— 从仪表盘读 `data-count`，这是**权威的未读数**；
      * `notifications` —— `/student/tasks_and_deadlines` 这类真实 HTML 页面上的
        **待办 / 即将截止**条目（学生真正要看的就是这个）。
    通知正文需要 mnn-hub 的接口形状（未公开），所以页面上给一个直达
    ManageBac 通知中心的链接，而不是假装读过。
    """
    try:
        html = _page(client, "/student")
    except (requests.RequestException, PermissionError) as exc:
        return _capability({"notifications": [], "status": _failure(exc), "partial": True,
                            "sources": {}}, [])

    meta = _notification_meta(html)
    rows: list = []
    status = "ok"
    partial = False

    # 待办 / 截止日期：真实 HTML 页面
    try:
        deadline_html = _page(client, "/student/tasks_and_deadlines")
        rows = parse_deadline_rows(deadline_html, client.base_url)
    except (requests.RequestException, PermissionError) as exc:
        status = _failure(exc)
        partial = True

    payload = {
        "notifications": rows[:MAX_ITEMS],
        "unread_count": meta.get("count"),
        "hub": meta.get("hub", ""),
        "namespace": meta.get("namespace", ""),
        "notifications_url": _safe_student_url(client.base_url, meta.get("link") or "/student/notifications"),
        "status": status,
        "partial": partial or len(rows) > MAX_ITEMS,
        "sources": {"student_notifications": status},
    }
    # 一条待办都没有、而且通知数确实是 0 → 这是「真的没有」，不是「读不出来」
    if not rows and status == "ok":
        payload["status"] = "ok"
    return _capability(payload, rows[:MAX_ITEMS])


def _safe_student_url(base_url, path):
    from urllib.parse import urljoin
    return safe_link(base_url, urljoin(base_url.rstrip("/") + "/", str(path or "").lstrip("/")))


def _notification_meta(html):
    """从仪表盘的触发器元素上读通知的真实元数据（数 / hub / 命名空间）。"""
    soup = BeautifulSoup(html, "html.parser")
    el = (soup.select_one(".js-messages-and-notifications-trigger")
          or soup.select_one("[data-mnn-hub-endpoint]")
          or soup.select_one("[data-count]"))
    out = {"count": None, "hub": "", "namespace": "", "link": ""}
    if el is None:
        return out
    raw = el.get("data-count")
    try:
        out["count"] = int(str(raw).strip())
    except (TypeError, ValueError):
        out["count"] = None
    out["hub"] = str(el.get("data-mnn-hub-endpoint") or "")[:200]
    out["namespace"] = str(el.get("data-namespace") or "")[:40]
    anchor = soup.select_one('a[href*="/student/notifications"]')
    out["link"] = anchor.get("href") if anchor is not None else ""
    return out


def parse_deadline_rows(html, base_url):
    """解析 `/student/tasks_and_deadlines` 上的待办条目（**照真实结构写**）。

    2026-09-17 从真实页面 dump 出来的结构（`/tmp` 转储）：

        <div class="f-tile f-task-tile">
          <div class="f-tile__body">
            <p class="f-tile__title h5">
              <a class="f-tile__title-link" href="/student/classes/11516640/core_tasks/27588216">
                <span>behavior time</span></a>
            </p>
            <div class="f-tile__description">
              <span><svg class="fi-clock"/> Sep 20, 11:55 PM</span>
              <span class="vr"></span>
              <a href="/student/classes/11516640">IB DP 2028届 ESS SL by Yan (Grade 11)</a>
              <span class="badge"><span class="badge-label">Summative</span></span>
              <span class="badge"><span class="badge-label">Coursework</span></span>
              <span class="badge" data-bs-title="Waiting">…Pending…</span>
            </div>
          </div>
        </div>

    上一版找的是 `.task-item / .deadline-item / [data-task-id]` —— 一个都不存在，
    所以「待办」永远是 0 条。现在按上面的真实类名解析。
    """
    soup = BeautifulSoup(html, "html.parser")
    tiles = soup.select(".f-task-tile")
    if not tiles:
        tiles = soup.select(".js-tasks .f-tile")
    rows = []
    for tile in tiles:
        title_el = tile.select_one(".f-tile__title-link") or tile.select_one(".f-tile__title")
        title = _text(title_el, 200)
        if not title:
            continue
        link = safe_link(base_url, title_el.get("href", "")) if title_el is not None else ""

        class_id = ""
        match = re.search(r"/classes/(\d+)(?:/|$)", link or "")
        if match:
            class_id = match.group(1)

        course, due_text = "", ""
        desc = tile.select_one(".f-tile__description")
        if desc is not None:
            for anchor in desc.select('a[href*="/classes/"]'):
                href = anchor.get("href") or ""
                if re.search(r"/classes/\d+/?$", href):
                    course = _text(anchor, 200)
                    sub = re.search(r"/classes/(\d+)", href)
                    if sub and not class_id:
                        class_id = sub.group(1)
                    break
            clock = desc.find("span")
            due_text = _text(clock, 80)

        labels = [t for t in (_text(b, 60) for b in tile.select(".badge .badge-label")) if t]
        status_el = tile.select_one(".badge[data-bs-title]")
        status = _text(status_el, 60) if status_el is not None else (labels[-1] if labels else "")

        kind = message_type(" ".join(labels) + " " + title)
        if kind in ("other", "grade"):
            kind = "assignment"

        due_iso = _parse_due_text(due_text)
        rows.append({
            "id": hashlib.sha256((title + "|" + link + "|" + due_text).encode()).hexdigest()[:20],
            "type": kind,
            "title": title,
            "content": " · ".join(labels),
            "course": course,
            "classId": class_id,
            "class_id": class_id,
            "date": due_iso or due_text,
            "due": due_iso or due_text,
            "due_text": due_text,
            "status": status,
            "score": "",
            "link": link,
            "read": None,
            "author": "",
        })
    return rows


#: ManageBac 待办上的日期形态：`Sep 20, 11:55 PM` / `Sep 20` / `20 Sep 2026, 11:55 PM`
_DUE_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_due_text(text):
    """`Sep 20, 11:55 PM` → `2026-09-20 23:55`（解析不出来就回空串，前端显示原文）。

    年份：页面不给年份 → 用今年；如果算出来的日期已经过去 180 天以上，
    就当成明年（学期的截止日期通常在前方，不会突然跳到半年前）。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?"
                      r"(?:\s*,?\s*(\d{1,2}):(\d{2})\s*(AM|PM)?)?", raw, re.I)
    if not match:
        return ""
    mon_name = match.group(1)[:3].lower()
    if mon_name not in _DUE_MONTHS:
        return ""
    month = _DUE_MONTHS[mon_name]
    day = int(match.group(2))
    year = int(match.group(3)) if match.group(3) else datetime.now().year
    hour = int(match.group(4)) if match.group(4) else 23
    minute = int(match.group(5)) if match.group(5) else 55
    ampm = (match.group(6) or "").upper()
    if ampm == "PM" and hour < 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    try:
        moment = datetime(year, month, day, hour, minute)
    except ValueError:
        return ""
    if not match.group(3) and (datetime.now() - moment).days > 180:
        try:
            moment = moment.replace(year=year + 1)
        except ValueError:
            pass
    return moment.strftime("%Y-%m-%d %H:%M")


def fetch_discussions(session, class_id, course=""):
    client = _student_client(session)
    class_id = str(class_id)
    if not class_id.isdigit():
        raise ValueError("invalid_class_id")
    result = _collection(client, f"/student/classes/{class_id}/discussions", class_id=class_id,
                         course=course, kind="discussion")
    return _capability(result, result["items"])


def fetch_messages(client, class_id=None, *, budget_seconds=16.0):
    """Aggregate personal tasks/grades and discussions with a scheduling budget.

    Budget is checked between courses/sources; a pending HTTP call uses the
    client's timeout. Grade cards reference the task, not a fabricated grade date.
    """
    started = time.monotonic()
    courses = {str(key): name for key, name in client.get_classes().items()}
    if class_id is not None:
        class_id = str(class_id)
        if not class_id.isdigit():
            raise ValueError("invalid_class_id")
        if class_id not in courses:
            raise PermissionError("class_not_accessible")
        selected = {class_id: courses[class_id]}
    else:
        selected = courses
    rows, sources, truncated = [], {}, False
    for cid, name in selected.items():
        if time.monotonic() - started >= budget_seconds or len(rows) >= MAX_ITEMS:
            sources[cid] = {"tasks": "not_loaded", "discussions": "not_loaded"}
            truncated = True
            continue
        state = sources[cid] = {}
        try:
            cards = client.get_class_tasks(cid, class_name=name)
            state["tasks"] = "ok"
            for card in cards:
                kind = message_type(card.kind)
                if kind == "other":
                    kind = "assignment"
                row = {"id": f"{cid}:{kind}:{card.task_id}", "source_id": str(card.task_id),
                       "type": kind, "title": card.title, "content": "", "course": name,
                       "classId": cid, "class_id": cid, "date": "",
                       "due": card.due_at.isoformat() if card.due_at else card.due_text,
                       "due_inferred": bool(getattr(card, "due_inferred", False)),
                       "status": card.status or "", "score": card.score_text or "",
                       "link": safe_link(client.base_url, card.href), "read": None}
                rows.append(row)
                if card.score_text and len(rows) < MAX_ITEMS:
                    rows.append(dict(row, id=f"{cid}:grade:{card.task_id}", type="grade",
                                     related_id=row["id"]))
                if len(rows) >= MAX_ITEMS:
                    truncated = True
                    state["tasks"] = "partial"
                    break
        except (requests.RequestException, PermissionError) as exc:
            state["tasks"] = _failure(exc)
        if len(rows) >= MAX_ITEMS or time.monotonic() - started >= budget_seconds:
            state["discussions"] = "not_loaded"
            truncated = True
            continue
        # Do not immediately issue more requests after a throttling/auth failure.
        if state["tasks"] in ("rate_limited", "authentication_required"):
            state["discussions"] = "not_loaded"
            truncated = True
            break
        discussion = fetch_discussions(client, cid, name)
        state["discussions"] = discussion["status"]
        for item in discussion["items"]:
            item = dict(item, source_id=item["id"], id=f"{cid}:discussion:{item['id']}")
            rows.append(item)
            if len(rows) >= MAX_ITEMS:
                truncated = True
                state["discussions"] = "partial"
                break
        if state["discussions"] in ("rate_limited", "authentication_required"):
            truncated = True
            break
    partial = truncated or any(value != "ok" for section in sources.values() for value in section.values())
    return _capability({"messages": rows, "courses": [{"id": cid, "name": name} for cid, name in courses.items()],
                        "sources": sources, "partial": partial, "status": "partial" if partial else "ok"}, rows)


def fetch_exams(session, class_id):
    """Exams are task kinds in the student core_tasks page, not a separate API."""
    client = _student_client(session)
    result = fetch_messages(client, class_id=class_id)
    exams = [row for row in result["messages"] if row["type"] == "exam"]
    task_status = result["sources"].get(str(class_id), {}).get("tasks", "unavailable")
    return _capability({"status": task_status, "partial": task_status != "ok",
                        "sources": {"tasks": task_status}}, exams)


def _members(html):
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for node in soup.select("[data-student-id], .student-item, .class-member")[:MAX_ITEMS]:
        name = node.select_one(".name, .student-name")
        if name:
            result.append({"id": str(node.get("data-student-id") or ""), "name": _text(name, 200)})
    return result


def _resources(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    rows, seen = [], set()
    for anchor in soup.select(".resource-item a[href], .attachment a[href], .files a[href], a[href*='/attachments/']"):
        link = safe_link(base_url, anchor.get("href", ""))
        if link and link not in seen:
            seen.add(link)
            rows.append({"title": _text(anchor, 300), "link": link, "type": "resource"})
    return rows[:MAX_ITEMS]


def fetch_class_details(session, class_id):
    """Only expose courses in the authenticated student's own course list."""
    client = _student_client(session)
    class_id = str(class_id)
    if not class_id.isdigit():
        raise ValueError("invalid_class_id")
    courses = {str(k): v for k, v in client.get_classes().items()}
    if class_id not in courses:
        raise PermissionError("class_not_accessible")
    result = {"classId": class_id, "id": class_id, "name": courses[class_id], "teacher": "",
              "teaching_group": "", "students": [], "grades": [], "resources": [], "messages": [],
              "stats": {"total": 0, "completed": 0, "completion_rate": None, "average_score": None,
                        "scope": "loaded_tasks", "graded_count": 0},
              "sources": {"students": "unavailable", "resources": "unavailable", "basic": "unavailable"}}
    try:
        html = _page(client, f"/student/classes/{class_id}/units")
        soup = BeautifulSoup(html, "html.parser")
        result["teacher"] = _text(soup.select_one(".class-teacher, [data-class-teacher]"), 300)
        result["teaching_group"] = _text(soup.select_one(".teaching-group, [data-teaching-group]"), 300)
        result["sources"]["basic"] = "ok"
        for key, suffix, parser in (("students", "students|members", _members),
                                    ("resources", "resources|files", lambda text: _resources(text, client.base_url))):
            link = _discovered_link(html, client.base_url, rf"/student/classes/{class_id}/(?:{suffix})(?:/|$)")
            if link:
                try:
                    content = _page(client, link)
                    result[key] = parser(content)
                    result["sources"][key] = "ok" if result[key] else "unrecognized"
                except (requests.RequestException, PermissionError) as exc:
                    result["sources"][key] = _failure(exc)
    except (requests.RequestException, PermissionError) as exc:
        result["sources"]["basic"] = _failure(exc)
    try:
        cards = client.get_class_tasks(class_id, class_name=courses[class_id])
        completed = 0
        scores = []
        for card in cards[:MAX_ITEMS]:
            done = str(card.status or "").strip().lower() in ("submitted", "complete", "completed", "graded", "returned")
            completed += int(done)
            kind = message_type(card.kind)
            if kind == "other":
                kind = "assignment"
            row = {"id": str(card.task_id), "type": kind, "title": card.title,
                   "course": courses[class_id], "classId": class_id, "class_id": class_id,
                   "date": card.due_at.isoformat() if card.due_at else card.due_text,
                   "status": card.status or "", "score": card.score_text or "",
                   "link": safe_link(client.base_url, card.href), "content": ""}
            result["messages"].append(row)
            if card.score_text:
                result["grades"].append(dict(row, type="grade"))
                # Never average incompatible grade scales (IB 1–7, rubric, %).
                m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", card.score_text)
                if m and 0 <= float(m.group(1)) <= 100:
                    scores.append(float(m.group(1)))
        total = len(result["messages"])
        result["stats"].update(total=total, completed=completed,
                                completion_rate=round(completed / total * 100, 1) if total else None,
                                average_score=round(sum(scores) / len(scores), 2) if scores else None,
                                graded_count=len(result["grades"]), average_sample_count=len(scores),
                                average_unit="percent")
        result["sources"]["grades"] = "partial" if len(cards) > MAX_ITEMS else "ok"
    except (requests.RequestException, PermissionError) as exc:
        result["sources"]["grades"] = _failure(exc)
    discussions = fetch_discussions(client, class_id, courses[class_id])
    result["messages"].extend(discussions["items"])
    result["sources"]["discussions"] = discussions["status"]
    result["partial"] = any(value != "ok" for value in result["sources"].values())
    result["status"] = "partial" if result["partial"] else "ok"
    result["capabilities"] = {
        key: _capability({"status": result["sources"].get(key, "unavailable")}, result.get(key, []))
        for key in ("students", "resources", "grades")
    }
    # A shallow snapshot contains no envelope, so JSON has no recursive object.
    has_data = any(value == "ok" for value in result["sources"].values()) or bool(result["messages"])
    snapshot = dict(result)
    return _capability(result, [snapshot] if has_data else [])
