"""UDFs for the chess broadcast pipeline (pure Python, no table imports).

The broadcast renders four kinds of shot: the overhead board cam, a player cam, the clock graphic and a
result card. Frames are classified from simple pixel statistics, and board frames are read back into a
FEN-like occupancy string, so the final position of a game comes straight from its last board frame.
"""

from __future__ import annotations

import PIL.Image

import pixeltable as pxt

BOARD_PX = 256  # the board occupies the top-left 256x256 of every board-cam frame (8 squares of 32 px)
SQUARE_PX = BOARD_PX // 8
WHITE_PIECE = (245, 245, 235)
BLACK_PIECE = (25, 25, 25)


def _mean(img: PIL.Image.Image) -> tuple[float, float, float]:
    small = img.convert('RGB').resize((16, 16))
    px = list(small.getdata())
    n = len(px)
    return (sum(p[0] for p in px) / n, sum(p[1] for p in px) / n, sum(p[2] for p in px) / n)


@pxt.udf
def shot_kind(frame: PIL.Image.Image) -> str:
    """'board' | 'player' | 'clock' | 'result' from the frame's mean colour."""
    r, g, b = _mean(frame)
    if b > r + 40 and b > g + 20:
        return 'player'
    if r + g + b < 120:
        return 'clock'
    if r > 200 and g > 200 and b > 200:
        return 'result'
    return 'board'


@pxt.udf
def board_fen(frame: PIL.Image.Image, kind: str) -> str | None:
    """Occupancy of the board as 8 ranks separated by '/': 'P' white piece, 'p' black piece, '.' empty."""
    if kind != 'board':
        return None
    img = frame.convert('RGB')
    ranks = []
    for row in range(8):
        rank = ''
        for col in range(8):
            cx, cy = col * SQUARE_PX + SQUARE_PX // 2, row * SQUARE_PX + SQUARE_PX // 2
            p = img.getpixel((cx, cy))
            if all(abs(p[i] - WHITE_PIECE[i]) < 30 for i in range(3)):
                rank += 'P'
            elif all(abs(p[i] - BLACK_PIECE[i]) < 30 for i in range(3)):
                rank += 'p'
            else:
                rank += '.'
        ranks.append(rank)
    return '/'.join(ranks)


@pxt.udf
def scene_count(scenes: pxt.Json) -> int:
    """Number of detected scenes."""
    return len(scenes or [])


@pxt.udf
def scene_coverage(scenes: pxt.Json, duration: float | None) -> float | None:
    """Fraction of the video covered by the detected scenes (1.0 = nothing dropped at the end)."""
    if not scenes or not duration:
        return None
    last = scenes[-1]
    return round((last['start_time'] + last['duration']) / duration, 3)


@pxt.udf
def scene_cuts(scenes: pxt.Json) -> list[float]:
    """Start times of every scene after the first: the cut points to split the broadcast into shots."""
    return [round(float(s['start_time']), 3) for s in (scenes or [])[1:]]


@pxt.udf
def piece_count(fen: str | None) -> int | None:
    """Pieces on the board in a board_fen string."""
    if fen is None:
        return None
    return sum(1 for ch in fen if ch in 'Pp')
