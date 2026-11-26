"""Framework-independent two-player chess clock.

Use ``ChessClock(initial_seconds, increment_seconds=0)`` for a shared time
control, or pass dictionaries keyed by ``"white"`` and ``"black"`` to let
each player have a separate lobby selection. Start with ``start(color)`` and
call ``complete_move(moving_color)`` after each completed move.
"""

import math
import time
from collections.abc import Mapping
from numbers import Real
from typing import Callable, Optional


WHITE = "white"
BLACK = "black"
_COLORS = (WHITE, BLACK)


class ChessClock:
    """Track remaining time, increments, and flag fall for two players.

    ``initial_seconds`` and ``increment_seconds`` may each be a non-negative
    numeric value (shared by both players; initial time must be positive), or
    a mapping containing separate ``white`` and ``black`` values. An injected
    monotonic time source is provided for deterministic testing.
    """

    def __init__(
        self,
        initial_seconds,
        increment_seconds=0,
        *,
        monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._initial = self._normalize_values(
            initial_seconds, "initial_seconds", allow_zero=False
        )
        self._increments = self._normalize_values(
            increment_seconds, "increment_seconds", allow_zero=True
        )
        self._remaining = dict(self._initial)
        self._monotonic = monotonic
        self._turn: Optional[str] = None
        self._running = False
        self._started = False
        self._game_over = False
        self._flagged_color: Optional[str] = None
        self._last_tick: Optional[float] = None

    @staticmethod
    def _normalize_color(color: str) -> str:
        if not isinstance(color, str) or color.lower() not in _COLORS:
            raise ValueError("color must be 'white' or 'black'")
        return color.lower()

    @classmethod
    def _normalize_values(cls, values, name: str, *, allow_zero: bool) -> dict:
        if isinstance(values, Mapping):
            normalized = {}
            for color in _COLORS:
                if color not in values:
                    raise ValueError("{} must include white and black".format(name))
                normalized[color] = cls._validate_seconds(
                    values[color], name, allow_zero=allow_zero
                )
            if len(values) != len(_COLORS):
                raise ValueError("{} must include only white and black".format(name))
            return normalized
        value = cls._validate_seconds(values, name, allow_zero=allow_zero)
        return {WHITE: value, BLACK: value}

    @staticmethod
    def _validate_seconds(value: Real, name: str, *, allow_zero: bool) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("{} must be a finite number of seconds".format(name))
        value = float(value)
        if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
            qualifier = "non-negative" if allow_zero else "greater than zero"
            raise ValueError("{} must be finite and {}".format(name, qualifier))
        return value

    @property
    def flagged_color(self) -> Optional[str]:
        """Color whose time expired, or ``None`` if neither has flagged."""
        self._update()
        return self._flagged_color

    @property
    def current_turn(self) -> Optional[str]:
        """Color currently on move, or ``None`` before the first start."""
        self._update()
        return self._turn

    @property
    def is_running(self) -> bool:
        """Whether the clock is currently counting down."""
        self._update()
        return self._running

    @property
    def is_game_over(self) -> bool:
        """Whether ``complete_move(..., game_over=True)`` ended the clock."""
        return self._game_over

    def start(self, color: str) -> None:
        """Start or resume the specified side's clock.

        The first call selects the starting side. After pausing, a subsequent
        call must name the same active side. A completed move changes the
        active side even while paused.
        """
        color = self._normalize_color(color)
        if self._game_over:
            raise RuntimeError("a game-over clock cannot be started")
        if self._flagged_color is not None:
            raise RuntimeError("a flagged clock cannot be started")
        if self._started and color != self._turn:
            raise ValueError("color must match the current side to move")
        if self._running:
            return
        if not self._started:
            self._turn = color
        self._last_tick = self._monotonic()
        self._running = True
        self._started = True

    def pause(self) -> None:
        """Pause the active clock without changing the side to move."""
        self._update()
        self._running = False
        self._last_tick = None

    def complete_move(self, moving_color: str, game_over: bool = False) -> None:
        """Award increment for a completed move and hand off the turn.

        ``moving_color`` must be the current side. If ``game_over`` is true,
        the mover still receives the increment, then the clock pauses and
        cannot be restarted. May be called while paused after the first start.
        """
        moving_color = self._normalize_color(moving_color)
        self._update()
        if not self._started:
            raise RuntimeError("the clock must be started before completing a move")
        if self._game_over or self._flagged_color is not None:
            raise RuntimeError("cannot complete a move after game over or flag fall")
        if moving_color != self._turn:
            raise ValueError("moving_color must match the current side to move")

        self._remaining[moving_color] += self._increments[moving_color]
        if game_over:
            self._running = False
            self._last_tick = None
            self._game_over = True
            return

        self._turn = BLACK if moving_color == WHITE else WHITE
        if self._running:
            self._last_tick = self._monotonic()

    def remaining(self, color: str) -> float:
        """Return a player's remaining time in seconds, clamped at zero."""
        color = self._normalize_color(color)
        self._update()
        return self._remaining[color]

    def _update(self) -> None:
        if not self._running or self._last_tick is None:
            return
        now = self._monotonic()
        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        self._remaining[self._turn] -= elapsed
        if self._remaining[self._turn] <= 0:
            self._remaining[self._turn] = 0.0
            self._flagged_color = self._turn
            self._running = False
            self._last_tick = None


def _self_check() -> None:
    """Focused deterministic tests; run with ``python clocks.py``."""

    class FakeMonotonic:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    timer = FakeMonotonic()
    clock = ChessClock(
        {"white": 10, "black": 5},
        {"white": 2, "black": 1},
        monotonic=timer,
    )
    clock.start("white")
    timer.now += 3
    assert clock.remaining("white") == 7
    clock.complete_move("white")
    assert clock.remaining("white") == 9
    assert clock.current_turn == "black"
    timer.now += 2
    clock.pause()
    assert clock.remaining("black") == 3
    timer.now += 20
    assert clock.remaining("black") == 3
    clock.start("black")
    timer.now += 3
    assert clock.flagged_color == "black"
    assert not clock.is_running

    timer.now += 10
    finished = ChessClock(10, 2, monotonic=timer)
    finished.start("white")
    timer.now += 1
    finished.complete_move("white", game_over=True)
    stopped_at = finished.remaining("black")
    timer.now += 30
    assert finished.remaining("black") == stopped_at
    assert finished.is_game_over

    for bad_initial in (0, -1, float("inf"), float("nan"), True):
        try:
            ChessClock(bad_initial)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid initial time was accepted")
    for bad_increment in (-1, float("inf"), float("nan"), True):
        try:
            ChessClock(10, bad_increment)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid increment was accepted")

    print("ChessClock self-check passed")


if __name__ == "__main__":
    _self_check()
