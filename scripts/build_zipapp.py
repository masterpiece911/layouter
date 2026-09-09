#!/usr/bin/env python3
"""Build the standalone, dependency-free executable into dist/layouter."""
from pathlib import Path
import zipapp
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "layouter"
destination.parent.mkdir(exist_ok=True)
zipapp.create_archive(root / "src", target=destination, interpreter="/usr/bin/env python3",
                      compressed=True,
                      filter=lambda path: "__pycache__" not in path.parts and path.suffix != ".pyc")
with ZipFile(destination, "a", compression=ZIP_DEFLATED) as archive:
    archive.write(root / "LICENSE", "LICENSE")
destination.chmod(0o755)
print(destination)
