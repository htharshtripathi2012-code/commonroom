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


if __name__ == "__main__":
    unittest.main()
