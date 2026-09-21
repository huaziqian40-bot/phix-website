# ManageBac 后端活动数据契约

本契约对应 `webapp_managebac.py` 新增公开函数。原 `fetch_view()` 与已有客户端函数保持不变。实现依赖同目录 `webapp_mb_stream.py`；打包本地桥时需要同时包含此文件。

## 已登录会话接口（优先供路由复用）

```python
from webapp_managebac import ManageBacClient
from webapp_mb_stream import fetch_announcements, fetch_discussions, fetch_exams, fetch_class_details

# email/password 来自调用方当前用户凭据，禁止记录到日志。
client = ManageBacClient(configured_base_url)
try:
    client.login(email, password)
    feed = fetch_announcements(client)
    discussions = fetch_discussions(client, class_id)
    exams = fetch_exams(client, class_id)
    details = fetch_class_details(client, class_id)
finally:
    client.session.close()
```

上述四个模块级函数均返回以下附加字段，并保留后文的原字段供兼容对接：

```json
{"available": true, "data": [], "note": "已读取学生页面；结果范围仅限当前账号可见内容。"}
```

- `available`：来源成功（包括确认的空列表），或已有可展示的部分数据。应结合 `partial/status/note` 展示限制。不可读且无数据时为 `false`。
- `data`：通知/讨论/考试为消息数组；课程详情为零或一个详情对象组成的数组。详情顶层原有字段仍保留。`data` 不包含自身，不存在递归引用。
- `note`：面向用户的中文来源说明；不包含上游原始错误或凭据。
- 课程详情额外提供 `capabilities.students/resources/grades`，每项也含 `available/data/note/status`。缺少成员或资源入口可独立返回 `available: false`，不影响已取得的个人成绩。

`session` 参数推荐传已经登录的 `ManageBacClient`。也接受已经登录的 `requests.Session`，但调用方必须显式设置 `session.base_url = configured_base_url`；不会从 Cookie 猜测主机，不会替调用方关闭会话。会话级讨论函数依靠学校端授权；面向 HTTP 的 `webapp_managebac.fetch_discussions` 还会验证课程列表归属。

## 调用入口

```python
import webapp_managebac as mb

mb.fetch_notifications(email, password, base_url, timeout=12.0)
mb.fetch_messages(email, password, base_url,
                  class_id=None, timeout=12.0, budget_seconds=16.0)
mb.fetch_discussions(email, password, base_url, class_id, timeout=12.0)
mb.fetch_class_details(email, password, base_url, class_id, timeout=12.0)
```

每个函数使用一个学生会话登录一次，并在成功、失败时都关闭 Session。所有数据为 JSON 可序列化对象。函数不改变通知已读状态、不回复讨论、不提交作业。

- `LoginError`：初次登录失败；路由转换为认证提示，不把原始异常文本直传浏览器。
- `ValueError('invalid_class_id')`：课程 ID 非数字。
- `PermissionError('class_not_accessible')`：当前账号课程列表不含该 ID。
- 初始课程列表读取异常可抛 `requests.RequestException`，由路由统一转换上游错误；后续可选数据源失败返回 `sources` 状态，保留已获得数据。
- 不新增模块全局缓存。调用方应使用当前用户独立缓存并在更新凭据/退出/解绑后清除；建议成功结果 TTL 120 秒、失败结果短暂缓存 30 秒，429 至少等待 60 秒再尝试。不能共享不同学生数据。

## 通知流

```json
{
  "notifications": [
    {
      "id": "notification_1",
      "type": "discussion",
      "title": "Example discussion",
      "content": "Plain text",
      "course": "Demo course",
      "classId": "12",
      "class_id": "12",
      "date": "2026-09-16T10:00:00Z",
      "status": "",
      "read": null,
      "link": "https://school.example.invalid/student/classes/12/discussions/8",
      "author": "Example teacher"
    }
  ],
  "status": "ok",
  "partial": false,
  "sources": {"student_notifications": "ok"}
}
```

`read: null` 表示未知，不应当作未读；`date` 可能为上游展示文本或空字符串，仅合法日期可参与机器排序。`type` 为 `discussion | assignment | exam | grade | announcement | resource | other`。未知类型保留 `other`。

通知解析不会依靠所有页面链接生成“假通知”。没有识别出消息节点且没有明确空状态时返回 `unrecognized`。当前没有真实临时账号验证通知选择器，因此接口可交付，真实完整通知抓取能力尚不能宣称验证成功。

## 多类型消息

```json
{
  "messages": [
    {
      "id": "12:exam:8",
      "source_id": "8",
      "type": "exam",
      "title": "Example exam",
      "content": "",
      "course": "Demo course",
      "classId": "12",
      "class_id": "12",
      "date": "",
      "due": "2026-09-16T12:00:00",
      "due_inferred": false,
      "status": "Submitted",
      "score": "80%",
      "link": "https://school.example.invalid/student/classes/12/tasks/8",
      "read": null
    },
    {
      "id": "12:grade:8",
      "source_id": "8",
      "type": "grade",
      "related_id": "12:exam:8",
      "title": "Example exam",
      "content": "",
      "course": "Demo course",
      "classId": "12",
      "class_id": "12",
      "date": "",
      "due": "2026-09-16T12:00:00",
      "due_inferred": false,
      "status": "Submitted",
      "score": "80%",
      "link": "https://school.example.invalid/student/classes/12/tasks/8",
      "read": null
    }
  ],
  "courses": [{"id": "12", "name": "Demo course"}],
  "sources": {"12": {"tasks": "ok", "discussions": "ok"}},
  "partial": false,
  "status": "ok"
}
```

