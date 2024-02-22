import contextlib
import importlib
import importlib.abc
import importlib.machinery
import itertools
import json
import logging
import os
import site
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from importlib.machinery import ExtensionFileLoader, ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType
from typing import Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

from maturin_import_hook._building import (
    BuildCache,
    BuildStatus,
    LockedBuildCache,
    develop_build_project,
    find_maturin,
    fix_direct_url,
    get_installation_freshness,
    get_installation_mtime,
    maturin_output_has_warnings,
)
from maturin_import_hook._logging import logger
from maturin_import_hook._resolve_project import (
    MaturinProject,
    ProjectResolver,
    is_maybe_maturin_project,
)
from maturin_import_hook.error import ImportHookError
from maturin_import_hook.settings import MaturinSettings

__all__ = [
    "MaturinProjectImporter",
    "install",
    "uninstall",
    "IMPORTER",
    "DEFAULT_EXCLUDED_DIR_NAMES",
]


DEFAULT_EXCLUDED_DIR_NAMES = {
    "__pycache__",
    "target",
    "dist",
    ".git",
    "venv",
    ".venv",
    ".pytest_cache",
}


class MaturinProjectImporter(importlib.abc.MetaPathFinder):
    """An import hook for automatically rebuilding editable installed maturin projects."""

    def __init__(
        self,
        *,
        settings: Optional[MaturinSettings] = None,
        build_dir: Optional[Path] = None,
        lock_timeout_seconds: Optional[float] = 120,
        enable_reloading: bool = True,
        enable_automatic_installation: bool = True,
        force_rebuild: bool = False,
        excluded_dir_names: Optional[Set[str]] = None,
        show_warnings: bool = True,
    ) -> None:
        self._resolver = ProjectResolver()
        self._settings = settings
        self._build_cache = BuildCache(build_dir, lock_timeout_seconds)
        self._enable_reloading = enable_reloading
        self._enable_automatic_installation = enable_automatic_installation
        self._force_rebuild = force_rebuild
        self._show_warnings = show_warnings
        self._excluded_dir_names = DEFAULT_EXCLUDED_DIR_NAMES if excluded_dir_names is None else excluded_dir_names
        self._maturin_path: Optional[Path] = None
        self._reload_tmp_path: Optional[Path] = None

    def get_settings(self, module_path: str, source_path: Path) -> MaturinSettings:
        """This method can be overridden in subclasses to customize settings for specific projects."""
        return self._settings if self._settings is not None else MaturinSettings.default()

    def find_maturin(self) -> Path:
        """this method can be overridden to specify an alternative maturin binary to use"""
        if self._maturin_path is None:
            self._maturin_path = find_maturin((1, 4, 0), (2, 0, 0))
        return self._maturin_path

    def find_spec(
        self,
        fullname: str,
        path: Optional[Sequence[Union[str, bytes]]] = None,
        target: Optional[ModuleType] = None,
    ) -> Optional[ModuleSpec]:
        is_top_level_import = path is None
        if not is_top_level_import:
            return None
        assert "." not in fullname
        package_name = fullname

        already_loaded = package_name in sys.modules
        if already_loaded and not self._enable_reloading:
            # there would be no point triggering a rebuild in this case. see docs/reloading.md
            logger.debug('package "%s" is already loaded and enable_reloading=False', package_name)
            return None

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                '%s searching for "%s"%s', type(self).__name__, package_name, " (reload)" if already_loaded else ""
            )

        start = time.perf_counter()

        # sys.path includes site-packages and search roots for editable installed packages
        search_paths = [Path(p) for p in sys.path]

        spec = None
        rebuilt = False
        for search_path in search_paths:
            project_dir, is_editable = _load_dist_info(search_path, package_name)
            if project_dir is not None:
                logger.debug('found project linked by dist-info: "%s"', project_dir)
                if not is_editable and not self._enable_automatic_installation:
                    logger.debug(
                        "package not installed in editable-mode and enable_automatic_installation=False. not rebuilding"
                    )
                else:
                    spec, rebuilt = self._rebuild_project(package_name, project_dir)
                    if spec is not None:
                        break

            project_dir = _find_maturin_project_above(search_path)
            if project_dir is not None:
                logger.debug(
                    'found project above the search path: "%s" ("%s")',
                    project_dir,
                    search_path,
                )
                spec, rebuilt = self._rebuild_project(package_name, project_dir)
                if spec is not None:
                    break

        if spec is not None:
            if already_loaded and self._enable_reloading:
                assert spec is not None
                spec = self._handle_reload(package_name, spec)
            duration = time.perf_counter() - start
            if rebuilt:
                logger.info('rebuilt and loaded package "%s" in %.3fs', package_name, duration)
            else:
                logger.debug('loaded package "%s" in %.3fs', package_name, duration)
        return spec

    def _handle_reload(self, package_name: str, spec: ModuleSpec) -> ModuleSpec:
        """trick python into reloading the extension module by symlinking the project

        see docs/reloading.md for full details
        """
        debug_log_enabled = logger.isEnabledFor(logging.DEBUG)
        if debug_log_enabled:
            logger.debug('handling reload of "%s"', package_name)

        if self._reload_tmp_path is None:
            self._reload_tmp_path = Path(tempfile.mkdtemp(prefix=type(self).__name__))

        if spec.origin is None:
            logger.error("module spec has no origin. cannot reload")
            return spec
        origin = Path(spec.origin)
        if origin.name != "__init__.py":
            logger.error('unexpected package origin: "%s". Not reloading', origin)
            return spec

        this_reload_dir = Path(tempfile.mkdtemp(prefix=package_name, dir=self._reload_tmp_path))
        (this_reload_dir / package_name).symlink_to(origin.parent)
        if debug_log_enabled:
            logger.debug("package reload symlink: %s", this_reload_dir)

        path_finder = PathFinder()
        reloaded_spec = path_finder.find_spec(package_name, path=[str(this_reload_dir)])
        if reloaded_spec is None:
            logger.error('failed to find package during reload "%s"', package_name)
            return spec

        name_prefix = f"{package_name}."
        to_unload = sorted(
            name
            for name, module in sys.modules.items()
            if name.startswith(name_prefix) and isinstance(module.__loader__, ExtensionFileLoader)
        )
        if debug_log_enabled:
            logger.debug("unloading %s modules: %s", len(to_unload), to_unload)
        for name in to_unload:
            del sys.modules[name]

        return reloaded_spec

    def _rebuild_project(
        self,
        package_name: str,
        project_dir: Path,
    ) -> Tuple[Optional[ModuleSpec], bool]:
        resolved = self._resolver.resolve(project_dir)
        if resolved is None:
            return None, False
        logger.debug(
            'resolved package "%s", module "%s"',
            resolved.package_name,
            resolved.module_full_name,
        )
        if package_name != resolved.package_name:
            logger.debug(
                'package name "%s" of project does not match "%s". Not importing',
                resolved.package_name,
                package_name,
            )
            return None, False

        if not self._enable_automatic_installation and not _is_editable_installed_package(project_dir, package_name):
            logger.debug(
                'package "%s" is not already installed and enable_automatic_installation=False. Not importing',
                package_name,
            )
            return None, False

        logger.debug('importing project "%s" as "%s"', project_dir, package_name)

        with self._build_cache.lock() as build_cache:
            settings = self.get_settings(package_name, project_dir)
            spec, reason = self._get_spec_for_up_to_date_package(
                package_name, project_dir, resolved, settings, build_cache
            )
            if spec is not None:
                return spec, False
            logger.debug('package "%s" will be rebuilt because: %s', package_name, reason)

            logger.info('building "%s"', package_name)
            start = time.perf_counter()
            maturin_output = develop_build_project(self.find_maturin(), resolved.cargo_manifest_path, settings)
            fix_direct_url(project_dir, package_name)
            logger.debug(
                'compiled project "%s" in %.3fs',
                package_name,
                time.perf_counter() - start,
            )

            if self._show_warnings and maturin_output_has_warnings(maturin_output):
                self._log_build_warnings(package_name, maturin_output, is_fresh=True)

            spec = _find_spec_for_package(package_name)
            if spec is None:
                msg = f'cannot find package "{package_name}" after installation'
                raise ImportHookError(msg)

            installed_package_root = _find_installed_package_root(resolved, spec)
            if installed_package_root is None:
                logger.error("could not get installed package root")
            else:
                mtime = get_installation_mtime(
                    _get_installation_paths(installed_package_root, self._excluded_dir_names)
                )
                if mtime is None:
                    logger.error("could not get installed package mtime")
                else:
                    build_status = BuildStatus(mtime, project_dir, settings.to_args(), maturin_output)
                    build_cache.store_build_status(build_status)

        return spec, True

    def _get_spec_for_up_to_date_package(
        self,
        package_name: str,
        project_dir: Path,
        resolved: MaturinProject,
        settings: MaturinSettings,
        build_cache: LockedBuildCache,
    ) -> Tuple[Optional[ModuleSpec], Optional[str]]:
        """Return a spec for the package if it exists and is newer than the source
        code that it is derived from.
        """
        logger.debug('checking whether the package "%s" is up to date', package_name)

        if self._force_rebuild:
            return None, "forcing rebuild"

        spec = _find_spec_for_package(package_name)
        if spec is None:
            return None, "package not already installed"

        installed_package_root = _find_installed_package_root(resolved, spec)
        if installed_package_root is None:
            return None, "could not find installed package root"

        build_status = build_cache.get_build_status(project_dir)
        if build_status is None:
            return None, "no build status found"
        if build_status.source_path != project_dir:
            return None, "source path in build status does not match the project dir"
        if build_status.maturin_args != settings.to_args():
            return None, "current maturin args do not match the previous build"

        installed_paths = _get_installation_paths(installed_package_root, self._excluded_dir_names)
        source_paths = _get_source_paths(
            project_dir, resolved.all_path_dependencies, installed_package_root, self._excluded_dir_names
        )
        freshness = get_installation_freshness(source_paths, installed_paths, build_status)
        if not freshness.is_fresh:
            return None, freshness.reason

        logger.debug('package up to date: "%s" ("%s")', package_name, spec.origin)

        if self._show_warnings and maturin_output_has_warnings(build_status.maturin_output):
            self._log_build_warnings(package_name, build_status.maturin_output, is_fresh=False)

        return spec, None

    def _log_build_warnings(self, module_path: str, maturin_output: str, is_fresh: bool) -> None:
        prefix = "" if is_fresh else "the last "
        message = '%sbuild of "%s" succeeded with warnings:\n%s'
        if self._show_warnings:
            logger.warning(message, prefix, module_path, maturin_output)
        else:
            logger.debug(message, prefix, module_path, maturin_output)


