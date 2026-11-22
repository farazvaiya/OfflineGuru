# Offline Guru — Project Structure

## Purpose
Offline lecture tool for a teacher laptop with no internet dependency. Teacher speaks English, students in the same LAN see Bangla captions and optionally listen to synthesized Bangla audio.

## High-level idea
- One Python process on the teacher machine.
- Mic/audio → segmenter → ASR (English transcript) → translation (English → Bangla) → optional TTS (Bangla audio).
- Caption text is pushed to students quickly; audio is generated separately and may be delayed by voice synthesis without blocking captions.
- Everything is offline: models load from local directories, storage is local SQLite, Wi-Fi hotspot is used for student access.

## Runtime shape
- Front ends:
  - `guru/server/static/teacher.html` and related JS/CSS: teacher console.
  - `guru/server/static/student.html` and related JS/CSS: student viewer.
- Server:
  - `guru/server/app.py`: FastAPI app with REST endpoints and WebSockets.
- Pipeline:
  - `guru/pipeline.py`: live capture, segmentation, ASR, translation, TTS orchestration.
- Models:
  - `guru/asr.py`: offline speech recognition wrapper.
  - `guru/translate.py`: offline English → Bangla translation wrappers.
  - `guru/tts.py`: offline Bangla TTS wrapper.
- Storage and state:
  - `guru/storage.py`: SQLite-backed persistence for sessions, segments, materials, students, answers, documents, metrics.
- Education features:
  - `guru/education/service.py`: summary, quiz, grading, feedback orchestration.
  - `guru/education/quiz.py`: quiz construction from transcript and documents.
  - `guru/education/summary.py`: extractive bilingual summary building.
  - `guru/education/grading.py`: MCQ and short-answer grading and feedback.
  - `guru/education/documents.py`: PDF/PPTX/TXT ingestion, chunking, local retrieval.
  - `guru/education/transcript_utils.py`: transcript text utilities for sentences, key terms, definitions, numbers.
- Supporting pieces:
  - `guru/audio.py`: capture and segmentation primitives.
  - `guru/bus.py`: in-process pub/sub for WebSocket fan-out.
  - `guru/config.py`: typed settings and env override handling.
  - `guru/net.py`: local network/hotspot helpers.

## Top-level project tree
```text
./
├── config/
├── data/
├── docs/
├── guru/
│   ├── asr.py
│   ├── audio.py
│   ├── bus.py
│   ├── config.py
│   ├── net.py
│   ├── opus_benchmark.py
│   ├── pipeline.py
│   ├── server/
│   │   ├── app.py
│   │   └── static/
│   ├── storage.py
│   ├── tts.py
│   ├── translate.py
│   └── education/
│       ├── documents.py
│       ├── grading.py
│       ├── quiz.py
│       ├── service.py
│       ├── summary.py
│       └── transcript_utils.py
├── models/
├── scripts/
└── reports/
```

## Core data flow
1. Teacher starts a session from the teacher console.
2. A session code is generated; students join with that code.
3. Audio is captured, split into speech segments, transcribed to English.
4. Each finalized English segment is translated into Bangla.
5. The Bangla caption is sent to students.
6. If TTS is enabled, Bangla text is synthesized to audio and cached by segment.
7. Later, the teacher can generate a summary and quiz from the transcript and uploaded documents.

## Key responsibilities by area

### Capture and segmentation
- `guru/audio.py` handles microphone capture and voice activity segmentation.
- Segments are time-bounded speech chunks, not full recordings.
- `guru/config.py` controls segment size, silence detection, preroll, and capture device settings.

### Speech recognition
- `guru/asr.py` wraps the offline ASR model.
- It transcribes each segment to English and reports confidence and latency.
- Model loading is local-only; missing model is treated as unavailable, not as a network fallback.

### Translation
- `guru/translate.py` provides offline English → Bangla translation.
- It includes the main production backend and optional alternatives.
- The default backend is fast and designed for live classroom latency.
- Translation result includes whether numbers were preserved, because that matters for lecture content.

### Text-to-speech
- `guru/tts.py` converts Bangla text into spoken audio.
- Audio is cached by text so repeated playback does not re-synthesize unnecessarily.
- TTS is intentionally decoupled from caption delivery so a slow voice engine does not delay captions.

### Orchestration
- `guru/pipeline.py` wires capture, ASR, translation, and TTS together.
- It uses bounded queues and separate worker threads so one slow stage does not stall the whole system.
- It also records per-stage metrics for latency and drop behavior.

### Web layer
- `guru/server/app.py` exposes:
  - session creation and lifecycle
  - student join flow
  - transcript and segment access
  - teacher-only material controls
  - audio serving for segments
  - WebSocket live updates
- The teacher side is token-protected; student side is scoped to each student’s session and answers.

### Storage
- `guru/storage.py` is the local data layer.
- It stores sessions, segments, materials, students, answers, topic stats, metrics, documents, and document chunks.
- Credentials are stored as hashes, not plaintext.
- Retention and export features are included.

### Education workflows
- `guru/education/service.py` coordinates summary, quiz, grading, and feedback flows.
- `guru/education/quiz.py` builds grounded quiz items from transcript sentences and uploaded materials.
- `guru/education/summary.py` builds extractive bilingual summaries linked to transcript evidence.
- `guru/education/grading.py` grades MCQs and short answers and produces bilingual feedback.
- `guru/education/transcript_utils.py` supports sentence splitting, term extraction, definition parsing, and number extraction.
- `guru/education/documents.py` handles document ingestion and chunk-based retrieval for grounding.

## Configuration
- `config/default.toml` is the main runtime configuration.
- Settings are grouped by concern: ASR, translation, TTS, education, server, storage, audio.
- Environment variables with a `GURU_` prefix can override config values.
- Model paths, server port, retention policy, queue sizes, and feature flags all live here.

## Data stored locally
- Sessions and session metadata
- Student names and per-student tokens
- Segments with English text, Bangla text, timings, and confidence
- Materials: summaries and quizzes
- Answers and topic-level stats
- Uploaded document metadata and text chunks
- Latency metrics
- Cached synthesized audio for segments

Note:
- Raw microphone audio is not stored by default.
- Only synthesized Bangla audio per segment is cached, and that can be disabled.

## What is intentionally offline
- Model loading uses local files only.
- There is no cloud fallback for ASR, translation, or TTS.
- Student access is over the local network, not through an external service.
- Document ingestion and retrieval are local and deterministic.

## Current language direction
- The system currently supports English speech → English transcript → Bangla caption + Bangla TTS.
- It does not currently provide a general Bangla → English translation path for live lecture audio.

## Where to look next
- For live pipeline behavior: `guru/pipeline.py`
- For server API and session lifecycle: `guru/server/app.py`
- For classroom features: `guru/education/service.py` and its neighbors
- For configuration and defaults: `config/default.toml` and `guru/config.py`
- For storage shape: `guru/storage.py`
