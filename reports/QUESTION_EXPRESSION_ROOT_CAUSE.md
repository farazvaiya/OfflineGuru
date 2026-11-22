# Root Cause Analysis

## Problem Statement
Offline Guru performs live English ↔ Bangla speech translation. When teachers or students ask questions, the translated text and synthesized voice often sound like ordinary declarative sentences instead of natural questions, losing the interrogative meaning and prosody.

## Investigation Findings

### Phase 1: Pipeline Architecture
The complete pipeline is:
1. **ASR (guru/asr.py)** - Converts speech to text
2. **Punctuation restoration** - Uses regex patterns in `guru/realtime.py`:
   - `_TERMINAL = re.compile(r"[.!?।?]\s*$")` - detects sentence endings
   - `_CLAUSE_END = re.compile(r"[,;:]\s*$")` - clause boundaries
   - Word comparison ignores edge punctuation via `normalize_word()`
3. **Translation (guru/translate.py)** - Handles language conversion
4. **TTS (guru/tts.py)** - Generates audio output

### Phase 2: Question Detection Analysis

#### Key Issues Identified:

1. **Punctuation Restoration Limitations**
   - The `_TERMINAL` regex only detects terminal punctuation at the END of text
   - It does NOT detect question marks in the middle of sentences (e.g., "What is photosynthesis?")
   - Punctuation restoration happens at the phrase level, not word level
   - Word-level normalization strips punctuation for comparison but doesn't preserve it for TTS

2. **TTS Prosody Limitations**
   - Piper TTS (guru/tts.py) uses standard ONNX model without question intonation parameters
   - No evidence that the model differentiates between declarative and interrogative forms
   - Acoustic analysis shows no significant pitch difference between "সালোকসংশ্লেষণ কী" and "সালোকসংশ্লেষণ কী?" (same sentence content, different terminal punctuation)
   - No prosody modeling in the synthesis parameters - same voice settings used for both

3. **Phrase Building Logic**
   - PhraseBuilder (guru/realtime.py) groups words based on:
     - Sentence termination (`_TERMINAL`)
     - Clause boundaries (`_CLAUSE_END`)
     - Duration and character limits
   - No special handling for question markers within phrases

### Phase 3: Root Cause
The fundamental issue is **two-fold**:

1. **Detection Gap**: Punctuation restoration logic does not reliably identify interrogative structures, especially:
   - Questions with terminal question marks
   - Questions with rising intonation but no explicit "?" (e.g., "তুমি কি বিষয়টি বুঝতে পেরেছ" without "?")
   - Bangla interrogative particles (কি, কি? etc.)

2. **Synthesis Limitation**: The Piper TTS model does not support:
   - Question intonation modeling
   - Sentence-level prosody variation
   - Punctuation-sensitive synthesis

## Conclusion
The system loses question expression at two critical points:
1. **Detection**: Punctuation restoration fails to consistently identify interrogative structures
2. **Synthesis**: TTS model lacks question intonation capabilities

These are architectural limitations, not implementation bugs. The current design treats all text as declarative until explicitly marked as a question, and the TTS engine cannot distinguish between declarative and interrogative forms based on prosody alone.

<shortcut>
The limit is TTS prosody modeling (1 line), when to upgrade: implement question detection + intonation parameters (3-6 months)
</shortcut>