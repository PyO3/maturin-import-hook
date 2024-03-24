import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from typing import Any, Optional

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
    maturin_args: list[str]
    maturin_output: str

    def to_json(self) -> dict[str, Any]:
        return {
            "build_mtime": self.build_mtime,
            "source_path": str(self.source_path),
            "maturin_args": self.maturin_args,
            "maturin_output": self.maturin_output,
        }

    @staticmethod
    def from_json(json_data: dict[Any, Any]) -> Optional["BuildStatus"]:
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
        self._build_dir = build_dir if build_dir is not None else get_default_build_dir()
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


def get_default_build_dir() -> Path:
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


def find_maturin(lower_version: tuple[int, int, int], upper_version: tuple[int, int, int]) -> Path:
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


def get_maturin_version(maturin_path: Path) -> tuple[int, int, int]:
    success, output = run_maturin(maturin_path, ["--version"])
    if not success:
        msg = f'running "{maturin_path} --version" failed'
        raise MaturinError(msg)
    match = re.fullmatch(r"maturin ([0-9]+)\.([0-9]+)\.([0-9]+)\n", output)
    if match is None:
        msg = f'unexpected version string: "{output}"'
        raise MaturinError(msg)
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def run_maturin(maturin_path: Path, args: list[str]) -> tuple[bool, str]:
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
        stripped_output = output.rstrip("\n")
        logger.debug(
            "maturin output (has warnings: %r):%s%s",
            maturin_output_has_warnings(output),
            "\n" if "\n" in stripped_output else " ",
            stripped_output,
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


@dataclass
class Freshness:
    is_fresh: bool
    reason: str
    oldest_installed_path: Optional[Path]
    newest_source_path: Optional[Path]


def get_installation_freshness(
    source_paths: Iterable[Path],
    installed_paths: Iterable[Path],
    build_status: BuildStatus,
) -> Freshness:
    """
    determine whether an installed package or extension module is 'fresh', meaning that it is newer than any of the
    source files that it is derived from and matches the metadata of the last build by the import hook.

    Args:
        source_paths: an iterable of *file* paths that should trigger a rebuild if any are newer than any installed path
        installed_paths: an iterable of *file* paths that should trigger a rebuild if any are older than any source path
        build_status: the metadata of the last build, to compare with the installed paths
    """
    debug_enabled = logger.isEnabledFor(logging.DEBUG)

    try:
        oldest_installed_path, installation_mtime = min(
            ((path, path.stat().st_mtime) for path in installed_paths), key=itemgetter(1)
        )
    except ValueError:
        return Freshness(False, "no installed files found", None, None)
    except OSError as e:
        # non-fatal as perhaps rebuilding will clear out bad files,
        # but this could also be turned into an error
        logger.error("error reading installed file mtimes: %r (%s)", e, e.filename)
        return Freshness(False, "failed to read installed files", None, None)

    if debug_enabled:
        logger.debug("oldest installed file: %s (at %f)", oldest_installed_path, installation_mtime)

    if abs(build_status.build_mtime - installation_mtime) > 5e-3:
        return Freshness(False, "installation mtime does not match build status mtime", oldest_installed_path, None)

    try:
        newest_source_path, source_mtime = max(
            ((path, path.stat().st_mtime) for path in source_paths), key=itemgetter(1)
        )
    except ValueError:
        msg = "no source files found"
        raise ImportHookError(msg) from None
    except OSError as e:
        # fatal because a build is unlikely to succeed anyway,
        # but this could also be turned into a non-fatal log message
        msg = f"error reading source file mtimes: {e!r} ({e.filename})"
        raise ImportHookError(msg) from None

    if debug_enabled:
        logger.debug("newest source file: %s (at %f)", newest_source_path, source_mtime)

    if installation_mtime == source_mtime:
        # writes made in quick succession often result in exactly identical mtimes because the resolution of the mtime
        # timer is not always very high (eg 3ms on a sample Linux machine in tmpfs and ext4). Some filesystems only have
        # resolution of ~1 second so this edge case is worth considering.
        return Freshness(False, "installation may be out of date", oldest_installed_path, newest_source_path)
    elif installation_mtime < source_mtime:
        return Freshness(False, "installation is out of date", oldest_installed_path, newest_source_path)
    else:
        return Freshness(True, "", oldest_installed_path, newest_source_path)


def get_installation_mtime(installed_paths: Iterable[Path]) -> Optional[float]:
    try:
        installation_mtime = min(path.stat().st_mtime for path in installed_paths)
    except ValueError:
        logger.debug("no installed files found")
        return None
    except OSError as e:
        logger.error("error reading installed file mtimes: %r (%s)", e, e.filename)
        return None
    else:
        return installation_mtime
