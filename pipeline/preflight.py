"""Validate the pinned Python runtime and libraries without external services."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
MODULES = ("bs4", "bleach", "cairosvg", "markdown", "PIL.Image", "fitz")


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temp = Path(stream.name)
    os.replace(temp, path)


def check():
    report = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
              "ok": False, "completed": False,
              "python": platform.python_version(), "executable": sys.executable,
              "checks": {}}
    try:
        expected = Path(__file__).with_name(".python-version").read_text().strip()
        if platform.python_version() != expected:
            raise RuntimeError(f"Expected Python {expected}; found {platform.python_version()}")
        report["checks"]["python"] = True
        import tomllib
        dependencies = tomllib.loads(Path(__file__).with_name("pyproject.toml").read_text())["project"]["dependencies"]
        versions = {}
        for dependency in dependencies:
            package, expected_version = dependency.split("==")
            actual = importlib.metadata.version(package)
            if actual.split("+")[0] != expected_version:
                raise RuntimeError(f"{package}: expected {expected_version}, found {actual}")
            versions[package] = actual
        for module in MODULES:
            importlib.import_module(module)
        report["checks"]["libraries"] = versions
        report["checks"]["imports"] = list(MODULES)
        report["ok"] = True
        report["exit_code"] = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["exit_code"] = 1
    report["completed"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / ".cache/pipeline/preflight.json")
    args = parser.parse_args(argv)
    try:
        report = check()
    except Exception as exc:
        report = {"schema_version": 1, "ok": False, "completed": True,
                  "exit_code": 1, "error": f"{type(exc).__name__}: {exc}"}
    write_report(args.report, report)
    if not report["ok"]:
        print(report["error"], file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
