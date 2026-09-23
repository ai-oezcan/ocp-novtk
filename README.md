# OCP bindings against external OCCT (no VTK)

This repository builds a derivative of [CadQuery/OCP](https://github.com/CadQuery/OCP)
8.0.1.0 with VTK bindings disabled. It produces the `cadquery-ocp-novtk`
Python distribution, which imports as `OCP` and targets **CPython 3.13** on
Linux x86_64 and Windows x64. The source and generated bindings retain the
upstream Apache-2.0 license; `pywrap` is an upstream Git submodule.

The wheel contains the compiled OCP extension and typing stubs, **not OCCT**.
It links the independently installed conda-forge `occt` 8.0.1 `novtk` shared
libraries. These platform wheels are not manylinux or standalone wheels; use
them inside an activated conda environment containing the matching OCCT build.
They are intended as GitHub Release assets, not PyPI uploads. No VTK, FFmpeg,
or JBIG runtime is packaged by this repository; inspect the selected conda
packages and wheel provenance before redistribution.

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
