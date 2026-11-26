from dataclasses import dataclass


@dataclass
class PieceModel:
    name: str
    symbol: str
    color: str
    position: str | None = None


_SYMBOLS = {
    "pawn": ("♙", "♟"),
    "rook": ("♖", "♜"),
    "knight": ("♘", "♞"),
    "bishop": ("♗", "♝"),
    "queen": ("♕", "♛"),
    "king": ("♔", "♚"),
}


def _piece(name: str, color: str = "white", position: str | None = None) -> PieceModel:
    color = color.lower()
    if color not in ("white", "black"):
        raise ValueError("color must be 'white' or 'black'")

    symbol = _SYMBOLS[name][0 if color == "white" else 1]
    return PieceModel(name=name, symbol=symbol, color=color, position=position)


def pawn(color: str = "white", position: str | None = None) -> PieceModel:
    return _piece("pawn", color, position)


def rook(color: str = "white", position: str | None = None) -> PieceModel:
    return _piece("rook", color, position)


def knight(color: str = "white", position: str | None = None) -> PieceModel:
    return _piece("knight", color, position)


def bishop(color: str = "white", position: str | None = None) -> PieceModel:
    return _piece("bishop", color, position)


def queen(color: str = "white", position: str | None = None) -> PieceModel:
    return _piece("queen", color, position)


def king(color: str = "white", position: str | None = None) -> PieceModel:
    return _piece("king", color, position)
