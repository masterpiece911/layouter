PYTHON ?= python3
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
DESTDIR ?=
BUILD_VENV ?= .venv/build
BUILD_PYTHON = $(BUILD_VENV)/bin/python

.PHONY: help deps build-deps runtime test check build zipapp source release install

help:
	@printf '%s\n' 'make deps     Install locked npm dependencies' 'make test     Run the Python unit tests' 'make check    Validate the example workflow' 'make build    Build the full executable and source archive' 'make release  Install build dependencies, run checks, build and verify all release artifacts' 'make install  Build and install into PREFIX/bin (default: ~/.local/bin)' 'Override Python with PYTHON=/path/to/python3 (3.11 or newer).' 'Installation supports PREFIX, BINDIR, and DESTDIR; releases require Node 22+, npm, and dpkg-deb.'

deps:
	npm ci --prefix react

build-deps:
	"$(PYTHON)" -m venv "$(BUILD_VENV)"
	"$(BUILD_PYTHON)" -m pip install -r requirements-build.txt

runtime: deps
	"$(PYTHON)" scripts/build_react_runtime.py

test:
	PYTHONPATH=src "$(PYTHON)" -m unittest discover -s tests -v

check:
	PYTHONPATH=src "$(PYTHON)" -m layouter -f examples/morning.toml --check morning garden

zipapp: runtime
	"$(PYTHON)" scripts/build_zipapp.py

source: runtime
	"$(PYTHON)" scripts/build_source_archive.py

build: zipapp source

release: build-deps check
	"$(BUILD_PYTHON)" scripts/build_release.py

install: runtime
	"$(PYTHON)" scripts/build_zipapp.py --local-python --output "$(DESTDIR)$(BINDIR)/layouter"
