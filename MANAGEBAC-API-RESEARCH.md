# ManageBac 通知与课程详情 API 调研

本报告区分三类证据：已读代码中的既有能力、官方公共 API 文档、无凭据网络探测。测试没有读取或使用真实学生凭据，没有写入学校端数据。

## 结论

现有 `webapp_managebac.py` 使用学生网站的 Rails 登录表单和会话 Cookie，随后读取 HTML；它没有公共 API token。任务示例中的 `/api/v2/...` 不能直接接到此会话上。官方公共 API 使用独立的区域 API 主机和 `/v2/...` 路径，并需要 `auth-token` 或 OAuth Bearer 凭据。

不能把通知数据源不可读解释为没有通知。本次独立适配模块 `webapp_mb_stream.py` 因此保留 `status`、`partial`、`sources`，未知页面结构、未发现入口、权限失败和限流均可被页面区分。

## 已确认的现有学生接口

| 功能 | 方法和路径 | 数据结构与证据 |
|---|---|---|
| 登录 | GET `/login`、POST 页面表单 action | CSRF + 学生会话 Cookie；原有客户端实现 |
| 会话检查/主页 | GET `/student` | HTML；原有 `is_logged_in()` 使用此路径 |
| 课程列表 | GET `/student/classes/my?page=N` | `{class_id: name}`；现有解析器可分页 |
| 作业、考试与个人得分 | GET `/student/classes/{id}/core_tasks` | `Task(class_id, class_name, task_id, title, href, due_at, due_text, status, category, kind, score_text, ...)` |
| 总评 | GET `/student/classes/{id}/units` | 既有侧栏总评文本 |
| 讨论 | GET `/student/classes/{id}/discussions` | 桌面版既有客户端提供此方法；解析器注释标注真实页面验证，选择器 `div.discussion[id^=discussion_]`、`.h4.title`、`.author`、`.fr-view` |

说明：讨论的“真实页面验证”来自既有源码注释，不是本次使用临时账号重新验证。

## 官方公共 API

官方 OpenAPI 声明区域主机包括 `https://api.managebac.cn`、`https://api.managebac.com` 等，认证方式包括 `auth-token` 请求头和 OAuth Bearer。学生网站登录 Cookie 与这些凭据不等价。

| 需求 | 官方文档确认的实际 GET 路径 | 说明 |
|---|---|---|
| 课程详情 | `/v2/classes/{id}` | 课程名称、年级、program、subject、教师 ID 等 |
| 课程学期 | `/v2/classes/{id}/terms` | 学期、日期、学年；成绩接口依赖 term ID |
| 作业/考试 | `/v2/classes/{id}/tasks` | assessment type、category、assigned students、scoring configuration；考试可以是任务类型 |
| 单项任务 | `/v2/classes/{class_id}/tasks/{id}` | 单项任务及评分配置 |
| 学期作业成绩 | `/v2/classes/{class_id}/assessments/term/{term_id}/grades` | **全班各学生**的任务成绩；不可直接向普通学生透传 |
| 学期总评 | `/v2/classes/{class_id}/assessments/term/{term_id}/term-grades` | 各课程体系返回形状不同 |
| 单任务评估 | `/v2/classes/{class_id}/tasks/{id}/students` | 学生评估结果；同样需要按当前学生授权过滤 |

官方成绩字段包括 `group_mark`、`score`、`max_points`、`dropbox_status`、`binary` 等。MYP 的 criterion 分数、DP 数字/字母等级与百分比不可直接混算。适配层仅对明确带 `%` 的当前已加载个人成绩计算平均值，并返回样本数。

所审阅的官方 Classes、Coursework 文档及 API 目录没有证实任务示例中的 `/api/v2/notifications`、`announcements`、`discussions`、`exams`、`resources` 路径。此结论是“未证实”，不表示所有版本、租户或私有接口都没有这些能力。

## 本次无凭据端点探测

对项目既有学校域名执行 7 次匿名 GET，禁用自动重定向，课程 ID 使用不存在真实性保证的占位数字 `1`：

| 候选路径 | HTTP | Content-Type |
|---|---:|---|
| `/api/v2/notifications` | 404 | `text/html; charset=UTF-8` |
| `/api/v2/announcements` | 404 | 同上 |
| `/api/v2/classes/1/discussions` | 404 | 同上 |
| `/api/v2/classes/1/exams` | 404 | 同上 |
| `/api/v2/classes/1/grades` | 404 | 同上 |
| `/api/v2/classes/1/students` | 404 | 同上 |
| `/api/v2/classes/1/resources` | 404 | 同上 |

