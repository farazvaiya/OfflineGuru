from __future__ import annotations

from pathlib import Path

import pytest

from guru.bilingual import BidirectionalPiperTts, BidirectionalTranslator, TurnLanguageRouter, language_pair


@pytest.mark.parametrize(("source", "target"), [("en", "bn"), ("bn", "en")])
def test_language_pair_routes_both_directions(source, target):
    assert language_pair(source) == (source, target)


def test_auto_router_waits_for_stable_confidence_and_locks_turn():
    router = TurnLanguageRouter("auto", min_letters=6, confidence=0.75)
    router.observe_stable("abc")
    assert router.snapshot() == {"source_language": "en", "target_language": "bn"}
    router.observe_stable("বাংলা ভাষা")
    assert router.snapshot() == {"source_language": "bn", "target_language": "en"}
    router.observe_stable("বাংলা English words")
    assert router.snapshot() == {"source_language": "bn", "target_language": "en"}
    router.end_turn()
    router.observe_stable("This is a full English sentence")
    assert router.snapshot() == {"source_language": "en", "target_language": "bn"}
    assert router.turn_switches == 2


def test_fixed_router_does_not_switch_for_code_switching():
    router = TurnLanguageRouter("bn")
    router.observe_stable("this is Latin code switching")
    router.end_turn()
    assert router.snapshot() == {"source_language": "bn", "target_language": "en"}
    assert router.turn_switches == 0


def test_bilingual_backends_point_at_cached_models_and_real_voices(settings):
    translator = BidirectionalTranslator(settings)
    bn_en = translator._build("bn", "en")
    en_bn = translator._build("en", "bn")
    assert bn_en.model_dir.name == "opus-bn-en-ct2"
    assert bn_en.tokenizer_dir.name == "opus-bn-en-src"
    assert en_bn.model_dir.name == "missing-mt"  # fixture intentionally disables the old EN→BN path
    settings.tts.voice_onnx = "models/piper/bn_BD-google-medium.onnx"
    settings.tts.voice_config = "models/piper/bn_BD-google-medium.onnx.json"
    settings.tts.english_voice_onnx = "models/piper/en_US-lessac-medium.onnx"
    settings.tts.english_voice_config = "models/piper/en_US-lessac-medium.onnx.json"
    voices = BidirectionalPiperTts(settings)
    assert voices._voice("bn").voice_path.is_file()
    assert voices._voice("en").voice_path.is_file()
    assert voices._voice("bn").voice_path.name == "bn_BD-google-medium.onnx"
    assert voices._voice("en").voice_path.name == "en_US-lessac-medium.onnx"


def test_bidirectional_pipeline_targets_translated_caption_and_voice(settings, tmp_path: Path):
    import asyncio
    from guru.bus import EventBus
    from guru.pipeline import ClassroomPipeline
    from guru.storage import Storage
    from guru.translate import Translation
    from tests.test_realtime import FakeStreamingAsr
    from tests.conftest import FakeTts

    class RouteTranslator:
        available = True
        def __init__(self): self.calls = []
        def translate_for(self, text, source, target):
            self.calls.append((text, source, target))
            answer = "It is a leaf." if target == "en" else "এটি একটি পাতা।"
            return Translation(source=text, text=answer, latency_ms=4.0)
        def translate_batch(self, texts, beam_size=None):
            return [self.translate_for(t, "en", "bn") for t in texts]

    class RoutedTts(FakeTts):
        def __init__(self): super().__init__(); self.targets=[]
        def synthesize_for(self, text, target):
            self.targets.append(target)
            return super().synthesize(text)

    async def run(source, target, source_text, expected):
        settings.asr.streaming = True
        settings.tts.enabled = True
        settings.storage.data_dir = str(tmp_path / source)
        storage = Storage(tmp_path / (source + ".db"))
        bus = EventBus()
        translator, tts = RouteTranslator(), RoutedTts()
        pipeline = ClassroomPipeline(settings, bus, storage, FakeStreamingAsr(), translator, tts)
        sid = storage.create_session("route", "tok", (source + target).upper(), source, target)
        pipeline.session_id = sid
        pipeline.loop = asyncio.get_running_loop()
        sub = bus.subscribe("student", sid)
        seg = storage.add_segment(sid, start_s=0, end_s=1,
            en=source_text if source == "en" else "", bn=source_text if source == "bn" else "",
            source_text=source_text, source_language=source, target_language=target)
        import time
        pipeline._enrich({"segment":seg, "finalized_at":time.perf_counter(),
                          "source_language":source, "target_language":target})
        event = await asyncio.wait_for(sub.queue.get(), 1)
        assert event["type"] == "segment"
        payload=event["segment"]
        assert (payload["source_text"], payload["translated_text"]) == (source_text, expected)
        assert (payload["source_language"], payload["target_language"]) == (source, target)
        assert payload[target] == expected and payload[source] == source_text
        assert translator.calls == [(source_text, source, target)]
        assert pipeline._tts_q.get_nowait()["target_language"] == target
        storage.close()

    async def scenario():
        await run("en", "bn", "This is a leaf.", "এটি একটি পাতা।")
        await run("bn", "en", "এটি একটি পাতা।", "It is a leaf.")
    asyncio.run(scenario())
