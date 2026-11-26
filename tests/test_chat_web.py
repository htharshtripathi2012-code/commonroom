import http.cookiejar
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from web import _safe_return_to, create_server


class ChatWebTests(unittest.TestCase):
    def test_return_targets_are_local_only(self):
        self.assertEqual(_safe_return_to("/?team=2&channel=3"), "/?team=2&channel=3")
        self.assertEqual(_safe_return_to("//example.com"), "/")
        self.assertEqual(_safe_return_to("/\\example.com"), "/")
        self.assertEqual(_safe_return_to("/☃"), "/")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = create_server(
            port=0,
            host="127.0.0.1",
            db_path=Path(self.temp_dir.name) / "chat.sqlite3",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()

    def request(self, path, form=None, opener=None):
        data = urlencode(form).encode() if form is not None else None
        headers = (
            {"Content-Type": "application/x-www-form-urlencoded"}
            if form is not None
            else {}
        )
        request = Request(self.base_url + path, data=data, headers=headers)
        with (opener or self.opener).open(request, timeout=5) as response:
            return response.status, response.geturl(), response.read().decode()

    @staticmethod
    def csrf_token(page):
        match = re.search(
            r'name="csrf_token" value="([^"]+)"', page
        )
        if not match:
            raise AssertionError("Page did not contain a CSRF token")
        return match.group(1)

    def test_server_rendered_signup_channel_and_thread_work_without_javascript(self):
        status, final_url, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertTrue(final_url.endswith("/login"))

        status, _, page = self.request("/signup")
        self.assertEqual(status, 200)
        self.request(
            "/signup",
            {"username": "alice", "password": "a-secure-local-password"},
        )
        _, _, page = self.request("/")
        csrf = self.csrf_token(page)

        with self.assertRaises(HTTPError) as context:
            self.request(
                "/teams/create",
                {"csrf_token": "invalid", "name": "Should not exist"},
            )
        self.assertEqual(context.exception.code, 403)
        context.exception.close()

        self.request(
            "/teams/create",
            {"csrf_token": csrf, "name": "Friends"},
        )
        _, _, page = self.request("/")
        self.assertIn("Friends", page)
        self.assertIn('class="workspace-rail"', page)
        team_match = re.search(r'href="/\?team=(\d+)"', page)
        feed_match = re.search(
            r'<ol class="message-feed" data-kind="channel" data-team="(\d+)" data-id="(\d+)"',
            page,
        )
        self.assertIsNotNone(team_match)
        self.assertIsNotNone(feed_match)
        team_id, channel_id = feed_match.groups()

        _, _, page = self.request(
            "/channels/create",
            {
                "csrf_token": csrf,
                "team_id": team_id,
                "name": "project-room",
            },
        )
        self.assertIn("#project-room", page)
        channel_id = re.search(
            r'<ol class="message-feed" data-kind="channel" data-team="\d+" data-id="(\d+)"',
            page,
        ).group(1)
        return_to = f"/?team={team_id}&channel={channel_id}"
        message = "<script>not executable</script>"
        self.request(
            "/messages/send",
            {
                "csrf_token": csrf,
                "kind": "channel",
                "conversation_id": channel_id,
                "return_to": return_to,
                "body": message,
            },
        )
        _, _, page = self.request(return_to)
        self.assertIn("&lt;script&gt;not executable&lt;/script&gt;", page)
        self.assertNotIn("<script>not executable</script>", page)
        self.assertIn('class="message-avatar"', page)
        root_id = re.search(r'data-message-id="(\d+)"', page).group(1)

        thread_url = f"/?team={team_id}&channel={channel_id}&thread={root_id}"
        _, _, thread_page = self.request(thread_url)
        self.assertIn('name="parent_id"', thread_page)
        self.request(
            "/messages/send",
            {
                "csrf_token": csrf,
                "kind": "channel",
                "conversation_id": channel_id,
                "parent_id": root_id,
                "return_to": thread_url,
                "body": "A reply without a client app",
            },
        )
        _, _, thread_page = self.request(thread_url)
        self.assertIn("A reply without a client app", thread_page)
        self.assertIn('src="/static/app.js"', thread_page)
        self.assertIn("<noscript>", thread_page)

        api_status, _, api_response = self.request(
            f"/api/messages?kind=channel&id={channel_id}&after=0"
        )
        self.assertEqual(api_status, 200)
        self.assertEqual(json.loads(api_response)["messages"][0]["id"], int(root_id))

        bob_opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request("/signup", opener=bob_opener)
        self.request(
            "/signup",
            {"username": "bob", "password": "another-secure-password"},
            opener=bob_opener,
        )
        _, _, bob_page = self.request("/", opener=bob_opener)
        bob_csrf = self.csrf_token(bob_page)
        invite_code = re.search(r"<code>([A-F0-9]+)</code>", page).group(1)
        self.request(
            "/teams/join",
            {"csrf_token": bob_csrf, "invite_code": invite_code},
            opener=bob_opener,
        )
        self.request(
            "/messages/send",
            {
                "csrf_token": bob_csrf,
                "kind": "channel",
                "conversation_id": channel_id,
                "return_to": return_to,
                "body": "Hello from a second browser",
            },
            opener=bob_opener,
        )
        _, _, api_response = self.request(
            f"/api/messages?kind=channel&id={channel_id}&after={root_id}"
        )
        remote_messages = json.loads(api_response)["messages"]
        self.assertEqual(remote_messages[0]["author"], "bob")
        self.assertEqual(remote_messages[0]["body"], "Hello from a second browser")

        _, _, dm_page = self.request(
            "/dms/create",
            {"csrf_token": csrf, "team_id": team_id, "username": "bob"},
        )
        dm_id = re.search(
            r'<ol class="message-feed" data-kind="dm" data-team="\d+" data-id="(\d+)"',
            dm_page,
        ).group(1)
        self.request(
            "/messages/send",
            {
                "csrf_token": bob_csrf,
                "kind": "dm",
                "conversation_id": dm_id,
                "return_to": f"/?team={team_id}&dm={dm_id}",
                "body": "Private message across devices",
            },
            opener=bob_opener,
        )
        _, _, api_response = self.request(
            f"/api/messages?kind=dm&id={dm_id}&after=0"
        )
        self.assertEqual(
            json.loads(api_response)["messages"][0]["body"],
            "Private message across devices",
        )

    def test_admin_gets_separate_dashboard_and_members_are_denied(self):
        self.request(
            "/signup",
            {"username": "alice", "password": "a-secure-local-password"},
        )
        store = self.server.RequestHandlerClass.store
        store.configure_admin("alice", "ignored-existing-account-password")
        status, _, page = self.request("/admin")
        self.assertEqual(status, 200)
        self.assertIn("Administration", page)
        self.assertIn("Message review", page)
        self.assertIn("DNS resolver and device MAC are not visible", page)

        bob_opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request(
            "/signup",
            {"username": "bob", "password": "another-secure-password"},
            opener=bob_opener,
        )
        _, _, bob_home = self.request("/", opener=bob_opener)
        with self.assertRaises(HTTPError) as context:
            self.request("/admin", opener=bob_opener)
        self.assertEqual(context.exception.code, 403)
        context.exception.close()
        admin_id = store.authenticate("alice", "a-secure-local-password")["id"]
        bob_id = next(
            item["id"]
            for item in store.admin_list_users(admin_id)
            if item["username"] == "bob"
        )
        _, _, admin_page = self.request("/admin")
        self.request(
            "/admin/users/kick",
            {
                "csrf_token": self.csrf_token(admin_page),
                "user_id": bob_id,
                "return_to": "/admin",
            },
        )
        _, final_url, _ = self.request("/", opener=bob_opener)
        self.assertTrue(final_url.endswith("/login"))


if __name__ == "__main__":
    unittest.main()
