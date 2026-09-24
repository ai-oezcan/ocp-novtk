"""Bundle the pinned conda-forge OCCT runtime and its native dependency closure.

The archive is a relocatable, conda-prefix-relative native overlay for a plain
CPython 3.13 venv. It is deliberately separate from the immutable OCP wheel.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import ntpath
import os
import re
import shutil
import sys
import sysconfig
import tarfile
import tempfile
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from build_wheel import OCCT, VERSION, sha256

TAG = "occt-runtime-8.0.1-novtk.1"
LINUX_FMT = ("12.2.0", "h76c4fd7_1", "022af33b6414620813d13c072251e96af68549657fa149fc41fbb4e71d99f115")
FORBIDDEN = ("vtk", "ffmpeg", "jbig")
MAX_ARCHIVE_SIZE = 2_000_000_000  # GitHub Release's per-asset limit is 2 GiB.
NOTICE_NAMES = ("license", "licence", "copying", "copyright", "notice")
WINDOWS_SYSTEM_PACKAGES = ("ucrt", "vc", "vc14_runtime", "vcomp14")
WINDOWS_SYSTEM_REQUIREMENTS = (
    "Windows 10 or newer (OS Universal CRT)",
    "Microsoft Visual C++ 2015-2022 Redistributable x64, installed from Microsoft",
)


def platform_tag() -> str:
    """Return the wheel-compatible native platform tag, or reject the host."""
    if sys.platform == "linux" and sysconfig.get_platform() == "linux-x86_64":
        return "linux_x86_64"
    if sys.platform == "win32" and sysconfig.get_platform() == "win-amd64":
        return "win_amd64"
    raise ValueError("only Linux x86_64 and Windows x64 are supported")


def safe_path(raw: str) -> PurePosixPath:
    """Validate a conda or archive member path without normalizing traversal."""
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ValueError(f"unsafe package path: {raw!r}")
    if raw.startswith("/") or ntpath.splitdrive(raw)[0] or any(
        part in ("", ".", "..") or ":" in part for part in raw.split("/")
    ):
        raise ValueError(f"unsafe package path: {raw!r}")
    return PurePosixPath(raw)


def native_path(path: PurePosixPath, platform: str) -> bool:
    """Select native libraries, headers and resource data, not conda tools."""
    parts = path.parts
    if parts[0] == "fonts":
        return len(parts) > 1
    if platform == "linux_x86_64":
        if parts[:2] == ("etc", "fonts"):
            return len(parts) > 2
        if parts[0] not in ("lib", "include", "share"):
            return False
        relative = parts
    else:
        if len(parts) < 3 or parts[0] != "Library" or parts[1] not in (
            "bin", "lib", "include", "share", "etc", "plugins"
        ):
            return False
        relative = parts[1:]
    if len(parts) < 2 or path.suffix.lower() in (".exe", ".bat", ".cmd", ".ps1"):
        return False
    return relative[:2] not in (("share", "doc"), ("share", "man"), ("share", "info"),
                                ("share", "cmake"), ("share", "pkgconfig"),
                                ("lib", "cmake"), ("lib", "pkgconfig"))


def records_in(prefix: Path) -> dict[str, dict[str, Any]]:
    """Index installed conda records, rejecting ambiguous package names."""
    metadata = prefix / "conda-meta"
    if not metadata.is_dir():
        raise ValueError(f"missing conda-meta in {prefix}")
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(metadata.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        name = record.get("name")
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None or name.startswith("__"):
            raise ValueError(f"invalid installed package name in {path}")
        key = name.casefold()
        if key in records:
            raise ValueError(f"duplicate installed package: {name}")
        records[key] = record
    return records


def closure(records: dict[str, dict[str, Any]], platform: str) -> list[dict[str, Any]]:
    """Follow OCCT and the wheel's extra native dependencies, ignoring virtual specs."""
    build, expected_hash = OCCT[platform]
    root = records.get("occt")
    if root is None or (root.get("version"), root.get("build"), root.get("sha256")) != (
        "8.0.1", build, expected_hash
    ):
        raise ValueError(f"expected occt 8.0.1 {build} with SHA256 {expected_hash}")
    if root.get("fn") != f"occt-8.0.1-{build}.conda":
        raise ValueError("OCCT artifact filename does not match the platform pin")
    if urlparse(root.get("url", "")).path.rsplit("/", 1)[-1] != root["fn"]:
        raise ValueError("OCCT artifact URL does not match the platform pin")
    if platform == "linux_x86_64":
        fmt = records.get("fmt")
        if fmt is None or (fmt.get("version"), fmt.get("build"), fmt.get("sha256")) != LINUX_FMT:
            raise ValueError("missing pinned libfmt.so.12 package required by Linux OCP.so")
    pending = deque(["occt", "fmt"] if platform == "linux_x86_64" else ["occt"])
    visited: set[str] = set()
    selected: list[dict[str, Any]] = []
    while pending:
        key = pending.popleft()
        if key in visited:
            continue
        if key not in records:
            raise ValueError(f"unresolved conda dependency: {key}")
        visited.add(key)
        record = records[key]
        name = record["name"]
        if any(marker in name.casefold() for marker in FORBIDDEN):
            raise ValueError(f"forbidden native dependency: {name}")
        url = record.get("url")
        parsed = urlparse(url) if isinstance(url, str) else None
        if (parsed is None or parsed.scheme != "https" or
                parsed.netloc != "conda.anaconda.org" or
                not parsed.path.startswith("/conda-forge/")):
            raise ValueError(f"package has no conda-forge URL: {name}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            c not in "0123456789abcdef" for c in digest
        ):
            raise ValueError(f"package has no SHA256 artifact provenance: {name}")
        for field in ("version", "build"):
            value = record.get(field)
            if not isinstance(value, str) or not value or safe_path(value).parts != (value,):
                raise ValueError(f"package missing or invalid {field}: {name}")
        depends = record.get("depends", [])
        if platform == "win_amd64" and key in WINDOWS_SYSTEM_PACKAGES:
            continue  # Microsoft licenses do not permit this standalone binary release.
        if not isinstance(depends, list) or any(not isinstance(spec, str) for spec in depends):
            raise ValueError(f"invalid dependencies for {name}")
        for spec in depends:
            dependency = spec.split(maxsplit=1)[0].casefold() if spec.strip() else ""
            if not dependency:
                raise ValueError(f"empty dependency of {name}")
            if not dependency.startswith("__"):
                pending.append(dependency)
        selected.append(record)
    return sorted(selected, key=lambda record: record["name"].casefold())


