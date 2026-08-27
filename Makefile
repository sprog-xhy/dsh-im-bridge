# dsh-im-bridge convenience commands (Ubuntu/Linux; Windows uses scripts/*.ps1)
PY ?= python3
VENV := .venv/bin/python
PIP := $(VENV) -m pip

.PHONY: install test check run demo selfcheck help

install:              ## create venv and install the package (+ dev deps)
	$(PY) -m venv .venv
	$(PIP) install -e ".[dev]"

test:                 ## run the unit test suite
	$(VENV) -m pytest -q

check:                ## self-diagnostics (config + dsh connectivity)
	$(VENV) -m dsh_im_bridge --check --config config.yaml 2>/dev/null || $(VENV) -m dsh_im_bridge --check

run:                  ## run the bridge in the foreground
	$(VENV) -m dsh_im_bridge --config config.yaml

demo:                 ## confirmation-flow demo (fake dsh, auto-answer after 4s)
	$(VENV) scripts/demo_confirmation.py --auto 4

help:                 ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