def _find_spec_for_package(package_name: str) -> Optional[ModuleSpec]:
    path_finder = PathFinder()
    spec = path_finder.find_spec(package_name)
    if spec is not None:
        return spec
    logger.debug('spec for package "%s" not found', package_name)
    if _is_installed_package(package_name):
        logger.debug(
            'package "%s" appears to be installed. Refreshing packages and trying again',
            package_name,
        )
        site.addsitepackages(None)
        return path_finder.find_spec(package_name)
    else:
        return None


def _is_installed_package(package_name: str) -> bool:
    for path_str in site.getsitepackages():
        path = Path(path_str)
        if (path / package_name).is_dir() or (path / f"{package_name}.pth").is_file():
            return True
    return False


def _is_editable_installed_package(project_dir: Path, package_name: str) -> bool:
    for path_str in site.getsitepackages():
        path = Path(path_str)
        pth_file = path / f"{package_name}.pth"
        if pth_file.is_file():
            pth_link = Path(pth_file.read_text().strip())
            if project_dir == pth_link or project_dir in pth_link.parents:
                return True

        if (path / package_name).is_dir():
            linked_package_dir, is_editable = _load_dist_info(path, package_name)
            return linked_package_dir == project_dir and is_editable
    return False


def _find_maturin_project_above(path: Path) -> Optional[Path]:
    for search_path in itertools.chain((path,), path.parents):
        if is_maybe_maturin_project(search_path):
            return search_path
    return None