作业和考试卡从既有个人任务列表产生；有个人得分的任务额外生成 Grade 卡并用 `related_id` 关联原卡。没有成绩发布时间时 `date` 为空，截止日期单独放 `due`。讨论从课程讨论列表产生。公告和资源类消息来自通知流；不会把资源文件存在伪装成刚发布的通知。

`fetch_discussions` 返回 `{messages, status, partial, sources: {discussions: status}, pages}`；它保留讨论源 ID。前端组合多个来源时建议使用 `type + classId + id` 做 DOM key，通知跳转可通过 `link` 或课程及 `source_id` 关联。

## 课程详情

```json
{
  "classId": "12",
  "id": "12",
  "name": "Demo course",
  "teacher": "",
  "teaching_group": "",
  "students": [{"id": "2", "name": "Example student"}],
  "grades": [],
  "resources": [{"title": "Example file", "link": "https://school.example.invalid/attachments/3", "type": "resource"}],
  "messages": [],
  "stats": {
    "total": 0,
    "completed": 0,
    "completion_rate": null,
    "average_score": null,
    "scope": "loaded_tasks",
    "graded_count": 0,
    "average_sample_count": 0,
    "average_unit": "percent"
  },
  "sources": {
    "basic": "ok",
    "students": "ok",
    "resources": "ok",
    "grades": "ok",
    "discussions": "ok"
  },
  "partial": false,
  "status": "ok"
}
```

`grades` 和 `messages` 元素为统一消息字段；历史成绩的范围仅为已加载的当前学生任务（含既有任务页面返回的过往项目），不承诺覆盖全部学年。详情任务卡当前 `date` 为任务截止显示值，详情 UI 应标注“截止时间”；活动聚合接口则通过 `due` 明确区分。教师、教学组没有匹配元素时为空，不填猜测值。

`completion_rate` 为 0–100，分母是 `total`；识别 Submitted/Complete/Completed/Graded/Returned 为完成。只有明确 `%` 形式成绩纳入 `average_score`，样本数为 `average_sample_count`。不把 IB 1–7 等级、分数比及百分比混算。无有效数据时返回 `null`。

## 状态与 UI 对接

| 状态 | 含义 | 建议展示 |
|---|---|---|
| `ok` | 来源成功（可能合法为空） | 展示结果或空状态 |
| `partial` | 截断、预算耗尽或部分来源失败 | 展示已有数据及“不完整”提示 |
| `unrecognized` | 返回页面但无法识别结构 | 数据暂无法读取 |
| `unavailable` | 未发现入口或 404 | 当前来源暂不可用 |
| `not_loaded` | 本轮预算不足/上游限流后未继续 | 本轮尚未读取 |
| `authentication_required` | 学生会话失效或 401 | 重新连接提示 |
| `forbidden` | 403 | 当前账号无权限 |
| `rate_limited` | 429 | 稍后重试，保留旧缓存 |
| `timeout` | 上游超时 | 保留已有数据，显示重试 |
| `upstream_error` | 其他请求错误 | 显示来源错误 |

HTTP 响应建议附 `Cache-Control: no-store`，应用内部缓存仍按用户隔离。渲染 `title/content/author` 必须使用文本节点或既有转义函数；这些字段不是受信任 HTML。

## 性能与请求上限

- 通知：主页 1 请求；最多两个发现的入口，每源最多 5 页；合并最多 100 项，截断标 `partial`。
- 消息：先取当前课程列表，再逐课 1 个任务请求 + 最多 5 个讨论分页；总消息最多 100。16 秒为**开始后续来源前的调度预算**，不是硬性请求墙钟上限；单次 HTTP 按 `timeout`，课程列表分页及在途请求仍可使总时长超出预算。
- 课程详情：课程列表权限验证 + units + 任务 + 讨论；成员和资源只有实际发现入口时才请求。
- 所有接口首轮登录和课程列表读取沿用现有客户端；调用频率应由路由缓存控制，避免通知和每个课程详情反复登录。
- 学生成员和资源目前只读取第一页、最多 100 项，空但结构未确认返回 `unrecognized`；不宣称全量。

## 验证结果

`python -m unittest test_webapp_mb_stream -v`：34 项通过（含 available/data/note、原始已登录 Session 适配、考试提取、详情 JSON 无循环引用）。

既有 `test_webapp_data.t15_due_normalization()` 回归：13 项断言通过，0 失败，包含本地假 ManageBac HTTP 服务上的真实 `fetch_view()` 抓取链路。

已将 `webapp_managebac.py` 与修改前 ZIP 的 AST 对照，8 个原函数/类定义完全一致，包括 `ManageBacClient`、`fetch_view`、`_due_fields` 和异常类型。新增代码未更改旧作业抓取语义。真实学校临时账号端到端验证尚未执行，限制详见 `MANAGEBAC-API-RESEARCH.md`。
