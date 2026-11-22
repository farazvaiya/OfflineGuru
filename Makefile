.PHONY: venv deps models demo test lint run verify-offline loadtest clean help
PY := .venv/bin/python

help:
	@echo "make venv           create virtualenv + install runtime deps"
	@echo "make models         download/convert models (ONE-TIME, needs Internet)"
	@echo "make demo           synthesise the repeatable demo lecture"
	@echo "make test           run unit + integration tests"
	@echo "make run            start the classroom server"
	@echo "make verify-offline prove runtime works with zero network access"
	@echo "make loadtest       run the 30-student load test (server must be running)"

venv:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

deps:
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install -r requirements-prep.txt --extra-index-url https://download.pytorch.org/whl/cpu

models:
	$(PY) scripts/prepare_models.py --with-en-demo-voice

demo:
	$(PY) scripts/make_demo_lecture.py

test:
	$(PY) -m pytest -q

run:
	./scripts/serve.sh

verify-offline:
	$(PY) scripts/verify_offline.py

loadtest:
	$(PY) scripts/loadtest.py --clients 30 --wav data/demo/lecture_45s.wav

clean:
	rm -rf .venv __pycache__ .pytest_cache
