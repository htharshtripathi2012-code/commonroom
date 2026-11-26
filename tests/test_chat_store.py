import sqlite3
import tempfile
import unittest
from pathlib import Path

from chat_store import ChatError, ChatStore


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "chat.sqlite3"
        self.store = ChatStore(self.database)
        self.alice = self.store.create_user("alice", "correct-horse-battery")
        self.bob = self.store.create_user("bob", "another-secure-password")
        self.outsider = self.store.create_user("outsider", "a-different-password")
        self.team = self.store.create_team(self.alice["id"], "Friends")
        self.store.join_team(self.bob["id"], self.team["invite_code"])
        self.channel_id = self.store.list_channels(
            self.team["id"], self.alice["id"]
        )[0]["id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_password_authentication_and_persistent_sessions(self):
        self.assertEqual(
            self.store.authenticate("ALICE", "correct-horse-battery")["id"],
            self.alice["id"],
        )
        self.assertIsNone(self.store.authenticate("alice", "wrong-password"))
        token, csrf_token = self.store.create_session(self.alice["id"])
        reopened = ChatStore(self.database)
        session = reopened.get_session(token)
        self.assertEqual(session["user"], self.alice)
        self.assertEqual(session["csrf_token"], csrf_token)

    def test_duplicate_accounts_and_invalid_passwords_are_rejected(self):
        with self.assertRaisesRegex(ChatError, "already taken"):
            self.store.create_user("ALICE", "another-secure-password")
        with self.assertRaisesRegex(ChatError, "between 10 and 128"):
            self.store.create_user("shortpass", "short")

    def test_team_channels_direct_messages_and_threads_are_scoped(self):
        channel_message = self.store.send_message(
            self.alice["id"], "channel", self.channel_id, "A channel message"
        )
        reply = self.store.send_message(
            self.bob["id"],
            "channel",
            self.channel_id,
            "A threaded reply",
            channel_message["id"],
        )
        self.assertEqual(
            self.store.list_messages(
                self.alice["id"],
                "channel",
                self.channel_id,
                parent_id=channel_message["id"],
            )[0]["id"],
            reply["id"],
        )

        dm_id = self.store.create_direct_message(
            self.team["id"], self.alice["id"], "bob"
        )
        self.store.send_message(self.bob["id"], "dm", dm_id, "A private message")
        self.assertEqual(
            len(self.store.list_messages(self.alice["id"], "dm", dm_id)), 1
        )
        with self.assertRaisesRegex(ChatError, "Conversation not found"):
            self.store.list_messages(self.outsider["id"], "dm", dm_id)
        with self.assertRaisesRegex(ChatError, "not belong"):
            self.store.send_message(
                self.alice["id"], "channel", self.channel_id, "Wrong thread", reply["id"]
            )

    def test_message_validation_and_channel_uniqueness(self):
        with self.assertRaisesRegex(ChatError, "already exists"):
            self.store.create_channel(self.team["id"], self.alice["id"], "GENERAL")
        with self.assertRaisesRegex(ChatError, "between 1 and 4000"):
            self.store.send_message(self.alice["id"], "channel", self.channel_id, "  ")
        with self.assertRaisesRegex(ChatError, "4000"):
            self.store.send_message(
                self.alice["id"], "channel", self.channel_id, "x" * 4001
            )

    def test_admin_can_filter_activity_review_and_moderate_users(self):
        admin = self.store.configure_admin("admin", "unique-admin-password")
        self.store.record_activity(self.bob["id"], "192.0.2.25")
        dm_id = self.store.create_direct_message(
            self.team["id"], self.alice["id"], "bob"
        )
        private_message = self.store.send_message(
            self.bob["id"], "dm", dm_id, "Private message for review"
        )

        self.assertEqual(
            self.store.admin_list_users(admin["id"], "192.0.2.25")[0]["username"],
            "bob",
        )
        reviewed = self.store.admin_list_messages(admin["id"], "Private message")
        self.assertEqual(reviewed[0]["body"], "Private message for review")
        self.store.admin_delete_message(admin["id"], private_message["id"])
        self.assertEqual(self.store.admin_list_messages(admin["id"]), [])

        token, _ = self.store.create_session(self.bob["id"])
        self.store.admin_kick_user(admin["id"], self.bob["id"])
        self.assertIsNone(self.store.get_session(token))
        self.store.admin_set_user_active(admin["id"], self.bob["id"], False)
        self.assertIsNone(self.store.authenticate("bob", "another-secure-password"))
        with self.assertRaisesRegex(ChatError, "cannot deactivate your own"):
            self.store.admin_set_user_active(admin["id"], admin["id"], False)

    def test_admin_deletion_erases_user_content_and_transfers_team_ownership(self):
        admin = self.store.configure_admin("admin", "unique-admin-password")
        self.store.send_message(
            self.alice["id"], "channel", self.channel_id, "Remove with account"
        )

        self.store.admin_delete_user(admin["id"], self.alice["id"])

        teams = self.store.admin_list_teams(admin["id"])
        self.assertEqual(teams[0]["owner"], "admin")
        members = self.store.admin_list_team_members(admin["id"])
        self.assertTrue(
            any(
                member["username"] == "admin"
                and member["role"] == "owner"
                and member["team_id"] == self.team["id"]
                for member in members
            )
        )
        self.assertEqual(self.store.admin_list_messages(admin["id"]), [])
        self.assertNotIn(
            "alice",
            [user["username"] for user in self.store.admin_list_users(admin["id"])],
        )

    def test_admin_actions_require_an_admin_and_protect_last_admin(self):
        admin = self.store.configure_admin("admin", "unique-admin-password")
        with self.assertRaisesRegex(ChatError, "Administrator access"):
            self.store.admin_list_users(self.bob["id"])
        with self.assertRaisesRegex(ChatError, "cannot remove your own administrator"):
            self.store.admin_set_user_role(admin["id"], admin["id"], "member")

    def test_existing_database_gets_admin_and_activity_columns(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

        upgraded = ChatStore(legacy_path)
        user = upgraded.create_user("legacy", "legacy-secure-password")
        upgraded.record_activity(user["id"], "192.0.2.9")
        upgraded.configure_admin("legacy", "unused-existing-account-password")
        self.assertEqual(
            upgraded.admin_list_users(user["id"])[0]["role"], "admin"
        )
        self.assertEqual(
            upgraded.admin_list_users(user["id"])[0]["last_ip"], "192.0.2.9"
        )


if __name__ == "__main__":
    unittest.main()
