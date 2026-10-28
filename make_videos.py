"""Render small, deterministic chess-broadcast videos into data/ (no downloads needed).

Each game is one MP4 at 25 fps, 320x256: board-cam shots (the board updates every 0.5 s, one move at a
time), a player cam, the clock graphic, and a 1-second result card as the very last shot. data/truth.json
records the exact shot list, frame count and final board of every game, so the pipeline can check that
scene detection and frame extraction keep the last scene and the last frame.

    python make_videos.py
"""

from __future__ import annotations

import json
import random
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
DATA = HERE / 'data'
FPS = 25
W, H = 320, 256
SQ = 32
LIGHT, DARK = (240, 217, 181), (181, 136, 99)
WHITE_PIECE, BLACK_PIECE = (245, 245, 235), (25, 25, 25)

GAMES = [
    # game_id, round, white, black, result, shot plan [(kind, seconds)]
    ('r1-b1', 1, 'Okafor', 'Lindqvist', '1-0',
     [('board', 4.0), ('player', 1.5), ('board', 3.0), ('clock', 1.5), ('board', 2.5), ('result', 1.0)]),
    ('r1-b2', 1, 'Haddad', 'Moreau', '1/2-1/2',
     [('board', 3.0), ('clock', 1.5), ('board', 3.5), ('player', 2.0), ('board', 2.0), ('result', 1.0)]),
    ('r2-b1', 2, 'Lindqvist', 'Haddad', '0-1',
     [('player', 1.5), ('board', 5.0), ('clock', 1.5), ('board', 3.0), ('result', 1.0)]),
    ('r2-b2', 2, 'Moreau', 'Okafor', '1-0',
     [('board', 2.5), ('player', 1.5), ('board', 2.5), ('clock', 1.5), ('board', 3.0), ('player', 1.5), ('result', 1.0)]),
]


def start_board() -> list[list[str]]:
    b = [['.'] * 8 for _ in range(8)]
    for c in range(8):
        b[0][c] = b[1][c] = 'p'
        b[6][c] = b[7][c] = 'P'
    return b


def fen(b: list[list[str]]) -> str:
    return '/'.join(''.join(r) for r in b)


def move(b: list[list[str]], side: str, rng: random.Random) -> None:
    pieces = [(r, c) for r in range(8) for c in range(8) if b[r][c] == side]
    empties = [(r, c) for r in range(2, 6) for c in range(8) if b[r][c] == '.']
    (r0, c0), (r1, c1) = rng.choice(pieces), rng.choice(empties)
    b[r1][c1], b[r0][c0] = side, '.'


def draw_board(b, game_id: str, ply: int) -> Image.Image:
    img = Image.new('RGB', (W, H), (90, 90, 90))
    d = ImageDraw.Draw(img)
    for r in range(8):
        for c in range(8):
            x, y = c * SQ, r * SQ
            d.rectangle([x, y, x + SQ - 1, y + SQ - 1], fill=LIGHT if (r + c) % 2 == 0 else DARK)
            if b[r][c] != '.':
                d.ellipse([x + 6, y + 6, x + SQ - 7, y + SQ - 7], fill=WHITE_PIECE if b[r][c] == 'P' else BLACK_PIECE)
    d.text((262, 10), game_id, fill=(230, 230, 230))
    d.text((262, 30), f'ply {ply}', fill=(230, 230, 230))
    return img


def draw_card(kind: str, g) -> Image.Image:
    game_id, rnd, white, black, result, _ = g
    if kind == 'player':
        img = Image.new('RGB', (W, H), (40, 60, 200))
        ImageDraw.Draw(img).text((20, 110), f'{white} vs {black}', fill=(255, 255, 255))
    elif kind == 'clock':
        img = Image.new('RGB', (W, H), (12, 12, 18))
        ImageDraw.Draw(img).text((110, 110), '01:23:45  00:58:10', fill=(120, 120, 120))
    else:  # result
        img = Image.new('RGB', (W, H), (238, 238, 238))
        ImageDraw.Draw(img).text((40, 110), f'Round {rnd}  {white} {result} {black}', fill=(10, 10, 10))
    return img


def render(g, rng: random.Random) -> dict:
    game_id, rnd, white, black, result, plan = g
    path = DATA / f'{game_id}.mp4'
    out = av.open(str(path), 'w')
    st = out.add_stream('libx264', rate=FPS)
    st.width, st.height, st.pix_fmt = W, H, 'yuv420p'
    st.time_base = Fraction(1, FPS)
    b, ply, n, shots = start_board(), 0, 0, []
    for kind, secs in plan:
        frames = int(round(secs * FPS))
        shots.append({'kind': kind, 'start_time': round(n / FPS, 3), 'duration': round(frames / FPS, 3), 'frames': frames})
        for i in range(frames):
            if kind == 'board':
                if i > 0 and i % (FPS // 2) == 0 and frames - i > 6:  # one move every 0.5 s, none in the last few frames
                    move(b, 'P' if ply % 2 == 0 else 'p', rng)
                    ply += 1
                img = draw_board(b, game_id, ply)
            else:
                img = draw_card(kind, g)
            frame = av.VideoFrame.from_ndarray(np.asarray(img), format='rgb24')
            frame.pts = n
            for pkt in st.encode(frame):
                out.mux(pkt)
            n += 1
    for pkt in st.encode():
        out.mux(pkt)
    out.close()
    return {'game_id': game_id, 'path': str(path.relative_to(HERE)), 'fps': FPS, 'frames': n,
            'duration': round(n / FPS, 3), 'shots': shots, 'final_fen': fen(b), 'plies': ply}


def main() -> None:
    DATA.mkdir(exist_ok=True)
    rng = random.Random(20261028)
    truth = [render(g, rng) for g in GAMES]
    (DATA / 'truth.json').write_text(json.dumps(truth, indent=1))
    for x in truth:
        print(f"{x['game_id']}: {x['frames']} frames, {len(x['shots'])} shots, {x['plies']} plies -> {x['path']}")


if __name__ == '__main__':
    main()
