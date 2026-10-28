"""Chess broadcast pipeline driver (no server).

    python make_videos.py                 # render data/*.mp4 + data/truth.json
    python pipeline.py ingest             # insert the recordings; scenes, frames, keyframes and clips compute
    python pipeline.py report             # per-round summary, cut lists, final positions
    python pipeline.py verify             # compare against data/truth.json: last scene and last frame kept?
    python pipeline.py export             # frames (kind, board, time) -> output/frames.csv

Every command takes --target (default 'chess'); use pxt://<org>:<db> for a cloud database
(`pxt schema update app.py pxt://<org>:<db>` binds the tables at the database root).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pixeltable as pxt

HERE = Path(__file__).parent
TRUTH = HERE / 'data' / 'truth.json'
META = {
    'r1-b1': (1, 'Okafor', 'Lindqvist', '1-0'),
    'r1-b2': (1, 'Haddad', 'Moreau', '1/2-1/2'),
    'r2-b1': (2, 'Lindqvist', 'Haddad', '0-1'),
    'r2-b2': (2, 'Moreau', 'Okafor', '1-0'),
}


def tbl(target: str, name: str) -> pxt.Table:
    sep = '/' if not target.endswith('/') else ''
    return pxt.get_table(f'{target}{sep}{name}')


def ingest(a) -> None:
    games = tbl(a.target, 'games')
    have = {r['game_id'] for r in games.select(games.game_id).collect()}
    rows = []
    for game_id, (rnd, white, black, result) in META.items():
        if game_id in have:
            continue
        rows.append({'game_id': game_id, 'round_no': rnd, 'white': white, 'black': black, 'result': result,
                     'video': str(HERE / 'data' / f'{game_id}.mp4')})
    t0 = time.time()
    if rows:
        status = games.insert(rows, on_error='ignore')
        print(f'inserted {status.num_rows} games, {status.num_excs} errors in {time.time() - t0:.1f}s')
    for v in ('games', 'frames', 'keyframes', 'clips'):
        print(f'{v}: {tbl(a.target, v).count()} rows')


def report(a) -> None:
    games = tbl(a.target, 'games')
    frames = tbl(a.target, 'frames')
    for rnd in (1, 2):
        print(f'== round {rnd}')
        q = games.where(games.round_no == rnd).order_by(games.game_id)
        for r in q.select(games.game_id, games.white, games.black, games.result, games.duration,
                          games.n_scenes, games.n_scenes_hist, games.coverage, games.cuts).collect():
            print(f"{r['game_id']}: {r['white']} {r['result']} {r['black']} | {r['duration']:.2f}s, "
                  f"{r['n_scenes']} scenes (hist {r['n_scenes_hist']}), coverage {r['coverage']}, cuts {r['cuts']}")
    print('== final positions (last board frame)')
    for gid in META:
        r = (frames.where((frames.game_id == gid) & (frames.kind == 'board')).order_by(frames.t, asc=False)
             .limit(1).select(frames.t, frames.fen, frames.pieces).collect())
        if len(r):
            print(f"{gid}: t={r[0]['t']:.2f}s pieces={r[0]['pieces']} {r[0]['fen']}")
    kinds = frames.group_by(frames.kind).select(frames.kind, n=pxt.functions.count(frames.pos)).collect()
    print('frames by shot kind:', {r['kind']: r['n'] for r in kinds})


def verify(a) -> None:
    truth = {x['game_id']: x for x in json.loads(TRUTH.read_text())}
    games, frames = tbl(a.target, 'games'), tbl(a.target, 'frames')
    keyframes, clips = tbl(a.target, 'keyframes'), tbl(a.target, 'clips')
    ok = True
    for r in games.order_by(games.game_id).select(
            games.game_id, games.duration, games.stream_frames, games.scenes, games.coverage).collect():
        tr = truth[r['game_id']]
        last = r['scenes'][-1] if r['scenes'] else None
        exp_last = tr['shots'][-1]
        nf = frames.where(frames.game_id == r['game_id']).count()
        last_kind = (frames.where(frames.game_id == r['game_id']).order_by(frames.t, asc=False).limit(1)
                     .select(frames.kind).collect()[0]['kind'])
        fen = (frames.where((frames.game_id == r['game_id']) & (frames.kind == 'board')).order_by(frames.t, asc=False)
               .limit(1).select(frames.fen).collect()[0]['fen'])
        kf = keyframes.where(keyframes.game_id == r['game_id']).count()
        cl = clips.where(clips.game_id == r['game_id']).select(clips.clip_seconds).collect()
        checks = {
            'scenes == shots': len(r['scenes']) == len(tr['shots']),
            'last scene starts at result card': last is not None and abs(last['start_time'] - exp_last['start_time']) < 0.05,
            'coverage == 1.0': r['coverage'] is not None and abs(r['coverage'] - 1.0) < 0.01,
            'stream frames == rendered': r['stream_frames'] == tr['frames'],
            'last sampled frame is result card': last_kind == 'result',
            'final board matches': fen == tr['final_fen'],
            'clips cover video': abs(sum(c['clip_seconds'] or 0 for c in cl) - tr['duration']) < 0.2,
        }
        bad = [k for k, v in checks.items() if not v]
        ok &= not bad
        print(f"{r['game_id']}: scenes {len(r['scenes'])}/{len(tr['shots'])}, sampled frames {nf}, "
              f"keyframes {kf}, clips {len(cl)} -> {'OK' if not bad else 'FAIL ' + ', '.join(bad)}")
    print('ALL OK' if ok else 'SOME CHECKS FAILED')


def export(a) -> None:
    frames = tbl(a.target, 'frames')
    out = HERE / 'output'
    out.mkdir(exist_ok=True)
    q = frames.order_by(frames.game_id, frames.t).select(frames.game_id, frames.t, frames.kind, frames.fen, frames.pieces)
    pxt.io.export_csv(q, out / 'frames.csv')
    print(f'wrote {out / "frames.csv"}')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['ingest', 'report', 'verify', 'export'])
    p.add_argument('--target', default='chess', help='catalog dir, or pxt://<org>:<db>')
    a = p.parse_args()
    {'ingest': ingest, 'report': report, 'verify': verify, 'export': export}[a.cmd](a)


if __name__ == '__main__':
    main()
