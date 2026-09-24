"""Smoke-test the separate native runtime with the OCP wheel in a plain CPython venv.

Run with the venv's Python after extracting the runtime into --prefix and installing
--wheel plus build123d==0.13.0. On Linux, the interpreter must be launched with
--prefix/lib on its loader path if the extension's RUNPATH does not find it.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import sysconfig
from email.parser import Parser
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

NAME = "cadquery-ocp-novtk"
VERSION = "8.0.1.0.0+novtk.1"
DIST_INFO = f"cadquery_ocp_novtk-{VERSION}.dist-info"
FORBIDDEN = re.compile(
    r"vtk|ffmpeg|jbig|(?:^|[._-])(?:lib)?(?:avcodec|avformat|avutil|avdevice|swscale|swresample|postproc)(?:[._-]|$)",
    re.IGNORECASE,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def check_native_files(prefix: Path, platform: str) -> None:
    # Never inspect pip's site-packages: wheels unrelated to this native closure
    # may legitimately contain e.g. FFmpeg or VTK. The runtime's installed native
    # files live outside the venv's Python subtree.
    roots = [prefix / "lib", prefix / "include", prefix / "share"] if platform == "linux_x86_64" else [prefix / "Library"]
    for root in roots:
        if not root.is_dir():
            continue
        for directory, directories, filenames in os.walk(root, followlinks=False):
            if root == prefix / "lib" and Path(directory) == root:
                directories[:] = [name for name in directories if not re.fullmatch(r"python\d+(?:\.\d+)?", name, re.IGNORECASE)]
            for name in (*directories, *filenames):
                relative = Path(directory, name).relative_to(prefix)
                require(not FORBIDDEN.search(name), f"prohibited native runtime file: {relative}")


def check_linux(binary: Path, libs: Path) -> int:
    require(libs.is_dir(), f"missing runtime library directory: {libs}")
    result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=True)
    listing = result.stdout + result.stderr
    require("not found" not in listing, f"unresolved native dependency:\n{listing}")
    resolved = []
    for line in result.stdout.splitlines():
        name, separator, target = line.strip().partition("=>")
        if not name.startswith("libTK"):
            continue
        require(bool(separator and target.strip()), f"unresolved OCCT dependency: {line}")
        location = Path(target.strip().split()[0]).resolve()
        require(location.is_file() and location.parent == libs, f"OCCT dependency outside runtime: {line}")
        resolved.append(location)
    require(bool(resolved), f"no external OCCT libraries resolved by ldd:\n{listing}")

    # ldd inspects the extension in another process; also inspect this process
    # after importing OCP to catch a preloaded or dynamically opened system TK.
    loaded = []
    with Path("/proc/self/maps").open(encoding="utf-8") as mappings:
        for line in mappings:
            fields = line.split(maxsplit=5)
            if len(fields) < 6:
                continue
            pathname = fields[5].strip()
            if not Path(pathname).name.startswith("libTK"):
                continue
            location = Path(pathname).resolve()
            require(location.is_file() and location.parent == libs, f"loaded OCCT library outside runtime: {pathname}")
            loaded.append(location)
    require(bool(loaded), "no OCCT libraries loaded into the consumer process")
    return len(set(loaded))


def check_windows(libs: Path) -> int:
    require(libs.is_dir(), f"missing runtime DLL directory: {libs}")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    kernel.K32EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    kernel.K32EnumProcessModules.restype = ctypes.c_int

    count = 256
    while True:
        handles = (ctypes.c_void_p * count)()
        needed = ctypes.c_uint32()
        require(
            bool(kernel.K32EnumProcessModules(kernel.GetCurrentProcess(), handles, ctypes.sizeof(handles), ctypes.byref(needed))),
            f"cannot enumerate loaded DLLs (Windows error {ctypes.get_last_error()})",
        )
        if needed.value <= ctypes.sizeof(handles):
            break
        count = needed.value // ctypes.sizeof(ctypes.c_void_p) + 1

    loaded = []
    for handle in handles[: needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        filename = ctypes.create_unicode_buffer(32768)
        length = kernel.GetModuleFileNameW(handle, filename, len(filename))
        require(bool(length and length < len(filename)), f"cannot locate loaded DLL (Windows error {ctypes.get_last_error()})")
        location = Path(filename.value).resolve()
        if not re.fullmatch(r"TK.*\.dll", location.name, re.IGNORECASE):
            continue
        require(kernel.GetModuleHandleW(str(location)) == handle, f"cannot resolve loaded OCCT DLL: {location}")
        require(location.parent == libs, f"loaded OCCT DLL outside runtime: {location}")
        loaded.append(location)
    require(bool(loaded), "no OCCT DLLs loaded into the consumer process")
    return len(set(loaded))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True, help="extracted runtime and CPython venv prefix")
    parser.add_argument("--wheel", type=Path, required=True, help="installed OCP wheel artifact")
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    wheel = args.wheel.resolve()

    require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13), "requires CPython 3.13")
    require(sys.prefix != sys.base_prefix and Path(sys.prefix).resolve() == prefix, "run with the --prefix CPython venv's Python")
    require((prefix / "pyvenv.cfg").is_file(), "--prefix must be a plain Python venv")
    require(Path(os.path.abspath(sys.executable)).is_relative_to(prefix), "Python executable must belong to --prefix")
    require(not (prefix / "conda-meta").exists() and not (Path(sys.base_prefix) / "conda-meta").exists(), "conda environment is not a plain venv")
    require("CONDA_PREFIX" not in os.environ and "CONDA_DEFAULT_ENV" not in os.environ, "conda environment variables must be absent")

    if sys.platform == "linux" and sysconfig.get_platform() == "linux-x86_64":
        platform, suffix = "linux_x86_64", ".so"
        libs = (prefix / "lib").resolve()
    elif sys.platform == "win32" and sysconfig.get_platform() == "win-amd64":
        platform, suffix = "win_amd64", ".cp313-win_amd64.pyd"
        libs = (prefix / "Library" / "bin").resolve()
    else:
        raise RuntimeError("only Linux x86_64 and Windows x64 are supported")

    expected_wheel = f"cadquery_ocp_novtk-{VERSION}-cp313-cp313-{platform}.whl"
    require(wheel.is_file() and wheel.name == expected_wheel, f"expected OCP wheel {expected_wheel}: {wheel}")
    package = metadata.distribution(NAME)
    require(package.metadata["Name"].lower().replace("_", "-") == NAME and package.version == VERSION, "installed OCP distribution does not match wheel version")
    require(not package.requires, "OCP wheel must not resolve another OCP distribution")
    for dist in metadata.distributions():
        name = dist.metadata["Name"].lower().replace("_", "-")
        require(name not in {"cadquery-ocp", "ocp"}, f"conflicting public OCP distribution: {name}")
    with ZipFile(wheel) as archive:
        wheel_metadata = Parser().parsestr(archive.read(f"{DIST_INFO}/METADATA").decode("utf-8"))
        require(wheel_metadata["Name"].lower().replace("_", "-") == NAME and wheel_metadata["Version"] == package.version, "wheel metadata does not match installed OCP distribution")
        spec = importlib.util.find_spec("OCP")
        require(spec is not None and spec.origin is not None, "OCP extension not installed")
        binary = Path(spec.origin).resolve()
        require(binary.name == "OCP" + suffix, f"unexpected OCP extension: {binary}")
        require(binary == Path(package.locate_file(binary.name)).resolve() and binary.is_relative_to(prefix), f"OCP must come from the installed venv wheel: {binary}")
        with archive.open(binary.name) as wheel_binary, binary.open("rb") as installed_binary:
            require(sha256(wheel_binary) == sha256(installed_binary), "installed OCP extension does not match --wheel")

    check_native_files(prefix, platform)
    if platform == "linux_x86_64":
        # Import after the dynamic loader's search path was set *before Python started*.
        import OCP
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    else:
        require(libs.is_dir(), f"missing runtime DLL directory: {libs}")
        dll_directory = os.add_dll_directory(str(libs))  # Keep this handle alive through the entire consumer smoke.
        import OCP
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    require(Path(OCP.__file__).resolve() == binary, "imported OCP extension is not the wheel binary")
    require(not BRepPrimAPI_MakeBox(1, 1, 1).Shape().IsNull(), "OCP cannot create a solid")

    import build123d

    require(metadata.version("build123d") == "0.13.0", "build123d 0.13.0 is required")
    box = build123d.Box(2, 3, 4)
    require(box.is_valid and abs(box.volume - 24) < 1e-8, "build123d box is invalid")
    with TemporaryDirectory(prefix="ocp-novtk-smoke-") as temporary:
        step, brep = Path(temporary) / "box.step", Path(temporary) / "box.brep"
        build123d.export_step(box, step)
        build123d.export_brep(box, brep)
        from_step = build123d.import_step(step)
        from_brep = build123d.import_brep(brep)
        require(from_step.is_valid and abs(from_step.volume - 24) < 1e-6, "STEP round-trip failed")
        require(from_brep.is_valid and abs(from_brep.volume - 24) < 1e-8, "BREP round-trip failed")
    # STEP/BREP may load additional OCCT modules: inspect the process only
    # after all consumer operations have exercised the native runtime.
    if platform == "linux_x86_64":
        count = check_linux(binary, libs)
    else:
        count = check_windows(libs)
        dll_directory.close()
    print(f"OCP {package.version} / build123d 0.13.0; {count} loaded OCCT libraries from {libs}; STEP/BREP round-trip passed")


if __name__ == "__main__":
    main()
