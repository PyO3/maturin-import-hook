import hashlib
import json
import logging
import os
import platform
import re
import shutil
import site
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import filelock

from maturin_import_hook._logging import logger
from maturin_import_hook.error import ImportHookError, MaturinError
from maturin_import_hook.settings import MaturinSettings


@dataclass
class BuildStatus:
    """Information about the build of a project triggered by the import hook.

    Used to decide whether a project needs to be rebuilt.
    """

    build_mtime: float
    source_path: Path
    maturin_args: List[str]
    maturin_output: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "build_mtime": self.build_mtime,
            "source_path": str(self.source_path),
            "maturin_args": self.maturin_args,
            "maturin_output": self.maturin_output,
        }

    @staticmethod
    def from_json(json_data: Dict[Any, Any]) -> Optional["BuildStatus"]:
        try:
            return BuildStatus(
                build_mtime=json_data["build_mtime"],
                source_path=Path(json_data["source_path"]),
                maturin_args=json_data["maturin_args"],
                maturin_output=json_data["maturin_output"],
            )
        except KeyError:
            logger.debug("failed to parse BuildStatus from %s", json_data)
            return None


class LockedBuildCache:
    def __init__(self, build_dir: Path) -> None:
        self._build_dir = build_dir

    def _build_status_path(self, source_path: Path) -> Path:
        path_hash = hashlib.sha1(bytes(source_path)).hexdigest()
        build_status_dir = self._build_dir / "build_status"
        build_status_dir.mkdir(parents=True, exist_ok=True)
        return build_status_dir / f"{path_hash}.json"

    def store_build_status(self, build_status: BuildStatus) -> None:
        with self._build_status_path(build_status.source_path).open("w") as f:
            json.dump(build_status.to_json(), f, indent="  ")

    def get_build_status(self, source_path: Path) -> Optional[BuildStatus]:
        try:
            with self._build_status_path(source_path).open("r") as f:
                return BuildStatus.from_json(json.load(f))
        except FileNotFoundError:
            return None

    def tmp_project_dir(self, project_path: Path, module_name: str) -> Path:
        path_hash = hashlib.sha1(bytes(project_path)).hexdigest()
        return self._build_dir / "project" / f"{module_name}_{path_hash}"


class BuildCache:
    def __init__(self, build_dir: Optional[Path], lock_timeout_seconds: Optional[float]) -> None:
        self._build_dir = build_dir if build_dir is not None else _get_default_build_dir()
        self._lock = filelock.FileLock(
            self._build_dir / "lock", timeout=-1 if lock_timeout_seconds is None else lock_timeout_seconds
        )

    @contextmanager
    def lock(self) -> Generator[LockedBuildCache, None, None]:
        with _acquire_lock(self._lock):
            yield LockedBuildCache(self._build_dir)


@contextmanager
def _acquire_lock(lock: filelock.FileLock) -> Generator[None, None, None]:
    try:
        try:
            with lock.acquire(blocking=False):
                yield
        except filelock.Timeout:
            logger.info("waiting on lock %s", lock.lock_file)
            with lock.acquire():
                yield
    except filelock.Timeout:
        message = (
            f'Acquiring lock "{lock.lock_file}" timed out after {lock.timeout} seconds. '
            "If the project is still compiling and needs more time you can increase the "
            "timeout using the lock_timeout_seconds argument to import_hook.install() "
            "(or set to None to wait indefinitely)"
        )
        raise ImportHookError(message) from None


def _get_default_build_dir() -> Path:
    build_dir = os.environ.get("MATURIN_BUILD_DIR", None)
    if build_dir:
        shared_build_dir = Path(build_dir)
    elif os.access(sys.exec_prefix, os.W_OK):
        return Path(sys.exec_prefix) / "maturin_build_cache"
    else:
        shared_build_dir = _get_cache_dir() / "maturin_build_cache"
    version_string = sys.version.split()[0]
    interpreter_hash = hashlib.sha1(sys.exec_prefix.encode()).hexdigest()
    return shared_build_dir / f"{version_string}_{interpreter_hash}"


def _get_cache_dir() -> Path:
    os_name = platform.system()
    if os_name == "Linux":
        xdg_cache_dir = os.environ.get("XDG_CACHE_HOME", None)
        return Path(xdg_cache_dir) if xdg_cache_dir else Path("~/.cache").expanduser()
    elif os_name == "Darwin":
        return Path("~/Library/Caches").expanduser()
    elif os_name == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", None)
        return Path(local_app_data) if local_app_data else Path(r"~\AppData\Local").expanduser()
    else:
        logger.warning("unknown OS: %s. defaulting to ~/.cache as the cache directory", os_name)
        return Path("~/.cache").expanduser()


