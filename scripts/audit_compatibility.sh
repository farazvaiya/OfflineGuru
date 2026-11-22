#!/bin/bash
set -euo pipefail

PY="${PY:-.venv/bin/python}"

echo "== Python =="
$PY --version

echo "== ctranslate2 =="
$PY -c 'import ctranslate2; print("ctranslate2", ctranslate2.__version__)'

echo "== transformers =="
$PY -c 'import transformers; print("transformers", transformers.__version__)'

echo "== ctranslate2 Interpreter class =="
$PY -c 'import ctranslate2; print("Translator:", hasattr(ctranslate2, "Translator"))'

echo "== OPUS-MT tokenizer class check =="
$PY -c '
from transformers import AutoTokenizer, MarianTokenizer
print("AutoTokenizer:", AutoTokenizer)
print("MarianTokenizer:", MarianTokenizer)
' 

echo "== requirements check =="
$PY -c 'import sentencepiece, tokenizers, sacremoses; print("sentencepiece/tokens/sacremoses OK")'

echo "== psutil =="
$PY -c 'import psutil; print("psutil", psutil.__version__)'

echo "== Done =="
