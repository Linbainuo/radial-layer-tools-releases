#!/usr/bin/env python3
"""Build a version-checked Radial Layer Tools release archive."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRECTORY = ROOT / "radial_layer_tools"
VERSION_PATTERN = re.compile(
    r"^PLUGIN_VERSION\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
EXCLUDED_NAMES = {
    "radial_layer_tools_config.json",
    "Thumbs.db",
    ".DS_Store",
}


def plugin_version() -> str:
    source = (PLUGIN_DIRECTORY / "__init__.py").read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(source)
    if match is None:
        raise RuntimeError("PLUGIN_VERSION was not found")
    return match.group(1)


def included_files() -> list[Path]:
    files = []
    for path in PLUGIN_DIRECTORY.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(path)
    return sorted(files)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", help="Expected tag version, with or without v")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()

    version = plugin_version()
    expected = str(args.version or version).lstrip("vV")
    if expected != version:
        raise RuntimeError(
            f"Tag version {expected!r} does not match PLUGIN_VERSION {version!r}")

    args.output.mkdir(parents=True, exist_ok=True)
    archive_path = args.output / f"RadialLayerTools-v{version}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included_files():
            relative = path.relative_to(PLUGIN_DIRECTORY)
            archive.write(path, (Path("radial_layer_tools") / relative).as_posix())

    digest = sha256(archive_path)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    print(archive_path)
    print(checksum_path)


if __name__ == "__main__":
    main()
