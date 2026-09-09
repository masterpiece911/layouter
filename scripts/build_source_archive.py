#!/usr/bin/env python3
"""Build a distributable source archive into dist/."""
from pathlib import Path
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
destination = root / "dist" / f"layouter-{version}-source.zip"
destination.parent.mkdir(exist_ok=True)
excluded_parts = {".git", ".pytest_cache", "__pycache__", ".venv", "build", "dist"}

with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in excluded_parts or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.suffix == ".pyc" or not path.is_file():
            continue
        archive.write(path, Path(f"layouter-{version}") / relative)

print(destination)
