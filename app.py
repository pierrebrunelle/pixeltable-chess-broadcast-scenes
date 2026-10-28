"""Chess tournament broadcast pipeline built with Pixeltable.

Insert a round's board-cam recordings and Pixeltable does the rest as computed columns and views:
container + stream metadata, scene detection (content and histogram detectors), the cut list, frame
sampling with a shot classifier and a board reader, keyframes, and per-shot clips split at the detected
cuts. The final position of every game is read from its last board frame, and the result card (the
last, 1-second shot) has to survive scene detection and frame extraction.

Layout: UDFs live in udfs.py; this file holds table models and @pxt.query functions. pipeline.py drives it.

    pxt schema update app.py chess
    python make_videos.py
    python pipeline.py ingest
    python pipeline.py report
"""

from __future__ import annotations

import pixeltable as pxt
import pixeltable.functions as pxtf

from udfs import board_fen, piece_count, scene_count, scene_coverage, scene_cuts, shot_kind

TableModel = pxt.model_base()


class Games(TableModel, name='games'):
    """One board's broadcast recording for one round."""

    game_id = pxt.Column(type=pxt.String, primary_key=True)
    round_no: pxt.Int
    white: pxt.String
    black: pxt.String
    result: pxt.String
    video: pxt.Video

    meta = pxtf.video.get_metadata(video)
    duration = pxtf.video.get_duration(video)
    stream_frames = meta.streams[0].frames.astype(pxt.Int)
    fps = meta.streams[0].average_rate.astype(pxt.Float)
    scenes = pxtf.video.scene_detect_content(video, threshold=27.0, min_scene_len=10)
    scenes_hist = pxtf.video.scene_detect_histogram(video, min_scene_len=10)
    n_scenes = scene_count(scenes)
    n_scenes_hist = scene_count(scenes_hist)
    coverage = scene_coverage(scenes, duration)
    cuts = scene_cuts(scenes)

    __indexes__ = [pxt.BtreeIndex(round_no)]


class Frames(
    TableModel,
    name='frames',
    base=Games,
    # 5 frames per second of broadcast; frame_attrs carries index/pts/time
    iterator=pxtf.video.frame_iterator(Games.video, fps=5.0),
):
    """Component view: sampled frames, classified by shot and read back into a board position."""

    t = frame_attrs.time.astype(pxt.Float)  # type: ignore[name-defined]  # noqa: F821
    kind = shot_kind(frame)  # type: ignore[name-defined]  # noqa: F821
    fen = board_fen(frame, kind)  # type: ignore[name-defined]  # noqa: F821
    pieces = piece_count(fen)
    img_meta = pxtf.image.get_metadata(frame)  # type: ignore[name-defined]  # noqa: F821


class Keyframes(
    TableModel,
    name='keyframes',
    base=Games,
    iterator=pxtf.video.frame_iterator(Games.video, keyframes_only=True),
):
    """Component view: the encoder's keyframes only (cheap thumbnails at every hard cut)."""

    t = frame_attrs.time.astype(pxt.Float)  # type: ignore[name-defined]  # noqa: F821
    kind = shot_kind(frame)  # type: ignore[name-defined]  # noqa: F821


class Clips(
    TableModel,
    name='clips',
    base=Games,
    # fixed 4-second highlight clips; the last, shorter clip is kept (min_segment_duration=0.5)
    iterator=pxtf.video.video_splitter(Games.video, duration=4.0, min_segment_duration=0.5),
):
    """Component view: fixed-length clips for highlight reels."""

    clip_seconds = pxtf.video.get_duration(video_segment)  # type: ignore[name-defined]  # noqa: F821


@pxt.query
def game_scenes(game_id: str):
    """Detected scenes and the cut list of one game."""
    return Games.where(Games.game_id == game_id).select(
        Games.game_id, Games.n_scenes, Games.scenes, Games.cuts, Games.coverage
    )


@pxt.query
def final_position(game_id: str):
    """The last board-cam frame of a game: its time and board occupancy."""
    return (
        Frames.where((Frames.game_id == game_id) & (Frames.kind == 'board'))
        .order_by(Frames.t, asc=False)
        .limit(1)
        .select(Frames.game_id, Frames.t, Frames.fen, Frames.pieces)
    )


@pxt.query
def round_summary(round_no: int):
    """Scene counts, coverage and result for every board of a round."""
    return (
        Games.where(Games.round_no == round_no)
        .order_by(Games.game_id)
        .select(Games.game_id, Games.white, Games.black, Games.result, Games.duration, Games.n_scenes, Games.coverage)
    )


@pxt.query
def shots_of(game_id: str):
    """Sampled frames of one game with their shot kind."""
    return Frames.where(Frames.game_id == game_id).order_by(Frames.t).select(Frames.t, Frames.kind, Frames.pieces)
