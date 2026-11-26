import json
import mimetypes
import os
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from chess import Game, board_creation
from clocks import ChessClock
from lan import get_lan_ipv4
from rooms import RoomError, RoomManager


_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_FILES = {
    "/static/style.css": _STATIC_DIR / "style.css",
    "/static/app.js": _STATIC_DIR / "app.js",
}
_LOCK = threading.RLock()
_ROOMS = RoomManager()
_ROOM_CLOCKS: dict[str, ChessClock] = {}
_ROOM_CLOCK_SETTINGS: dict[str, dict[str, int]] = {}
_HOTSEAT_GAME = board_creation()
_HOTSEAT_CLOCK_SETTINGS = {"initial_seconds": 300, "increment_seconds": 0}
_HOTSEAT_CLOCK = ChessClock(**_HOTSEAT_CLOCK_SETTINGS)
_HOTSEAT_CLOCK.start("white")


def _clock_state(clock: ChessClock) -> dict:
    return {
        "remaining_seconds": {
            color: max(0, round(clock.remaining(color), 1))
            for color in ("white", "black")
        },
        "flagged_color": clock.flagged_color,
    }


def _game_state(
    game: Game,
    clock: ChessClock,
    clock_settings: dict[str, int],
    selected_square: str | None = None,
    mode: str = "hotseat",
    room_code: str | None = None,
    player_color: str | None = None,
    waiting: bool = False,
) -> dict:
    checkmate = game.is_checkmate()
    stalemate = game.is_stalemate()
    clock_data = _clock_state(clock)
    flagged_color = clock_data["flagged_color"]
    winner = None
    if checkmate:
        winner = "black" if game.turn == "white" else "white"
    elif flagged_color is not None:
        winner = "black" if flagged_color == "white" else "white"

    return {
        "pieces": [
            {
                "square": square,
                "name": piece.name,
                "color": piece.color,
                "symbol": piece.symbol,
            }
            for square, piece in game.board.squares.items()
        ],
        "turn": game.turn,
        "check": game.is_in_check(),
        "checkmate": checkmate,
        "stalemate": stalemate,
        "timed_out": flagged_color is not None,
        "winner": winner,
        "game_over": checkmate or stalemate or flagged_color is not None,
        "waiting": waiting,
        "mode": mode,
        "room_code": room_code,
        "player_color": player_color,
        "clock": {**clock_settings, **clock_data},
        "legal_moves": (
            game.get_legal_moves(selected_square) if selected_square is not None else []
        ),
    }


def _clock_settings(payload: dict) -> dict[str, int]:
    initial_seconds = payload.get("initial_seconds", 300)
    increment_seconds = payload.get("increment_seconds", 0)
    if (
        isinstance(initial_seconds, bool)
        or not isinstance(initial_seconds, int)
        or not 10 <= initial_seconds <= 3600
    ):
        raise ValueError("Initial clock time must be between 10 and 3600 seconds")
    if (
        isinstance(increment_seconds, bool)
        or not isinstance(increment_seconds, int)
        or not 0 <= increment_seconds <= 60
    ):
        raise ValueError("Clock increment must be between 0 and 60 seconds")
    return {
        "initial_seconds": initial_seconds,
        "increment_seconds": increment_seconds,
    }


