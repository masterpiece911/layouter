"""Zipapp entry point: preserve the CLI's exit status."""
from layouter.cli import main

raise SystemExit(main())
