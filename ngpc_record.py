"""Video + sound recording to MP4, in emulated frames; encoding never touches the core.

The GIF button answers "show this in a gallery": a few seconds, no sound, held in
memory. This one answers "show the game": minutes, with its music, streamed to disk as
it plays -- raw frames and a WAV beside them -- and muxed by ffmpeg at the end.

⚡ SYNC IS OWNED HERE, NOT BY THE CALLER. The shell drains the core's audio once per
tick, AFTER the tick's frames ran, and also drains it (and throws it away) while the
player scrubs rewind. Writing audio as it arrives would let those two habits pull the
sound away from the picture. So the WAV is only ever allowed to be as long as the
frames already recorded -- at the CONSOLE's rate, 6.144 MHz / (515 x 199) -- extra sound
waits in a short buffer, and whatever is missing at the end is padded with silence.
Fast-forward and slow motion therefore record at real speed, sound included.

Without ffmpeg the sound is still kept: the WAV is the one file that needs nothing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

FPS = 6144000.0 / (515.0 * 199.0)       # 59.9227 -- the console's, not 60
SAMPLE_RATE = 44100
BYTES_PER_SAMPLE = 4                     # stereo, signed 16-bit
WIDTH, HEIGHT = 160, 152
SCALE = 4                                # 640 x 608: nearest-neighbour, no blur
MAX_FRAMES = int(FPS * 60 * 10)          # ten minutes, ~2.6 GB of raw frames at most
_SLACK = SAMPLE_RATE // 2                # audio allowed to run ahead, in samples


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


class VideoRecording:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._dir = Path(tempfile.mkdtemp(prefix=".rec_", dir=self.path.parent))
        self._raw = open(self._dir / "frames.rgb", "wb")
        self._wav = wave.open(str(self._dir / "sound.wav"), "wb")
        self._wav.setnchannels(2)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)
        self.frames = 0
        self._written = 0                # audio samples in the WAV
        self._pending = bytearray()
        self.closed = False

    def _target(self) -> int:
        return round(self.frames * SAMPLE_RATE / FPS)

    def feed_frame(self, framebuffer) -> bool:
        """One emulated frame. Returns True once the length limit is reached."""
        if self.closed or self.frames >= MAX_FRAMES:
            return True
        a = np.asarray(framebuffer(), dtype=np.uint16).reshape(HEIGHT, WIDTH)
        rgb = np.stack(((a & 15) * 17, ((a >> 4) & 15) * 17, ((a >> 8) & 15) * 17),
                       axis=-1).astype(np.uint8)
        self._raw.write(rgb.tobytes())
        self.frames += 1
        self._flush_audio()
        return self.frames >= MAX_FRAMES

    def feed_audio(self, pcm: bytes) -> None:
        if self.closed or not pcm:
            return
        self._pending += pcm
        self._flush_audio()

    def _flush_audio(self) -> None:
        room = self._target() - self._written
        if room > 0 and self._pending:
            take = min(room, len(self._pending) // BYTES_PER_SAMPLE) * BYTES_PER_SAMPLE
            self._wav.writeframes(bytes(self._pending[:take]))
            del self._pending[:take]
            self._written += take // BYTES_PER_SAMPLE
        # Sound with no picture to go with it (rewind scrubbing): keep only a little.
        excess = len(self._pending) - _SLACK * BYTES_PER_SAMPLE
        if excess > 0:
            del self._pending[:excess - excess % BYTES_PER_SAMPLE]

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._flush_audio()
        missing = self._target() - self._written
        if missing > 0:
            self._wav.writeframes(bytes(missing * BYTES_PER_SAMPLE))
            self._written += missing
        self._wav.close()
        self._raw.close()

    def export(self, ffmpeg: str | None) -> list[Path]:
        """Close, then write the MP4 (or, with no ffmpeg, the WAV alone). Returns the
        files produced; the scratch folder is removed either way."""
        self.close()
        try:
            if self.frames == 0:
                return []
            if ffmpeg is None:
                wav = self.path.with_suffix(".wav")
                os.replace(self._dir / "sound.wav", wav)
                return [wav]
            tmp = self.path.with_name(self.path.stem + ".part.mp4")
            cmd = [ffmpeg, "-y", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
                   "-r", f"{FPS:.6f}", "-i", str(self._dir / "frames.rgb"),
                   "-i", str(self._dir / "sound.wav"),
                   "-vf", f"scale={WIDTH * SCALE}:{HEIGHT * SCALE}:flags=neighbor",
                   # 4x4 blocks and 4:2:0 chroma line up exactly: no colour bleed.
                   "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                   "-movflags", "+faststart", str(tmp)]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            done = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            if done.returncode != 0:
                if tmp.exists():
                    tmp.unlink()
                raise RuntimeError(done.stderr.strip().splitlines()[-1]
                                   if done.stderr.strip() else f"ffmpeg exit {done.returncode}")
            os.replace(tmp, self.path)
            return [self.path]
        finally:
            shutil.rmtree(self._dir, ignore_errors=True)

    def discard(self) -> None:
        self.close()
        shutil.rmtree(self._dir, ignore_errors=True)


def install_button(page, layout, repo):
    """The REC button: click to start, click to stop. Lazy Qt imports, like ngpc_gif."""
    from PyQt6.QtCore import QThread, Qt, pyqtSignal
    from PyQt6.QtWidgets import QPushButton
    import ngpc_settings as cfg

    def t(key):
        return cfg.tr(cfg.language(page._settings), key)

    class Encoder(QThread):
        result = pyqtSignal(str)

        def __init__(self, recording):
            super().__init__(page)
            self.recording = recording

        def run(self):
            try:
                ffmpeg = find_ffmpeg()
                files = self.recording.export(ffmpeg)
                if not files:
                    self.result.emit(t("rec_empty"))
                elif ffmpeg is None:
                    self.result.emit(t("rec_no_ffmpeg").format(name=files[0].name))
                else:
                    self.result.emit(t("rec_saved").format(name=files[0].name))
            except Exception as exc:
                self.result.emit(t("rec_failed") + str(exc))

    button = QPushButton("⏺")
    button.setObjectName("barBtn")
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setToolTip(t("rec_tooltip"))
    layout.addWidget(button)
    page._rec_btn = button
    page._rec = None
    page._rec_encoder = None

    def finish(wait=False):
        rec, page._rec = page._rec, None
        button.setText("⏺")
        if rec is not None:
            worker = Encoder(rec)
            page._rec_encoder = worker
            button.setEnabled(False)
            worker.result.connect(lambda msg: page._flash(msg, 4000))
            worker.finished.connect(lambda: button.setEnabled(True))
            worker.start()
        if wait and page._rec_encoder is not None:
            page._rec_encoder.wait()

    def feed_frame():
        rec = page._rec
        if rec is None or page.machine is None:
            return
        try:
            full = rec.feed_frame(page.machine.framebuffer)
            if rec.frames % 30 == 0:
                button.setText(f"■ {int(rec.frames / FPS)} s")
            if full:
                finish()
        except Exception as exc:
            page._rec = None
            rec.discard()
            button.setText("⏺")
            page._flash(t("rec_failed") + str(exc), 4000)

    def feed_audio(pcm):
        if page._rec is not None:
            page._rec.feed_audio(pcm)

    def toggle():
        if page._rec is not None:
            finish()
            return
        if page.machine is None:
            return
        from datetime import datetime
        folder = Path(cfg.screenshot_dir(page._settings) or repo / "screenshots")
        stem = page._rom_path.stem if page._rom_path else "ngpc"
        page._rec = VideoRecording(folder / f"{stem}_{datetime.now():%Y%m%d_%H%M%S}.mp4")
        button.setText("■ 0 s")
        page.setFocus()

    button.clicked.connect(toggle)
    page._rec_finish = finish
    page._rec_feed = feed_frame
    page._rec_audio = feed_audio