这些结果只能证明学校网站的上述匿名路径返回 404，不能证明公共 API 主机上任何认证后的能力。本次没有可用的临时学校账号，也没有创建学校账号的管理权限，因此没有登录后的端到端上游验证。

## 新增客户端适配接口

独立模块 `webapp_mb_stream.py` 接收已登录的 `ManageBacClient`，自身不保存密码或 Cookie：

- `fetch_notifications(client)`：读取 `/student`，从页面发现同源通知和公告入口，合并并去重。若未发现独立入口，读取首页上明确的消息节点。每源最多 5 页、100 条，合并输出最多 100 条。
- `fetch_discussions(client, class_id, course='')`：只读既有学生讨论路径，使用已确认的讨论选择器。
- `fetch_class_details(client, class_id)`：先从当前账号课程列表核验课程可访问性，再抓取当前学生任务/成绩、讨论；成员和资源仅跟随课程页实际出现的入口。未发现入口返回 `unavailable`。
- `parse_messages(...)`、`message_type(...)`：统一 Discussion、Assignment、Exam、Grade、Announcement、Resource、other 类型。

这些首先是 Python 客户端能力，不是网站 HTTP 路由。`webapp_managebac.py` 已新增同名的账密入口以及 `fetch_messages(...)`，统一使用 `(email, password, base_url, ...)` 调用方式，详情见 `MANAGEBAC-DATA-CONTRACT.md`。HTTP 路由与登录会话、缓存及前端集成由主实现负责。缓存应按站点用户和凭据版本隔离，退出/解绑/更新凭据后失效；不得建立跨用户共用结果缓存。429 应抑制立即重复请求，保留既有缓存并显示数据源状态。

通知结构：

```json
{"id":"example","type":"discussion","title":"Example","content":"Text","course":"Demo course","classId":"12","class_id":"12","date":"2026-09-16T10:00:00Z","status":"","read":null,"link":"https://school.example.invalid/student/classes/12/discussions/8","author":"Example teacher"}
```

`read: null` 表示上游未给出已读状态。未知消息类型保留为 `other`，不假称为通知公告。上游 HTML 转纯文本；链接仅接受同源 HTTP(S)，未知外链不渲染为可点击资源。

课程详情包含 `classId, id, name, teacher, teaching_group, students, grades, resources, messages, stats, sources, partial`。统计范围明确为 `loaded_tasks`，不是完整历史；无法计算时为 `null`。

## 验证

执行：

```powershell
python -m unittest test_webapp_mb_stream -v
```

测试全部使用 `example.invalid` 域名和 Mock 客户端。测试覆盖：消息类型、纯文本抽取、同源链接校验、未知已读状态、通知/公告入口发现、分页去重、循环分页终止、5 页/100 条限制、分页失败保留已有数据、登录失效、429、未知结构与合法空数据区分、课程权限校验、成员/资源入口、评分尺度隔离。

此测试证明离线适配与错误处理，不证明真实学校页面的通知、成员及资源选择器已匹配。后续临时账号验证需要确认这些节点和具体通知入口，并补充经过匿名化的测试 fixture；不要保存真实账号响应或个人信息到仓库。

## 推荐实施方案

优先复用调用方已登录的学生会话：`fetch_announcements(session)` 抓取通知与公告，随后按需调用 `fetch_discussions(session, class_id)`、`fetch_exams(session, class_id)` 和 `fetch_class_details(session, class_id)`。`session` 推荐传 `ManageBacClient`，原始 `requests.Session` 需显式设置 `base_url`。通知优先加载，课程详情在点击时读取，避免每次打开页面遍历所有课程成员和资源。

所有能力均提供 `{available, data, note}`，并保留详细 `status/partial/sources`。课程详情的成员、资源、成绩还有独立 `capabilities`。无法识别学生页面或没有入口时返回 `available: false`；不会为满足 UI 编造成员、通知或资源。合法空数据与不可用分别表示。当前 34 项隔离测试通过；实际学校选择器验证仍需临时账号。

## 官方来源

- [ManageBac Public API 集成启用说明](https://help.managebac.com/hc/en-us/articles/360018226931-Enabling-ManageBac-Public-API-for-Integrations)
- [官方 API 文档目录](https://guide.fariaedu.com/integrations-portal/llms.txt)
- [Classes OpenAPI 文档](https://guide.fariaedu.com/integrations-portal/managebac/public-rest-apis/v2/classes.md)
- [Coursework OpenAPI 文档](https://guide.fariaedu.com/integrations-portal/managebac/public-rest-apis/v2/coursework.md)
- [公共 API Overview](https://guide.fariaedu.com/integrations-portal/managebac/public-rest-apis/overview.md)
