"""Check the installed wheel, its consumer and external OCCT dependencies in a clean conda env."""

import ctypes
import importlib.metadata as metadata
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


EXPECTED_VERSION = "8.0.1.0.0+catalix.1"
prefix = Path(os.environ["CONDA_PREFIX"]).resolve()
assert (sys.version_info[:2], sys.implementation.name) == ((3, 13), "cpython")
package = metadata.distribution("cadquery-ocp-novtk")
assert package.version == EXPECTED_VERSION, package.version
assert not list(package.requires or []), "wheel must not resolve another OCP distribution"
for dist in metadata.distributions():
    name = dist.metadata["Name"].lower().replace("_", "-")
    assert name not in {"cadquery-ocp", "ocp"}, f"public OCP also installed: {name}"

import OCP
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

binary = Path(OCP.__file__).resolve()
assert binary == Path(package.locate_file(binary.name)).resolve(), binary
assert binary.is_relative_to(prefix), f"OCP must be installed in the conda prefix: {binary}"
assert BRepPrimAPI_MakeBox(1, 1, 1).Shape().IsNull() is False

if sys.platform == "linux":
    libs = (prefix / "lib").resolve()
    result = subprocess.run(["ldd", str(binary)], check=True, capture_output=True, text=True).stdout
    assert "not found" not in result, result
    matches = []
    for line in result.splitlines():
        if "libTK" not in line:
            continue
        name, separator, target = line.partition("=>")
        assert separator and target.strip(), line
        location = Path(target.strip().split()[0]).resolve()
        assert location.is_relative_to(libs), f"OCCT dependency outside active conda prefix: {line}"
        matches.append(name)
    assert matches, f"no external OCCT library dependencies found:\n{result}"
elif sys.platform == "win32":
    libs = (prefix / "Library" / "bin").resolve()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    matches = []
    for candidate in libs.glob("TK*.dll"):
        handle = kernel.GetModuleHandleW(candidate.name)
        if not handle:
            continue
        filename = ctypes.create_unicode_buffer(32768)
        length = kernel.GetModuleFileNameW(handle, filename, len(filename))
        assert length and length < len(filename), candidate
        loaded = Path(filename.value).resolve()
        assert loaded.is_relative_to(libs), f"OCCT dependency outside active conda prefix: {loaded}"
        matches.append(candidate.name)
    assert matches, "no external OCCT DLLs were loaded from the active conda prefix"
else:
    raise AssertionError("unsupported platform")

import build123d

assert metadata.version("build123d") == "0.13.0"
box = build123d.Box(2, 3, 4)
assert box.is_valid and abs(box.volume - 24) < 1e-8
with TemporaryDirectory(prefix="ocp-novtk-smoke-") as temporary:
    step = Path(temporary) / "box.step"
    brep = Path(temporary) / "box.brep"
    build123d.export_step(box, step)
    build123d.export_brep(box, brep)
    from_step = build123d.import_step(step)
    from_brep = build123d.import_brep(brep)
    assert from_step.is_valid and abs(from_step.volume - 24) < 1e-6
    assert from_brep.is_valid and abs(from_brep.volume - 24) < 1e-8
print(f"OCP {package.version} / build123d {metadata.version('build123d')}; {len(matches)} external OCCT libraries from {libs}; STEP/BREP round-trip passed")
