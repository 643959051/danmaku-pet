"""EdgeTTS synthesis and local MP3 playback."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from danmaku_pet.models import AudioClip
from danmaku_pet.shared.plans import estimate_speech_ms

logger = logging.getLogger(__name__)


def estimate_duration_ms(text: str) -> int:
    """Conservative fallback when synthesis or audio-device probing fails."""

    return estimate_speech_ms(text)


class EdgeTTSService:
    def __init__(
        self,
        cache_dir: Path,
        *,
        voice: str,
        rate: str = "+8%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        cache_enabled: bool = True,
    ) -> None:
        self.cache_dir = cache_dir
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.cache_enabled = cache_enabled
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_error: str | None = None

    def _cache_path(self, text: str) -> Path:
        seed = f"{text}\x00{self.voice}\x00{self.rate}\x00{self.volume}\x00{self.pitch}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.mp3"

    async def synthesize(self, text: str) -> AudioClip:
        text = (text or "").strip()
        if not text:
            return AudioClip(path=None, duration_ms=800, error="空文本", text=text)
        path = self._cache_path(text)
        if self.cache_enabled and path.is_file() and path.stat().st_size > 0:
            return AudioClip(path=path, duration_ms=0, cached=True, text=text)
        if not self.cache_enabled:
            path = self.cache_dir / f"tmp-{int(time.time() * 1000)}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part.mp3")
        try:
            import edge_tts

            communicate = edge_tts.Communicate(
                text,
                self.voice,
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch,
            )
            await communicate.save(str(temporary))
            if not temporary.is_file() or temporary.stat().st_size < 256:
                raise RuntimeError("EdgeTTS 生成的音频为空")
            os.replace(temporary, path)
            self.last_error = None
            return AudioClip(path=path, duration_ms=0, cached=False, text=text)
        except Exception as exc:  # network, cert, voice or device errors
            self.last_error = str(exc)
            logger.warning("EdgeTTS 合成失败，转为静音动作: %s", exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return AudioClip(
                path=None,
                duration_ms=estimate_duration_ms(text),
                cached=False,
                error=str(exc),
                text=text,
            )

    def status(self) -> dict[str, object]:
        return {
            "engine": "edge-tts",
            "voice": self.voice,
            "cache_dir": str(self.cache_dir),
            "last_error": self.last_error,
        }


class AudioPlayer:
    """Small pygame wrapper with lazy initialization.

    A missing audio device must never take down the text/visual loop, so every
    operation degrades to a measured/estimated duration.
    """

    def __init__(self) -> None:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        self._pygame = None
        self._mixer_ready = False
        self._sounds: dict[str, object] = {}
        self._channel = None
        self.last_error: str | None = None

    def _ensure_mixer(self) -> bool:
        if self._mixer_ready:
            return True
        if self._pygame is None:
            try:
                import pygame

                self._pygame = pygame
            except Exception as exc:
                self.last_error = f"pygame 导入失败: {exc}"
                logger.warning(self.last_error)
                return False
        try:
            self._pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            self._mixer_ready = True
            return True
        except Exception as exc:
            self.last_error = f"音频设备初始化失败: {exc}"
            logger.warning(self.last_error)
            return False

    def measure(self, path: Path | None) -> int | None:
        if path is None or not self._ensure_mixer():
            return None
        try:
            sound = self._load(path)
            return int(float(sound.get_length()) * 1000)  # type: ignore[attr-defined]
        except Exception as exc:
            self.last_error = f"音频时长读取失败: {exc}"
            logger.warning(self.last_error)
            return None

    def prepare(self) -> bool:
        """Initialize the mixer eagerly so the diagnostics page is truthful.

        Without this the first ``/api/status`` would report "音频设备未就绪"
        even on a perfectly working machine, simply because nothing has been
        played yet.
        """

        return self._ensure_mixer()

    def _load(self, path: Path):
        key = str(path)
        sound = self._sounds.get(key)
        if sound is None:
            assert self._pygame is not None
            sound = self._pygame.mixer.Sound(key)
            if len(self._sounds) >= 8:
                self._sounds.pop(next(iter(self._sounds)))
            self._sounds[key] = sound
        return sound

    def play(self, path: Path | None) -> bool:
        if path is None or not self._ensure_mixer():
            return False
        try:
            sound = self._load(path)
            self._channel = sound.play()
            return self._channel is not None
        except Exception as exc:
            self.last_error = f"音频播放失败: {exc}"
            logger.warning(self.last_error)
            return False

    def stop(self) -> None:
        if self._mixer_ready and self._pygame is not None:
            try:
                self._pygame.mixer.stop()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()
        self._sounds.clear()
        if self._mixer_ready and self._pygame is not None:
            try:
                self._pygame.mixer.quit()
            except Exception:
                pass
        self._mixer_ready = False

    def status(self) -> dict[str, object]:
        return {
            "engine": "pygame",
            "mixer_ready": self._mixer_ready,
            "last_error": self.last_error,
        }
