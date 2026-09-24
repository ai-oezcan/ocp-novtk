# OCP bindings against external OCCT (no VTK)

This repository builds a derivative of [CadQuery/OCP](https://github.com/CadQuery/OCP)
8.0.1.0 with VTK bindings disabled. It produces the `cadquery-ocp-novtk`
Python distribution, which imports as `OCP` and targets **CPython 3.13** on
Linux x86_64 and Windows x64. The source and generated bindings retain the
upstream Apache-2.0 license; `pywrap` is an upstream Git submodule.

The wheel contains the compiled OCP extension and typing stubs, **not OCCT**.
It links the pinned conda-forge `occt` 8.0.1 `novtk` shared libraries. The
wheel is not manylinux or standalone: install the matching native runtime
separately, either in a conda environment or from this repository's
`occt-runtime-8.0.1-novtk.1` GitHub Release. The native archives contain the
OCCT package, its resolved conda-forge native dependency closure, headers,
license notices and per-package provenance, but no Python interpreter or OCP
wheel. Neither runtime archive bundles VTK, FFmpeg or JBIG.

## Install a released wheel

Create an environment using conda-forge only, with Python 3.13 and **one**
platform-specific OCCT build:

| Platform | OCCT conda build |
| --- | --- |
| Linux x86_64 | `occt=8.0.1=novtk_h6e372de_101` |
| Windows x64 | `occt=8.0.1=novtk_h6bfc850_101` |

Download the matching `cadquery_ocp_novtk-8.0.1.0.0+novtk.1-cp313-cp313-*.whl`
from this repository's tagged GitHub Release, verify its SHA-256 against the
adjacent `.provenance.json` asset, and install that **local wheel** with pip.
For example, on Linux:

```sh
micromamba create -n ocp-novtk -c conda-forge --override-channels \
  python=3.13 pip 'occt=8.0.1=novtk_h6e372de_101'
micromamba activate ocp-novtk
python -m pip install --no-index --no-deps ./cadquery_ocp_novtk-8.0.1.0.0+novtk.1-cp313-cp313-linux_x86_64.whl
python -c 'import OCP; print(OCP.__file__)'
```

Use the `win_amd64` wheel and Windows OCCT build on Windows. The distribution
name deliberately satisfies `build123d==0.13.0`'s
`cadquery-ocp-novtk>=8.0,<8.1` requirement. When installing build123d, pin
`cadquery-ocp-novtk==8.0.1.0.0+novtk.1` in a pip constraints file so a
resolver cannot replace the locally installed wheel with an unrelated public
distribution. Never install the separate `ocp` or `cadquery-ocp` distribution
in this environment.

## Install without micromamba at runtime

Download the matching `occt-runtime-8.0.1-novtk.1-{linux_x86_64,win_amd64}`
archive and adjacent `.provenance.json` from this repository's separate
native-runtime release. Verify `archive_sha256` and the OCCT package identity
against the provenance before extraction. Install the matching OCP wheel from
the OCP release described above; verify its own adjacent provenance first.

Extract the native archive **at the root of a plain CPython 3.13 virtual
environment**, not inside `site-packages`. This puts Linux OCCT libraries in
`<venv>/lib` alongside the existing wheel's `$ORIGIN/../..` loader path and
Windows DLLs in `<venv>/Library/bin`. Install the local OCP wheel with
`python -m pip install --no-index --no-deps` and install `build123d==0.13.0`
with the exact wheel-version constraint above. For Linux processes, set
`LD_LIBRARY_PATH=<venv>/lib` before launching Python so the OCCT libraries'
transitive dependencies resolve there, including the wheel's separately
linked, pinned `libfmt.so.12`. Set
`FONTCONFIG_FILE=<venv>/etc/fonts/fonts.conf` to use the relocated bundled
fonts; its font cache uses the user's XDG directory rather than the build
prefix. On Windows, set
`FONTCONFIG_FILE=<venv>/Library/etc/fonts/fonts.conf`, then call
`os.add_dll_directory(str(Path(sys.prefix) / "Library" / "bin"))` **before**
the first `OCP` or `build123d` import and keep the returned handle alive for
the process lifetime. A packaged launcher must perform these steps; activating
micromamba is not needed for the installed runtime.

Linux must provide glibc 2.34 or newer (the wheel was built on Ubuntu 22.04).
Bundling OCCT and its dependencies cannot replace the host C library; importing
this wheel fails on Ubuntu 20.04 with `GLIBC_2.34 not found`.

`.github/workflows/runtime.yml` resolves conda dependencies only in CI, then
extracts each archive into a relocated plain venv and checks native loader
paths and build123d STEP/BREP round-trips. These archives are a delivery input,
not an installer: Catalix has no production installer or installer integration
yet. Future installer work must verify the same extracted assets within its
actual launch and upgrade paths; this repository does not claim that test.

## Build and release

`.github/workflows/bindings.yml` is the canonical Linux/Windows build:
generate C++ bindings using the pinned `pywrap` submodule and OCCT headers,
compile each extension against platform OCCT shared libraries, generate stubs,
assemble the wheels, and smoke-test each in a fresh conda environment with
build123d. The root and generated CMake projects require
`-DOCP_ENABLE_VTK=OFF`; a VTK-enabled build is not supported here.

`environment.devenv.yml` records the build environments. The tagged workflow
checks wheel contents and the pinned OCCT artifact SHA-256 values in
`packaging/build_wheel.py`, then publishes both platform wheels with their
source-commit and OCCT provenance as public GitHub Release assets. A tag
cannot stand in for successful builds: inspect both platform jobs and release
assets before consuming it. Do not run `auditwheel repair` or vendor the OCCT
libraries into these wheels.

The separately tagged `.github/workflows/runtime.yml` release refuses to
overwrite existing assets and publishes Linux and Windows native archives
with license notices and package hashes after both platform smoke jobs pass.
It does not modify or rebuild the existing OCP wheel release.
