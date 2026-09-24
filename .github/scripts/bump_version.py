"""Set the version in pyproject.toml.

Usage: bump_version.py (patch | minor | major | X.Y.Z)
Prints the new version. Refuses anything that is not greater than the current one.
"""

import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
VERSION_LINE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.M)


def main(arg):
    text = PYPROJECT.read_text()
    match = VERSION_LINE.search(text)
    if not match:
        sys.exit("No version = \"X.Y.Z\" line in pyproject.toml")
    current = tuple(int(n) for n in match.groups())
    major, minor, patch = current
    if arg == "patch":
        new = (major, minor, patch + 1)
    elif arg == "minor":
        new = (major, minor + 1, 0)
    elif arg == "major":
        new = (major + 1, 0, 0)
    elif re.fullmatch(r"v?\d+\.\d+\.\d+", arg):
        new = tuple(int(n) for n in arg.lstrip("v").split("."))
    else:
        sys.exit(f"Expected patch, minor, major, or X.Y.Z, got '{arg}'")
    if new <= current:
        sys.exit(f"{'.'.join(map(str, new))} is not greater than the current {'.'.join(map(str, current))}")
    version = ".".join(map(str, new))
    PYPROJECT.write_text(VERSION_LINE.sub(f'version = "{version}"', text, count=1))
    print(version)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else "")
