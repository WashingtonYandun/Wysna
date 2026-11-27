"""End-to-end tests for one-line web installers and Windows Native SDK Inno Setup."""
from __future__ import annotations

from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import threading

ROOT = Path(__file__).resolve().parents[2]


def _host_native_platform() -> str:
    sys_name = platform.system()
    machine = platform.machine().lower()
    if sys_name == "Darwin" and machine in ("arm64", "aarch64"):
        return "aarch64-macos"
    if sys_name == "Linux" and machine in ("x86_64", "amd64"):
        return "x86_64-linux"
    return "x86_64-linux"


def _make_fake_release(release_dir: Path, version: str, plat: str, corrupt_hash: bool = False) -> Path:
    pkg_root = release_dir / "wysna-native"
    (pkg_root / "bin").mkdir(parents=True, exist_ok=True)
    binary = pkg_root / "bin" / "wysna"
    binary.write_text(f"#!/bin/sh\necho 'Wysna {version} native SDK'\n", encoding="utf-8")
    binary.chmod(0o755)

    archive_name = f"wysna-{version}-native-{plat}.tar.gz"
    archive_path = release_dir / archive_name
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(pkg_root, arcname="wysna-native")

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if corrupt_hash:
        digest = "0" * 64
    (release_dir / "SHA256SUMS").write_text(f"{digest}  {archive_name}\n", encoding="utf-8")
    return archive_path


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def _run_with_server(release_dir: Path, script_args: list[str], tmp_home: Path) -> subprocess.CompletedProcess[str]:
    handler = partial(_QuietHandler, directory=str(release_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "HOME": str(tmp_home),
            "WYSNA_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        }
        return subprocess.run(
            ["sh", str(ROOT / "scripts" / "install.sh"), *script_args],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_install_sh_installs_verifies_checksum_and_uninstalls(tmp_path: Path):
    if platform.system() == "Windows":
        return
    plat = _host_native_platform()
    if (platform.system(), platform.machine().lower()) not in (
        ("Darwin", "arm64"),
        ("Darwin", "aarch64"),
        ("Linux", "x86_64"),
        ("Linux", "amd64"),
    ):
        return

    release_dir = tmp_path / "release"
    release_dir.mkdir()
    _make_fake_release(release_dir, "9.9.9", plat, corrupt_hash=False)

    home = tmp_path / "home"
    prefix = tmp_path / "prefix"
    home.mkdir()

    proc = _run_with_server(
        release_dir,
        ["--version", "v9.9.9", "--prefix", str(prefix), "--no-modify-path"],
        home,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Wysna 9.9.9 native SDK" in proc.stdout
    installed_bin = prefix / "bin" / "wysna"
    assert installed_bin.is_symlink()
    assert (prefix / "share" / "wysna" / "bin" / "wysna").is_file()

    uninst = subprocess.run(
        ["sh", str(ROOT / "scripts" / "install.sh"), "--prefix", str(prefix), "--uninstall"],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "HOME": str(home)},
    )
    assert uninst.returncode == 0, uninst.stderr
    assert not installed_bin.exists()
    assert not (prefix / "share" / "wysna").exists()


def test_install_sh_rejects_checksum_mismatch(tmp_path: Path):
    if platform.system() == "Windows":
        return
    if (platform.system(), platform.machine().lower()) not in (
        ("Darwin", "arm64"),
        ("Darwin", "aarch64"),
        ("Linux", "x86_64"),
        ("Linux", "amd64"),
    ):
        return

    plat = _host_native_platform()
    release_dir = tmp_path / "release_bad"
    release_dir.mkdir()
    _make_fake_release(release_dir, "9.9.9", plat, corrupt_hash=True)

    home = tmp_path / "home"
    prefix = tmp_path / "prefix"
    home.mkdir()

    proc = _run_with_server(
        release_dir,
        ["--version", "v9.9.9", "--prefix", str(prefix), "--no-modify-path"],
        home,
    )
    assert proc.returncode != 0
    assert "SHA-256 mismatch" in proc.stderr
    assert not (prefix / "bin" / "wysna").exists()


def test_windows_installer_scripts_and_iss_contracts():
    ps1 = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    for token in (
        "SecurityProtocolType]::Tls12",
        "Get-FileHash",
        "SHA256SUMS",
        "WYSNA_DIST",
        "WYSNA_UNINSTALL",
        "wysna-native",
        "wysna.exe",
    ):
        assert token in ps1, token

    iss = (ROOT / "packaging" / "windows" / "wysna_native.iss").read_text(encoding="utf-8")
    for token in (
        "PrivilegesRequired=lowest",
        "{localappdata}\\Programs\\Wysna",
        "{app}\\bin",
        "open-terminal.cmd",
        "UninstallDisplayIcon={app}\\bin\\wysna.exe",
    ):
        assert token in iss, token
