"""Reject incomplete, impure or unverifiable release assets before publication."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from zipfile import ZipFile

from build_wheel import DIST_INFO, OCCT, VERSION, sha256


folder = Path(sys.argv[1])
assert os.environ["GITHUB_REF_NAME"] == "ocp-novtk-8.0.1.0.0-novtk.1"
expected = {f"cadquery_ocp_novtk-{VERSION}-cp313-cp313-{platform}.whl" for platform in OCCT}
actual = {path.name for path in folder.glob("*.whl")}
assert actual == expected, f"expected exactly two native platform wheels: {actual}"
assert {path.name for path in folder.iterdir()} == actual | {name + ".provenance.json" for name in expected}

for name in sorted(expected):
    wheel = folder / name
    proof = json.loads((folder / (name + ".provenance.json")).read_text(encoding="utf-8"))
    platform = next(platform for platform in OCCT if name.endswith(f"-{platform}.whl"))
    build, occt_hash = OCCT[platform]
    assert proof["wheel"] == name and proof["wheel_sha256"] == sha256(wheel)
    assert proof["source_sha"] == os.environ["GITHUB_SHA"]
    assert proof["pywrap_sha"] == os.environ["PYWRAP_SHA"]
    assert proof["occt"]["sha256"] == occt_hash and proof["occt"]["build"] == build
    assert proof["occt"]["filename"] == f"occt-8.0.1-{build}.conda"
    assert proof["tag"] == f"cp313-cp313-{platform}"
    with ZipFile(wheel) as archive:
        assert archive.testzip() is None
        members = set(archive.namelist())
        extension = ".cp313-win_amd64.pyd" if platform == "win_amd64" else ".so"
        assert "OCP" + extension in members
        assert "OCP-stubs/__init__.pyi" in members
        assert f"{DIST_INFO}/licenses/LICENSE" in members
        assert all(
            member == "OCP" + extension
            or (member.startswith("OCP-stubs/") and member.endswith(".pyi"))
            or member.startswith(f"{DIST_INFO}/")
            for member in members
        ), members
        assert not any(
            Path(member).name.lower().startswith(("vtk", "ivtk"))
            or "ffmpeg" in member.lower()
            or "libtk" in Path(member).name.lower()
            for member in members
        )
        assert f"Tag: {proof['tag']}\n" in archive.read(f"{DIST_INFO}/WHEEL").decode()
        metadata = archive.read(f"{DIST_INFO}/METADATA").decode()
        assert "Name: cadquery-ocp-novtk\n" in metadata
        assert f"Version: {VERSION}\n" in metadata
        assert "License-Expression: Apache-2.0\n" in metadata
        assert "Requires-Dist: " not in metadata
        rows = list(csv.reader(io.StringIO(archive.read(f"{DIST_INFO}/RECORD").decode())))
        assert {row[0] for row in rows} == members
print("Both platform wheels and pinned OCCT provenance verified")
