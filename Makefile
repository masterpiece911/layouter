PYTHON ?= python3

.PHONY: help test check build zipapp source release

help:
	@printf '%s\n' 'make test     Run the unit tests' 'make check    Validate the example workflow' 'make build    Build the executable and source archive' 'make release  Run checks, then build release artifacts' 'Override Python with PYTHON=/path/to/python3 (3.11 or newer).'

test:
	PYTHONPATH=src "$(PYTHON)" -m unittest discover -s tests -v

check:
	PYTHONPATH=src "$(PYTHON)" -m layouter -f examples/debug.toml --check debug checkout

zipapp:
	"$(PYTHON)" scripts/build_zipapp.py

source:
	"$(PYTHON)" scripts/build_source_archive.py

build: zipapp source

release: test check
	$(MAKE) build PYTHON="$(PYTHON)"
