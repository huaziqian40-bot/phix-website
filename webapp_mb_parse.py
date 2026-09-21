"""ManageBac 学生端 HTML 解析器（官网抓取层本地副本）。

来源：`D:\\phl-lite-dev\\hellopinghe\\managebac\\parse.py`（用户自己的代码）**逐字搬来**，
只做了一件事：去掉跨包依赖（原文件无 `..` 相对导入，本身即自包含）。

选择器来源（已在真实页面验证，见原文件注释）：
- 课程列表页 /student/classes/my: li.f-menu-submenu-item > a[href] > span.f-menu-submenu-link-title
- 作业卡 /student/classes/<id>/core_tasks: div.fusion-card-item.short-assignment
- DDL 页 /student/tasks_and_deadlines?view=...: 纯文本行解析
- 总评页 /student/classes/<id>/units: div.sidebar-items-list > div.cell

**解析逻辑一行未改**——改解析等于改抓取正确性，风险不值得。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

#: DDL 页日期行, 如 "Sep 12, 11:59 PM"
DUE_LINE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
    r"(\d{1,2}),\s+(\d{1,2}):(\d{2})\s*(AM|PM)$",
    re.IGNORECASE,
)

STATUS_LINE = re.compile(
    r"^(Pending|Submitted|Late|Missing|Overdue|Not Submitted|"
    r"Not Assessed Yet|Complete|Completed|Returned|Excused)$",
    re.IGNORECASE,
)

#: 页面上明确的“无课程”提示
NO_CLASSES_MARKER = "No classes found"

#: `.due-date` 上的**相对**日期形态（ManageBac 对近一周内的事件用这种"人话"日期）：
#: `Thursday at 12:00 PM` / `Wednesday at  9:45 AM`（时刻前可能有两个空格）。
REL_DUE = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)[a-z]*\s+at\s+(\d{1,2}):(\d{2})\s*(AM|PM)$",
    re.IGNORECASE,
)

#: 星期名 → **`date.weekday()` 的编号**（周一=0 … 周日=6；不是 isoweekday 的 1..7！）
WEEKDAY_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

#: 「周几」文本里的时刻（与 REL_DUE 分开，便于单独取时刻）
CLOCK = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.IGNORECASE)


def _clock(text: str) -> tuple[int, int]:
    """从 `… at 12:00 PM` 这类文本里取 (时, 分)；取不到回 (23, 59)。"""
    m = CLOCK.search(text or "")
    if not m:
        return 23, 59
    hour = int(m.group(1)) % 12
    minute = int(m.group(2))
    if m.group(3).upper() == "PM":
        hour += 12
    return hour, minute


#: 「还没交」的状态（ManageBac 上这类通常指**本周还没到/还没提交**的任务）
UNSUBMITTED_STATUS = ("pending", "not submitted", "未提交", "not started", "overdue", "late")


def _status_direction(status: str | None) -> str:
    """按状态决定「周几」该往哪个方向推算：'future' / 'past' / 'nearest'。

    真实数据（管理员账号 45 条）核对过：`Pending` 里既有远处旧学年的期末考
    （2026-01-14 的 `WH: The Final Exam`），也有当周的 `A1.1.3 HW`（今天）。
    所以这条**只作为没有 date-badge 时的兜底**；有徽标时一律用徽标的月/日。

    * Pending / Not Submitted / Late / 未提交 → **最近未来**（这类还没交，往前看才对；
      今天就是那天时取今天）
    * 其它（Submitted / 已出分 / Graded / 空以外的明确状态）→ **最近过去**
    * 状态缺失 → **最近的那一次**（前后都算，取绝对差最小；平局取过去）
    """
    text = (status or "").strip().lower()
    if not text:
        return "nearest"
    if any(k in text for k in UNSUBMITTED_STATUS):
        return "future"
    return "past"


def _infer_weekday_datetime(text: str, today: date,
                           direction: str = "nearest") -> datetime | None:
    """把 `Thursday at 12:00 PM` 这类**相对日期**按今天推算成 datetime。

    `direction`：
      * `'past'`    → 今天或之前最近的那个星期几（不晚于今天）
      * `'future'`  → 今天或之后最近的那个星期几（不早于今天）
      * `'nearest'` → 两者取**绝对差最小**的那个（平局取过去）

    调用方必须把这结果标成**推断值**（`due_inferred: true`）——它不是页面上的权威日期。
    """
    m = REL_DUE.match((text or "").strip())
    if not m:
        return None
    target = WEEKDAY_NUM.get(m.group(1)[:3].lower())
    if target is None:
        return None
    hour, minute = _clock(text)
    base = datetime(today.year, today.month, today.day, hour, minute)
    back = (today.weekday() - target) % 7           # 今天或之前最近：回退天数
    fwd = (target - today.weekday()) % 7            # 今天或之后最近：前进天数
    if direction == "past":
        delta = -back
    elif direction == "future":
        delta = fwd
    else:
        delta = -back if back <= fwd else fwd       # 平局（back == fwd）取过去
    return base + timedelta(days=delta)


@dataclass
class Deadline:
    title: str
    course: str | None
    due_at: datetime | None
    due_text: str
    status: str | None
    category: str          # upcoming / past / overdue
    source_url: str


@dataclass
class Task:
    """单门课 core_tasks 页的一张作业卡."""

    class_id: str
    class_name: str
    task_id: str
    title: str
    href: str
    due_at: datetime | None
    due_text: str
    status: str | None         # Pending / Submitted / Late / ...
    category: str | None       # Summative / Formative
    kind: str | None           # Coursework / Final Exam / Quiz / ...
    past_due: bool
    can_submit: bool           # 卡片上是否还有 Submit Coursework 按钮
    score_text: str | None
    #: `due_at` 是**按「周几」文本推算**出来的（不是卡片上的精确日期）→ 回给前端时标成推断值
    due_inferred: bool = False


def _infer_task_year(month: int, day: int, today: date, past_due: bool = False) -> int | None:
    """卡片只给 月/日, 需推断年份.

    利用 ManageBac 自己的 past-due 徽标定方向:
    - past_due  → 取"今天或之前"里最晚的那个年份(刚过去的那个学期)
    - 否则      → 取"今天或之后"里最早的年份(真正未截止的作业/考试)
    候选限定在 ±400 天内。
    """
    options: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if abs((candidate - today).days) <= 400:
            options.append(candidate)
    if not options:
        return None
    if past_due:
        past = [d for d in options if d <= today]
        return max(past).year if past else max(options).year
    future = [d for d in options if d >= today]
    return min(future).year if future else min(options).year


def _derive_due(month: int | None, day: int | None, due_text: str, today: date,
                past_due: bool, status: str | None = None) -> tuple[datetime | None, bool]:
    """卡片 → (due_at, 是否**推断**出来的)。

    优先级（高 → 低）：
      ① 卡片的 `.date-badge`（月 + 日）+ `.due-date` 里的时刻 → **精确日期**，不算推断；
         年份由 `_infer_task_year` 按 ManageBac 自己的 past-due 徽标定向推断（原有逻辑）。
      ② `.due-date` 是相对日期（`Thursday at 12:00 PM`）→ **按 `status` 分流**推算
         （未交 → 最近未来；已交/已出分 → 最近过去；状态缺失 → 绝对差最小），
         一律**标成推断值**（调用方会带 `due_inferred: true` 回给前端）。
      ③ 都不行 → (None, False)。
    """
    if month and day:
        year = _infer_task_year(month, day, today, past_due)
        if year is not None:
            hour, minute = _clock(due_text)
            return datetime(year, month, day, hour, minute), False
    rel = _infer_weekday_datetime(due_text, today, _status_direction(status))
    if rel is not None:
        return rel, True
    return None, False


def extract_task_cards(html: str, class_id: str = "", class_name: str = "",
                       today: date | None = None) -> list[Task]:
    """解析 /student/classes/<id>/core_tasks 页面的作业卡片(新版 UI)."""
    today = today or date.today()
    soup = BeautifulSoup(html, "html.parser")
    tasks: list[Task] = []

    for card in soup.select("div.fusion-card-item.short-assignment"):
        link = card.select_one(".h4.title a[href]")
        if link is None:
            continue
        href = link.get("href") or ""
        task_id_m = re.search(r"/core_tasks/(\d+)", href)

        badge = card.select_one(".date-badge")
        month_txt = badge.select_one(".month").get_text(strip=True) if badge else ""
        day_txt = badge.select_one(".day").get_text(strip=True) if badge else ""
        past_due = "past-due" in (badge.get("class") or []) if badge else False
        month = MONTHS.get(month_txt[:3].upper())
        day = int(day_txt) if day_txt.isdigit() else None

        # 状态要先读：没有 date-badge 时，"周几"往过去还是未来推，取决于交没交
        status_el = card.select_one(".badge .badge-label")
        status = status_el.get_text(strip=True) if status_el else None

        due_span = card.select_one(".due-date")
        due_text = due_span.get_text(" ", strip=True) if due_span else ""
        due_at, due_inferred = _derive_due(month, day, due_text, today, past_due, status)
        # 拿不到徽标时，用算出来的日期再判一次「是否已过期」——
        # ManageBac 只对已过期卡片加 past-due 徽标，徽标缺失时不能一律当未过期。
        if due_at is not None and not past_due:
            if not (month and day) and due_at <= datetime(today.year, today.month, today.day, 23, 59):
                past_due = True

        labels = [el.get_text(strip=True) for el in card.select(".labels-set div.label")]
        category = next((t for t in labels if t.lower() in ("formative", "summative")), None)
        kind = next((t for t in labels if t.lower() not in ("formative", "summative")), None)

        score_el = card.select_one(".assessment.task-score")
        score_text = " ".join(score_el.get_text(" ", strip=True).split()) if score_el else None

        tasks.append(Task(
            class_id=class_id,
            class_name=class_name,
            task_id=task_id_m.group(1) if task_id_m else "",
            title=link.get_text(strip=True)[:200],
            href=href,
            due_at=due_at,
            due_text=due_text,
            status=status,
            category=category,
            kind=kind,
            past_due=past_due,
            can_submit=any(
                re.search("Submit Coursework", a.get_text(" ", strip=True), re.IGNORECASE)
                for a in card.find_all("a")
            ),
            score_text=score_text,
            due_inferred=due_inferred,
        ))
    return tasks


#: 卡片上的操作链接文本(不是课程名, 必须忽略)
_ACTION_WORDS = {"leave", "units", "tasks", "updates", "grades", "class", "classes", "course"}


def extract_classes(html: str) -> dict[str, str]:
    """从课程列表页解析 {class_id: 课程名}.

    一张课程卡片会同时产生多个链接(标题链接 + Units/Tasks/Leave 等操作链接),
    因此对同一 class_id 保留最长文本(即课程全名), 并忽略纯动作词。
    """
    soup = BeautifulSoup(html, "html.parser")
    classes: dict[str, str] = {}

    def _offer(cid: str, name: str) -> None:
        name = name.strip()
        if not name or len(name) < 2 or name.lower() in _ACTION_WORDS:
            return
        old = classes.get(cid)
        if old is None or len(name) > len(old):
            classes[cid] = name[:150]

    # 版式 A: 左侧菜单(GPA-Scraper 验证的选择器)
    for a in soup.select("li.f-menu-submenu-item a[href]"):
        href = a.get("href") or ""
        match = re.search(r"/student/classes/(\d+)", href)
        title = a.select_one("span.f-menu-submenu-link-title")
        if match and title:
            _offer(match.group(1), title.get_text(strip=True))

    # 版式 B: #classes 卡片流
    for a in soup.select("#classes a[href]"):
        href = a.get("href") or ""
        match = re.search(r"/student/classes/(\d+)", href)
        if match:
            _offer(match.group(1), a.get_text(" ", strip=True))

    return classes


def _parse_due_line(line: str, reference: datetime, category: str) -> datetime | None:
    match = DUE_LINE.match(line.strip())
    if not match:
        return None
    month = MONTHS[match.group(1)[:3].upper()]
    day = int(match.group(2))
    hour = int(match.group(3))
    minute = int(match.group(4))
    meridiem = match.group(5).upper()
    if meridiem == "PM" and hour < 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0

    year = reference.year
    dt = reference.replace(year=year, month=month, day=day, hour=hour, minute=minute,
                           second=0, microsecond=0)
    # upcoming: 月份小于当前月 → 属于明年; past/overdue: 超前 45 天以上 → 属于去年
    if category == "upcoming" and dt.date() < reference.date() and month < reference.month:
        dt = dt.replace(year=year + 1)
    elif category != "upcoming" and dt > reference + timedelta(days=45):
        dt = dt.replace(year=year - 1)
    return dt


def extract_deadlines(html: str, category: str, source_url: str,
                      reference: datetime | None = None) -> list[Deadline]:
    """从 Tasks & Deadlines 页面解析 DDL 列表(纯文本行算法)."""
    reference = reference or datetime.now()
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.replace("\u00a0", " ").strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    due_indexes = [i for i, ln in enumerate(lines) if _parse_due_line(ln, reference, category)]
    items: list[Deadline] = []

    for pos, due_index in enumerate(due_indexes):
        title = lines[due_index - 1] if due_index >= 1 else ""
        due_at = _parse_due_line(lines[due_index], reference, category)
        if not title or due_at is None:
            continue
        # 排除页脚/分组标题等假标题
        if re.match(r"^(Upcoming|Past|Overdue|Show More|Guides|Privacy)", title, re.I):
            continue
        if re.match(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),", title, re.I):
            continue

        next_due = due_indexes[pos + 1] if pos + 1 < len(due_indexes) else len(lines)
        block = lines[due_index + 1:next_due]
        course = block[0] if block and not STATUS_LINE.match(block[0]) else None
        status = next((ln for ln in block if STATUS_LINE.match(ln)), None)

        items.append(Deadline(
            title=title[:200],
            course=course[:120] if course else None,
            due_at=due_at,
            due_text=lines[due_index],
            status=status,
            category=category,
            source_url=source_url,
        ))
    return items


def extract_overall_grade(units_html: str) -> str | None:
    """从单科 units 页侧栏提取总评(GPA-Scraper 的取法, 第 4 个 cell)."""
    soup = BeautifulSoup(units_html, "html.parser")
    sidebar = soup.find("div", class_="sidebar-items-list")
    if sidebar is None:
        return None
    cells = sidebar.find_all("div", class_="cell")
    if len(cells) < 4:
        return None
    parts = [ln for ln in cells[3].get_text("\n").splitlines() if ln.strip()]
    if len(parts) < 2:
        return None
    grade = parts[1].replace("(", "").replace(")", "").strip()
    return grade or None


def _txt(el, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()[:limit]
