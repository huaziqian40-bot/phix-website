"""Offline tests: only synthetic fixture pages, no accounts or network access."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import webapp_mb_stream as stream

BASE = 'https://school.example.invalid'


def response(html, path='/student'):
    return SimpleNamespace(text=html, url=BASE + path)


def notification(identity='n1', title='Example', link='/student/classes/12/discussions/8'):
    return f'<div data-notification-id="{identity}" data-type="discussion" class="unread"><a class="title" href="{link}">{title}</a><time datetime="2026-09-16T10:00:00Z"></time><div class="content">Body</div></div>'


# ---------------------------------------------------------------- 真实页面夹具
#
# 下面两个夹具不是编的，是 **2026-09-17 从真机 dump 下来的结构**（`PHIX_MB_DUMP=1`
# 把学生页面存到 /tmp 后照抄）。原来那版测试用的是 `data-notification-id` /
# `.notification-item` 这类**真实站点上根本不存在**的类名，于是「永远命中不了」
# 也被测成了预期行为 —— 用户看到的是「页面结构没认出来」。
#
# 真实情况（这就是重写的依据）：
#   * 仪表盘上通知**只有一个触发器元素**，带
#       data-mnn-hub-endpoint="https://mnn-hub.prod.faria.cn"
#       data-token="<JWT>"  data-namespace="student"  data-count="0"
#       <div id="mnn-sidebar-content"></div>
#     —— 通知正文由**独立的 mnn-hub 服务**用 JWT 下发，不在学校站点的 HTML 里；
#   * 真正有内容的页面是 `/student/tasks_and_deadlines`，条目是 `.f-task-tile`。

DASH_DEFAULT = ('<a class="btn btn-blank js-messages-and-notifications-trigger notifications-count" '
                'data-count="0" data-mnn-hub-endpoint="https://mnn-hub.prod.faria.cn" '
                'data-namespace="student" data-locale="en">'
                '<div class="dropdown-menu modal-message-notifications">'
                '<div id="mnn-sidebar-content"></div></div></a>'
                '<a href="/student/notifications">Notifications</a>')


def dashboard(count=0, hub='https://mnn-hub.prod.faria.cn', link='/student/notifications'):
    """仪表盘：通知触发器（权威未读数在这里）+ 通知中心入口。"""
    return ('<div class="card"><a class="btn js-messages-and-notifications-trigger notifications-count" '
            f'data-count="{count}" data-mnn-hub-endpoint="{hub}" data-namespace="student">'
            '<div id="mnn-sidebar-content"></div></a>'
            f'<a href="{link}">Notifications</a></div>')


def task_tile(idx=1, title='behavior time', due='Sep 20, 11:55 PM', cid='11516640',
              course='IB DP 2028届 ESS SL by Yan (Grade 11)',
              badges=('Summative', 'Coursework'), status='Pending'):
    """`/student/tasks_and_deadlines` 上的一条待办（`.f-task-tile` 的真实结构）。"""
    badge_html = ''.join(
        f'<span class="badge color-box-custom"><span class="badge-label">{b}</span></span>'
        for b in badges)
    return (
        '<div class="f-tile f-tile--inline f-task-tile f-tile--wrap-suffix">'
        '<div class="f-tile__prefix"><div class="f-surface-icon color-box-custom">'
        '<svg class="fi fi-clipboard_check_fill"></svg></div></div>'
        '<div class="f-tile__body">'
        '<p class="f-tile__title h5">'
        f'<a class="f-tile__title-link link-dark f-truncate-item" '
        f'href="/student/classes/{cid}/core_tasks/{idx}"><span class="f-truncate-item">{title}</span></a>'
        '</p>'
        '<div class="f-tile__description color-secondary"><div class="hstack gap-2 flex-wrap f-truncate">'
        f'<span><svg class="fi fi-clock"></svg> {due}</span>'
        '<span class="vr"></span>'
        f'<a class="f-truncate-item link-dark" href="/student/classes/{cid}">{course}</a>'
        f'{badge_html}'
        f'<span class="badge color-box-gray" data-bs-title="Waiting">'
        f'<span class="badge-label">{status}</span></span>'
        '</div></div></div></div>')


def tasks_page(*tiles):
    return '<div class="js-tasks vstack gap-6">' + ''.join(tiles) + '</div>'


def notifications_client(html=DASH_DEFAULT, tasks=None, **kw):
    """造一个「仪表盘 + 待办页」两次 GET 的假 client。"""
    client = Mock(base_url=BASE)
    client._get.side_effect = [response(html), response(tasks if tasks is not None else tasks_page(), '/student/tasks_and_deadlines')]
    return client


class StreamTests(unittest.TestCase):
    def client(self, html):
        client = Mock(base_url=BASE)
        client._get.return_value = response(html)
        return client

    def test_types(self):
        for label, expected in [('Final Exam', 'exam'), ('Quiz', 'exam'), ('Discussion', 'discussion'),
                                ('Announcement', 'announcement'), ('Assignment', 'assignment'),
                                ('Grade', 'grade'), ('Resource', 'resource'), ('unknown', 'other')]:
            self.assertEqual(stream.message_type(label), expected)

    def test_links(self):
        self.assertEqual(stream.safe_link(BASE, '/student'), BASE + '/student')
        for link in ['javascript:alert(1)', 'https://evil.example/file', '//evil.example/file', '', None,
                     'https://user:password@school.example.invalid/student']:
            self.assertEqual(stream.safe_link(BASE, link), '')

    def test_notification_contract(self):
        rows = stream.parse_messages(notification(), BASE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['classId'], '12')
        self.assertEqual(rows[0]['type'], 'discussion')
        self.assertIs(rows[0]['read'], False)
        self.assertEqual(rows[0]['date'], '2026-09-16T10:00:00Z')

    def test_discussion_fixture(self):
        html = '<div class="discussion" id="discussion_8"><h4 class="h4 title"><a href="/student/classes/12/discussions/8">Discussion</a></h4><div class="author">Example teacher</div><div class="fr-view"><b>Text</b><script>alert(1)</script></div></div>'
        row = stream.parse_messages(html, BASE)[0]
        self.assertEqual(row['content'], 'Text')
        self.assertEqual(row['type'], 'discussion')
        self.assertIsNone(row['read'])

    def test_no_navigation_as_notifications(self):
        self.assertEqual(stream.parse_messages('<nav><a href="/student/classes/12">Course</a></nav>', BASE), [])

    def test_dashboard_meta(self):
        """通知的**权威未读数**来自仪表盘上的触发器元素（data-count），不是猜的。"""
        client = Mock(base_url=BASE)
        client._get.side_effect = [response(dashboard(count=3)), response(tasks_page(), '/student/tasks_and_deadlines')]
        data = stream.fetch_notifications(client)
        self.assertEqual(data['unread_count'], 3)
        self.assertEqual(data['hub'], 'https://mnn-hub.prod.faria.cn')
        self.assertEqual(data['namespace'], 'student')
        self.assertEqual(data['notifications_url'], BASE + '/student/notifications')
        self.assertEqual(client._get.call_count, 2)

    def test_deadline_tiles_parsed(self):
        """待办页上的 `.f-task-tile` 被解析成条目（标题/截止/课程/课程号/状态）。"""
        client = notifications_client(tasks=tasks_page(
            task_tile(1, 'behavior time', 'Sep 20, 11:55 PM', '11516640'),
            task_tile(2, '数学作业', 'Sep 21, 08:00 AM', '11517686', course='Math AA HL')))
        data = stream.fetch_notifications(client)
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(len(data['notifications']), 2)
        first = data['notifications'][0]
        self.assertEqual(first['title'], 'behavior time')
        self.assertEqual(first['classId'], '11516640')
        self.assertEqual(first['due'], '2026-09-20 23:55')      # 归一成可排序的形态
        self.assertEqual(first['due_text'], 'Sep 20, 11:55 PM')
        self.assertTrue(first['link'].startswith(BASE + '/student/classes/11516640/core_tasks/'))

    def test_nothing_is_ok_not_unrecognized(self):
        """**一条通知、一条待办都没有**时是「真的没有」（ok），不是「读不出来」。

        这是本文件重写的核心：旧实现永远认不出真实页面，于是把「0 条」说成
        `unrecognized`，用户看到「页面结构没认出来，无法确认有没有数据」。
        """
        data = stream.fetch_notifications(notifications_client())
        self.assertEqual(data['status'], 'ok')
        self.assertNotEqual(data['status'], 'unrecognized')
        self.assertEqual(data['notifications'], [])
        self.assertEqual(data['unread_count'], 0)
        self.assertTrue(data['available'])

    def test_deadline_fetch_failure_is_a_status_not_silent_empty(self):
        """待办页取不到 → 给状态（timeout），而不是假装「没有待办」。"""
        client = Mock(base_url=BASE)
        client._get.side_effect = [response(dashboard()),
                                   requests.Timeout('slow')]
        data = stream.fetch_notifications(client)
        self.assertEqual(data['status'], 'timeout')
        self.assertTrue(data['partial'])
        self.assertEqual(data['notifications'], [])
        self.assertFalse(data['available'])
        self.assertEqual(data['unread_count'], 0)      # 未读数仍然给出来（来自仪表盘）

    def test_capability_envelope(self):
        good = stream.fetch_announcements(notifications_client(tasks=tasks_page(task_tile())))
        self.assertTrue(good['available'])
        self.assertEqual(good['data'], good['notifications'])
        self.assertIsInstance(good['note'], str)
        # 待办页读不出来且没有任何数据 → available False（不吹牛）
        failed = stream.fetch_announcements(Mock(
            base_url=BASE,
            _get=Mock(side_effect=[response(dashboard()), requests.Timeout('slow')])))
        self.assertFalse(failed['available'])
        self.assertEqual(failed['data'], [])

    def test_raw_authenticated_session_adapter(self):
        session = requests.Session()
        self.addCleanup(session.close)
        session.base_url = BASE
        session.get = Mock(side_effect=[
            Mock(text=dashboard(count=1), url=BASE + '/student'),
            Mock(text=tasks_page(task_tile()), url=BASE + '/student/tasks_and_deadlines'),
        ])
        result = stream.fetch_announcements(session)
        self.assertTrue(result['available'])
        self.assertEqual(result['unread_count'], 1)
        self.assertEqual(len(result['notifications']), 1)

    def test_raw_session_requires_explicit_host(self):
        session = requests.Session()
        self.addCleanup(session.close)
        with self.assertRaises(ValueError):
            stream.fetch_announcements(session)

    def test_exam_capability(self):
        client = self.client('<div class="empty-state"></div>')
        client.get_classes.return_value = {'12': 'Demo course'}
        client.get_class_tasks.return_value = [
            SimpleNamespace(status='Submitted', kind='Final Exam', task_id='1', title='Exam', due_at=None,
                            due_text='', score_text='80%', href='/student/classes/12/tasks/1')]
        result = stream.fetch_exams(client, '12')
        self.assertTrue(result['available'])
        self.assertEqual([row['type'] for row in result['data']], ['exam'])

    def test_detail_capabilities_are_json_serializable(self):
        import json
        client = self.client('<main>Units</main>')
        client.get_classes.return_value = {'12': 'Demo course'}
        client.get_class_tasks.return_value = []
        result = stream.fetch_class_details(client, '12')
        self.assertTrue(result['available'])
        self.assertFalse(result['capabilities']['students']['available'])
        self.assertFalse(result['capabilities']['resources']['available'])
        self.assertEqual(result['data'][0]['classId'], '12')
        json.dumps(result)

    def test_unrecognized_markup_is_not_a_crash(self):
        """仪表盘结构不认识时**不能崩**，且未读数如实为 None（不知道），而不是编一个 0。"""
        data = stream.fetch_notifications(notifications_client(html='<main>Welcome</main>'))
        self.assertIsNone(data['unread_count'])
        self.assertEqual(data['hub'], '')
        self.assertEqual(data['notifications'], [])

    def test_explicit_empty(self):
        data = stream.fetch_notifications(self.client('<div class="empty-state">No notifications</div>'))
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['notifications'], [])

    def test_login_redirect(self):
        client = self.client('<input id="session_password" type="password">')
        self.assertEqual(stream.fetch_notifications(client)['status'], 'authentication_required')

    def test_rate_limit(self):
        client = self.client('')
        upstream = requests.Response()
        upstream.status_code = 429
        client._get.side_effect = requests.HTTPError(response=upstream)
        self.assertEqual(stream.fetch_notifications(client)['status'], 'rate_limited')
        self.assertEqual(client._get.call_count, 1)

    # ---- 下面这几条测的是 `_collection` 的**分页纪律**（讨论那条路还在用它）----
    # 原来它们挂 `fetch_notifications` 上，而通知那块已经不走分页了（真实通知由
    # 独立 mnn-hub 服务下发）。分页规则本身必须继续被测，所以改挂 `fetch_discussions`。
    def _disc(self, *pages):
        client = Mock(base_url=BASE)
        client._get.side_effect = [response(p, '/student/classes/12/discussions') for p in pages]
        return client

    def test_pagination_deduplication(self):
        client = self._disc(
            notification() + '<a rel="next" href="/student/classes/12/discussions?page=2">Next</a>',
            notification() + notification('n2', 'Second'))
        data = stream.fetch_discussions(client, '12')
        self.assertEqual(len(data['items']), 2)          # n1 去重，只留一份
        self.assertFalse(data['partial'])

    def test_repeated_pagination_is_partial(self):
        """翻页指回同一页 → 立刻收手并标 partial（不无限翻）。"""
        client = self._disc(notification() + '<a rel="next" href="/student/classes/12/discussions">Next</a>')
        data = stream.fetch_discussions(client, '12')
        self.assertTrue(data['partial'])
        self.assertEqual(client._get.call_count, 1)

    def test_external_pagination_not_followed(self):
        """`rel=next` 指到站外 → **绝不跟过去**（同源校验）。"""
        client = self._disc(notification() + '<a rel="next" href="https://evil.example">Next</a>')
        stream.fetch_discussions(client, '12')
        self.assertEqual(client._get.call_count, 1)

    def test_second_page_failure_preserves_rows(self):
        client = self._disc(
            notification() + '<a rel="next" href="/student/classes/12/discussions?page=2">Next</a>',
            requests.Timeout('slow'))
        client._get.side_effect = [response(notification() + '<a rel="next" href="/student/classes/12/discussions?page=2">Next</a>',
                                           '/student/classes/12/discussions'),
                                   requests.Timeout('slow')]
        data = stream.fetch_discussions(client, '12')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['status'], 'timeout')
        self.assertTrue(data['partial'])

    def test_page_limit_is_partial(self):
        """最多翻 MAX_PAGES 页，超出标 partial。"""
        client = self._disc(*[
            notification(str(i)) + f'<a rel="next" href="/student/classes/12/discussions?page={i+1}">Next</a>'
            for i in range(6)])
        data = stream.fetch_discussions(client, '12')
        self.assertEqual(client._get.call_count, stream.MAX_PAGES)
        self.assertEqual(len(data['items']), stream.MAX_PAGES)
        self.assertTrue(data['partial'])

    def test_cap(self):
        """单次抽取最多 MAX_ITEMS 条待办（超出标 partial）。"""
        client = notifications_client(tasks=tasks_page(
            *[task_tile(i, 'T%d' % i) for i in range(1, stream.MAX_ITEMS + 11)]))
        data = stream.fetch_notifications(client)
        self.assertEqual(len(data['notifications']), stream.MAX_ITEMS)
        self.assertTrue(data['partial'])

    def test_forbidden_course(self):
        client = self.client('')
        client.get_classes.return_value = {'12': 'Demo course'}
        with self.assertRaises(PermissionError):
            stream.fetch_class_details(client, '13')
        client._get.assert_not_called()

    def test_invalid_class(self):
        with self.assertRaises(ValueError):
            stream.fetch_discussions(self.client(''), '../admin')

    def test_class_stats_and_unknown_sections(self):
        client = self.client('<main>Units</main>')
        client.get_classes.return_value = {'12': 'Demo course'}
        client.get_class_tasks.return_value = [
            SimpleNamespace(status='Submitted', kind='Final Exam', task_id='1', title='Exam', due_at=None,
                            due_text='', score_text='80%', href='/student/classes/12/tasks/1'),
            SimpleNamespace(status='Pending', kind='Coursework', task_id='2', title='Task', due_at=None,
                            due_text='', score_text='6/7', href='/student/classes/12/tasks/2')]
        detail = stream.fetch_class_details(client, '12')
        self.assertEqual(detail['stats']['completion_rate'], 50.0)
        self.assertEqual(detail['stats']['average_score'], 80.0)
        self.assertEqual(detail['stats']['average_sample_count'], 1)
        self.assertEqual(detail['messages'][0]['type'], 'exam')
        self.assertEqual(detail['sources']['students'], 'unavailable')
        self.assertTrue(detail['partial'])

    def test_members_and_resources_discovery(self):
        client = self.client('')
        client.get_classes.return_value = {'12': 'Demo course'}
        client.get_class_tasks.return_value = []
        client._get.side_effect = [response('<a href="/student/classes/12/students">Students</a><a href="/student/classes/12/resources">Resources</a>'),
                                   response('<div data-student-id="2"><span class="name">Example student</span></div>'),
                                   response('<div class="resource-item"><a href="/attachments/3">Example file</a></div>'),
                                   response('<div class="empty-state"></div>')]
        detail = stream.fetch_class_details(client, '12')
        self.assertEqual(detail['students'][0]['name'], 'Example student')
        self.assertEqual(detail['resources'][0]['link'], BASE + '/attachments/3')
        self.assertIsNone(detail['stats']['average_score'])


class PublicEntryTests(unittest.TestCase):
    def setUp(self):
        import webapp_managebac
        self.mb = webapp_managebac
        self.client = Mock(base_url=BASE)
        self.client.get_classes.return_value = {'12': 'Demo course'}
        self.client._get.return_value = response('<div class="empty-state"></div>')
        self.client.get_class_tasks.return_value = []
        self.patcher = patch.object(self.mb, 'ManageBacClient', return_value=self.client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_public_notification_entry(self):
        result = self.mb.fetch_notifications('demo@example.invalid', 'temporary', BASE)
        self.assertEqual(result['status'], 'ok')
        self.client.login.assert_called_once_with('demo@example.invalid', 'temporary')
        self.client.session.close.assert_called_once()

    def test_login_error_closes_session(self):
        self.client.login.side_effect = self.mb.LoginError('login_failed')
        with self.assertRaises(self.mb.LoginError):
            self.mb.fetch_notifications('demo@example.invalid', 'temporary', BASE)
        self.client.session.close.assert_called_once()
        self.client._get.assert_not_called()

    def test_discussion_entry_access_check(self):
        with self.assertRaises(PermissionError):
            self.mb.fetch_discussions('demo@example.invalid', 'temporary', BASE, '99')
        self.client._get.assert_not_called()
        self.client.session.close.assert_called_once()

    def test_details_entry(self):
        result = self.mb.fetch_class_details('demo@example.invalid', 'temporary', BASE, '12')
        self.assertEqual(result['classId'], '12')
        self.assertEqual(result['status'], 'partial')
        self.client.session.close.assert_called_once()

    def test_messages_aggregate_types_and_grade_dates(self):
        self.client.get_class_tasks.return_value = [
            SimpleNamespace(status='Submitted', kind='Final Exam', task_id='1', title='Exam', due_at=None,
                            due_text='2026-09-16', score_text='80%', href='/student/classes/12/tasks/1')]
        self.client._get.return_value = response('<div class="discussion" id="discussion_2"><h4 class="h4 title">Demo</h4></div>')
        result = self.mb.fetch_messages('demo@example.invalid', 'temporary', BASE, class_id='12')
        self.assertEqual([r['type'] for r in result['messages']], ['exam', 'grade', 'discussion'])
        self.assertEqual(len({r['id'] for r in result['messages']}), 3)
        self.assertEqual(result['messages'][1]['date'], '')
        self.assertEqual(result['messages'][1]['due'], '2026-09-16')
        self.client.session.close.assert_called_once()

    def test_messages_budget_preserves_courses(self):
        result = self.mb.fetch_messages('demo@example.invalid', 'temporary', BASE, budget_seconds=0)
        self.assertEqual(len(result['courses']), 1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['messages'], [])
        self.client.get_class_tasks.assert_not_called()

    def test_messages_throttle_stops_further_requests(self):
        upstream = requests.Response()
        upstream.status_code = 429
        self.client.get_class_tasks.side_effect = requests.HTTPError(response=upstream)
        result = self.mb.fetch_messages('demo@example.invalid', 'temporary', BASE)
        self.assertEqual(result['sources']['12']['tasks'], 'rate_limited')
        self.client._get.assert_not_called()

    def test_discussions_public_contract(self):
        result = self.mb.fetch_discussions('demo@example.invalid', 'temporary', BASE, '12')
        self.assertEqual(result['messages'], [])
        self.assertEqual(result['status'], 'ok')
        self.assertIn('sources', result)


if __name__ == '__main__':
    unittest.main()
