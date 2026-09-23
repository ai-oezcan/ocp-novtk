"""Assemble an ABI-specific wheel from the *locally compiled* OCP module and stubs.

OCCT is deliberately external: it must be installed in the active conda prefix.
This is not an auditwheel/manylinux wheel and must never vendor shared libraries.
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import sysconfig
from zipfile import ZIP_DEFLATED, ZipFile


NAME = "cadquery-ocp-novtk"
VERSION = "8.0.1.0.0+novtk.1"
DIST_INFO = f"cadquery_ocp_novtk-{VERSION}.dist-info"
OCCT = {
    "linux_x86_64": ("novtk_h6e372de_101", "a614d7625a2e2278ff877eea340f1a3d980c691e0d3f8522a6da3fd28c38b2d4"),
    "win_amd64": ("novtk_h6bfc850_101", "a87336f2f318be46188479ad593b8b0c3129b7f2e25f5e7fd97ec0c7efc2f4eb"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--stubs", type=Path, required=True)
    parser.add_argument("--license", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--pywrap-sha", required=True)
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 13) or sys.implementation.name != "cpython":
        parser.error("wheels must be assembled with CPython 3.13")
    if sys.platform == "linux" and sysconfig.get_platform() == "linux-x86_64":
        platform, suffix = "linux_x86_64", ".so"
    elif sys.platform == "win32" and sysconfig.get_platform() == "win-amd64":
        platform, suffix = "win_amd64", ".cp313-win_amd64.pyd"
    else:
        parser.error("only Linux x86_64 and Windows x64 are supported")
    if args.binary.name != "OCP" + suffix or not args.binary.is_file():
        parser.error(f"expected a freshly compiled OCP{suffix} module")
    if not args.license.is_file() or not args.stubs.is_dir():
        parser.error("generated stubs and Apache-2.0 LICENSE are required")
    stubs = sorted(args.stubs.rglob("*.pyi"))
    if not stubs or not (args.stubs / "__init__.pyi").is_file():
        parser.error("expected generated OCP-stubs/__init__.pyi and submodule stubs")
    for source_hash in (args.source_sha, args.pywrap_sha):
        if len(source_hash) != 40 or any(c not in "0123456789abcdef" for c in source_hash):
            parser.error("source commits must be full 40-character lowercase git SHAs")

    prefix = Path(os.environ["CONDA_PREFIX"])
    build, expected_hash = OCCT[platform]
    records = list((prefix / "conda-meta").glob(f"occt-8.0.1-{build}.json"))
    if len(records) != 1:
        parser.error(f"active conda prefix must contain occt 8.0.1 {build}")
    record = json.loads(records[0].read_text(encoding="utf-8"))
    if record.get("name") != "occt" or record.get("version") != "8.0.1" or record.get("build") != build:
        parser.error("active OCCT package record does not match the platform pin")
    if record.get("sha256") != expected_hash:
        parser.error("active OCCT package SHA256 does not match the pinned conda-forge artifact")
    if not str(record.get("url", "")).startswith("https://conda.anaconda.org/conda-forge/"):
        parser.error("OCCT must come from conda-forge")

    tag = f"cp313-cp313-{platform}"
    filename = f"cadquery_ocp_novtk-{VERSION}-{tag}.whl"
    args.output.mkdir(parents=True, exist_ok=True)
    wheel = args.output / filename
    metadata = (
        "Metadata-Version: 2.4\n"
        f"Name: {NAME}\nVersion: {VERSION}\n"
        "Summary: CPython OCP bindings for external conda-forge OCCT 8.0.1 novtk\n"
        "Requires-Python: >=3.13,<3.14\n"
        "Requires-External: occt (8.0.1 novtk)\n"
        "License-Expression: Apache-2.0\n"
        "License-File: LICENSE\n"
    ).encode()
    wheel_metadata = f"Wheel-Version: 1.0\nGenerator: ocp-novtk native builder\nRoot-Is-Purelib: false\nTag: {tag}\n".encode()
    files = {"OCP" + suffix: args.binary.read_bytes()}
    for stub in stubs:
        files[(Path("OCP-stubs") / stub.relative_to(args.stubs)).as_posix()] = stub.read_bytes()
    files[f"{DIST_INFO}/licenses/LICENSE"] = args.license.read_bytes()
    files[f"{DIST_INFO}/METADATA"] = metadata
    files[f"{DIST_INFO}/WHEEL"] = wheel_metadata
    record_path = f"{DIST_INFO}/RECORD"
    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    for name, data in sorted(files.items()):
        if name.startswith("/") or ".." in Path(name).parts:
            parser.error(f"unsafe archive path: {name}")
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow((record_path, "", ""))
    files[record_path] = record_buffer.getvalue().encode()
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)

    provenance = {
        "wheel": filename,
        "wheel_sha256": sha256(wheel),
        "source_sha": args.source_sha,
        "pywrap_sha": args.pywrap_sha,
        "occt": {"filename": record.get("fn"), "url": record["url"], "sha256": expected_hash, "build": build},
        "tag": tag,
    }
    (args.output / (filename + ".provenance.json")).write_text(
        json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(wheel)


if __name__ == "__main__":
    main()
