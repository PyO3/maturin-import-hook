"""
Utilities for interacting with python virtual environments and packages
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class PackageInstallerBackend(Enum):
    PIP = "pip"
    UV = "uv"

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def from_env() -> PackageInstallerBackend:
        # set by `runner.py`
        key = "MATURIN_IMPORT_HOOK_TEST_PACKAGE_INSTALLER"
        if key not in os.environ:
            msg = f"environment variable {key} not set"
            raise RuntimeError(msg)
        return PackageInstallerBackend(os.environ[key])


class PackageInstaller:
    def __init__(self, *, backend: PackageInstallerBackend, interpreter_path: Path) -> None:
        self._backend = backend
        self._interpreter = interpreter_path

    @staticmethod
    def from_env() -> PackageInstaller:
        return PackageInstaller(backend=PackageInstallerBackend.from_env(), interpreter_path=Path(sys.executable))

    @property
    def backend(self) -> PackageInstallerBackend:
        return self._backend

    @property
    def interpreter(self) -> Path:
        return self._interpreter

    def _pip_command(self, name: str) -> list[str]:
        """the package installers have a 'pip-compatible' interface"""
        if self._backend == PackageInstallerBackend.UV:
            return ["uv", "pip", name, "--python", str(self._interpreter)]
        elif self._backend == PackageInstallerBackend.PIP:
            return [str(self._interpreter), "-m", "pip", name, "--disable-pip-version-check"]
        else:
            raise ValueError(self)

    def install(self, project: str | Path, *, editable: bool) -> None:
        log.info(
            "using %s to install%s '%s' into '%s'",
            self._backend,
            " (editable)" if editable else "",
            project,
            self._interpreter,
        )
        cmd = self._pip_command("install")
        if editable:
            cmd += ["--editable"]
        cmd += [str(project)]
        proc = subprocess.run(cmd, capture_output=True, check=True)
        log.debug("%s", proc.stdout.decode())

    def uninstall(self, *project_names: str) -> None:
        log.info("using %s to uninstall %s from '%s'", self._backend, project_names, self._interpreter)
        cmd = self._pip_command("uninstall")
        if self._backend == PackageInstallerBackend.UV:
            cmd += [*project_names]
        elif self._backend == PackageInstallerBackend.PIP:
            cmd += ["-y", *project_names]
        else:
            raise ValueError(self)
        subprocess.check_call(cmd)

    def install_requirements_file(self, requirements_path: Path) -> None:
        log.info(
            "using %s to install requirements from '%s' into %s", self._backend, requirements_path, self._interpreter
        )
        cmd = [*self._pip_command("install"), "-r", str(requirements_path.name)]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            cwd=requirements_path.parent,
            check=False,
        )
        if proc.returncode != 0:
            log.error(proc.stdout.decode())
            log.error(proc.stderr.decode())
            msg = "package installation failed"
            raise RuntimeError(msg)
        log.debug("%s", proc.stdout.decode())

    def package_names(self) -> set[str]:
        cmd = [*self._pip_command("list"), "--format", "json"]
        packages = json.loads(subprocess.check_output(cmd).decode())
        return {package["name"] for package in packages}

    def pip_show(self, project_name: str) -> tuple[int, str]:
        """show package info in the format of 'pip show'"""
        if self._backend == PackageInstallerBackend.UV:
            # TODO(matt): use uv once the --files option is supported https://github.com/astral-sh/uv/issues/2526
            cmd = [sys.executable, "-m", "pip", "show", "--disable-pip-version-check", "-f", project_name]
        elif self._backend == PackageInstallerBackend.PIP:
            cmd = [sys.executable, "-m", "pip", "show", "--disable-pip-version-check", "-f", project_name]
        else:
            raise ValueError(self._backend)

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return proc.returncode, proc.stdout.decode()


def _create_virtual_env_command(
    interpreter_path: Path, venv_path: Path, installer_backend: PackageInstallerBackend
) -> list[str]:
    if installer_backend == PackageInstallerBackend.UV:
        log.info("using uv to create virtual environments")
        return ["uv", "venv", "--seed", "--python", str(interpreter_path), str(venv_path)]
    elif shutil.which("virtualenv") is not None:
        log.info("using virtualenv to create virtual environments")
        return ["virtualenv", "--python", str(interpreter_path), str(venv_path)]
    else:
        log.info("using venv to create virtual environments")
        return [str(interpreter_path), "-m", "venv", str(venv_path)]


def _is_windows() -> bool:
    return platform.system() == "Windows"


class VirtualEnv:
    def __init__(self, root: Path, installer_backend: PackageInstallerBackend) -> None:
        self._root = root.resolve()
        self._is_windows = _is_windows()
        self._package_installer = PackageInstaller(backend=installer_backend, interpreter_path=self.interpreter_path)

    @staticmethod
    def from_env() -> VirtualEnv:
        return VirtualEnv(Path(sys.exec_prefix), PackageInstallerBackend.from_env())

    @staticmethod
    def create(root: Path, interpreter_path: Path, installer_backend: PackageInstallerBackend) -> VirtualEnv:
        if root.exists():
            log.info("removing virtualenv at %s", root)
            shutil.rmtree(root)
        if not interpreter_path.exists():
            raise FileNotFoundError(interpreter_path)
        log.info("creating test virtualenv at '%s' from '%s'", root, interpreter_path)
        proc = subprocess.run(
            [str(interpreter_path), "-c", "import sys; print(sys.version)"], capture_output=True, check=True
        )
        log.info("python: %s", proc.stdout.decode().strip())

        cmd = _create_virtual_env_command(interpreter_path, root, installer_backend)
        proc = subprocess.run(cmd, capture_output=True, check=True)
        log.debug("%s", proc.stdout.decode())
        assert root.is_dir()
        return VirtualEnv(root, installer_backend)

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def bin_dir(self) -> Path:
        return self._root / ("Scripts" if self._is_windows else "bin")

    def script_path(self, script_name: str) -> Path:
        return self.bin_dir / (f"{script_name}.exe" if self._is_windows else script_name)

    @property
    def interpreter_path(self) -> Path:
        if self._is_windows:
            interpreter = self.bin_dir / "python.exe"
            if not interpreter.exists():
                interpreter = self.bin_dir / "python"
        else:
            interpreter = self.bin_dir / "python"
        assert interpreter.exists()
        return interpreter

    @property
    def installer(self) -> PackageInstaller:
        return self._package_installer

    def activate(self, env: dict[str, str]) -> None:
        """set the environment as-if venv/bin/activate was run"""
        path = env.get("PATH", "").split(os.pathsep)
        path.insert(0, str(self.bin_dir))
        env["PATH"] = os.pathsep.join(path)
        env["VIRTUAL_ENV"] = str(self.root_dir)
