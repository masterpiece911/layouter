#!/usr/bin/env python3
"""Measure fresh CLI processes for source-native and actual packaged portable runtimes."""
import argparse
import os
from pathlib import Path
import statistics
import subprocess
import time

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--runs', type=int, default=5)
args = p.parse_args()
if args.runs < 1:
    p.error('--runs must be positive')
env = {key: value for key, value in os.environ.items() if key not in {'LAYOUTER_REACT_RUNTIME', 'PYTHONPATH'}}
for label, source, extra in [
    ('full/TOML', 'toml', {}),
    ('full/portable TSX', 'tsx', {}),
    ('full/native override', 'tsx', {'LAYOUTER_REACT_RUNTIME': str(root / 'react/runner.mjs')}),
]:
    samples = []
    for _ in range(args.runs):
        start = time.perf_counter()
        subprocess.run([str(root / 'dist/layouter'), '--check', '--file',
                        str(root / f'tests/fixtures/react-parity.{source}')],
                       cwd=root, env={**env, **extra}, capture_output=True, check=True)
        samples.append((time.perf_counter() - start) * 1000)
    print(f'{label}: median {statistics.median(samples):.0f} ms, range {min(samples):.0f}–{max(samples):.0f} ms')
for name in ('layouter', 'layouter-core'):
    print(f'{name}: {(root / "dist" / name).stat().st_size:,} bytes')
