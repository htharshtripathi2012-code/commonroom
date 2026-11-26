"""Progressively enhanced, server-rendered LAN team chat."""

from __future__ import annotations

import html
import json
import mimetypes
import os
import threading
import time
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from chat_store import ChatError, ChatStore
from lan import get_lan_ipv4


_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_FILES = {
    "/static/style.css": _STATIC_DIR / "style.css",
    "/static/app.js": _STATIC_DIR / "app.js",
}
_COOKIE_NAME = "lan_chat_session"
_BODY_LIMIT = 16_384


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _safe_return_to(value: str | None) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return "/"
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or "\\" in value
        or not value.isascii()
        or any(ord(character) < 32 for character in value)
    ):
        return "/"
    return value


def _int_value(value: str | None, label: str) -> int:
    try:
        result = int(value or "")
    except (TypeError, ValueError) as error:
        raise ChatError(f"Choose a valid {label}.") from error
    if result <= 0:
        raise ChatError(f"Choose a valid {label}.")
    return result


def _url(path: str, **params) -> str:
    return f"{path}?{urlencode({key: value for key, value in params.items() if value is not None})}"


def _render_auth_page(
    title: str,
    description: str,
    form: str,
    error: str = "",
) -> str:
    message = (
        f'<p class="notice error" role="alert">{_escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#2d353b">
  <title>{_escape(title)} · Commonroom</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body class="auth-body">
  <main class="auth-shell">
    <a class="wordmark" href="/"><span class="brand-glyph" aria-hidden="true">●</span> commonroom</a>
    <section class="auth-card">
      <p class="eyebrow">PRIVATE LAN WORKSPACE</p>
      <h1>{_escape(title)}</h1>
      <p class="muted">{_escape(description)}</p>
      {message}
      {form}
    </section>
    <p class="auth-footnote">Hosted on your local network. Open this site from any device on the same Wi-Fi.</p>
  </main>
</body>
</html>"""


def _render_message(
    message: dict,
    *,
    team_id: int,
    kind: str,
    conversation_id: int,
    threaded: bool = False,
) -> str:
    timestamp = _escape(message["created_at"])
    display_time = _escape(message["created_at"].replace("T", " ")[:16])
    thread_link = ""
    if not threaded and message["parent_id"] is None:
        context = {"team": team_id, "thread": message["id"]}
        context["channel" if kind == "channel" else "dm"] = conversation_id
        thread_link = (
            f'<a class="thread-link" href="{_url("/", **context)}">Reply in thread</a>'
        )
    return f"""<li class="message" data-message-id="{message["id"]}">
  <span class="message-avatar" aria-hidden="true">{_escape(message["author"][:1].upper())}</span>
  <div class="message-content">
    <div class="message-meta"><strong>{_escape(message["author"])}</strong><time datetime="{timestamp}">{display_time}</time></div>
    <p>{_escape(message["body"])}</p>{thread_link}
  </div>
</li>"""


def _message_feed(
    messages: list[dict],
    *,
    team_id: int,
    kind: str,
    conversation_id: int,
    parent_id: int | None = None,
) -> str:
    latest = messages[-1]["id"] if messages else (parent_id or 0)
    items = "".join(
        _render_message(
            message,
            team_id=team_id,
            kind=kind,
            conversation_id=conversation_id,
            threaded=parent_id is not None,
        )
        for message in messages
    )
    if not items:
        items = '<li class="empty-feed">No messages yet. Start the conversation.</li>'
    parent_attribute = "" if parent_id is None else f' data-parent="{parent_id}"'
    return (
        f'<ol class="message-feed" data-kind="{kind}" data-team="{team_id}" '
        f'data-id="{conversation_id}" data-after="{latest}"{parent_attribute}>'
        f"{items}</ol>"
    )


def _render_workspace(
    store: ChatStore,
    user: dict,
    csrf_token: str,
    query: dict[str, list[str]],
) -> str:
    error = query.get("error", [""])[0][:250]
    teams = store.list_teams(user["id"])
    requested_team = query.get("team", [None])[0]
    team = None
    if requested_team:
        try:
            team = store.get_team(int(requested_team), user["id"])
        except (ValueError, ChatError):
            team = None
    if team is None and teams:
        team = teams[0]

    active_context: dict = {}
    channels: list[dict] = []
    direct_messages: list[dict] = []
    if team is not None:
        channels = store.list_channels(team["id"], user["id"])
        direct_messages = store.list_direct_messages(team["id"], user["id"])

    requested_dm = query.get("dm", [None])[0]
    requested_channel = query.get("channel", [None])[0]
    selected_dm = None
    selected_channel = None
    if team is not None and requested_dm:
        selected_dm = next(
            (item for item in direct_messages if str(item["id"]) == requested_dm),
            None,
        )
    if team is not None and selected_dm is None:
        selected_channel = next(
            (item for item in channels if str(item["id"]) == requested_channel),
            None,
        )
        if selected_channel is None and channels:
            selected_channel = channels[0]

    kind = "dm" if selected_dm else "channel"
    conversation_id = (
        selected_dm["id"] if selected_dm else selected_channel["id"] if selected_channel else None
    )
    active_context = {"team": team["id"]} if team else {}
    if selected_dm:
        active_context["dm"] = selected_dm["id"]
        heading = selected_dm["username"]
        subheading = "Direct message"
    elif selected_channel:
        active_context["channel"] = selected_channel["id"]
        heading = f"#{selected_channel['name']}"
        subheading = f"Channel in {team['name']}"
    else:
        heading = "Your workspace"
        subheading = "Create a team or join one with an invite code."

    messages: list[dict] = []
    thread_root = None
    thread_messages: list[dict] = []
    thread_value = query.get("thread", [None])[0]
    if team is not None and conversation_id is not None:
        messages = store.list_messages(
            user["id"], kind, conversation_id, parent_id=None
        )
        if thread_value:
            try:
                root = store.get_message(user["id"], _int_value(thread_value, "thread"))
                expected_field = "channel_id" if kind == "channel" else "direct_message_id"
                if (
                    root["team_id"] == team["id"]
                    and root[expected_field] == conversation_id
                    and root["parent_id"] is None
                ):
                    thread_root = root
                    thread_messages = store.list_messages(
                        user["id"],
                        kind,
                        conversation_id,
                        parent_id=root["id"],
                    )
            except ChatError:
                thread_root = None

    team_links = "".join(
        f'<a class="team-rail-link {"selected" if team and item["id"] == team["id"] else ""}" '
        f'href="{_url("/", team=item["id"])}" title="{_escape(item["name"])}" aria-label="{_escape(item["name"])}"'
        f'{" aria-current=\"page\"" if team and item["id"] == team["id"] else ""}>'
        f'<span class="team-avatar" aria-hidden="true">{_escape(item["name"][:1].upper())}</span></a>'
        for item in teams
    )
    channel_links = "".join(
        f'<a class="nav-link {"selected" if selected_channel and item["id"] == selected_channel["id"] else ""}"'
        f'{" aria-current=\"page\"" if selected_channel and item["id"] == selected_channel["id"] else ""} '
        f'href="{_url("/", team=team["id"], channel=item["id"])}"><span>#</span>{_escape(item["name"])}</a>'
        for item in channels
    ) if team else ""
    dm_links = "".join(
        f'<a class="nav-link {"selected" if selected_dm and item["id"] == selected_dm["id"] else ""}"'
        f'{" aria-current=\"page\"" if selected_dm and item["id"] == selected_dm["id"] else ""} '
        f'href="{_url("/", team=team["id"], dm=item["id"])}"><span class="dm-dot">●</span>{_escape(item["username"])}</a>'
        for item in direct_messages
    ) if team else ""

    channel_select_options = "".join(
        f'<option value="{item["id"]}">#{_escape(item["name"])}</option>'
        for item in channels
    )
    dm_form = ""
    if team:
        members = store.list_members(team["id"], user["id"])
        member_options = "".join(
            f'<option value="{_escape(member["username"])}">{_escape(member["username"])}</option>'
            for member in members if member["id"] != user["id"]
        )
        dm_form = f"""<form class="inline-form" action="/dms/create" method="post">
  <input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">
  <input type="hidden" name="team_id" value="{team["id"]}">
  <label class="sr-only" for="dm-peer">Start a direct message</label>
  <select id="dm-peer" name="username" required aria-label="Choose a team member">
    <option value="">Message someone…</option>{member_options}
  </select><button class="icon-button" type="submit" aria-label="Start direct message">+</button>
</form>"""

    error_notice = (
        f'<p class="notice error" role="alert">{_escape(error)}</p>' if error else ""
    )
    team_forms = f"""<details class="sidebar-details" id="team-actions">
  <summary>New team</summary>
  <form action="/teams/create" method="post" class="stack-form">
    <input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">
    <label for="team-name">Team name</label>
    <input id="team-name" name="name" maxlength="60" required placeholder="Design crew">
    <button class="small-button" type="submit">Create team</button>
  </form>
</details>
<details class="sidebar-details">
  <summary>Join a team</summary>
  <form action="/teams/join" method="post" class="stack-form">
    <input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">
    <label for="invite-code">Invite code</label>
    <input id="invite-code" name="invite_code" maxlength="20" required autocomplete="off">
    <button class="small-button" type="submit">Join team</button>
  </form>
</details>"""

    if team is None:
        content = f"""<section class="welcome-card">
  <div class="welcome-main">
    <p class="eyebrow">COMMONROOM / PRIVATE NETWORK</p>
    <h1>Make room<br><em>for conversation.</em></h1>
    <p>A shared space for your people. Start a team or join one with an invite code, then talk in channels, direct messages, and threads.</p>
  </div>
  <ol class="welcome-steps">
    <li><span>01</span><strong>Create a team</strong><small>Open “New team” in the sidebar and invite people with its code.</small></li>
    <li><span>02</span><strong>Join your people</strong><small>Open “Join a team” and enter a code shared with you.</small></li>
  </ol>
</section>"""
    else:
        if conversation_id is not None:
            feed = _message_feed(
                messages,
                team_id=team["id"],
                kind=kind,
                conversation_id=conversation_id,
            )
            return_to = _url("/", **active_context)
            composer = f"""<form class="composer" action="/messages/send" method="post">
  <input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">
  <input type="hidden" name="kind" value="{kind}">
  <input type="hidden" name="conversation_id" value="{conversation_id}">
  <input type="hidden" name="return_to" value="{_escape(return_to)}">
  <label class="sr-only" for="message-body">Write a message</label>
  <textarea id="message-body" name="body" rows="2" maxlength="4000" required placeholder="Message {_escape(heading)}"></textarea>
  <button class="send-button" type="submit">Send <span aria-hidden="true">↗</span></button>
</form>"""
            thread_panel = ""
            if thread_root:
                thread_feed = _message_feed(
                    thread_messages,
                    team_id=team["id"],
                    kind=kind,
                    conversation_id=conversation_id,
                    parent_id=thread_root["id"],
                )
                thread_return = _url("/", **active_context, thread=thread_root["id"])
                thread_panel = f"""<aside class="thread-panel">
  <div class="thread-heading"><div><p class="eyebrow">THREAD</p><h2>Replies</h2></div>
    <a class="icon-button close-thread" href="{_url("/", **active_context)}" aria-label="Close thread">×</a>
  </div>
  <ol class="thread-root">{_render_message(thread_root, team_id=team["id"], kind=kind, conversation_id=conversation_id, threaded=True)}</ol>
  {thread_feed}
  <form class="composer thread-composer" action="/messages/send" method="post">
    <input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">
    <input type="hidden" name="kind" value="{kind}">
    <input type="hidden" name="conversation_id" value="{conversation_id}">
    <input type="hidden" name="parent_id" value="{thread_root["id"]}">
    <input type="hidden" name="return_to" value="{_escape(thread_return)}">
    <label class="sr-only" for="thread-body">Reply to thread</label>
    <textarea id="thread-body" name="body" rows="2" maxlength="4000" required placeholder="Reply to thread…"></textarea>
    <button class="send-button" type="submit">Reply</button>
  </form>
</aside>"""
            content = f"""<section class="conversation">
  <header class="conversation-heading">
    <div><p class="eyebrow">{_escape(subheading)}</p><h1>{_escape(heading)}</h1></div>
    <div class="live-tools"><span class="live-indicator"><i></i><span>Live</span></span>
      <a class="refresh-link" href="{_url("/", **active_context)}">Refresh</a></div>
  </header>
  <div class="conversation-body {"has-thread" if thread_root else ""}">
    <div class="feed-column">{feed}{composer}
      <noscript><p class="noscript-note">Automatic updates need JavaScript. Use Refresh to check for new messages; sending works without JavaScript.</p></noscript>
    </div>{thread_panel}
  </div>
</section>"""
        else:
            content = """<section class="welcome-card compact">
  <p class="eyebrow">TEAM SPACE</p><h1>Your team is ready.</h1>
  <p>Create a channel or start a direct message to begin chatting.</p>
</section>"""

    invite_banner = ""
    if team:
        invite_banner = (
            f'<span class="invite-note">Invite code <code>{_escape(team["invite_code"])}</code></span>'
        )
    workspace = f"""<div class="workspace">
  <nav class="workspace-rail" aria-label="Workspaces">
    <a class="rail-home" href="/" aria-label="Commonroom home"><span>c</span></a>
    <div class="team-rail-list">{team_links or '<span class="rail-empty">—</span>'}</div>
    <a class="rail-add" href="#team-actions" aria-label="Create or join a team">+</a>
  </nav>
  <aside class="sidebar" id="workspace-navigation" aria-label="Workspace navigation">
    <a class="workspace-title" href="/"><span>{_escape(team["name"]) if team else "Your workspaces"}</span><span aria-hidden="true">⌄</span></a>
    <div class="sidebar-scroll">
      {team_forms}
      {invite_banner}
      {f'<section class="nav-section"><div class="section-heading"><span>CHANNELS</span><span class="count">{len(channels)}</span></div><nav aria-label="Channels">{channel_links}</nav><details class="sidebar-details"><summary>Add channel</summary><form action="/channels/create" method="post" class="stack-form"><input type="hidden" name="csrf_token" value="{_escape(csrf_token)}"><input type="hidden" name="team_id" value="{team["id"]}"><label for="channel-name">Channel name</label><input id="channel-name" name="name" maxlength="40" pattern="[a-zA-Z0-9_-]+" required placeholder="project-launch"><button class="small-button" type="submit">Create channel</button></form></details></section>' if team else ''}
      {f'<section class="nav-section"><div class="section-heading"><span>DIRECT MESSAGES</span><span class="count">{len(direct_messages)}</span></div>{dm_form}<nav aria-label="Direct messages">{dm_links or "<p class=sidebar-empty>No direct messages yet.</p>"}</nav></section>' if team else ''}
    </div>
    <div class="user-card"><span class="user-avatar">{_escape(user["username"][:1].upper())}</span><div class="user-detail"><strong>{_escape(user["username"])}</strong><span>On this network</span></div>
      <form action="/logout" method="post"><input type="hidden" name="csrf_token" value="{_escape(csrf_token)}"><button class="logout-button" type="submit" aria-label="Log out">↗</button></form>
    </div>
  </aside>
  <main class="main-pane">
    <a class="mobile-nav-jump" href="#workspace-navigation"><span class="wordmark"><span class="brand-glyph" aria-hidden="true">●</span> commonroom</span><span>Workspace menu ↓</span></a>
    {error_notice}{content}
  </main>
</div>"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#2d353b">
  <title>{_escape(heading)} · Commonroom</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="/static/app.js" defer></script>
</head>
<body>
  <div class="app-frame">{workspace}</div>
</body>
</html>"""


class ChatRequestHandler(BaseHTTPRequestHandler):
    store: ChatStore

    def _run_request(self, method_handler) -> None:
        self._request_id = uuid.uuid4().hex
        self._response_status = 500
        self._response_started = False
        self._request_error = "-"
        started = time.monotonic()
        try:
            method_handler()
        except Exception as error:
            trace = error.__traceback__
            while trace is not None and trace.tb_next is not None:
                trace = trace.tb_next
            location = Path(trace.tb_frame.f_code.co_filename).name if trace else "unknown"
            line = trace.tb_lineno if trace else 0
            self._request_error = f"{type(error).__name__}@{location}:{line}"
            if not self._response_started:
                self._send_html(
                    _render_auth_page(
                        "Something went wrong",
                        "The request could not be completed.",
                        f'<p class="notice error">Reference {_escape(self._request_id)}</p><a class="button-link" href="/">Return home</a>',
                    ),
                    status=500,
                )
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            route = urlsplit(self.path).path
            method = self.command if self.command in ("GET", "POST") else "OTHER"
            print(
                f"[chat] request_id={self._request_id} method={method} "
                f"client={self.address_string()} route={route} "
                f"status={self._response_status} duration_ms={duration_ms} "
                f"error={self._request_error}"
            )

    def do_GET(self) -> None:
        self._run_request(self._handle_get)

    def do_POST(self) -> None:
        self._run_request(self._handle_post)

    def _session(self) -> dict | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(_COOKIE_NAME)
        return self.store.get_session(morsel.value if morsel else None)

    def _handle_get(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/healthz":
            self._send_json({"status": "ok"})
            return
        if request.path in _STATIC_FILES:
            self._send_file(_STATIC_FILES[request.path])
            return
        if request.path == "/api/messages":
            session = self._session()
            if session is None:
                self._send_json({"error": "Login required."}, status=401)
                return
            query = parse_qs(request.query)
            try:
                kind = query.get("kind", [""])[0]
                conversation_id = _int_value(query.get("id", [None])[0], "conversation")
                after_id = int(query.get("after", ["0"])[0])
                parent_text = query.get("parent", [None])[0]
                parent_id = _int_value(parent_text, "thread") if parent_text else None
                messages = self.store.list_messages(
                    session["user"]["id"], kind, conversation_id, after_id, parent_id
                )
            except (ChatError, ValueError) as error:
                self._send_json({"error": str(error)}, status=400)
                return
            self._send_json({"messages": messages})
            return
        if request.path == "/login":
            if self._session():
                self._redirect("/")
                return
            query = parse_qs(request.query)
            error = query.get("error", [""])[0][:250]
            form = """<form action="/login" method="post" class="auth-form">
  <label for="username">Username</label><input id="username" name="username" autocomplete="username" required autofocus>
  <label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
  <button class="primary-button" type="submit">Log in</button>
</form><p class="auth-switch">New here? <a href="/signup">Create an account</a></p>"""
            self._send_html(_render_auth_page("Welcome back", "Sign in to your local workspace.", form, error))
            return
        if request.path == "/signup":
            if self._session():
                self._redirect("/")
                return
            query = parse_qs(request.query)
            error = query.get("error", [""])[0][:250]
            form = """<form action="/signup" method="post" class="auth-form">
  <label for="username">Username</label><input id="username" name="username" minlength="3" maxlength="30" pattern="[A-Za-z0-9_.-]+" autocomplete="username" required autofocus>
  <label for="password">Password</label><input id="password" name="password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required>
  <span class="field-help">Use at least 10 characters. Your account is stored on this server.</span>
  <button class="primary-button" type="submit">Create account</button>
</form><p class="auth-switch">Already have an account? <a href="/login">Log in</a></p>"""
            self._send_html(_render_auth_page("Make yourself at home", "Create a local account to join teams and message people.", form, error))
            return
        if request.path == "/":
            session = self._session()
            if session is None:
                self._redirect("/login")
                return
            self._send_html(
                _render_workspace(
                    self.store,
                    session["user"],
                    session["csrf_token"],
                    parse_qs(request.query),
                )
            )
            return
        self._send_html(_render_auth_page("Not found", "That page does not exist.", '<a class="button-link" href="/">Return home</a>'), status=404)

    def _handle_post(self) -> None:
        request = urlsplit(self.path)
        try:
            form = self._read_form()
        except ChatError as error:
            self._redirect("/?" + urlencode({"error": str(error)}))
            return

        if request.path == "/signup":
            try:
                user = self.store.create_user(
                    form.get("username", ""), form.get("password", "")
                )
                token, _ = self.store.create_session(user["id"])
            except ChatError as error:
                self._redirect("/signup?" + urlencode({"error": str(error)}))
                return
            self._redirect("/", cookie=self._session_cookie(token))
            return
        if request.path == "/login":
            user = self.store.authenticate(
                form.get("username", ""), form.get("password", "")
            )
            if user is None:
                self._redirect("/login?" + urlencode({"error": "Username or password is incorrect."}))
                return
            token, _ = self.store.create_session(user["id"])
            self._redirect("/", cookie=self._session_cookie(token))
            return

        session = self._session()
        if session is None:
            self._redirect("/login")
            return
        if not hmac_compare(form.get("csrf_token", ""), session["csrf_token"]):
            self._send_html(
                _render_auth_page(
                    "Session expired",
                    "Refresh the page and submit the form again.",
                    '<a class="button-link" href="/">Return home</a>',
                ),
                status=403,
            )
            return
        user = session["user"]
        return_to = _safe_return_to(form.get("return_to"))

        try:
            if request.path == "/logout":
                self.store.delete_session(self._session_token())
                self._redirect("/login", cookie=self._expired_cookie())
                return
            if request.path == "/teams/create":
                team = self.store.create_team(user["id"], form.get("name", ""))
                channel = self.store.list_channels(team["id"], user["id"])[0]
                self._redirect(_url("/", team=team["id"], channel=channel["id"]))
                return
            if request.path == "/teams/join":
                team_id = self.store.join_team(
                    user["id"], form.get("invite_code", "")
                )
                channel = self.store.list_channels(team_id, user["id"])[0]
                self._redirect(_url("/", team=team_id, channel=channel["id"]))
                return
            if request.path == "/channels/create":
                team_id = _int_value(form.get("team_id"), "team")
                channel_id = self.store.create_channel(
                    team_id, user["id"], form.get("name", "")
                )
                self._redirect(_url("/", team=team_id, channel=channel_id))
                return
            if request.path == "/dms/create":
                team_id = _int_value(form.get("team_id"), "team")
                dm_id = self.store.create_direct_message(
                    team_id, user["id"], form.get("username", "")
                )
                self._redirect(_url("/", team=team_id, dm=dm_id))
                return
            if request.path == "/messages/send":
                kind = form.get("kind", "")
                conversation_id = _int_value(
                    form.get("conversation_id"), "conversation"
                )
                parent_id = (
                    _int_value(form.get("parent_id"), "thread")
                    if form.get("parent_id")
                    else None
                )
                self.store.send_message(
                    user["id"],
                    kind,
                    conversation_id,
                    form.get("body", ""),
                    parent_id,
                )
                self._redirect(return_to)
                return
        except ChatError as error:
            target = return_to if return_to != "/" else "/"
            separator = "&" if "?" in target else "?"
            self._redirect(f"{target}{separator}{urlencode({'error': str(error)})}")
            return
        self._send_html(
            _render_auth_page("Not found", "That action does not exist.", '<a class="button-link" href="/">Return home</a>'),
            status=404,
        )

    def _session_token(self) -> str | None:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get(_COOKIE_NAME)
        return morsel.value if morsel else None

    def _read_form(self) -> dict[str, str]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/x-www-form-urlencoded":
            raise ChatError("Submit this form from the Commonroom page.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ChatError("Invalid form size.") from error
        if length <= 0 or length > _BODY_LIMIT:
            raise ChatError("Form must be between 1 and 16384 bytes.")
        try:
            values = parse_qs(
                self.rfile.read(length).decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=False,
            )
        except UnicodeDecodeError as error:
            raise ChatError("Form must contain valid UTF-8 text.") from error
        return {key: items[0] for key, items in values.items() if items}

    @staticmethod
    def _session_cookie(token: str) -> str:
        return f"{_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"

    @staticmethod
    def _expired_cookie() -> str:
        return f"{_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    def _redirect(self, target: str, cookie: str | None = None) -> None:
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        self._response_status = 303
        self._response_started = True
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._send_html("Not found", status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._response_status = 200
        self._response_started = True
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self._response_status = status
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._response_status = status
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        return


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return isinstance(left, str) and hmac.compare_digest(left, right)


def create_server(
    port: int | None = None,
    host: str | None = None,
    db_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    if port is None:
        port = int(os.environ.get("CHAT_PORT", "8000"))
    if host is None:
        host = os.environ.get("CHAT_BIND_HOST") or get_lan_ipv4()
    if db_path is None:
        db_path = os.environ.get(
            "CHAT_DB_PATH", str(Path(__file__).parent / "data" / "commonroom.sqlite3")
        )
    store = ChatStore(db_path)
    handler = type("BoundChatRequestHandler", (ChatRequestHandler,), {"store": store})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    if os.name == "posix":
        os.umask(0o077)
    server = create_server()
    address, port = server.server_address
    wildcard_bind = address in ("0.0.0.0", "::")
    if wildcard_bind:
        print(f"Commonroom is listening on {address}:{port}.")
    else:
        url = f"http://{address}:{port}"
        print(f"Commonroom is available to devices on your Wi-Fi at {url}")
    open_browser = os.environ.get(
        "CHAT_OPEN_BROWSER", "false" if wildcard_bind else "true"
    ).lower() in ("1", "true", "yes", "on")
    if open_browser and not wildcard_bind:
        webbrowser.open(f"http://{address}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Commonroom.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