def _load_dist_info(
    path: Path, package_name: str, *, require_project_target: bool = True
) -> Tuple[Optional[Path], bool]:
    dist_info_path = next(path.glob(f"{package_name}-*.dist-info"), None)
    if dist_info_path is None:
        return None, False
    try:
        with (dist_info_path / "direct_url.json").open() as f:
            dist_info_data = json.load(f)
    except OSError:
        return None, False
    else:
        is_editable = dist_info_data.get("dir_info", {}).get("editable", False)
        url = dist_info_data.get("url")
        if url is None:
            return None, is_editable
        prefix = "file://"
        if not url.startswith(prefix):
            return None, is_editable
        linked_path = _uri_to_path(url)
        if not require_project_target or is_maybe_maturin_project(linked_path):
            return linked_path, is_editable
        else:
            return None, is_editable


def _uri_to_path(uri: str) -> Path:
    """based on https://stackoverflow.com/a/61922504"""
    parsed = urllib.parse.urlparse(uri)
    sep = os.path.sep
    host = f"{sep}{sep}{parsed.netloc}{sep}"
    path = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    return Path(os.path.normpath(os.path.join(host, path)))  # noqa: PTH118


def _find_installed_package_root(resolved: MaturinProject, package_spec: ModuleSpec) -> Optional[Path]:
    """Find the root of the files that change each time the project is rebuilt:
    - for mixed projects: the root directory or file of the extension module inside the source tree
    - for pure projects: the root directory of the installed package.
    """
    if resolved.extension_module_dir is not None:
        installed_package_root = _find_extension_module(
            resolved.extension_module_dir, resolved.module_name, require=False
        )
        if installed_package_root is None:
            logger.debug('no extension module found in "%s"', resolved.extension_module_dir)
        return installed_package_root
    elif package_spec.origin is not None:
        return Path(package_spec.origin).parent
    else:
        logger.debug("could not find installation location for pure package")
        return None


