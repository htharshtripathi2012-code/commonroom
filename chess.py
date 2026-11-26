from dataclasses import dataclass


@dataclass
class PieceModel:
    name: str
    symbol: str
    color: str
    position: str | None = None
    has_moved: bool = False


@dataclass(frozen=True)
class MoveResult:
    start: str
    end: str
    piece: str
    color: str
    captured_piece: str | None
    promotion: str | None
    check: bool
    checkmate: bool
    stalemate: bool


_SYMBOLS = {
    "pawn": ("♙", "♟"),
    "rook": ("♖", "♜"),
    "knight": ("♘", "♞"),
    "bishop": ("♗", "♝"),
    "queen": ("♕", "♛"),
    "king": ("♔", "♚"),
}
_PIECE_NAMES = tuple(_SYMBOLS)


def _create_piece(name: str, color: str = "white", position: str | None = None) -> PieceModel:
    if not isinstance(color, str) or color.lower() not in ("white", "black"):
        raise ValueError("color must be 'white' or 'black'")
    color = color.lower()
    symbol = _SYMBOLS[name][0 if color == "white" else 1]
    return PieceModel(name=name, symbol=symbol, color=color, position=position)


def pawn(color: str = "white", position: str | None = None) -> PieceModel:
    return _create_piece("pawn", color, position)


def rook(color: str = "white", position: str | None = None) -> PieceModel:
    return _create_piece("rook", color, position)


def knight(color: str = "white", position: str | None = None) -> PieceModel:
    return _create_piece("knight", color, position)


def bishop(color: str = "white", position: str | None = None) -> PieceModel:
    return _create_piece("bishop", color, position)


def queen(color: str = "white", position: str | None = None) -> PieceModel:
    return _create_piece("queen", color, position)


def king(color: str = "white", position: str | None = None) -> PieceModel:
    return _create_piece("king", color, position)


_PIECE_FACTORIES = {
    "pawn": pawn,
    "rook": rook,
    "knight": knight,
    "bishop": bishop,
    "queen": queen,
    "king": king,
}


class Board:
    def __init__(self):
        self.squares: dict[str, PieceModel] = {}

    def update_piece(self, position: str, piece: PieceModel | None) -> None:
        position = self._normalize_position(position)
        if piece is not None and not isinstance(piece, PieceModel):
            raise TypeError("piece must be a PieceModel or None")

        old_position = None
        if piece is not None and piece.position is not None:
            old_position = self._normalize_position(piece.position)

        previous_piece = self.squares.get(position)
        if previous_piece is not None:
            previous_piece.position = None

        if piece is None:
            self.squares.pop(position, None)
            return

        if old_position is not None and self.squares.get(old_position) is piece:
            del self.squares[old_position]
        self.squares[position] = piece
        piece.position = position

    def move_piece(self, from_position: str, to_position: str) -> PieceModel:
        from_position = self._normalize_position(from_position)
        to_position = self._normalize_position(to_position)
        piece = self.squares.get(from_position)
        if piece is None:
            raise ValueError(f"No piece at {from_position}")
        if from_position == to_position:
            raise ValueError("A piece must move to a different square")

        self.update_piece(to_position, piece)
        piece.has_moved = True
        self.squares.pop(from_position, None)
        return piece

    def get_piece(self, position: str) -> PieceModel | None:
        return self.squares.get(self._normalize_position(position))

    def remove_piece(self, position: str) -> PieceModel | None:
        position = self._normalize_position(position)
        piece = self.squares.pop(position, None)
        if piece is not None:
            piece.position = None
        return piece

    def copy(self) -> "Board":
        copied = Board()
        for position, piece in self.squares.items():
            copied.squares[position] = PieceModel(
                name=piece.name,
                symbol=piece.symbol,
                color=piece.color,
                position=position,
                has_moved=piece.has_moved,
            )
        return copied

    def __str__(self) -> str:
        rows = []
        for rank in range(8, 0, -1):
            row = [str(rank)]
            for file in "abcdefgh":
                piece = self.squares.get(f"{file}{rank}")
                row.append(piece.symbol if piece is not None else ".")
            rows.append(" ".join(row))
        rows.append("  a b c d e f g h")
        return "\n".join(rows)

    @staticmethod
    def _normalize_position(position: str) -> str:
        if not isinstance(position, str):
            raise ValueError("position must be a square such as 'e4'")
        position = position.lower()
        if (
            len(position) != 2
            or position[0] not in "abcdefgh"
            or position[1] not in "12345678"
        ):
            raise ValueError(f"Invalid board position: {position!r}")
        return position


