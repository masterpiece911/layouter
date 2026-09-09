#!/usr/bin/env python3
"""Build a distributable source archive into dist/."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "layouter-0.1.0-source.zip"
destination.parent.mkdir(exist_ok=True)
excluded_parts = {".git", ".pytest_cache", "__pycache__"}

with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path == destination or any(part in excluded_parts for part in relative.parts):
            continue
        if path.suffix == ".pyc" or not path.is_file():
            continue
        archive.write(path, Path("layouter-0.1.0") / relative)

print(destination)
