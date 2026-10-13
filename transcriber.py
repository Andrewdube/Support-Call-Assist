"""Live call transcription.

`AzureCallTranscriber` uses Azure AI Speech ConversationTranscriber, which
separates speakers (agent vs. customer) in real time. `ScriptedTranscriber`
replays a text transcript so the app can be demoed/tested without audio.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from .config import settings

log = logging.getLogger(__name__)

# callback(speaker, text, is_final)
OnText = Callable[[str, str, bool], Awaitable[None]]


class AzureCallTranscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop, on_text: OnText) -> None:
        import azure.cognitiveservices.speech as speechsdk  # imported lazily

        if not settings.speech_key:
            raise RuntimeError("AZURE_SPEECH_KEY is not set")
        self._sdk = speechsdk
        speech_cfg = speechsdk.SpeechConfig(subscription=settings.speech_key, region=settings.speech_region)
        speech_cfg.speech_recognition_language = settings.speech_language
        speech_cfg.set_property(speechsdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults, "true")
        audio_cfg = (
            speechsdk.audio.AudioConfig(device_name=settings.audio_input_device)
            if settings.audio_input_device
            else speechsdk.audio.AudioConfig(use_default_microphone=True)
        )
        self._tx = speechsdk.transcription.ConversationTranscriber(speech_config=speech_cfg, audio_config=audio_cfg)
        self._loop, self._on_text = loop, on_text

        def _emit(evt, final: bool) -> None:
            text = evt.result.text
            if text:
                speaker = evt.result.speaker_id or "Unknown"
                asyncio.run_coroutine_threadsafe(self._on_text(speaker, text, final), self._loop)

        self._tx.transcribing.connect(lambda e: _emit(e, False))
        self._tx.transcribed.connect(lambda e: _emit(e, True))
        self._tx.canceled.connect(lambda e: log.error("Transcription canceled: %s", e.cancellation_details))

    async def start(self) -> None:
        await asyncio.to_thread(lambda: self._tx.start_transcribing_async().get())

    async def stop(self) -> None:
        await asyncio.to_thread(lambda: self._tx.stop_transcribing_async().get())


class ScriptedTranscriber:
    """Replays lines like `Customer: my laptop won't turn on` with a delay."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_text: OnText, script: str, delay: float = 2.5) -> None:
        self._lines = [l.strip() for l in Path(script).read_text(encoding="utf-8").splitlines() if l.strip()]
        self._on_text, self._delay = on_text, delay
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        for line in self._lines:
            speaker, _, text = line.partition(":")
            if not text:
                speaker, text = "Unknown", line
            await self._on_text(speaker.strip(), text.strip(), True)
            await asyncio.sleep(self._delay)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
