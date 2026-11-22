#!/usr/bin/env python3
"""Synthesise a repeatable English classroom lecture for demos and evaluation.

The text is fixed, so we always know the ground truth.  Audio is produced by the
Piper English voice (prep-time only) and written as 16 kHz mono WAV, which is
exactly what the live pipeline consumes.

    .venv/bin/python scripts/make_demo_lecture.py
    # -> data/demo/lecture.wav, data/demo/lecture.txt
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SENTENCES = [
    "Good morning everyone. Today we are going to learn about photosynthesis.",
    "Photosynthesis is the process by which green plants make their own food using sunlight.",
    "Please write this definition in your notebook.",
    "The word photosynthesis comes from two parts: photo, which means light, and synthesis, which means putting together.",
    "Plants use a green pigment called chlorophyll to capture light energy.",
    "Chlorophyll is found inside small structures called chloroplasts.",
    "The leaves of a plant are called the food factory of the plant.",
    "During photosynthesis, plants take in carbon dioxide from the air through tiny pores called stomata.",
    "Water is absorbed from the soil by the roots.",
    "The roots transport water to the leaves through tubes called xylem.",
    "Light energy splits water molecules into hydrogen and oxygen.",
    "This process is called photolysis.",
    "The plant uses carbon dioxide and hydrogen to make glucose.",
    "Glucose is a simple sugar that stores chemical energy.",
    "Oxygen is released into the air as a byproduct.",
    "Six molecules of carbon dioxide and six molecules of water produce one molecule of glucose and six molecules of oxygen.",
    "Photosynthesis mainly happens in the green parts of the plant.",
    "Most photosynthesis occurs in the leaves.",
    "Plants need three things for photosynthesis: sunlight, water, and carbon dioxide.",
    "If any one of these is missing, photosynthesis slows down or stops.",
    "At night there is no sunlight, so photosynthesis does not happen.",
    "Respiration, on the other hand, happens all the time, day and night.",
    "Respiration is the process by which living cells release energy from glucose.",
    "So plants both make food and use food.",
    "Temperature also affects the rate of photosynthesis.",
    "The rate is fastest at around twenty five to thirty five degrees Celsius.",
    "Above forty degrees Celsius, the enzymes stop working properly.",
    "An enzyme is a protein that speeds up a chemical reaction.",
    "Water is a limiting factor when the soil is dry.",
    "Carbon dioxide is often the limiting factor in open air.",
    "Now let us look at the importance of photosynthesis.",
    "Photosynthesis produces the oxygen we breathe.",
    "It is the source of almost all food chains on Earth.",
    "Fossil fuels like coal and oil were formed from ancient plants.",
    "Without photosynthesis, life on Earth would not be possible.",
    "Please copy the diagram from the board and label the stomata and chloroplasts.",
    "Are there any questions before we move to the next topic?",
]


def main() -> int:
    from piper import PiperVoice
    from piper.config import SynthesisConfig

    voice_path = ROOT / "models" / "piper" / "en_US-lessac-medium.onnx"
    if not voice_path.is_file():
        sys.exit("English demo voice missing. Run: scripts/prepare_models.py --with-en-demo-voice")

    out_dir = ROOT / "data" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "lecture_22k.wav"
    final_path = out_dir / "lecture.wav"

    voice = PiperVoice.load(str(voice_path))
    cfg = SynthesisConfig(length_scale=1.05)
    silence = b"\x00\x00" * int(22050 * 0.65)  # 650 ms pause between sentences
    pcm = bytearray()
    for i, sentence in enumerate(SENTENCES):
        for chunk in voice.synthesize(sentence, cfg):
            pcm.extend(chunk.audio_int16_bytes)
        pcm.extend(silence)
        if (i + 1) % 10 == 0:
            print(f"  synthesised {i + 1}/{len(SENTENCES)}", flush=True)

    with wave.open(str(raw_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(bytes(pcm))

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(raw_path), "-ar", "16000", "-ac", "1", str(final_path)],
        check=True,
    )
    raw_path.unlink(missing_ok=True)
    (out_dir / "lecture.txt").write_text("\n".join(SENTENCES) + "\n", encoding="utf-8")
    with wave.open(str(final_path)) as wf:
        print(f"wrote {final_path} ({wf.getnframes() / wf.getframerate():.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