class Game:
    def __init__(self, board: Board | None = None):
        self.board = board if board is not None else Board()
        self.current_turn = "white"
        self.en_passant_target: str | None = None
        self.halfmove_clock = 0
        self.fullmove_number = 1
        self.move_history: list[MoveResult] = []

    @property
    def turn(self) -> str:
        return self.current_turn

    @property
    def is_game_over(self) -> bool:
        return self.is_checkmate() or self.is_stalemate()

    def add_piece(self, color: str, position: str, piece_type: str) -> PieceModel:
        if not isinstance(piece_type, str):
            raise ValueError(f"Unknown piece type: {piece_type!r}")
        factory = _PIECE_FACTORIES.get(piece_type.lower())
        if factory is None:
            raise ValueError(f"Unknown piece type: {piece_type!r}")
        piece = factory(color, position)
        self.board.update_piece(position, piece)
        return piece

    def get_legal_moves(self, position: str) -> list[str]:
        position = Board._normalize_position(position)
        piece = self.board.get_piece(position)
        if piece is None or piece.color != self.current_turn:
            return []
        legal = []
        for target in self._pseudo_legal_moves(position, piece):
            if not self._leaves_king_in_check(position, target, piece):
                legal.append(target)
        return legal

    def play_move(
        self,
        from_position: str,
        to_position: str,
        promotion: str | None = None,
    ) -> MoveResult:
        from_position = Board._normalize_position(from_position)
        to_position = Board._normalize_position(to_position)
        piece = self.board.get_piece(from_position)
        if piece is None:
            raise ValueError(f"No piece at {from_position}")
        if piece.color != self.current_turn:
            raise ValueError(f"It is {self.current_turn}'s turn")
        if self.is_game_over:
            raise ValueError("The game is already over")

        is_promotion = piece.name == "pawn" and to_position[1] in ("1", "8")
        if is_promotion:
            if promotion is not None and not isinstance(promotion, str):
                raise ValueError("promotion must be a piece name")
            promotion = "queen" if promotion is None else promotion.lower()
            if promotion not in ("queen", "rook", "bishop", "knight"):
                raise ValueError("A pawn can promote to queen, rook, bishop, or knight")
        elif promotion is not None:
            raise ValueError("Promotion can only be chosen for a pawn reaching its last rank")

        if to_position not in self.get_legal_moves(from_position):
            raise ValueError(f"Illegal move: {from_position} to {to_position}")

        captured = self.board.get_piece(to_position)
        is_en_passant = (
            piece.name == "pawn"
            and to_position == self.en_passant_target
            and captured is None
            and from_position[0] != to_position[0]
        )
        if is_en_passant:
            captured = self.board.remove_piece(f"{to_position[0]}{from_position[1]}")

        moving_color = piece.color
        moving_name = piece.name
        was_pawn = piece.name == "pawn"
        self.board.move_piece(from_position, to_position)

        if moving_name == "king" and abs(ord(to_position[0]) - ord(from_position[0])) == 2:
            rank = from_position[1]
            rook_from, rook_to = (
                ("h" + rank, "f" + rank)
                if to_position[0] == "g"
                else ("a" + rank, "d" + rank)
            )
            self.board.move_piece(rook_from, rook_to)

        if is_promotion:
            promoted_piece = _PIECE_FACTORIES[promotion](moving_color, to_position)
            promoted_piece.has_moved = True
            self.board.update_piece(to_position, promoted_piece)

        self.en_passant_target = None
        if was_pawn and abs(int(to_position[1]) - int(from_position[1])) == 2:
            middle_rank = (int(to_position[1]) + int(from_position[1])) // 2
            self.en_passant_target = f"{from_position[0]}{middle_rank}"

        self.halfmove_clock = (
            0 if was_pawn or captured is not None else self.halfmove_clock + 1
        )
        if moving_color == "black":
            self.fullmove_number += 1
        self.current_turn = "black" if moving_color == "white" else "white"

        check = self.is_in_check(self.current_turn)
        checkmate = check and self.is_checkmate()
        stalemate = not check and self.is_stalemate()
        result = MoveResult(
            start=from_position,
            end=to_position,
            piece=moving_name,
            color=moving_color,
            captured_piece=captured.name if captured is not None else None,
            promotion=promotion if is_promotion else None,
            check=check,
            checkmate=checkmate,
            stalemate=stalemate,
        )
        self.move_history.append(result)
        return result

    def move_piece(
        self,
        piece_or_position: PieceModel | str,
        new_position: str,
        promotion: str | None = None,
    ) -> MoveResult:
        if isinstance(piece_or_position, PieceModel):
            if piece_or_position.position is None:
                raise ValueError("Piece not found on the board.")
            from_position = piece_or_position.position
            if self.board.get_piece(from_position) is not piece_or_position:
                raise ValueError("Piece not found on the board.")
        else:
            from_position = piece_or_position
        return self.play_move(from_position, new_position, promotion)

    def is_square_attacked(self, position: str, by_color: str) -> bool:
        position = Board._normalize_position(position)
        if by_color not in ("white", "black"):
            raise ValueError("color must be 'white' or 'black'")
        target_x, target_y = self._coordinates(position)
        for source, piece in self.board.squares.items():
            if piece.color != by_color:
                continue
            source_x, source_y = self._coordinates(source)
            dx, dy = target_x - source_x, target_y - source_y
            if piece.name == "pawn":
                direction = 1 if piece.color == "white" else -1
                if abs(dx) == 1 and dy == direction:
                    return True
            elif piece.name == "knight":
                if (abs(dx), abs(dy)) in ((1, 2), (2, 1)):
                    return True
            elif piece.name == "king":
                if max(abs(dx), abs(dy)) == 1:
                    return True
            elif self._piece_attacks_square(source_x, source_y, target_x, target_y, piece.name):
                return True
        return False

    def is_in_check(self, color: str | None = None) -> bool:
        color = self.current_turn if color is None else color
        king_position = next(
            (
                position
                for position, piece in self.board.squares.items()
                if piece.name == "king" and piece.color == color
            ),
            None,
        )
        if king_position is None:
            return False
        opponent = "black" if color == "white" else "white"
        return self.is_square_attacked(king_position, opponent)

    def is_checkmate(self, color: str | None = None) -> bool:
        color = self.current_turn if color is None else color
        if not self.is_in_check(color):
            return False
        original_turn = self.current_turn
        self.current_turn = color
        try:
            return not any(
                self.get_legal_moves(position)
                for position, piece in self.board.squares.items()
                if piece.color == color
            )
        finally:
            self.current_turn = original_turn

    def is_stalemate(self, color: str | None = None) -> bool:
        color = self.current_turn if color is None else color
        if self.is_in_check(color):
            return False
        original_turn = self.current_turn
        self.current_turn = color
        try:
            return not any(
                self.get_legal_moves(position)
                for position, piece in self.board.squares.items()
                if piece.color == color
            )
        finally:
            self.current_turn = original_turn

    def display_board(self) -> None:
        print(self.board)

    def _pseudo_legal_moves(self, position: str, piece: PieceModel) -> list[str]:
        x, y = self._coordinates(position)
        moves: list[str] = []
        if piece.name == "pawn":
            direction = 1 if piece.color == "white" else -1
            next_y = y + direction
            if self._inside(x, next_y) and self._at(x, next_y) is None:
                moves.append(self._position(x, next_y))
                start_rank = 1 if piece.color == "white" else 6
                two_y = y + 2 * direction
                if y == start_rank and self._at(x, two_y) is None:
                    moves.append(self._position(x, two_y))
            for dx in (-1, 1):
                target_x, target_y = x + dx, y + direction
                if not self._inside(target_x, target_y):
                    continue
                target = self._at(target_x, target_y)
                if target is not None and target.color != piece.color and target.name != "king":
                    moves.append(self._position(target_x, target_y))
                elif self._position(target_x, target_y) == self.en_passant_target:
                    moves.append(self._position(target_x, target_y))
        elif piece.name == "knight":
            for dx, dy in ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)):
                self._add_if_available(moves, x + dx, y + dy, piece.color)
        elif piece.name == "king":
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        self._add_if_available(moves, x + dx, y + dy, piece.color)
            moves.extend(self._castling_moves(position, piece))
        else:
            directions = {
                "rook": ((1, 0), (-1, 0), (0, 1), (0, -1)),
                "bishop": ((1, 1), (1, -1), (-1, 1), (-1, -1)),
                "queen": (
                    (1, 0), (-1, 0), (0, 1), (0, -1),
                    (1, 1), (1, -1), (-1, 1), (-1, -1),
                ),
            }[piece.name]
            for dx, dy in directions:
                target_x, target_y = x + dx, y + dy
                while self._inside(target_x, target_y):
                    target = self._at(target_x, target_y)
                    if target is None:
                        moves.append(self._position(target_x, target_y))
                    else:
                        if target.color != piece.color and target.name != "king":
                            moves.append(self._position(target_x, target_y))
                        break
                    target_x += dx
                    target_y += dy
        return moves

    def _castling_moves(self, position: str, king_piece: PieceModel) -> list[str]:
        rank = "1" if king_piece.color == "white" else "8"
        if position != f"e{rank}" or king_piece.has_moved or self.is_in_check(king_piece.color):
            return []
        moves = []
        opponent = "black" if king_piece.color == "white" else "white"
        for rook_file, target_file, empty_files, safe_files in (
            ("h", "g", ("f", "g"), ("f", "g")),
            ("a", "c", ("b", "c", "d"), ("d", "c")),
        ):
            rook_piece = self.board.get_piece(f"{rook_file}{rank}")
            if (
                rook_piece is None
                or rook_piece.name != "rook"
                or rook_piece.color != king_piece.color
                or rook_piece.has_moved
                or any(self.board.get_piece(f"{file}{rank}") is not None for file in empty_files)
                or any(self.is_square_attacked(f"{file}{rank}", opponent) for file in safe_files)
            ):
                continue
            moves.append(f"{target_file}{rank}")
        return moves

    def _leaves_king_in_check(self, start: str, end: str, piece: PieceModel) -> bool:
        test_game = Game(self.board.copy())
        test_piece = test_game.board.get_piece(start)
        if test_piece is None:
            return True
        if (
            test_piece.name == "pawn"
            and end == self.en_passant_target
            and self.board.get_piece(end) is None
            and start[0] != end[0]
        ):
            test_game.board.remove_piece(f"{end[0]}{start[1]}")
        test_game.board.move_piece(start, end)
        if test_piece.name == "king" and abs(ord(end[0]) - ord(start[0])) == 2:
            rank = start[1]
            rook_from, rook_to = (
                ("h" + rank, "f" + rank) if end[0] == "g" else ("a" + rank, "d" + rank)
            )
            test_game.board.move_piece(rook_from, rook_to)
        return test_game.is_in_check(piece.color)

    def _piece_attacks_square(
        self, source_x: int, source_y: int, target_x: int, target_y: int, piece_name: str
    ) -> bool:
        dx, dy = target_x - source_x, target_y - source_y
        if dx == 0 and dy == 0:
            return False
        if piece_name == "rook" and not (dx == 0 or dy == 0):
            return False
        if piece_name == "bishop" and abs(dx) != abs(dy):
            return False
        if piece_name == "queen" and not (
            dx == 0 or dy == 0 or abs(dx) == abs(dy)
        ):
            return False
        step_x = (dx > 0) - (dx < 0)
        step_y = (dy > 0) - (dy < 0)
        x, y = source_x + step_x, source_y + step_y
        while (x, y) != (target_x, target_y):
            if self._at(x, y) is not None:
                return False
            x += step_x
            y += step_y
        return True

    def _add_if_available(self, moves: list[str], x: int, y: int, color: str) -> None:
        if not self._inside(x, y):
            return
        target = self._at(x, y)
        if target is None or (target.color != color and target.name != "king"):
            moves.append(self._position(x, y))

    def _at(self, x: int, y: int) -> PieceModel | None:
        return self.board.squares.get(self._position(x, y)) if self._inside(x, y) else None

    @staticmethod
    def _inside(x: int, y: int) -> bool:
        return 0 <= x < 8 and 0 <= y < 8

    @staticmethod
    def _coordinates(position: str) -> tuple[int, int]:
        return ord(position[0]) - ord("a"), int(position[1]) - 1

    @staticmethod
    def _position(x: int, y: int) -> str:
        return f"{chr(ord('a') + x)}{y + 1}"


