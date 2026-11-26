"""In-memory rooms for local-network chess games."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hmac
import secrets
from threading import RLock

from chess import Game, board_creation


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_CHARACTERS = frozenset(_CODE_ALPHABET)
_CHAT_HISTORY_LIMIT = 100
_CHAT_MESSAGE_MAX_LENGTH = 500


class RoomError(Exception):
    """Base class for room-related errors."""


class InvalidRoomError(RoomError):
    """The supplied room code is malformed or does not identify a room."""


class RoomFullError(RoomError):
    """The room already has both players."""


class InvalidPlayerTokenError(RoomError):
    """The token does not belong to a player in this room."""


class NotPlayersTurnError(RoomError):
    """The authenticated player's color does not match the game turn."""


@dataclass(frozen=True)
class Room:
    """Public room information and its color-to-token player assignments."""

    code: str
    game: Game
    players: dict[str, str]

    def player_color(self, token: str) -> str | None:
        """Return the token's color, or ``None`` if it is not in this room."""
        if not isinstance(token, str) or not token.isascii():
            return None
        for color, player_token in self.players.items():
            if hmac.compare_digest(player_token, token):
                return color
        return None

    @property
    def started(self) -> bool:
        return "black" in self.players

    @property
    def waiting(self) -> bool:
        return not self.started

    @property
    def status(self) -> str:
        return "started" if self.started else "waiting"


@dataclass
class _RoomState:
    room: Room
    tokens: dict[str, str]
    draw_offer_by: str | None = None
    finished_status: str | None = None
    winner: str | None = None
    chat_messages: list[dict] = field(default_factory=list)
    next_chat_id: int = 1