def owned_files(record: dict[str, Any]) -> set[PurePosixPath]:
    """Read both conda file manifests, validating every recorded path."""
    name = record["name"]
    files = record.get("files", [])
    paths_data = record.get("paths_data", {}).get("paths", [])
    if not isinstance(files, list) or not isinstance(paths_data, list):
        raise TypeError(f"invalid file manifest for {name}")
    result: set[PurePosixPath] = set()
    for raw in files:
        result.add(safe_path(raw))
    for entry in paths_data:
        if not isinstance(entry, dict) or not isinstance(entry.get("_path"), str):
            raise TypeError(f"invalid path entry for {name}")
        result.add(safe_path(entry["_path"]))
    return result


def extracted_cache(record: dict[str, Any]) -> Path | None:
    """Find the extracted conda package's info/licenses directory, if cached."""
    identity = "-".join(record[field] for field in ("name", "version", "build"))
    locations = [record.get("link", {}).get("source"), record.get("extracted_package_dir")]
    tarball = record.get("package_tarball_full_path")
    if isinstance(tarball, str):
        locations.append(str(Path(tarball).parent / identity))
    for location in locations:
        if isinstance(location, str) and Path(location).name == identity:
            cache = Path(location)
            if (cache / "info" / "licenses").is_dir():
                return cache
    return None


