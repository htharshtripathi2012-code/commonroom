from piece import PieceModel


class Board:
    def __init__(self):
        self.squares: dict[str, PieceModel] = {}

    def update_piece(self, position: str, piece: PieceModel | None) -> None:
        position = self._normalize_position(position)
        previous_piece = self.squares.get(position)
        if previous_piece is not None:
            previous_piece.position = None

        if piece is None:
            self.squares.pop(position, None)
            return

        if piece.position is not None:
            old_position = self._normalize_position(piece.position)
            if self.squares.get(old_position) is piece:
                del self.squares[old_position]

        self.squares[position] = piece
        piece.position = position

    def move_piece(self, from_position: str, to_position: str) -> None:
        from_position = self._normalize_position(from_position)
        to_position = self._normalize_position(to_position)
        piece = self.squares.get(from_position)
        if piece is None:
            raise ValueError(f"No piece at {from_position}")

        self.update_piece(to_position, piece)
        if from_position != to_position:
            self.squares.pop(from_position, None)

    def get_piece(self, position: str) -> PieceModel | None:
        return self.squares.get(self._normalize_position(position))

    def remove_piece(self, position: str) -> PieceModel | None:
        position = self._normalize_position(position)
        piece = self.squares.pop(position, None)
        if piece is not None:
            piece.position = None
        return piece

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