def build_wheel(
    maturin_path: Path,
    manifest_path: Path,
    output_dir: Path,
    settings: MaturinSettings,
) -> str:
    if "build" not in settings.supported_commands():
        msg = f'provided {type(settings).__name__} does not support the "build" command'
        raise ImportHookError(msg)
    success, output = run_maturin(
        maturin_path,
        [
            "build",
            "--manifest-path",
            str(manifest_path),
            "--interpreter",
            sys.executable,
            "--out",
            str(output_dir),
            *settings.to_args(),
        ],
    )
    if not success:
        msg = "Failed to build wheel with maturin"
        raise MaturinError(msg)
    return output


def develop_build_project(
    maturin_path: Path,
    manifest_path: Path,
    settings: MaturinSettings,
) -> str:
    if "develop" not in settings.supported_commands():
        msg = f'provided {type(settings).__name__} does not support the "develop" command'
        raise ImportHookError(msg)
    success, output = run_maturin(maturin_path, ["develop", "--manifest-path", str(manifest_path), *settings.to_args()])
    if not success:
        msg = "Failed to build package with maturin"
        raise MaturinError(msg)
    return output


# TODO(matt): remove once a maturin release can create editable installs and raise minimum supported version
def fix_direct_url(project_dir: Path, package_name: str) -> None:
    """Seemingly due to a bug, installing with `pip install -e` will write the correct entry into `direct_url.json` to
    point at the project directory, but calling `maturin develop` does not currently write this value correctly.
    """
    logger.debug("fixing direct_url for %s", package_name)
    for path in site.getsitepackages():
        dist_info = next(Path(path).glob(f"{package_name}-*.dist-info"), None)
        if dist_info is None:
            continue
        direct_url_path = dist_info / "direct_url.json"
        try:
            with direct_url_path.open() as f:
                direct_url = json.load(f)
        except OSError:
            continue
        url = project_dir.as_uri()
        if direct_url.get("url") != url:
            logger.debug("fixing direct_url.json for package %s", package_name)
            logger.debug('"%s" -> "%s"', direct_url.get("url"), url)
            direct_url = {"dir_info": {"editable": True}, "url": url}
            try:
                with direct_url_path.open("w") as f:
                    json.dump(direct_url, f)
            except OSError:
                return


def find_maturin(lower_version: Tuple[int, int, int], upper_version: Tuple[int, int, int]) -> Path:
    logger.debug("searching for maturin")
    maturin_path_str = shutil.which("maturin")
    if maturin_path_str is None:
        msg = "maturin not found"
        raise MaturinError(msg)
    maturin_path = Path(maturin_path_str)
    logger.debug('found maturin at: "%s"', maturin_path)
    version = get_maturin_version(maturin_path)
    if lower_version <= version < upper_version:
        logger.debug('maturin at: "%s" has version %s which is compatible with the import hook', maturin_path, version)
        return maturin_path
    else:
        msg = f"unsupported maturin version: {version}. Import hook requires >={lower_version},<{upper_version}"
        raise MaturinError(msg)


def get_maturin_version(maturin_path: Path) -> Tuple[int, int, int]:
    success, output = run_maturin(maturin_path, ["--version"])
    if not success:
        msg = f'running "{maturin_path} --version" failed'
        raise MaturinError(msg)
    match = re.fullmatch(r"maturin ([0-9]+)\.([0-9]+)\.([0-9]+)\n", output)
    if match is None:
        msg = f'unexpected version string: "{output}"'
        raise MaturinError(msg)
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def run_maturin(maturin_path: Path, args: List[str]) -> Tuple[bool, str]:
    command = [str(maturin_path), *args]
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("running command: %s", subprocess.list2cmdline(command))
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    output = result.stdout.decode()
    if result.returncode != 0:
        logger.error(f'command "{subprocess.list2cmdline(command)}" returned non-zero exit status: {result.returncode}')
        logger.error("maturin output:\n%s", output)
        return False, output
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "maturin output (has warnings: %r):\n%s",
            maturin_output_has_warnings(output),
            output,
        )
    return True, output


def build_unpacked_wheel(maturin_path: Path, manifest_path: Path, output_dir: Path, settings: MaturinSettings) -> str:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output = build_wheel(maturin_path, manifest_path, output_dir, settings)
    wheel_path = _find_single_file(output_dir, ".whl")
    if wheel_path is None:
        msg = "failed to generate wheel"
        raise MaturinError(msg)
    with zipfile.ZipFile(wheel_path, "r") as f:
        f.extractall(output_dir)
    return output


def _find_single_file(dir_path: Path, extension: Optional[str]) -> Optional[Path]:
    if dir_path.exists():
        candidate_files = [p for p in dir_path.iterdir() if extension is None or p.suffix == extension]
    else:
        candidate_files = []
    return candidate_files[0] if len(candidate_files) == 1 else None


def maturin_output_has_warnings(output: str) -> bool:
    return re.search(r"`.*` \((lib|bin)\) generated [0-9]+ warnings?", output) is not None
