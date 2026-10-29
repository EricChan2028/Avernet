#!/usr/bin/env python3
"""Static guard for migrated E2E Case executors.

This is a review/CI signal, not a security sandbox. Transport, persistence,
process-lifecycle and test-double modules are intentionally outside its default
scope; Case code must use the reviewed framework contracts instead.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys


FORBIDDEN_IMPORTS = {
    "requests", "httpx", "urllib", "urllib.request", "socket", "subprocess",
    "sqlite3", "pymysql", "mysql", "psycopg", "shlex",
}
FORBIDDEN_CALLS = {"urlopen", "create_connection", "Popen", "run", "system", "kill", "popen"}
FORBIDDEN_TEXT = (
    "X-Avernet-Principal", "X-E2E-Run-ID", "Authorization: Bearer", "Bearer ",
    "curl ", "pkill ", "kill -", "DROP TABLE", "TRUNCATE TABLE", "DELETE FROM",
)


def _module_name(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name
    return node.module or ""


def inspect(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        return [f"{path}: cannot parse case source ({type(error).__name__})"]
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = _module_name(node)
            if any(module == forbidden or module.startswith(forbidden + ".")
                   for forbidden in FORBIDDEN_IMPORTS):
                violations.append(f"{path}:{node.lineno}: forbidden raw dependency {module}")
        elif isinstance(node, ast.Call):
            function = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if function in FORBIDDEN_CALLS:
                violations.append(f"{path}:{node.lineno}: forbidden raw operation {function}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for text in FORBIDDEN_TEXT:
                if text in node.value:
                    violations.append(f"{path}:{node.lineno}: forbidden literal {text!r}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    violations = [violation for path in args.files for violation in inspect(path)]
    if violations:
        print("E2E CASE GUARD FAILED")
        print("\n".join(violations))
        return 1
    print(f"E2E CASE GUARD PASSED ({len(args.files)} file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
