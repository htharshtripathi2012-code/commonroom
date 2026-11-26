"""SQLite-backed identities and LAN chat data."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Iterator


_PASSWORD_ITERATIONS = 310_000
_SESSION_DAYS = 30
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
_CHANNEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MESSAGE_MAX_LENGTH = 4000
_MESSAGE_PAGE_SIZE = 100


class ChatError(ValueError):
    """A user-correctable chat operation error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChatStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member'
                        CHECK(role IN ('member', 'admin')),
                    active INTEGER NOT NULL DEFAULT 1,
                    last_ip TEXT,
                    last_seen TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    invite_code TEXT NOT NULL UNIQUE,
                    created_by INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_members (
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'member')),
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    created_by INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(team_id, name)
                );
                CREATE TABLE IF NOT EXISTS direct_messages (
                    id INTEGER PRIMARY KEY,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    user_a INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    user_b INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    UNIQUE(team_id, user_a, user_b),
                    CHECK(user_a < user_b)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    channel_id INTEGER REFERENCES channels(id) ON DELETE CASCADE,
                    direct_message_id INTEGER REFERENCES direct_messages(id) ON DELETE CASCADE,
                    parent_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
                    author_id INTEGER NOT NULL REFERENCES users(id),
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK(
                        (channel_id IS NOT NULL AND direct_message_id IS NULL)
                        OR
                        (channel_id IS NULL AND direct_message_id IS NOT NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS messages_channel
                    ON messages(channel_id, parent_id, id);
                CREATE INDEX IF NOT EXISTS messages_dm
                    ON messages(direct_message_id, parent_id, id);
                """
            )
            user_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(users)")
            }
            for name, declaration in (
                ("role", "TEXT NOT NULL DEFAULT 'member'"),
                ("active", "INTEGER NOT NULL DEFAULT 1"),
                ("last_ip", "TEXT"),
                ("last_seen", "TEXT"),
            ):
                if name not in user_columns:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN {name} {declaration}"
                    )
            connection.commit()

    @staticmethod
    def _user(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"] if "role" in row.keys() else "member",
        }

    def create_user(self, username: str, password: str) -> dict:
        if not isinstance(username, str) or not _USERNAME_PATTERN.fullmatch(username):
            raise ChatError("Use 3–30 letters, numbers, dots, dashes, or underscores.")
        if (
            not isinstance(password, str)
            or len(password) < 10
            or len(password) > 128
        ):
            raise ChatError("Password must be between 10 and 128 characters.")
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS
        )
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users
                        (username, username_key, salt, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        username.casefold(),
                        salt,
                        password_hash,
                        _now(),
                    ),
                )
                return {"id": cursor.lastrowid, "username": username, "role": "member"}
        except sqlite3.IntegrityError as error:
            raise ChatError("That username is already taken.") from error

    def authenticate(self, username: str, password: str) -> dict | None:
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, username, salt, password_hash, role, active
                FROM users WHERE username_key = ?
                """,
                (username.casefold(),),
            ).fetchone()
        if row is None or not row["active"]:
            hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), b"lan-chat-dummy-salt", _PASSWORD_ITERATIONS
            )
            return None
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), row["salt"], _PASSWORD_ITERATIONS
        )
        if not hmac.compare_digest(candidate, row["password_hash"]):
            return None
        return self._user(row)

    def configure_admin(self, username: str, password: str) -> dict:
        """Create the configured administrator or promote its existing account."""
        if not isinstance(username, str) or not _USERNAME_PATTERN.fullmatch(username):
            raise ChatError("Use 3–30 letters, numbers, dots, dashes, or underscores.")
        if (
            not isinstance(password, str)
            or len(password) < 10
            or len(password) > 128
        ):
            raise ChatError("Admin password must be between 10 and 128 characters.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, role FROM users WHERE username_key = ?",
                (username.casefold(),),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE users SET role = 'admin' WHERE id = ?", (row["id"],)
                )
                return {"id": row["id"], "username": row["username"], "role": "admin"}
        user = self.create_user(username, password)
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE id = ?", (user["id"],)
            )
        return {**user, "role": "admin"}

    def create_session(self, user_id: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=_SESSION_DAYS)).isoformat(
            timespec="seconds"
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(token_hash, user_id, csrf_token, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    user_id,
                    csrf_token,
                    expires_at,
                ),
            )
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
        return token, csrf_token

    def get_session(self, token: str | None) -> dict | None:
        if not isinstance(token, str) or not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.username, users.role, users.active,
                    sessions.csrf_token, sessions.expires_at
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= _now() or not row["active"]:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
                )
                return None
            return {
                "user": {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                },
                "csrf_token": row["csrf_token"],
            }

    @staticmethod
    def _require_admin(connection: sqlite3.Connection, admin_id: int) -> None:
        row = connection.execute(
            "SELECT role, active FROM users WHERE id = ?", (admin_id,)
        ).fetchone()
        if row is None or row["role"] != "admin" or not row["active"]:
            raise ChatError("Administrator access is required.")

    @staticmethod
    def _protect_last_admin(
        connection: sqlite3.Connection, target_id: int
    ) -> None:
        target = connection.execute(
            "SELECT role, active FROM users WHERE id = ?", (target_id,)
        ).fetchone()
        if target is None:
            raise ChatError("Account not found.")
        if target["role"] == "admin" and target["active"]:
            count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()[0]
            if count <= 1:
                raise ChatError("Keep at least one active administrator.")

    def record_activity(self, user_id: int, client_ip: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET last_ip = ?, last_seen = ? WHERE id = ?",
                (client_ip, _now(), user_id),
            )

    def admin_list_users(self, admin_id: int, search: str = "") -> list[dict]:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            needle = f"%{search.strip()}%"
            rows = connection.execute(
                """
                SELECT id, username, role, active, last_ip, last_seen, created_at
                FROM users
                WHERE username LIKE ? COLLATE NOCASE
                    OR COALESCE(last_ip, '') LIKE ?
                ORDER BY username COLLATE NOCASE
                LIMIT 200
                """,
                (needle, needle),
            ).fetchall()
            return [dict(row) for row in rows]

    def admin_list_teams(self, admin_id: int) -> list[dict]:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            rows = connection.execute(
                """
                SELECT teams.id, teams.name, teams.created_at, users.username AS owner,
                    COUNT(team_members.user_id) AS member_count
                FROM teams
                JOIN users ON users.id = teams.created_by
                LEFT JOIN team_members ON team_members.team_id = teams.id
                GROUP BY teams.id
                ORDER BY teams.created_at DESC
                LIMIT 200
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def admin_list_team_members(self, admin_id: int) -> list[dict]:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            rows = connection.execute(
                """
                SELECT team_members.team_id, teams.name AS team,
                    team_members.user_id, users.username, team_members.role
                FROM team_members
                JOIN teams ON teams.id = team_members.team_id
                JOIN users ON users.id = team_members.user_id
                ORDER BY teams.name COLLATE NOCASE, users.username COLLATE NOCASE
                LIMIT 1000
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def admin_list_messages(self, admin_id: int, search: str = "") -> list[dict]:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            needle = f"%{search.strip()}%"
            rows = connection.execute(
                """
                SELECT messages.id, messages.body, messages.created_at,
                    users.username AS author, teams.name AS team,
                    COALESCE('#' || channels.name, 'Direct message') AS location
                FROM messages
                JOIN users ON users.id = messages.author_id
                JOIN teams ON teams.id = messages.team_id
                LEFT JOIN channels ON channels.id = messages.channel_id
                WHERE messages.body LIKE ? COLLATE NOCASE
                    OR users.username LIKE ? COLLATE NOCASE
                    OR teams.name LIKE ? COLLATE NOCASE
                ORDER BY messages.id DESC
                LIMIT 200
                """,
                (needle, needle, needle),
            ).fetchall()
            return [dict(row) for row in rows]

    def admin_set_user_role(
        self, admin_id: int, target_id: int, role: str
    ) -> None:
        if role not in ("admin", "member"):
            raise ChatError("Choose a valid account role.")
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            if target_id == admin_id and role != "admin":
                raise ChatError("You cannot remove your own administrator access.")
            if role == "member":
                self._protect_last_admin(connection, target_id)
            cursor = connection.execute(
                "UPDATE users SET role = ? WHERE id = ?", (role, target_id)
            )
            if cursor.rowcount == 0:
                raise ChatError("Account not found.")

    def admin_set_user_active(
        self, admin_id: int, target_id: int, active: bool
    ) -> None:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            if target_id == admin_id and not active:
                raise ChatError("You cannot deactivate your own account.")
            if not active:
                self._protect_last_admin(connection, target_id)
            cursor = connection.execute(
                "UPDATE users SET active = ? WHERE id = ?",
                (int(active), target_id),
            )
            if cursor.rowcount == 0:
                raise ChatError("Account not found.")
            if not active:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))

    def admin_kick_user(self, admin_id: int, target_id: int) -> None:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            if target_id == admin_id:
                raise ChatError("You cannot kick your own account.")
            cursor = connection.execute(
                "DELETE FROM sessions WHERE user_id = ?", (target_id,)
            )
            if cursor.rowcount == 0:
                raise ChatError("That account has no active sessions.")

    def admin_delete_user(self, admin_id: int, target_id: int) -> None:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            if target_id == admin_id:
                raise ChatError("You cannot delete your own account.")
            self._protect_last_admin(connection, target_id)
            exists = connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (target_id,)
            ).fetchone()
            if exists is None:
                raise ChatError("Account not found.")
            connection.execute(
                "UPDATE teams SET created_by = ? WHERE created_by = ?",
                (admin_id, target_id),
            )
            connection.execute(
                "UPDATE channels SET created_by = ? WHERE created_by = ?",
                (admin_id, target_id),
            )
            connection.execute(
                """
                INSERT INTO team_members(team_id, user_id, role, joined_at)
                SELECT team_id, ?, 'owner', ? FROM team_members
                WHERE user_id = ? AND role = 'owner'
                ON CONFLICT(team_id, user_id) DO UPDATE SET role = 'owner'
                """,
                (admin_id, _now(), target_id),
            )
            connection.execute(
                "DELETE FROM messages WHERE author_id = ?", (target_id,)
            )
            connection.execute("DELETE FROM users WHERE id = ?", (target_id,))

    def admin_remove_team_member(
        self, admin_id: int, team_id: int, target_id: int
    ) -> None:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            member = connection.execute(
                "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
                (team_id, target_id),
            ).fetchone()
            if member is None:
                raise ChatError("That account is not a member of this team.")
            if member["role"] == "owner":
                connection.execute(
                    """
                    INSERT INTO team_members(team_id, user_id, role, joined_at)
                    VALUES (?, ?, 'owner', ?)
                    ON CONFLICT(team_id, user_id) DO UPDATE SET role = 'owner'
                    """,
                    (team_id, admin_id, _now()),
                )
                connection.execute(
                    "UPDATE teams SET created_by = ? WHERE id = ?",
                    (admin_id, team_id),
                )
            connection.execute(
                "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
                (team_id, target_id),
            )

    def admin_delete_team(self, admin_id: int, team_id: int) -> None:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            cursor = connection.execute("DELETE FROM teams WHERE id = ?", (team_id,))
            if cursor.rowcount == 0:
                raise ChatError("Team not found.")

    def admin_delete_message(self, admin_id: int, message_id: int) -> None:
        with self._connect() as connection:
            self._require_admin(connection, admin_id)
            cursor = connection.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            if cursor.rowcount == 0:
                raise ChatError("Message not found.")

    def delete_session(self, token: str | None) -> None:
        if not isinstance(token, str) or not token:
            return
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
            )

    def create_team(self, user_id: int, name: str) -> dict:
        name = self._clean_name(name, "Team", 60)
        with self._connect() as connection:
            for _ in range(10):
                invite_code = secrets.token_hex(5).upper()
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO teams(name, invite_code, created_by, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (name, invite_code, user_id, _now()),
                    )
                    team_id = cursor.lastrowid
                    connection.execute(
                        """
                        INSERT INTO team_members(team_id, user_id, role, joined_at)
                        VALUES (?, ?, 'owner', ?)
                        """,
                        (team_id, user_id, _now()),
                    )
                    connection.execute(
                        """
                        INSERT INTO channels(team_id, name, created_by, created_at)
                        VALUES (?, 'general', ?, ?)
                        """,
                        (team_id, user_id, _now()),
                    )
                    return {
                        "id": team_id,
                        "name": name,
                        "invite_code": invite_code,
                    }
                except sqlite3.IntegrityError:
                    continue
        raise ChatError("Unable to create a team. Please try again.")

    def join_team(self, user_id: int, invite_code: str) -> int:
        if not isinstance(invite_code, str):
            raise ChatError("Enter a valid team invite code.")
        normalized = invite_code.strip().upper()
        with self._connect() as connection:
            team = connection.execute(
                "SELECT id FROM teams WHERE invite_code = ?", (normalized,)
            ).fetchone()
            if team is None:
                raise ChatError("No team found for that invite code.")
            connection.execute(
                """
                INSERT OR IGNORE INTO team_members(team_id, user_id, role, joined_at)
                VALUES (?, ?, 'member', ?)
                """,
                (team["id"], user_id, _now()),
            )
            return team["id"]

    @staticmethod
    def _clean_name(name: str, kind: str, maximum: int) -> str:
        if not isinstance(name, str):
            raise ChatError(f"Enter a valid {kind.lower()} name.")
        clean = " ".join(name.split())
        if not clean or len(clean) > maximum:
            raise ChatError(f"{kind} name must be between 1 and {maximum} characters.")
        return clean

    @staticmethod
    def _require_team_member(connection: sqlite3.Connection, team_id: int, user_id: int):
        row = connection.execute(
            "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id),
        ).fetchone()
        if row is None:
            raise ChatError("You are not a member of that team.")

    def list_teams(self, user_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT teams.id, teams.name, teams.invite_code
                FROM teams JOIN team_members ON team_members.team_id = teams.id
                WHERE team_members.user_id = ?
                ORDER BY teams.name COLLATE NOCASE
                """,
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_team(self, team_id: int, user_id: int) -> dict:
        with self._connect() as connection:
            self._require_team_member(connection, team_id, user_id)
            row = connection.execute(
                "SELECT id, name, invite_code FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            if row is None:
                raise ChatError("Team not found.")
            return dict(row)

    def list_channels(self, team_id: int, user_id: int) -> list[dict]:
        with self._connect() as connection:
            self._require_team_member(connection, team_id, user_id)
            rows = connection.execute(
                """
                SELECT id, name FROM channels WHERE team_id = ?
                ORDER BY name COLLATE NOCASE
                """,
                (team_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_channel(self, team_id: int, user_id: int, name: str) -> int:
        if not isinstance(name, str):
            raise ChatError("Channel names may use lowercase letters, numbers, - and _.")
        normalized = name.strip().lower().lstrip("#")
        if not _CHANNEL_PATTERN.fullmatch(normalized):
            raise ChatError("Channel names may use lowercase letters, numbers, - and _.")
        try:
            with self._connect() as connection:
                self._require_team_member(connection, team_id, user_id)
                cursor = connection.execute(
                    """
                    INSERT INTO channels(team_id, name, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (team_id, normalized, user_id, _now()),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise ChatError("That channel already exists.") from error

    def list_members(self, team_id: int, user_id: int) -> list[dict]:
        with self._connect() as connection:
            self._require_team_member(connection, team_id, user_id)
            rows = connection.execute(
                """
                SELECT users.id, users.username FROM users
                JOIN team_members ON team_members.user_id = users.id
                WHERE team_members.team_id = ?
                ORDER BY users.username COLLATE NOCASE
                """,
                (team_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_direct_message(
        self, team_id: int, user_id: int, username: str
    ) -> int:
        with self._connect() as connection:
            self._require_team_member(connection, team_id, user_id)
            peer = connection.execute(
                """
                SELECT users.id, users.username FROM users
                JOIN team_members ON team_members.user_id = users.id
                WHERE team_members.team_id = ? AND users.username_key = ?
                """,
                (team_id, username.strip().casefold()),
            ).fetchone()
            if peer is None:
                raise ChatError("That username is not a member of this team.")
            if peer["id"] == user_id:
                raise ChatError("Choose another member to start a direct message.")
            user_a, user_b = sorted((user_id, peer["id"]))
            connection.execute(
                """
                INSERT OR IGNORE INTO direct_messages(team_id, user_a, user_b, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (team_id, user_a, user_b, _now()),
            )
            row = connection.execute(
                """
                SELECT id FROM direct_messages
                WHERE team_id = ? AND user_a = ? AND user_b = ?
                """,
                (team_id, user_a, user_b),
            ).fetchone()
            return row["id"]

    def list_direct_messages(self, team_id: int, user_id: int) -> list[dict]:
        with self._connect() as connection:
            self._require_team_member(connection, team_id, user_id)
            rows = connection.execute(
                """
                SELECT dm.id,
                    CASE WHEN dm.user_a = ? THEN user_b.username ELSE user_a.username END
                        AS username
                FROM direct_messages AS dm
                JOIN users AS user_a ON user_a.id = dm.user_a
                JOIN users AS user_b ON user_b.id = dm.user_b
                WHERE dm.team_id = ? AND (dm.user_a = ? OR dm.user_b = ?)
                ORDER BY username COLLATE NOCASE
                """,
                (user_id, team_id, user_id, user_id),
            ).fetchall()
            return [dict(row) for row in rows]

    def _require_conversation(
        self, connection: sqlite3.Connection, user_id: int, kind: str, conversation_id: int
    ) -> dict:
        if kind == "channel":
            row = connection.execute(
                """
                SELECT channels.id, channels.team_id, channels.id AS channel_id,
                    NULL AS direct_message_id
                FROM channels JOIN team_members
                    ON team_members.team_id = channels.team_id
                WHERE channels.id = ? AND team_members.user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
        elif kind == "dm":
            row = connection.execute(
                """
                SELECT direct_messages.id, direct_messages.team_id,
                    NULL AS channel_id, direct_messages.id AS direct_message_id
                FROM direct_messages JOIN team_members
                    ON team_members.team_id = direct_messages.team_id
                WHERE direct_messages.id = ? AND team_members.user_id = ?
                    AND (direct_messages.user_a = ? OR direct_messages.user_b = ?)
                """,
                (conversation_id, user_id, user_id, user_id),
            ).fetchone()
        else:
            raise ChatError("Unknown conversation type.")
        if row is None:
            raise ChatError("Conversation not found.")
        return dict(row)

    def send_message(
        self,
        user_id: int,
        kind: str,
        conversation_id: int,
        body: str,
        parent_id: int | None = None,
    ) -> dict:
        if not isinstance(body, str):
            raise ChatError("Message must be text.")
        body = body.strip()
        if not body or len(body) > _MESSAGE_MAX_LENGTH:
            raise ChatError("Message must be between 1 and 4000 characters.")
        with self._connect() as connection:
            conversation = self._require_conversation(
                connection, user_id, kind, conversation_id
            )
            if parent_id is not None:
                parent = connection.execute(
                    """
                    SELECT id, team_id, channel_id, direct_message_id, parent_id
                    FROM messages WHERE id = ?
                    """,
                    (parent_id,),
                ).fetchone()
                if (
                    parent is None
                    or parent["parent_id"] is not None
                    or parent["team_id"] != conversation["team_id"]
                    or parent["channel_id"] != conversation["channel_id"]
                    or parent["direct_message_id"]
                    != conversation["direct_message_id"]
                ):
                    raise ChatError("Thread message does not belong to this conversation.")
            cursor = connection.execute(
                """
                INSERT INTO messages
                    (team_id, channel_id, direct_message_id, parent_id,
                     author_id, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation["team_id"],
                    conversation["channel_id"],
                    conversation["direct_message_id"],
                    parent_id,
                    user_id,
                    body,
                    _now(),
                ),
            )
            message_id = cursor.lastrowid
            return self._get_message(connection, user_id, message_id)

    @staticmethod
    def _get_message(
        connection: sqlite3.Connection, user_id: int, message_id: int
    ) -> dict:
        row = connection.execute(
            """
            SELECT messages.id, messages.team_id, messages.channel_id,
                messages.direct_message_id, messages.parent_id,
                messages.author_id, users.username AS author,
                messages.body, messages.created_at
            FROM messages
            JOIN users ON users.id = messages.author_id
            WHERE messages.id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            raise ChatError("Message not found.")
        return dict(row)

    def get_message(self, user_id: int, message_id: int) -> dict:
        with self._connect() as connection:
            message = self._get_message(connection, user_id, message_id)
            self._require_conversation(
                connection,
                user_id,
                "channel" if message["channel_id"] is not None else "dm",
                message["channel_id"] or message["direct_message_id"],
            )
            return message

    def list_messages(
        self,
        user_id: int,
        kind: str,
        conversation_id: int,
        after_id: int = 0,
        parent_id: int | None = None,
    ) -> list[dict]:
        if after_id < 0:
            raise ChatError("Invalid message cursor.")
        with self._connect() as connection:
            conversation = self._require_conversation(
                connection, user_id, kind, conversation_id
            )
            column = "channel_id" if kind == "channel" else "direct_message_id"
            rows = connection.execute(
                f"""
                SELECT messages.id, messages.parent_id, messages.author_id,
                    users.username AS author, messages.body, messages.created_at
                FROM messages JOIN users ON users.id = messages.author_id
                WHERE messages.{column} = ? AND messages.id > ?
                    AND messages.parent_id IS ?
                ORDER BY messages.id DESC LIMIT ?
                """,
                (
                    conversation_id,
                    after_id,
                    parent_id,
                    _MESSAGE_PAGE_SIZE,
                ),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
