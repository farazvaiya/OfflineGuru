
import time, numpy as np, soundfile as sf, sys
from faster_whisper import WhisperModel

path = "data/demo/lecture.wav"
try:
    data, sr = sf.read(path, dtype="float32")
except Exception as e:
    print("no demo wav:", e); sys.exit(1)
print("audio", len(data)/sr, "s @", sr)

for name in ["models/whisper-tiny", "models/whisper-base"]:
    m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=3, local_files_only=True)
    for dur in [2.0, 4.0, 8.0, 12.0]:
        seg = data[int(3*sr):int((3+dur)*sr)]
        # warm
        list(m.transcribe(seg, language="en", beam_size=1, word_timestamps=True, vad_filter=False, condition_on_previous_text=False)[0])
        t0=time.perf_counter()
        segs, info = m.transcribe(seg, language="en", beam_size=1, word_timestamps=True, vad_filter=False, condition_on_previous_text=False)
        words=[w for s in segs for w in (s.words or [])]
        dt=time.perf_counter()-t0
        print(f"{name} dur={dur}s  asr={dt:.2f}s  rtf={dt/dur:.2f}  words={len(words)}")
    del m
