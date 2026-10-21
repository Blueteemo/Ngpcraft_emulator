"""Video + sound recording (ngpc_record): the sound must stay on the picture.

The shell drains audio AFTER a tick's frames and throws audio away while rewind is
scrubbed; the recorder, not the caller, is what keeps the WAV exactly as long as the
frames recorded. These tests feed it the shell's habits and measure the result.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

import ngpc_record as rec

PER_FRAME = rec.SAMPLE_RATE / rec.FPS          # ~735.95 samples


def _fb():
    return [0x0F0] * (rec.WIDTH * rec.HEIGHT)


def _pcm(samples: int, value: int = 1000) -> bytes:
    return (value.to_bytes(2, "little", signed=True) * 2) * samples


def _wav_samples(path: Path) -> int:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 44100)
        return w.getnframes()


def _export_wav(r: rec.VideoRecording) -> Path:
    files = r.export(None)
    assert len(files) == 1 and files[0].suffix == ".wav"
    return files[0]


def test_audio_after_the_frames_lands_in_full(tmp_path: Path):
    """The shell's order: N frames, THEN their audio in one drain."""
    r = rec.VideoRecording(tmp_path / "a.mp4")
    for _tick in range(10):
        for _ in range(3):
            r.feed_frame(_fb)
        r.feed_audio(_pcm(round(3 * PER_FRAME)))
    assert _wav_samples(_export_wav(r)) == round(30 * PER_FRAME)


def test_audio_with_no_picture_does_not_pull_the_sound_ahead(tmp_path: Path):
    """Rewind scrubbing drains audio and records no frame: that sound must not end up
    in front of the picture."""
    r = rec.VideoRecording(tmp_path / "b.mp4")
    r.feed_frame(_fb)
    r.feed_audio(_pcm(round(PER_FRAME)))
    r.feed_audio(_pcm(rec.SAMPLE_RATE * 5, value=-7))       # 5 s of scrub audio
    for _ in range(59):
        r.feed_frame(_fb)
    wav = _export_wav(r)
    assert _wav_samples(wav) == round(60 * PER_FRAME)
    with wave.open(str(wav)) as w:
        head = w.readframes(round(PER_FRAME))
    assert head == _pcm(round(PER_FRAME))[:len(head)], "the first frame keeps ITS sound"


def test_missing_audio_is_padded_with_silence(tmp_path: Path):
    r = rec.VideoRecording(tmp_path / "c.mp4")
    for _ in range(120):
        r.feed_frame(_fb)
    assert _wav_samples(_export_wav(r)) == round(120 * PER_FRAME)


def test_nothing_recorded_leaves_no_file_and_no_scratch(tmp_path: Path):
    r = rec.VideoRecording(tmp_path / "d.mp4")
    assert r.export(None) == []
    assert list(tmp_path.iterdir()) == []


def test_length_limit_stops_the_recording(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rec, "MAX_FRAMES", 5)
    r = rec.VideoRecording(tmp_path / "e.mp4")
    assert [r.feed_frame(_fb) for _ in range(6)] == [False] * 4 + [True, True]
    assert r.frames == 5
    r.discard()


@pytest.mark.skipif(rec.find_ffmpeg() is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not installed")
def test_mp4_has_the_picture_and_the_sound_at_the_same_length(tmp_path: Path):
    r = rec.VideoRecording(tmp_path / "f.mp4")
    for _ in range(180):                                     # ~3 s
        r.feed_frame(_fb)
        r.feed_audio(_pcm(round(PER_FRAME)))
    files = r.export(rec.find_ffmpeg())
    assert files == [tmp_path / "f.mp4"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.mp4"]   # no scratch left
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(files[0])],
        capture_output=True, text=True, check=True).stdout)["streams"]
    video = next(s for s in probe if s["codec_type"] == "video")
    audio = next(s for s in probe if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (640, 608)
    assert int(video["nb_frames"]) == 180
    assert abs(float(video["duration"]) - 180 / rec.FPS) < 0.02
    assert abs(float(audio["duration"]) - 180 / rec.FPS) < 0.05