def notices(record: dict[str, Any], records: dict[str, dict[str, Any]], prefix: Path,
            files: set[PurePosixPath], stage: Path, occupied: set[str],
            platform: str) -> tuple[list[str], dict[str, str]]:
    """Copy authentic license notices, failing if a file-bearing package lacks one."""
    name = record["name"]
    sources: list[tuple[Path, PurePosixPath, str]] = []
    cache = extracted_cache(record)
    if cache is not None:
        license_root = cache / "info" / "licenses"
        for path in sorted(license_root.rglob("*")):
            if path.is_file():
                relative = safe_path(path.relative_to(license_root).as_posix())
                if not path.resolve().is_relative_to(license_root.resolve()):
                    raise ValueError(f"license text escapes package cache: {path}")
                sources.append((path, relative, f"conda {name} {record['version']} info/licenses/{relative}"))
    if not sources:
        for relative in sorted(files):
            filename = relative.name.casefold()
            if not (filename.startswith(NOTICE_NAMES) or
                    "licenses" in tuple(part.casefold() for part in relative.parts)):
                continue
            source = prefix.joinpath(*relative.parts)
            if not source.is_file() or not source.resolve().is_relative_to(prefix.resolve()):
                raise ValueError(f"unsafe installed license text: {source}")
            sources.append((source, relative, f"installed {name} {relative}"))
    if files and not sources and name == "libfreetype6" and record["version"] == "2.14.3":
        freetype = records.get("freetype")
        if freetype is not None and freetype.get("version") == record["version"]:
            freetype_cache = extracted_cache(freetype)
            if freetype_cache is not None:
                for filename in ("FTL.TXT", "GPLv2.TXT"):
                    relative = PurePosixPath("docs", filename)
                    source = freetype_cache / "info" / "licenses" / "docs" / filename
                    if source.is_file() and source.resolve().is_relative_to(
                        (freetype_cache / "info" / "licenses").resolve()
                    ):
                        sources.append((
                            source, relative,
                            (
                                f"conda freetype 2.14.3 info/licenses/docs/{filename} "
                                "(same-version split package)"
                            ),
                        ))
                if len(sources) != 2:
                    sources.clear()
    if files and not sources and name == "libglu" and record.get("license") == "SGI-B-2.0":
        source = Path(__file__).resolve().parent / "licenses" / "libglu-SGI-B-2.0.txt"
        if source.is_file() and source.resolve().is_relative_to(Path(__file__).resolve().parent):
            sources.append((
                source, PurePosixPath(source.name),
                (
                    "packaging/licenses/libglu-SGI-B-2.0.txt; "
                    "Debian libglu1-mesa /usr/share/doc/libglu1-mesa/copyright:5-35 "
                    "(upstream SGI 1991-2000 notice)"
                ),
            ))
    if files and not sources:
        raise ValueError(f"missing license notice for file-bearing package {name}")
    written: list[str] = []
    origins: dict[str, str] = {}
    for source, relative, origin in sources:
        destination = PurePosixPath("licenses", name) / relative
        key = destination.as_posix().casefold() if platform == "win_amd64" else destination.as_posix()
        if key in occupied:
            raise ValueError(f"duplicate license notice: {destination}")
        occupied.add(key)
        target = stage.joinpath(*destination.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_size == 0:
            raise ValueError(f"empty license notice: {source}")
        shutil.copyfile(source, target)
        written.append(destination.as_posix())
        origins[destination.as_posix()] = origin
    return written, origins


def rewrite_fontconfig(source: Path, destination: Path, prefix: Path) -> bool:
    """Replace build-prefix fontconfig paths with config-relative paths.

    The venv's etc/fonts/fonts.conf can be selected with FONTCONFIG_FILE. Its
    system font paths remain untouched; its prefix cache uses the user's XDG
    cache instead of requiring a writable venv.
    """
    config = source.read_text(encoding="utf-8")
    root = prefix.absolute()
    changed = False
    expression = re.compile(r"<(dir|cachedir|include)\b([^>]*)>([^<]*)</\1>")

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        tag, attributes, text = match.groups()
        candidate = Path(html.unescape(text.strip()))
        if not candidate.is_absolute() or not candidate.is_relative_to(root):
            return match.group()
        changed = True
        attributes = re.sub(r"""\s+prefix\s*=\s*(?:\"[^\"]*\"|'[^']*')""", "", attributes)
        if tag == "cachedir":
            return f'<cachedir{attributes} prefix="xdg">fontconfig</cachedir>'
        relative = os.path.relpath(candidate, root / "etc" / "fonts").replace(os.sep, "/")
        return f'<{tag}{attributes} prefix="relative">{html.escape(relative)}</{tag}>'

    updated = expression.sub(replace, config)
    if str(root) in updated:
        raise ValueError(f"unrelocated fontconfig build prefix in {source}")
    if changed:
        destination.write_text(updated, encoding="utf-8")
    return changed


def copy_payload(prefix: Path, stage: Path, packages: list[dict[str, Any]],
                 records: dict[str, dict[str, Any]], platform: str) -> list[dict[str, Any]]:
    """Stage precisely the native, package-owned files and their license texts."""
    owned = {record["name"]: owned_files(record) for record in packages}
    payload: dict[PurePosixPath, str] = {}
    occupied: set[str] = {"provenance.json"}
    for name, files in owned.items():
        for relative in sorted(files):
            if any(marker in relative.as_posix().casefold() for marker in FORBIDDEN):
                raise ValueError(f"forbidden bundled filename: {relative}")
            if not native_path(relative, platform):
                continue
            key = relative.as_posix().casefold() if platform == "win_amd64" else relative.as_posix()
            if key in occupied:
                raise ValueError(f"duplicate bundled filename: {relative}")
            occupied.add(key)
            payload[relative] = name
    if not any(owner == "occt" for owner in payload.values()):
        raise ValueError("OCCT has no native payload in its installed file manifest")
    root = prefix.resolve(strict=True)
    transformations: dict[str, list[str]] = {record["name"]: [] for record in packages}
    for relative in sorted(payload):
        source = prefix.joinpath(*relative.parts)
        if not source.is_file():
            raise ValueError(f"missing package file: {source}")
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"package symlink escapes prefix: {source}")
        destination = stage.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.resolve() != stage.resolve() and not destination.parent.resolve().is_relative_to(stage.resolve()):
            raise ValueError(f"package path traverses a staged symlink: {relative}")
        if source.is_symlink() and platform == "linux_x86_64":
            resolved_relative = PurePosixPath(resolved.relative_to(root).as_posix())
            if resolved_relative not in payload:
                raise ValueError(f"package symlink points outside closure: {source}")
            target = os.path.relpath(stage.joinpath(*resolved_relative.parts), destination.parent)
            destination.symlink_to(target)
        else:
            if relative.as_posix() == "etc/fonts/fonts.conf" and rewrite_fontconfig(source, destination, prefix):
                transformations[payload[relative]].append(
                    "etc/fonts/fonts.conf: replaced build-prefix font paths with config-relative paths "
                    "and cache with the user's XDG cache; select with FONTCONFIG_FILE"
                )
            else:
                shutil.copy2(source, destination)
    provenance: list[dict[str, Any]] = []
    for record in packages:
        name = record["name"]
        license_files, license_sources = notices(
            record, records, prefix, owned[name], stage, occupied, platform
        )
        provenance.append({
            "name": name,
            "version": record["version"],
            "build": record["build"],
            "url": record["url"],
            "sha256": record["sha256"],
            "license": record.get("license"),
            "license_files": license_files,
            "license_sources": license_sources,
            "transformations": transformations[name],
            "files": [relative.as_posix() for relative in sorted(payload) if payload[relative] == name],
        })
    return provenance


