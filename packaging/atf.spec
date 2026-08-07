# PyInstaller spec. Build with: pyinstaller packaging/atf.spec
#
# One directory, not one file, and that is a decision rather than a default.
#
# The engine's built-in tools and adapters are shell scripts that it executes as
# subprocesses. In a one-file build they would be unpacked into a fresh temporary
# directory on every run: slower to start, dependent on the extraction preserving the
# executable bit, and read_file's workspace sandbox resolves paths with realpath, which
# a per-run temp directory makes needlessly confusing. One directory keeps them as real
# files next to the executable, where they can be inspected and where their permissions
# are whatever the build put there.
#
# What this binary does NOT remove is the shell dependency. The components call bash,
# jq, openssl, xxd, sha256sum, awk, realpath and coreutils, and the claude_code adapter
# needs the claude CLI. Freezing Python does not bundle any of those. It removes the
# need for a Python environment, which is a smaller but real benefit.

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# The built-in search layer, tools/, is package data of `builtin`. Adapters are not here:
# they are Python modules, reached through a static import in adapters/__init__.py, so the
# analysis pass follows them like any other code.
# collect_data_files reads it from the installed distribution rather than from a path
# relative to this file, so the bundle cannot disagree with what the wheel declares, and
# it lands at builtin/ inside the bundle: the same place relative to `paths` that it
# occupies when installed. That is what lets the resolver find it with one code path
# instead of a frozen special case.
datas = collect_data_files("builtin")

# The shell completion snippets, which `atf completion` reads and prints. Package data of
# `cli` for the same reason: this lands them at cli/completions/ inside the bundle, which is
# where cli/complete.py looks relative to its own module path.
datas += collect_data_files("cli", includes=["completions/*.sh"])

# jsonschema reads its own distribution metadata at import time; without this the import
# fails inside the bundle with a PackageNotFoundError.
datas += copy_metadata("jsonschema")

a = Analysis(
    # The installed distribution's entry point, not the checkout's development shim.
    ["entry.py"],
    datas=datas,
    hiddenimports=[
        # Pulled in by name rather than by a visible import.
        "jsonschema.validators",
        # util/ is reached only through lazy imports inside commands/flows.py's diagram and
        # graph commands, so the analysis pass cannot see it.
        "util.mermaid",
        "util.graph",
        "cli.progress",
    ],
    hookspath=[],
    excludes=[
        # Nothing here needs a GUI or a test runner; excluding them keeps the bundle
        # from quietly gaining tkinter.
        "tkinter",
        "unittest",
        "pydoc",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="atf",
    console=True,
    strip=False,
    upx=False,  # UPX trips antivirus heuristics and saves little on a CLI this size
)

COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="atf",
)
