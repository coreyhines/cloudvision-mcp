"""deploy/ scripts leave alone an install that another deployer manages.

The guard lives in deploy/lib.sh. It matches the first line that
write_caddyfile renders, so these tests call the shell functions directly.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
LIB = DEPLOY / "lib.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_lib(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "{LIB}"; {script}', "test", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def render(path: Path) -> None:
    result = run_lib(
        'write_caddyfile "$1" mcp.example.com basic mcp "$2" ""',
        str(path),
        "$2a$14$hash",
    )
    assert result.returncode == 0, result.stderr


def test_missing_caddyfile_is_ours(tmp_path: Path) -> None:
    result = run_lib('caddyfile_managed_elsewhere "$1"', str(tmp_path / "Caddyfile"))
    assert result.returncode == 1


def test_rendered_caddyfile_is_ours(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    render(caddyfile)
    result = run_lib('caddyfile_managed_elsewhere "$1"', str(caddyfile))
    assert result.returncode == 1


def test_foreign_caddyfile_is_managed_elsewhere(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("# Deployed by scripts/deploy.py\nexample.com {\n}\n")
    result = run_lib('caddyfile_managed_elsewhere "$1"', str(caddyfile))
    assert result.returncode == 0


def test_require_own_install_refuses_foreign(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("# Deployed by scripts/deploy.py\n")
    result = run_lib(
        'require_own_install "$1" install.sh 0; echo reached', str(caddyfile)
    )
    assert result.returncode == 1
    assert "reached" not in result.stdout
    assert "managed by another deployer" in result.stderr
    assert "# Deployed by scripts/deploy.py" in result.stderr


def test_require_own_install_force_takes_over(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("# Deployed by scripts/deploy.py\n")
    result = run_lib(
        'require_own_install "$1" install.sh 1; echo reached', str(caddyfile)
    )
    assert result.returncode == 0
    assert "reached" in result.stdout
    assert "--force given" in result.stderr


def test_require_own_install_allows_ours(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    render(caddyfile)
    result = run_lib(
        'require_own_install "$1" install.sh 0; echo reached', str(caddyfile)
    )
    assert result.returncode == 0
    assert "reached" in result.stdout


def test_empty_caddyfile_is_managed_elsewhere(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("")
    result = run_lib('caddyfile_managed_elsewhere "$1"', str(caddyfile))
    assert result.returncode == 0


@pytest.mark.parametrize(
    "script", ["install.sh", "rotate-basic-auth.sh", "uninstall.sh"]
)
def test_scripts_call_the_guard(script: str) -> None:
    text = (DEPLOY / script).read_text()
    assert "require_own_install" in text


@pytest.mark.parametrize(
    ("script", "first_change"),
    [
        ("install.sh", "\nresolve_src_dir\n"),
        ("rotate-basic-auth.sh", "\nwrite_caddyfile \\\n"),
        ("uninstall.sh", "\nfor svc in "),
    ],
)
def test_guard_runs_before_any_change(script: str, first_change: str) -> None:
    text = (DEPLOY / script).read_text()
    guard = text.index("\nrequire_own_install ")
    assert guard < text.index(first_change)