def make_archive(stage: Path, archive: Path, platform: str) -> None:
    """Write a prefix-relative archive with no host paths or executable tools."""
    members = sorted(stage.rglob("*"), key=lambda path: path.relative_to(stage).as_posix())
    if platform == "linux_x86_64":
        with archive.open("wb") as raw, gzip.GzipFile(
            fileobj=raw, mode="wb", mtime=0, filename="", compresslevel=6
        ) as compressed, tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
        ) as result:
            for path in members:
                info = result.gettarinfo(str(path), arcname=path.relative_to(stage).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if info.isfile():
                    with path.open("rb") as stream:
                        result.addfile(info, stream)
                else:
                    result.addfile(info)
    else:
        with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=6) as result:
            for path in members:
                if path.is_dir():
                    continue
                if path.is_symlink():
                    raise ValueError(f"Windows zip cannot encode symlinks: {path}")
                info = ZipInfo(path.relative_to(stage).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                with path.open("rb") as source, result.open(info, "w", force_zip64=True) as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)


def main() -> None:
    """Assemble the native runtime and adjacent auditable provenance JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path, help="installed conda prefix")
    parser.add_argument("--output", required=True, type=Path, help="release asset directory")
    args = parser.parse_args()
    try:
        platform = platform_tag()
        records = records_in(args.prefix)
        packages = closure(records, platform)
        if platform == "win_amd64" and any(name not in records for name in WINDOWS_SYSTEM_PACKAGES):
            raise ValueError("missing Windows system-runtime package metadata")
        filename = f"{TAG}-{platform}" + (".tar.gz" if platform == "linux_x86_64" else ".zip")
        args.output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="occt-runtime-") as workspace:
            stage = Path(workspace) / "prefix"
            stage.mkdir()
            metadata = {
                "archive": filename,
                "platform": platform,
                "wheel_version": VERSION,
                "system_requirements": list(WINDOWS_SYSTEM_REQUIREMENTS) if platform == "win_amd64" else [],
                "excluded_packages": [
                    {
                        "name": name,
                        "version": records[name]["version"],
                        "build": records[name]["build"],
                        "url": records[name]["url"],
                        "sha256": records[name]["sha256"],
                    }
                    for name in WINDOWS_SYSTEM_PACKAGES
                ] if platform == "win_amd64" else [],
                "occt": {
                    "filename": next(record["fn"] for record in packages if record["name"] == "occt"),
                    "url": next(record["url"] for record in packages if record["name"] == "occt"),
                    "sha256": OCCT[platform][1],
                    "build": OCCT[platform][0],
                },
                "packages": copy_payload(args.prefix, stage, packages, records, platform),
            }
            (stage / "provenance.json").write_text(
                json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            temporary = args.output / (filename + ".tmp")
            try:
                make_archive(stage, temporary, platform)
                if temporary.stat().st_size >= MAX_ARCHIVE_SIZE:
                    raise ValueError(f"runtime archive exceeds GitHub's 2 GiB asset limit: {temporary}")
                metadata["archive_sha256"] = sha256(temporary)
                os.replace(temporary, args.output / filename)
                (args.output / (filename + ".provenance.json")).write_text(
                    json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8"
                )
            finally:
                temporary.unlink(missing_ok=True)
        print(args.output / filename)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