class RoomManager:
    """Manage private, in-memory two-player chess rooms.

    ``create_room`` and ``join_room`` return ``(room, player_token)``.
    Tokens should be kept by the corresponding player and used for move
    submissions; room codes alone do not grant permission to play.
    """

    def __init__(self, code_length: int = 6) -> None:
        if not isinstance(code_length, int) or isinstance(code_length, bool):
            raise TypeError("code_length must be an integer")
        if not 4 <= code_length <= 10:
            raise ValueError("code_length must be between 4 and 10")
        self._code_length = code_length
        self._rooms: dict[str, _RoomState] = {}
        self._lock = RLock()

    def create_room(self) -> tuple[Room, str]:
        """Create a game with the creator as white and return their token."""
        with self._lock:
            code = self._new_code()
            token = self._new_token()
            players = {"white": token}
            room = Room(code=code, game=board_creation(), players=players)
            self._rooms[code] = _RoomState(room=room, tokens=players)
            return room, token

    def join_room(self, code: str) -> tuple[Room, str]:
        """Join an existing room as black, if its one player slot is open."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._rooms.get(normalized)
            if state is None:
                raise InvalidRoomError(f"No room found for code {normalized!r}")
            if state.room.started:
                raise RoomFullError(f"Room {normalized!r} is full")
            token = self._new_token()
            state.room.players["black"] = token
            return state.room, token

    def get_room(self, code: str) -> Room:
        """Look up a room by its shareable code."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._rooms.get(normalized)
            if state is None:
                raise InvalidRoomError(f"No room found for code {normalized!r}")
            return state.room

    def lookup_room(self, code: str) -> Room:
        """Alias for :meth:`get_room`."""
        return self.get_room(code)

    def get_player_color(self, code: str, player_token: str) -> str:
        """Authenticate a token and return its assigned color."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            return self._authenticate(state, player_token)

    def validate_move_submission(self, code: str, player_token: str) -> Room:
        """Verify the token and current turn, returning the room if allowed."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            color = self._authenticate(state, player_token)
            self._ensure_active(state)
            if state.room.game.turn != color:
                raise NotPlayersTurnError(
                    f"It is {state.room.game.turn}'s turn, not {color}'s"
                )
            return state.room

    def submit_move(
        self,
        code: str,
        player_token: str,
        from_position: str,
        to_position: str,
        promotion: str | None = None,
    ):
        """Validate player authority and apply a move through the chess API."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            color = self._authenticate(state, player_token)
            self._ensure_active(state)
            if state.room.game.turn != color:
                raise NotPlayersTurnError(
                    f"It is {state.room.game.turn}'s turn, not {color}'s"
                )
            return state.room.game.play_move(
                from_position, to_position, promotion=promotion
            )

    def protocol_state(self, code: str) -> dict:
        """Return a consistent snapshot of room protocol state."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            if not state.room.started:
                status = "waiting"
            elif state.finished_status is not None:
                status = state.finished_status
            elif state.room.game.is_checkmate():
                status = "checkmate"
            elif state.room.game.is_stalemate():
                status = "stalemate"
            else:
                status = "active"
            return {
                "status": status,
                "winner": state.winner,
                "draw_offer_by": state.draw_offer_by,
                "chat_messages": list(state.chat_messages),
            }

    def authenticate_player(self, code: str, player_token: str) -> str:
        """Authenticate a token and return its color."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            return self._authenticate(state, player_token)

    def submit_chat(
        self, code: str, player_token: str, message: str
    ) -> dict:
        """Add a validated, bounded chat message to a started active room."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            color = self._authenticate(state, player_token)
            self._ensure_active(state)
            if not isinstance(message, str):
                raise ValueError("Message must be a string")
            text = message.strip()
            if not text:
                raise ValueError("Message cannot be empty")
            if len(text) > _CHAT_MESSAGE_MAX_LENGTH:
                raise ValueError(
                    f"Message cannot exceed {_CHAT_MESSAGE_MAX_LENGTH} characters"
                )
            chat_message = {
                "id": state.next_chat_id,
                "color": color,
                "text": text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            state.next_chat_id += 1
            state.chat_messages.append(chat_message)
            del state.chat_messages[:-_CHAT_HISTORY_LIMIT]
            return dict(chat_message)

    def handle_draw_action(
        self, code: str, player_token: str, action: str
    ) -> None:
        """Offer, accept, or decline a draw in a started active room."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            color = self._authenticate(state, player_token)
            self._ensure_active(state)
            if action not in ("offer", "accept", "decline"):
                raise ValueError("Draw action must be offer, accept, or decline")
            if action == "offer":
                if state.draw_offer_by is not None:
                    raise ValueError("A draw offer is already pending")
                state.draw_offer_by = color
                return
            if state.draw_offer_by is None:
                raise ValueError("There is no pending draw offer")
            if state.draw_offer_by == color:
                raise ValueError("Only the opponent can respond to a draw offer")
            if action == "accept":
                state.finished_status = "draw"
                state.winner = None
            state.draw_offer_by = None

    def resign(self, code: str, player_token: str) -> None:
        """End an active room by resignation and award the game to the opponent."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            color = self._authenticate(state, player_token)
            self._ensure_active(state)
            state.finished_status = "resigned"
            state.winner = "black" if color == "white" else "white"
            state.draw_offer_by = None

    def dismiss_draw_offer(
        self, code: str, moving_color: str, game_over: bool = False
    ) -> None:
        """Dismiss a pending offer when its opponent makes a move."""
        normalized = self._normalize_code(code)
        with self._lock:
            state = self._get_state(normalized)
            if state.draw_offer_by is not None and (
                game_over or state.draw_offer_by != moving_color
            ):
                state.draw_offer_by = None

    @staticmethod
    def _ensure_active(state: _RoomState) -> None:
        if not state.room.started:
            raise ValueError("Waiting for the other player to join")
        if (
            state.finished_status is not None
            or state.room.game.is_checkmate()
            or state.room.game.is_stalemate()
        ):
            raise ValueError("The game is over")

    def _get_state(self, normalized_code: str) -> _RoomState:
        state = self._rooms.get(normalized_code)
        if state is None:
            raise InvalidRoomError(f"No room found for code {normalized_code!r}")
        return state

    @staticmethod
    def _authenticate(state: _RoomState, player_token: str) -> str:
        color = state.room.player_color(player_token)
        if color is None:
            raise InvalidPlayerTokenError("A valid player token is required")
        return color

    def _new_code(self) -> str:
        for _ in range(128):
            code = "".join(
                secrets.choice(_CODE_ALPHABET) for _ in range(self._code_length)
            )
            if code not in self._rooms:
                return code
        raise RuntimeError("Unable to allocate a unique room code")

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _normalize_code(code: str) -> str:
        if not isinstance(code, str):
            raise InvalidRoomError("Room code must be a string")
        normalized = code.strip().upper()
        if not normalized or any(char not in _CODE_CHARACTERS for char in normalized):
            raise InvalidRoomError("Room code is invalid")
        return normalized


if __name__ == "__main__":
    import unittest

    class RoomManagerSelfCheck(unittest.TestCase):
        def setUp(self):
            self.manager = RoomManager()
            self.room, self.white_token = self.manager.create_room()

        def test_create_and_join_assign_colors_once(self):
            self.assertTrue(self.room.waiting)
            self.assertFalse(self.room.started)
            self.assertEqual(self.manager.get_player_color(self.room.code, self.white_token), "white")
            joined_room, black_token = self.manager.join_room(self.room.code.lower())
            self.assertIs(joined_room, self.room)
            self.assertTrue(self.room.started)
            self.assertFalse(self.room.waiting)
            self.assertEqual(self.manager.get_player_color(self.room.code, black_token), "black")
            with self.assertRaises(RoomFullError):
                self.manager.join_room(self.room.code)

        def test_invalid_room_and_token_errors(self):
            with self.assertRaises(InvalidRoomError):
                self.manager.get_room("NOT-A-CODE")
            with self.assertRaises(InvalidRoomError):
                self.manager.get_room("ABC234")
            with self.assertRaises(InvalidPlayerTokenError):
                self.manager.validate_move_submission(self.room.code, "wrong-token")
            self.assertIsNone(self.room.player_color("токен"))

        def test_only_the_player_whose_turn_it_is_can_move(self):
            _, black_token = self.manager.join_room(self.room.code)
            with self.assertRaises(NotPlayersTurnError):
                self.manager.submit_move(self.room.code, black_token, "e7", "e5")
            self.manager.submit_move(self.room.code, self.white_token, "e2", "e4")
            self.manager.submit_move(self.room.code, black_token, "e7", "e5")
            self.assertEqual(self.room.game.turn, "white")

        def test_chat_validation_and_history_limit(self):
            self.manager.join_room(self.room.code)
            with self.assertRaises(InvalidPlayerTokenError):
                self.manager.submit_chat(self.room.code, "wrong-token", "hello")
            with self.assertRaises(ValueError):
                self.manager.submit_chat(self.room.code, self.white_token, "  ")
            with self.assertRaises(ValueError):
                self.manager.submit_chat(self.room.code, self.white_token, "x" * 501)

            for index in range(101):
                self.manager.submit_chat(
                    self.room.code, self.white_token, f"message {index}"
                )
            messages = self.manager.protocol_state(self.room.code)["chat_messages"]
            self.assertEqual(len(messages), 100)
            self.assertEqual(messages[0]["text"], "message 1")
            self.assertEqual(messages[-1]["text"], "message 100")

        def test_draw_offer_requires_opponent_and_acceptance_finishes_game(self):
            _, black_token = self.manager.join_room(self.room.code)
            self.manager.handle_draw_action(self.room.code, self.white_token, "offer")
            self.assertEqual(
                self.manager.protocol_state(self.room.code)["draw_offer_by"], "white"
            )
            with self.assertRaises(ValueError):
                self.manager.handle_draw_action(
                    self.room.code, self.white_token, "accept"
                )
            self.manager.handle_draw_action(
                self.room.code, black_token, "accept"
            )
            state = self.manager.protocol_state(self.room.code)
            self.assertEqual(state["status"], "draw")
            self.assertIsNone(state["winner"])
            with self.assertRaises(ValueError):
                self.manager.handle_draw_action(
                    self.room.code, self.white_token, "offer"
                )

        def test_opponents_move_dismisses_draw_offer(self):
            _, black_token = self.manager.join_room(self.room.code)
            self.manager.handle_draw_action(self.room.code, self.white_token, "offer")
            white_move = self.manager.submit_move(
                self.room.code, self.white_token, "e2", "e4"
            )
            self.manager.dismiss_draw_offer(self.room.code, white_move.color)
            self.assertEqual(
                self.manager.protocol_state(self.room.code)["draw_offer_by"], "white"
            )
            black_move = self.manager.submit_move(
                self.room.code, black_token, "e7", "e5"
            )
            self.manager.dismiss_draw_offer(self.room.code, black_move.color)
            self.assertIsNone(
                self.manager.protocol_state(self.room.code)["draw_offer_by"]
            )

        def test_resignation_awards_opponent_and_prevents_moves(self):
            _, black_token = self.manager.join_room(self.room.code)
            self.manager.resign(self.room.code, black_token)
            state = self.manager.protocol_state(self.room.code)
            self.assertEqual(state["status"], "resigned")
            self.assertEqual(state["winner"], "white")
            with self.assertRaises(ValueError):
                self.manager.submit_move(
                    self.room.code, self.white_token, "e2", "e4"
                )

    unittest.main()
