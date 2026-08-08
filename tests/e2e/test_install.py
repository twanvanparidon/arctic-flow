"""`atf` as an installed command, rather than a path into a build directory.

`install.sh` does three things with the artefact: verifies a checksum, unpacks the whole
directory under `<prefix>/lib`, and links only the executable into `<prefix>/bin`. The link
is where the interesting failure lives. A PyInstaller one-directory build finds its bundle
relative to the executable, and reaching that executable through a symlink from somewhere
else on the filesystem is exactly the case that has to keep working. Nothing before this
suite runs the binary from anywhere but where it was built.

`release.sh` is driven for real rather than imitated, so what is checked is the archive that
gets uploaded: its name, its checksum, and that what comes out of it runs.

**Deferred, and named here because nothing else covers it.** `install.sh`'s own download is
not exercised: `curl` off `/releases/latest`, the redirect that keeps a prerelease off the
default, the published asset names and the published checksum. They need a release that
exists, and this suite runs on the tag *before* the release job publishes one. Reaching the
network from a pre-release gate would make it fail for reasons that are not about the build.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from .conftest import REPOSITORY, requires

RELEASE_SCRIPT = REPOSITORY / "packaging" / "release.sh"
PLATFORM = "linux-x86_64"


@pytest.fixture(scope="session")
def version(binary: Path) -> str:
    printed = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True
    ).stdout
    return printed.split()[1]


@pytest.fixture
def packaged(binary: Path, tmp_path: Path) -> Path:
    """Run the real release script against the built binary, into a temporary directory."""
    requires("bash", "tar", "sha256sum")
    out = tmp_path / "release"
    result = subprocess.run(
        [
            "bash",
            str(RELEASE_SCRIPT),
            "--dist",
            str(binary.parent),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return out


class TestWhatReleaseShipProduces:
    def test_the_archive_is_named_for_its_version_and_platform(
        self, packaged: Path, version: str
    ) -> None:
        """`install.sh` builds this name from the tag, so the two have to agree."""
        assert (packaged / f"atf-{version}-{PLATFORM}.tar.gz").is_file()

    def test_the_checksum_verifies(self, packaged: Path, version: str) -> None:
        """Written with a bare filename so `sha256sum -c` works from the same directory,
        which is how install.sh runs it."""
        name = f"atf-{version}-{PLATFORM}.tar.gz"
        result = subprocess.run(
            ["sha256sum", "-c", f"{name}.sha256"],
            cwd=packaged,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_archive_holds_the_binary_where_install_sh_looks_for_it(
        self, packaged: Path, version: str
    ) -> None:
        with tarfile.open(packaged / f"atf-{version}-{PLATFORM}.tar.gz") as archive:
            assert "atf/atf" in archive.getnames()

    def test_the_executable_bit_survives_the_archive(
        self, packaged: Path, version: str, tmp_path: Path
    ) -> None:
        """This is why the workflow tars the artefact before uploading it: upload-artifact
        zips its input and drops the mode, leaving the release job a binary it cannot run."""
        unpacked = tmp_path / "unpacked"
        unpacked.mkdir()
        with tarfile.open(packaged / f"atf-{version}-{PLATFORM}.tar.gz") as archive:
            archive.extractall(unpacked, filter="data")
        assert os.access(unpacked / "atf" / "atf", os.X_OK)


class TestInstalledTheWayInstallShInstallsIt:
    @pytest.fixture
    def installed(self, packaged: Path, version: str, tmp_path: Path) -> Path:
        """`<prefix>/lib/atf` unpacked whole, `<prefix>/bin/atf` a link to the executable."""
        prefix = tmp_path / "prefix"
        library = prefix / "lib"
        binaries = prefix / "bin"
        library.mkdir(parents=True)
        binaries.mkdir(parents=True)
        with tarfile.open(packaged / f"atf-{version}-{PLATFORM}.tar.gz") as archive:
            archive.extractall(library, filter="data")
        link = binaries / "atf"
        link.symlink_to(library / "atf" / "atf")
        return link

    def test_it_runs_through_the_link(self, installed: Path, version: str) -> None:
        """The bundle is next to the real executable, not next to the link. PyInstaller has
        to resolve the one from the other, and this is the only place that is ever true."""
        result = subprocess.run(
            [str(installed), "--version"], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"atf {version}\n"

    def test_it_still_finds_its_built_ins_through_the_link(
        self, installed: Path, tmp_path: Path
    ) -> None:
        result = subprocess.run(
            [str(installed), "--workspace", str(tmp_path), "list"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "common/read_file" in result.stdout
        assert "echo" in result.stdout

    def test_it_runs_from_a_directory_that_has_nothing_to_do_with_it(
        self, installed: Path, examples: Path, tmp_path: Path
    ) -> None:
        """How a person actually uses it: `atf` on PATH, cwd wherever they happen to be."""
        requires("jq", "openssl", "xxd", "awk", "realpath")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = subprocess.run(
            [
                "atf",
                "--workspace",
                str(examples / "sign-release"),
                "run",
                "sign_release",
                "--input",
                "path=release-notes.md",
            ],
            cwd=elsewhere,
            capture_output=True,
            text=True,
            timeout=120,
            env={
                **os.environ,
                "PATH": f"{installed.parent}{os.pathsep}{os.environ['PATH']}",
                "ATF_VAULT_PASSWORD": "demo",
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().endswith("release-notes.md")