def _read_payload(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096:
            raise ValueError("Request body must be between 1 and 4096 bytes")
        payload = json.loads(handler.rfile.read(length))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Request body must contain valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _get_room(code: str):
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Room code is required")
    return _ROOMS.get_room(code.strip())


class ChessRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/healthz":
            self._send_json({"status": "ok"})
            return

        if request.path == "/":
            self._send_file(_STATIC_DIR / "index.html")
            return

        static_file = _STATIC_FILES.get(request.path)
        if static_file is not None:
            self._send_file(static_file)
            return

        query = parse_qs(request.query)
        if request.path == "/api/state":
            room_code = query.get("room", [None])[0]
            selected = query.get("from", [None])[0]
            token = query.get("token", [None])[0]
            with _LOCK:
                try:
                    if room_code is None:
                        state = _game_state(
                            _HOTSEAT_GAME,
                            _HOTSEAT_CLOCK,
                            _HOTSEAT_CLOCK_SETTINGS,
                            selected,
                        )
                    else:
                        room = _get_room(room_code)
                        player_color = room.player_color(token) if token else None
                        if player_color is None:
                            raise ValueError("A valid room player token is required")
                        clock = _ROOM_CLOCKS[room.code]
                        state = _game_state(
                            room.game,
                            clock,
                            _ROOM_CLOCK_SETTINGS[room.code],
                            selected,
                            mode="online",
                            room_code=room.code,
                            player_color=player_color,
                            waiting=not room.started,
                        )
                except (KeyError, RoomError, ValueError) as error:
                    self._send_json({"error": str(error)}, status=400)
                    return
            self._send_json(state)
            return

        if request.path == "/api/rooms/preview":
            code = query.get("code", [None])[0]
            with _LOCK:
                try:
                    room = _get_room(code)
                    state = {
                        "room_code": room.code,
                        "started": room.started,
                        "clock": _ROOM_CLOCK_SETTINGS[room.code],
                    }
                except (KeyError, RoomError, ValueError) as error:
                    self._send_json({"error": str(error)}, status=404)
                    return
            self._send_json(state)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        request = urlsplit(self.path)
        if request.path not in {
            "/api/new-game",
            "/api/rooms/create",
            "/api/rooms/join",
            "/api/move",
        }:
            self._send_json({"error": "Not found"}, status=404)
            return

        try:
            payload = _read_payload(self) if self.headers.get("Content-Length") else {}
            with _LOCK:
                if request.path == "/api/new-game":
                    global _HOTSEAT_GAME, _HOTSEAT_CLOCK, _HOTSEAT_CLOCK_SETTINGS
                    settings = _clock_settings(payload)
                    _HOTSEAT_GAME = board_creation()
                    _HOTSEAT_CLOCK_SETTINGS = settings
                    _HOTSEAT_CLOCK = ChessClock(**settings)
                    _HOTSEAT_CLOCK.start("white")
                    response = _game_state(
                        _HOTSEAT_GAME, _HOTSEAT_CLOCK, _HOTSEAT_CLOCK_SETTINGS
                    )
                elif request.path == "/api/rooms/create":
                    settings = _clock_settings(payload)
                    room, token = _ROOMS.create_room()
                    _ROOM_CLOCK_SETTINGS[room.code] = settings
                    _ROOM_CLOCKS[room.code] = ChessClock(**settings)
                    response = {
                        "room_code": room.code,
                        "player_token": token,
                        "player_color": "white",
                        "state": _game_state(
                            room.game,
                            _ROOM_CLOCKS[room.code],
                            settings,
                            mode="online",
                            room_code=room.code,
                            player_color="white",
                            waiting=True,
                        ),
                    }
                elif request.path == "/api/rooms/join":
                    code = payload.get("room_code")
                    room = _get_room(code)
                    room, token = _ROOMS.join_room(room.code)
                    clock = _ROOM_CLOCKS[room.code]
                    clock.start("white")
                    response = {
                        "room_code": room.code,
                        "player_token": token,
                        "player_color": "black",
                        "state": _game_state(
                            room.game,
                            clock,
                            _ROOM_CLOCK_SETTINGS[room.code],
                            mode="online",
                            room_code=room.code,
                            player_color="black",
                        ),
                    }
                elif request.path == "/api/move":
                    start = payload.get("from")
                    end = payload.get("to")
                    promotion = payload.get("promotion")
                    if not isinstance(start, str) or not isinstance(end, str):
                        raise ValueError("Move requires string 'from' and 'to' squares")

                    room_code = payload.get("room_code")
                    if room_code is None:
                        game = _HOTSEAT_GAME
                        clock = _HOTSEAT_CLOCK
                        settings = _HOTSEAT_CLOCK_SETTINGS
                        player_color = game.turn
                        mode = "hotseat"
                    else:
                        room = _get_room(room_code)
                        if not room.started:
                            raise ValueError("Waiting for the other player to join")
                        player_token = payload.get("player_token")
                        if not isinstance(player_token, str):
                            raise ValueError("A valid room player token is required")
                        room = _ROOMS.validate_move_submission(room.code, player_token)
                        player_color = room.player_color(player_token)
                        game = room.game
                        clock = _ROOM_CLOCKS[room.code]
                        settings = _ROOM_CLOCK_SETTINGS[room.code]
                        mode = "online"

                    clock.remaining(game.turn)
                    if clock.flagged_color is not None:
                        raise ValueError("The game is over: a player's clock has expired")
                    if player_color != game.turn:
                        raise ValueError(f"It is {game.turn}'s turn")
                    result = game.play_move(start, end, promotion)
                    clock.complete_move(
                        result.color, game_over=result.checkmate or result.stalemate
                    )
                    state = _game_state(
                        game,
                        clock,
                        settings,
                        mode=mode,
                        room_code=room_code,
                        player_color=player_color,
                    )
                    response = {"result": asdict(result), "state": state}
                else:
                    self._send_json({"error": "Not found"}, status=404)
                    return
        except (KeyError, RoomError, TypeError, ValueError) as error:
            self._send_json({"error": str(error)}, status=400)
            return
        self._send_json(response)

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._send_json({"error": "Static file not found"}, status=404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        print(f"[web] {self.address_string()} - {format % args}")


def create_server(
    port: int | None = None, host: str | None = None
) -> ThreadingHTTPServer:
    if port is None:
        port = int(os.environ.get("CHESS_PORT", "8000"))
    if host is None:
        host = os.environ.get("CHESS_BIND_HOST") or get_lan_ipv4()
    return ThreadingHTTPServer((host, port), ChessRequestHandler)


def main() -> None:
    server = create_server()
    address, port = server.server_address
    wildcard_bind = address in ("0.0.0.0", "::")
    if wildcard_bind:
        print(
            f"Chess is listening on {address}:{port}; "
            "use the address configured by your cluster ingress."
        )
    else:
        url = f"http://{address}:{port}"
        print(f"Chess is available to devices on your Wi-Fi at {url}")

    open_browser = os.environ.get(
        "CHESS_OPEN_BROWSER", "false" if wildcard_bind else "true"
    ).lower() in ("1", "true", "yes", "on")
    if open_browser and not wildcard_bind:
        webbrowser.open(f"http://{address}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping chess server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
