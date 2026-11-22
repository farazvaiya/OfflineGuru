from __future__ import annotations

from pathlib import Path

from guru.config import Settings, load_settings


def test_defaults_are_offline_local_paths():
    s = Settings()
    assert s.asr.model_dir.startswith("models/")
    assert s.translate.backend == "marian"
    assert s.tts.voice_onnx.startswith("models/")
    assert s.storage.keep_recordings is False  # raw mic audio never persisted


def test_toml_file_is_applied(tmp_path: Path):
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('[server]\nport = 9999\nrequire_teacher_token = false\n')
    s = load_settings(cfg)
    assert s.server.port == 9999
    assert s.server.require_teacher_token is False
    # Untouched sections keep defaults.
    assert s.asr.language == "en"


def test_env_overrides_typed(monkeypatch):
    monkeypatch.setenv("GURU_SERVER__PORT", "8080")
    monkeypatch.setenv("GURU_SERVER__REQUIRE_TEACHER_TOKEN", "false")
    monkeypatch.setenv("GURU_ASR__VAD_THRESHOLD", "0.02")
    monkeypatch.setenv("GURU_ASR__HOTWORDS", "glucose, chlorophyll")
    s = load_settings()
    assert s.server.port == 8080
    assert s.server.require_teacher_token is False  # bool coercion
    assert abs(s.asr.vad_threshold - 0.02) < 1e-9  # float coercion
    assert s.asr.hotwords == "glucose, chlorophyll"  # str passthrough


def test_env_override_bool_accepts_multiple_spellings(monkeypatch):
    for value, expected in (("1", True), ("yes", True), ("on", True),
                            ("0", False), ("false", False), ("no", False)):
        monkeypatch.setenv("GURU_TTS__ENABLED", value)
        assert load_settings().tts.enabled is expected


def test_relative_paths_resolve_against_project_root():
    s = Settings()
    resolved = s.resolve("models/whisper-base")
    assert resolved is not None and resolved.is_absolute()
    assert resolved.name == "whisper-base"
    assert s.resolve(None) is None


def test_load_settings_without_config_file_uses_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GURU_CONFIG", raising=False)
    missing = tmp_path / "nope.toml"
    s = load_settings(missing)
    assert s.server.port == 8000