def board_creation() -> Game:
    game = Game()
    back_rank = ("rook", "knight", "bishop", "queen", "king", "bishop", "knight", "rook")
    for file_index, piece_type in enumerate(back_rank):
        file_name = chr(ord("a") + file_index)
        game.add_piece("white", f"{file_name}1", piece_type)
        game.add_piece("black", f"{file_name}8", piece_type)
    for file_name in "abcdefgh":
        game.add_piece("white", f"{file_name}2", "pawn")
        game.add_piece("black", f"{file_name}7", "pawn")
    return game


def main() -> None:
    game = board_creation()
    while not game.is_game_over:
        game.display_board()
        print(f"{game.turn.capitalize()} to move")
        try:
            start = input("Move from (or 'q' to quit): ").strip()
            if start.lower() in ("q", "quit", "exit"):
                print("Game ended.")
                return
            end = input("Move to: ").strip()
            piece = game.board.get_piece(start)
            promotion = None
            if piece is not None and piece.name == "pawn" and end.lower()[-1:] in ("1", "8"):
                choice = input("Promote to queen, rook, bishop, or knight [queen]: ").strip()
                promotion = choice or "queen"
            result = game.play_move(start, end, promotion)
        except (EOFError, KeyboardInterrupt):
            print("\nGame ended.")
            return
        except ValueError as error:
            print(f"Invalid move: {error}")
            continue

        if result.checkmate:
            game.display_board()
            print(f"Checkmate. {result.color.capitalize()} wins.")
        elif result.stalemate:
            game.display_board()
            print("Stalemate. The game is a draw.")
        elif result.check:
            print("Check.")


if __name__ == "__main__":
    main()