def _find_extension_module(dir_path: Path, module_name: str, *, require: bool = False) -> Optional[Path]:
    if (dir_path / module_name / "__init__.py").exists():
        return dir_path / module_name

    # the suffixes include the platform tag and file extension eg '.cpython-311-x86_64-linux-gnu.so'
    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        extension_path = dir_path / f"{module_name}{suffix}"
        if extension_path.exists():
            return extension_path
    if require:
        msg = f'could not find module "{module_name}" in "{dir_path}"'
        raise ImportHookError(msg)
    return None


def _get_installation_paths(installed_package_root: Path, excluded_dir_names: Set[str]) -> Iterator[Path]:
    if installed_package_root.is_dir():
        yield from _get_files_in_dirs((installed_package_root,), excluded_dir_names, set())
    elif installed_package_root.is_file():
        yield installed_package_root
    else:
        return


def _get_source_paths(
    project_dir: Path,
    all_path_dependencies: List[Path],
    installed_package_root: Path,
    excluded_dir_names: Set[str],
) -> Iterator[Path]:
    excluded_dirs = set()
    if installed_package_root.is_dir():
        excluded_dirs.add(installed_package_root)

    return _get_files_in_dirs(
        itertools.chain((project_dir,), all_path_dependencies),
        excluded_dir_names,
        excluded_dirs,
    )


def _get_files_in_dirs(
    dir_paths: Iterable[Path],
    excluded_dir_names: Set[str],
    excluded_dir_paths: Set[Path],
) -> Iterator[Path]:
    for dir_path in dir_paths:
        for path in dir_path.iterdir():
            if path.is_dir():
                if path.name not in excluded_dir_names and path not in excluded_dir_paths:
                    yield from _get_files_in_dirs((path,), excluded_dir_names, excluded_dir_paths)
            else:
                yield path


IMPORTER: Optional[MaturinProjectImporter] = None


def install(
    *,
    settings: Optional[MaturinSettings] = None,
    build_dir: Optional[Path] = None,
    enable_reloading: bool = True,
    enable_automatic_installation: bool = True,
    force_rebuild: bool = False,
    excluded_dir_names: Optional[Set[str]] = None,
    lock_timeout_seconds: Optional[float] = 120,
    show_warnings: bool = True,
) -> MaturinProjectImporter:
    """Install an import hook for automatically rebuilding editable installed maturin projects.

    :param settings: settings corresponding to flags passed to maturin.

    :param build_dir: where to put the compiled artifacts. defaults to `$MATURIN_BUILD_DIR`,
        `sys.exec_prefix / 'maturin_build_cache'` or
        `$HOME/.cache/maturin_build_cache/<interpreter_hash>` in order of preference

    :param enable_reloading: enable workarounds to allow the extension modules to be reloaded with `importlib.reload()`

    :param enable_automatic_installation: whether to install detected packages using the import hook even if they
        are not already installed into the virtual environment or are installed in non-editable mode.

    :param force_rebuild: whether to always rebuild and skip checking whether anything has changed

    :param excluded_dir_names: directory names to exclude when determining whether a project has changed
        and so whether the extension module needs to be rebuilt

    :param lock_timeout_seconds: a lock is required to prevent projects from being built concurrently.
        If the lock is not released before this timeout is reached the import hook stops waiting and aborts

    :param show_warnings: whether to show compilation warnings

    """
    global IMPORTER
    if IMPORTER is not None:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(IMPORTER)
    IMPORTER = MaturinProjectImporter(
        settings=settings,
        build_dir=build_dir,
        enable_reloading=enable_reloading,
        enable_automatic_installation=enable_automatic_installation,
        force_rebuild=force_rebuild,
        excluded_dir_names=excluded_dir_names,
        lock_timeout_seconds=lock_timeout_seconds,
        show_warnings=show_warnings,
    )
    sys.meta_path.insert(0, IMPORTER)
    return IMPORTER


def uninstall() -> None:
    """Uninstall the project importer import hook."""
    global IMPORTER
    if IMPORTER is not None:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(IMPORTER)
        IMPORTER = None
