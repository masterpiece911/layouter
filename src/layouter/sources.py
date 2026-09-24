"""Workflow frontends. Only an explicitly selected programmable source executes code."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tomllib
from typing import Protocol

from .errors import ConfigError
from .react_runtime import runtime_runner, PROTOCOL_VERSION


class WorkflowSource(Protocol):
    path: Path

    def metadata(self) -> dict: ...
    def materialize(self, context: dict) -> dict: ...


class TomlSource:
    def __init__(self, path: Path):
        self.path = path
        try:
            with path.open('rb') as stream:
                self.document = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f'{path}: {exc}') from exc

    def metadata(self) -> dict:
        return {'args': self.document.get('args', {})}

    def materialize(self, context: dict) -> dict:
        return self.document


class ReactSource:
    """A two-request, newline-delimited JSON session with one evaluator process.

    The optional runtime is external to Python (also works from a zipapp).
    stderr is inherited so diagnostics cannot fill an unread pipe.
    """
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.process = None

    def __enter__(self):
        self.resources = ExitStack()
        try:
            node = shutil.which('node')
            if not node:
                raise ConfigError('React runtime missing: TSX workflows require Node.js 22 or newer; '
                                  'TOML workflows do not require Node.js')
            runner = self.resources.enter_context(runtime_runner())
            self.process = subprocess.Popen([node, str(runner)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, cwd=self.path.parent)
        except BaseException:
            self.resources.close()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.process:
                self.process.stdin.close()
                if self.process.poll() is None:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                self.process.stdout.close()
        finally:
            self.resources.close()

    def request(self, operation: str, **payload) -> dict:
        request = {'protocol': PROTOCOL_VERSION, 'operation': operation, 'workflow': str(self.path), **payload}
        try:
            self.process.stdin.write((json.dumps(request) + '\n').encode())
            self.process.stdin.flush()
            # Read unbuffered bytes with an operation deadline; a partial line must
            # not hang the CLI, and buffered read-ahead must not confuse select().
            import time
            deadline = time.monotonic() + 30
            response = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while not response.endswith(b'\n'):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ConfigError('React evaluator protocol failure: timed out after 30 seconds')
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        raise ConfigError('React evaluator protocol failure: process exited without a response')
                    response.extend(chunk)
                    if len(response) > 16 * 1024 * 1024:
                        raise ConfigError('React evaluator protocol failure: response exceeds 16 MiB')
            value = json.loads(response)
        except (OSError, ValueError) as exc:
            raise ConfigError(f'React evaluator protocol/JSON failure: {exc}') from exc
        if not isinstance(value, dict) or type(value.get('ok')) is not bool:
            raise ConfigError('React evaluator protocol failure: expected a response envelope')
        if not value['ok']:
            raise ConfigError(f"React {value.get('kind', 'evaluation')} failure: {value.get('message', 'unknown error')}")
        if not isinstance(value.get('result'), dict):
            raise ConfigError('React evaluator protocol failure: expected an object result')
        if value.get('protocol') != PROTOCOL_VERSION:
            raise ConfigError('React evaluator protocol version mismatch; rebuild or reinstall the runtime')
        return value['result']

    def metadata(self) -> dict:
        return self.request('metadata')

    def materialize(self, context: dict) -> dict:
        return self.request('render', context=context)


def source_for(path: Path) -> WorkflowSource:
    if path.suffix == '.tsx':
        return ReactSource(path)
    # Explicit --file historically accepts TOML regardless of filename suffix.
    return TomlSource(path)


def discover_paths(project: Path, selected: str | None, global_dir: Path,
                   workflow: str, discover: bool, force_global: bool) -> list[tuple[Path, str]]:
    local_dir = project / '.dev'
    if selected:
        if force_global:
            raise ConfigError('--file and --global cannot be used together')
        path = Path(selected).expanduser()
        path = path if path.is_absolute() else project / path
        if not path.is_file():
            raise ConfigError(f'Configuration file does not exist: {path}')
        return [(path, workflow)]

    def scope(directory: Path, name: str) -> Path | None:
        matches = [directory / (name + suffix) for suffix in ('.toml', '.tsx')
                   if (directory / (name + suffix)).is_file()]
        if len(matches) > 1:
            raise ConfigError(f'Ambiguous workflow {name!r} in {directory}: both .toml and .tsx exist; use --file')
        return matches[0] if matches else None

    names = {workflow}
    if discover:
        names = {path.stem for directory in ([global_dir] if force_global else [global_dir, local_dir])
                 for suffix in ('*.toml', '*.tsx') for path in directory.glob(suffix) if path.is_file()}
    result = []
    for name in sorted(names):
        path = None if force_global else scope(local_dir, name)
        path = path or scope(global_dir, name)
        if path:
            result.append((path, name))
        elif not discover:
            raise ConfigError(f"No {'global ' if force_global else ''}workflow {workflow!r} found at {global_dir / workflow}")
    return result
