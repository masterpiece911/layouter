"""Parse CLI options and coordinate validation, planning, and desktop reconciliation."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from . import __version__
from .config import declarations, load, resolve, workflow_data
from .errors import ConfigError, LayouterError
from .i3 import Compositor
from .model import digest
from .reconcile import Reconciler
from .runtime import Runtime


def parser() -> argparse.ArgumentParser:
    """Build the CLI parser, leaving tokens after the workflow as workflow arguments."""
    p = argparse.ArgumentParser(prog="layouter", allow_abbrev=False,
        usage="%(prog)s [options] [workflow [workflow-args...]]",
        description="Create missing development-workspace elements; preserve everything already running.",
        epilog="Options go before the workflow. Everything after it is a workflow argument.")
    p.add_argument("-C", dest="directory", metavar="DIR", help="select project directory")
    source = p.add_mutually_exclusive_group()
    source.add_argument("-f", "--file", help="use exactly this TOML file, relative to the selected project")
    source.add_argument("--global", dest="global_only", action="store_true",
                        help="use the global workflow even when a local workflow exists")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="list workflows and arguments; no desktop access")
    mode.add_argument("--check", action="store_true", help="validate the selected workflow; no desktop access")
    mode.add_argument("--dry-run", action="store_true", help="inspect live state and print a plan without changing it")
    p.add_argument("--sync", action="store_true",
                   help="correct managed placement, layouts, order, and sizes")
    p.add_argument("--no-focus", action="store_true", help="restore original compositor focus after creation")
    p.add_argument("--timeout", type=float, metavar="SECONDS", help="IPC/discovery timeout per operation (default: 30)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("workflow", nargs="?", default="default")
    p.add_argument("workflow_args", nargs=argparse.REMAINDER)
    return p


def main(argv: list[str] | None = None) -> int:
    """Resolve the project and workflow, dispatch the requested mode, and return an exit code."""
    p = parser()
    args = p.parse_args(argv)
    try:
        project = Path.cwd()
        if args.directory is not None:
            path = Path(args.directory).expanduser()
            project = (path if path.is_absolute() else project / path).resolve()
            if not project.is_dir():
                raise ConfigError(f"Project directory does not exist: {project}")
        config, sources = load(project, args.file, workflow=args.workflow, discover=args.list,
                               force_global=args.global_only)
        if args.timeout is not None:
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ConfigError("--timeout must be a finite positive number")
            config.setdefault("settings", {})["timeout"] = args.timeout
        if args.list:
            for name in config["workflows"]:
                data = workflow_data(config, name)
                signature = " ".join((f"[{a['name']}={a['default']}]" if "default" in a else f"<{a['name']}>")
                                     for a in declarations(data))
                print(f"{name}{' ' + signature if signature else ''}")
            return 0
        workflow = resolve(config, project, args.workflow, args.workflow_args, sources)
        if args.check:
            print(f"Valid: {workflow.name} | session={workflow.session!r} | id={workflow.session_id}")
            print(f"Project: {workflow.project}")
            print(f"{len(workflow.nodes)} compositor nodes; "
                  f"{sum(len(t.panes) for n in workflow.nodes for t in n.tabs)} kitty panes")
            return 0
        runtime = Runtime()

        def emit(action):
            print(f"{action.kind:6} {action.target}" + (f" ({action.detail})" if action.detail else ""), flush=True)

        with Compositor(workflow.timeout) as i3:
            reconciler = Reconciler(workflow, i3, runtime, emit=emit)
            if args.dry_run:
                for action in reconciler.plan(sync=args.sync):
                    emit(action)
                return 0
            # Placement changes focus temporarily, so serialize desktop mutations
            # across different projects and session identities.
            with runtime.lock("desktop-" + digest(i3.path)):
                reconciler.run(no_focus=args.no_focus, sync=args.sync)
        return 0
    except ConfigError as exc:
        print(f"layouter: {exc}", file=sys.stderr)
        return 2
    except (LayouterError, OSError, TimeoutError) as exc:
        print(f"layouter: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("layouter: interrupted; existing and newly launched applications were left running", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
