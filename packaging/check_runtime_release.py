"""Verify independently published OCP wheels and standalone OCCT runtime assets."""

import argparse
import json
import posixpath
import tarfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from build_runtime import (
    LINUX_FMT,
    TAG,
    WINDOWS_SYSTEM_PACKAGES,
    WINDOWS_SYSTEM_REQUIREMENTS,
)
from build_wheel import OCCT, VERSION, sha256


def check_wheel(folder: Path, platform: str) -> None:
    """Verify the published wheel's hash and native dependency provenance."""
    name = f"cadquery_ocp_novtk-{VERSION}-cp313-cp313-{platform}.whl"
    wheel = folder / name
    assert {p.name for p in folder.iterdir()} == {name, name + ".provenance.json"}
    proof = json.loads((folder / (name + ".provenance.json")).read_text(encoding="utf-8"))
    build, digest = OCCT[platform]
    assert proof["wheel"] == name and proof["wheel_sha256"] == sha256(wheel)
    assert proof["occt"]["build"] == build and proof["occt"]["sha256"] == digest
    assert proof["occt"]["filename"] == f"occt-8.0.1-{build}.conda"
    assert proof["tag"] == f"cp313-cp313-{platform}"
    with ZipFile(wheel) as archive:
        assert archive.testzip() is None
    print(f"Verified independently released {platform} OCP wheel: {wheel.name}")


def check_runtime(folder: Path, selected_platform: str | None = None) -> None:
    """Reject incomplete, unsafe, unlicensed or mismatched native archives."""
    names = {
        platform: f"{TAG}-{platform}.{'zip' if platform == 'win_amd64' else 'tar.gz'}"
        for platform in OCCT if selected_platform is None or platform == selected_platform
    }
    expected = {name for name in names.values()} | {name + ".provenance.json" for name in names.values()}
    assert {p.name for p in folder.iterdir()} == expected, "expected native archives and adjacent provenance"
    for platform, name in names.items():
        proof = json.loads((folder / (name + ".provenance.json")).read_text(encoding="utf-8"))
        assert proof["archive"] == name and proof["archive_sha256"] == sha256(folder / name)
        assert proof["platform"] == platform and proof["wheel_version"] == VERSION
        build, digest = OCCT[platform]
        assert proof["occt"]["build"] == build and proof["occt"]["sha256"] == digest
        assert proof["occt"]["filename"] == f"occt-8.0.1-{build}.conda"
        packages = proof["packages"]
        assert len({p["name"] for p in packages}) == len(packages)
        occt = next(p for p in packages if p["name"] == "occt")
        assert occt["sha256"] == digest and occt["build"] == build
        if platform == "win_amd64":
            assert proof["system_requirements"] == list(WINDOWS_SYSTEM_REQUIREMENTS)
            excluded = proof["excluded_packages"]
            assert {p["name"] for p in excluded} == set(WINDOWS_SYSTEM_PACKAGES)
            assert not any(p["name"] in WINDOWS_SYSTEM_PACKAGES for p in packages)
            for package in excluded:
                assert package["url"].startswith("https://conda.anaconda.org/conda-forge/")
                assert len(package["sha256"]) == 64
        else:
            assert proof["system_requirements"] == [] and proof["excluded_packages"] == []
        if platform == "linux_x86_64":
            fmt = next(p for p in packages if p["name"] == "fmt")
            assert (fmt["version"], fmt["build"], fmt["sha256"]) == LINUX_FMT
        for package in packages:
            assert package["url"].startswith("https://conda.anaconda.org/conda-forge/")
            assert len(package["sha256"]) == 64 and package["license"]
            assert not any(term in package["name"].lower() for term in ("vtk", "ffmpeg", "jbig"))
        if platform == "win_amd64":
            with ZipFile(folder / name) as archive:
                assert archive.testzip() is None
                members = archive.namelist()
                embedded = json.loads(archive.read("provenance.json"))
        else:
            with tarfile.open(folder / name, "r:gz") as archive:
                members = []
                embedded = None
                for member in archive:
                    assert member.isfile() or member.isdir() or member.issym(), member.name
                    if member.issym():
                        target = posixpath.normpath(
                            posixpath.join(posixpath.dirname(member.name), member.linkname)
                        )
                        assert not member.linkname.startswith("/") and target != ".." and not target.startswith("../"), member.name
                    if member.name == "provenance.json":
                        embedded = json.load(archive.extractfile(member))
                    members.append(member.name)
        assert embedded is not None and embedded == {
            key: value for key, value in proof.items() if key != "archive_sha256"
        }, "embedded provenance differs from adjacent asset"
        for member in members:
            path = PurePosixPath(member)
            assert not path.is_absolute() and ".." not in path.parts and path.parts
            assert not any(term in member.lower() for term in ("vtk", "ffmpeg", "jbig")), member
            if platform == "win_amd64" and member.lower().endswith(".dll"):
                assert not path.name.lower().startswith((
                    "msvcp", "vcruntime", "concrt", "vcomp", "api-ms-win-crt", "ucrtbase"
                )), f"Microsoft redistributable DLL must not be published: {member}"
        listed = set(members)
        assert len(listed) == len(members), "duplicate archive member"
        assert any(p.startswith("fonts/") for p in listed), "bundled font resources missing"
        for package in packages:
            assert set(package["files"]) <= listed, f"missing payload for {package['name']}"
            licenses = set(package["license_files"])
            if package["files"]:
                assert licenses, f"missing license notice for {package['name']}"
            assert licenses <= listed and set(package["license_sources"]) == licenses
            assert all(path.startswith(f"licenses/{package['name']}/") for path in licenses)
        assert any(p.startswith("licenses/occt/") for p in members), "OCCT license notice missing"
        native_dir = "Library/bin/TK" if platform == "win_amd64" else "lib/libTK"
        assert any(p.startswith(native_dir) for p in members), f"OCCT libraries missing from {name}"
        assert not any(p.startswith(("conda-meta/", "lib/python", "Lib/site-packages/")) for p in members)
        print(f"Verified {platform} native archive, {len(packages)} packages, {len(members)} entries")


def main() -> None:
    """Validate one platform wheel or both platform runtime release assets."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wheel-dir", type=Path)
    group.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--platform", choices=tuple(OCCT))
    args = parser.parse_args()
    if args.wheel_dir:
        if not args.platform:
            parser.error("--platform is required with --wheel-dir")
        check_wheel(args.wheel_dir, args.platform)
    else:
        check_runtime(args.runtime_dir, args.platform)


if __name__ == "__main__":
    main()
