
import time, numpy as np, soundfile as sf
from faster_whisper import WhisperModel
data, sr = sf.read("data/demo/lecture.wav", dtype="float32")
seg = data[int(3*sr):int(11*sr)]
m = WhisperModel("models/whisper-base", device="cpu", compute_type="int8", cpu_threads=3, local_files_only=True)
def run(**kw):
    list(m.transcribe(seg, language="en", vad_filter=False, condition_on_previous_text=False, **kw)[0])
    t0=time.perf_counter(); list(m.transcribe(seg, language="en", vad_filter=False, condition_on_previous_text=False, **kw)[0]); return time.perf_counter()-t0
for label, kw in [
  ("beam1+words", dict(beam_size=1, word_timestamps=True)),
  ("beam1", dict(beam_size=1)),
  ("beam2+words", dict(beam_size=2, word_timestamps=True)),
  ("beam1+words+prompt", dict(beam_size=1, word_timestamps=True, initial_prompt="photosynthesis chlorophyll glucose")),
]:
    ts=[run(**kw) for _ in range(2)]
    print(f"{label}: {min(ts):.2f}s (8s audio, rtf {min(ts)/8:.2f})")
