# Hackathon demo script (7 minutes)

Goal: prove a teacher's laptop can run a Bangla-translating classroom with **no
Internet**, for 30 students, with measured latency.

## Pre-flight (10 minutes before)

```bash
cd offline_guru
.venv/bin/python scripts/verify_offline.py          # expect PASS, ~40 s
./scripts/hotspot.sh status                         # note interface + IP
./scripts/serve.sh                                  # leave running
```

* Open `http://127.0.0.1:8000/teacher` on the laptop.
* Open `http://<laptop-ip>:8000/` on two phones connected to the hotspot.
* Turn **mobile data off** on the demo phones (so the demo is honest).
* Put the phones' browsers at 100 % zoom; enable one phone's audio.
* Have `data/demo/lecture.wav` ready as the microphone fallback.
* Charger connected (thermal headroom).

## Runbook

**0:00 — The problem (30 s)**
"Rural classrooms have no reliable Internet. Cloud AI is unusable there. This is
Offline Guru: a teacher's laptop becomes the classroom server, hotspot and AI —
with zero cloud calls."

**0:30 — Show it's offline (30 s)**
```bash
sudo nmcli networking off       # or show Wi-Fi client disabled, Ethernet unplugged
```
Show the phones are on the laptop's hotspot (no Internet). The teacher console
still loads. `/api/health` shows the local IP and "Internet: none".

**1:00 — Start the class (45 s)**
Teacher console → title "Photosynthesis" → **ক্লাস তৈরি করুন**. Point at the class
code and QR. Phones join with the code and a name; they appear in the student
list within a second (measured join p50 ≈ 150 ms at 30 clients).

**1:45 — Teach (2 min)**
Type today's vocabulary into **আজকের মূল শব্দ**: `photosynthesis, chlorophyll,
glucose, stomata`. Press **🎤 শুরু করুন** and speak 3–4 sentences, or use
**▶ ফাইল চালান** with `data/demo/lecture.wav` if the room is loud.

On the phone, Bangla captions appear ~1.5 s after each pause (p95 1.9 s). Tap
**🔈 অডিও বন্ধ** → the Bangla sentence is spoken in the student's headphones.
Point out: the caption arrives before the audio, because TTS is decoupled.

**3:45 — Show the measurements (45 s)**
Teacher console health panel: ASR p50 ≈ 1.3 s, translation ≈ 0.2 s, e2e p50 ≈
1.5 s, p95 ≈ 1.9 s, RAM ≈ 2.8 GB free. Say: "these are measured on this 2017
i5 laptop, not estimates; the load test below is in the repo."

**4:30 — Learning materials (1 min)**
Press **নতুন করে তৈরি করুন** under সারসংক্ষেপ: a Bangla summary with timestamps,
grounded in the transcript (no invented facts). Press **তৈরি করুন** under কুইজ:
5 questions, each with evidence. Show a question whose options are real lecture
terms. Press **অনুমোদন দিন** → the quiz appears on the student phone.

**5:30 — Student answers and feedback (45 s)**
A student answers one MCQ and one short answer. The MCQ returns instant Bangla
feedback with the correct answer and the timestamp to review. The short answer
that is vague comes back as "শিক্ষক যাচাই করবেন" — not marked wrong. Answer a
second one correctly to show green feedback.

**6:15 — Teacher controls and privacy (30 s)**
Show the answer table, correct one grade (**✓**). Show that another student's
phone cannot see it. Show **এক্সপোর্ট (JSON)** and **সব ডেটা মুছুন** — data
stays local, and deletion is real (rows + audio files).

**6:45 — Honest limitations (15 s)**
"The translation model is small and makes lexical mistakes; the teacher can edit
any caption and the correction reaches students instantly. We measured on
synthetic clean speech — real classroom accuracy is the next study."

## Numbers you may quote (all measured, see docs/EVALUATION.md)

| Claim | Value |
|---|---|
| ASR WER (base, clean synthetic speech) | 3.8% |
| Text end-to-end, p50 / p95 | 1.50 s / 1.88 s |
| ASR / translation / TTS p50 | 1.30 s / 0.20 s / 0.31 s |
| 30 clients: delivery | 10/10 segments to all 30, 0 drops |
| 30 clients: fan-out spread p95 | 6.1 ms |
| Runtime RAM | ~800 MB |
| Offline verification | zero external connections |

## If something breaks

| Problem | Recovery |
|---|---|
| Mic picks up nothing | use **▶ ফাইল চালান** with `data/demo/lecture.wav`; say so honestly |
| Phone can't connect | read the join URL from the console; check the phone is on the hotspot, not mobile data |
| Audio blocked by the browser | tap the audio button once (user-gesture requirement) |
| Summary/quiz empty | the transcript needs content; re-run with the demo WAV |
| Laptop slows down | check `/api/health` → `asr_queue_drops`; switch to `whisper-tiny` |

Do not claim 30 phones over Wi-Fi was tested — it was measured at the application
layer on one laptop, which is exactly what the repo's load test shows.
