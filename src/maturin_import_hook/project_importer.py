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
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from importlib.machinery import ExtensionFileLoader, ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType
from typing import ClassVar, Optional, Union

from maturin_import_hook._building import (
    BuildCache,
    BuildStatus,
    LockedBuildCache,
    develop_build_project,
    find_maturin,
    get_installation_freshness,
    get_installation_mtime,
    maturin_output_has_warnings,
)
from maturin_import_hook._common import LazySessionTemporaryDirectory
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
    "ProjectFileSearcher",
    "DefaultProjectFileSearcher",
]


class ProjectFileSearcher(ABC):
    @abstractmethod
    def get_source_paths(
        self,
        project_dir: Path,
        all_path_dependencies: list[Path],
        installed_package_root: Path,
    ) -> Iterator[Path]:
        """find the files corresponding to the source code of the given project"""
        raise NotImplementedError

    @abstractmethod
    def get_installation_paths(self, installed_package_root: Path) -> Iterator[Path]:
        """find the files corresponding to the installed files of the given project"""
        raise NotImplementedError


class MaturinProjectImporter(importlib.abc.MetaPathFinder):
    """An import hook for automatically rebuilding editable installed maturin projects."""

    def __init__(
        self,
        *,
        settings: Optional[MaturinSettings] = None,
        file_searcher: Optional[ProjectFileSearcher] = None,
        build_dir: Optional[Path] = None,
        lock_timeout_seconds: Optional[float] = 120,
        enable_reloading: bool = True,
        enable_automatic_installation: bool = False,
        force_rebuild: bool = False,
        show_warnings: bool = True,
    ) -> None:
        self._resolver = ProjectResolver()
        self._settings = settings
        self._file_searcher = file_searcher if file_searcher is not None else DefaultProjectFileSearcher()
        self._build_cache = BuildCache(build_dir, lock_timeout_seconds)
        self._enable_reloading = enable_reloading
        self._enable_automatic_installation = enable_automatic_installation
        self._force_rebuild = force_rebuild
        self._show_warnings = show_warnings
        self._maturin_path: Optional[Path] = None
        self._reload_tmp_path = LazySessionTemporaryDirectory(prefix=type(self).__name__)

    def get_settings(self, module_path: str, source_path: Path) -> MaturinSettings:
        """This method can be overridden in subclasses to customize settings for specific projects."""
        return self._settings if self._settings is not None else MaturinSettings.default()

    def find_maturin(self) -> Path:
        """this method can be overridden to specify an alternative maturin binary to use"""
        if self._maturin_path is None:
            self._maturin_path = find_maturin((1, 5, 0), (2, 0, 0))
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

        if spec.origin is None:
            logger.error("module spec has no origin. cannot reload")
            return spec
        origin = Path(spec.origin)
        if origin.name != "__init__.py":
            logger.error('unexpected package origin: "%s". Not reloading', origin)
            return spec

        this_reload_dir = Path(tempfile.mkdtemp(prefix=package_name, dir=self._reload_tmp_path.path))
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
    ) -> tuple[Optional[ModuleSpec], bool]:
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
                mtime = get_installation_mtime(self._file_searcher.get_installation_paths(installed_package_root))
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
    ) -> tuple[Optional[ModuleSpec], Optional[str]]:
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

        installed_paths = self._file_searcher.get_installation_paths(installed_package_root)
        source_paths = self._file_searcher.get_source_paths(
            project_dir, resolved.all_path_dependencies, installed_package_root
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
) -> tuple[Optional[Path], bool]:
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


class DefaultProjectFileSearcher(ProjectFileSearcher):
    """the default file searcher implements some lightweight and conservative filtering to ignore most unwanted parts
    of a source tree.
    """

    # based on
    # - https://github.com/github/gitignore/blob/main/Rust.gitignore
    # - https://github.com/github/gitignore/blob/main/Python.gitignore
    # - https://github.com/jupyter/notebook/blob/main/.gitignore
    DEFAULT_SOURCE_EXCLUDED_DIR_NAMES: ClassVar[set[str]] = {
        ".cache",
        ".env",
        ".git",
        ".idea",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".nox",
        ".pyre",
        ".pytest_cache",
        ".ropeproject",
        ".spyderproject",
        ".spyproject",
        ".tox",
        ".venv",
        ".vscode",
        ".yarn",
        "__pycache__",
        "dist",
        "env",
        "node_modules",
        "target",
        "venv",
    }
    DEFAULT_SOURCE_EXCLUDED_DIR_MARKERS: ClassVar[set[str]] = {
        "CACHEDIR.TAG",  # https://bford.info/cachedir/
    }
    DEFAULT_SOURCE_EXCLUDED_FILE_EXTENSIONS: ClassVar[set[str]] = {
        ".so",
        ".pyc",
    }

    def __init__(
        self,
        *,
        source_excluded_dir_names: Optional[set[str]] = None,
        source_excluded_dir_markers: Optional[set[str]] = None,
        source_excluded_file_extensions: Optional[set[str]] = None,
    ) -> None:
        """
        Args:
            source_excluded_dir_names: when searching for source files,
                ignore (do not recurse into) directories with these names (case sensitive)
            source_excluded_dir_markers: when searching for source files,
                ignore (do not recurse into) directories that contain a file with this name (case sensitive)
            source_excluded_file_extensions: when searching for source files,
                ignore files with these file extensions (case insensitive) (values should include the leading `.`)
        """
        super().__init__()
        self._source_excluded_dir_names = (
            source_excluded_dir_names
            if source_excluded_dir_names is not None
            else self.DEFAULT_SOURCE_EXCLUDED_DIR_NAMES
        )
        self._source_excluded_dir_markers = (
            source_excluded_dir_markers
            if source_excluded_dir_markers is not None
            else self.DEFAULT_SOURCE_EXCLUDED_DIR_MARKERS
        )
        self._source_excluded_file_extensions = (
            source_excluded_file_extensions
            if source_excluded_file_extensions is not None
            else self.DEFAULT_SOURCE_EXCLUDED_FILE_EXTENSIONS
        )

    def get_source_paths(
        self,
        project_dir: Path,
        all_path_dependencies: list[Path],
        installed_package_root: Path,
    ) -> Iterator[Path]:
        excluded_dirs = set()
        excluded_files = set()
        if installed_package_root.is_dir():
            excluded_dirs.add(installed_package_root)
        else:
            excluded_files.add(installed_package_root)

        for root_dir in itertools.chain((project_dir,), all_path_dependencies):
            for path in self.get_files_in_dir(
                root_dir,
                excluded_dirs,
                self._source_excluded_dir_names,
                self._source_excluded_dir_markers,
                self._source_excluded_file_extensions,
            ):
                if path not in excluded_files:
                    yield path

    def get_installation_paths(self, installed_package_root: Path) -> Iterator[Path]:
        if installed_package_root.is_dir():
            yield from self.get_files_in_dir(installed_package_root, set(), {"__pycache__"}, set(), {".pyc"})
        elif installed_package_root.is_file():
            yield installed_package_root
        else:
            return

    def get_files_in_dir(
        self,
        root_path: Path,
        ignore_dirs: set[Path],
        excluded_dir_names: set[str],
        excluded_dir_markers: set[str],
        excluded_file_extensions: set[str],
    ) -> Iterator[Path]:
        if root_path.name in excluded_dir_names:
            return
        if not root_path.exists():
            raise FileNotFoundError(root_path)

        for dir_str, dirs, files in os.walk(root_path, topdown=True):
            dir_path = Path(dir_str)
            include_dir = dir_path not in ignore_dirs and not any(dir_name in excluded_dir_markers for dir_name in dirs)

            if include_dir:
                dirs[:] = sorted(dir_name for dir_name in dirs if dir_name not in excluded_dir_names)
                files.sort()
                for filename in files:
                    file_path = dir_path / filename
                    if file_path.suffix.lower() not in excluded_file_extensions:
                        yield file_path
            else:
                dirs.clear()  # do not recurse further into this directory


IMPORTER: Optional[MaturinProjectImporter] = None


def install(
    *,
    settings: Optional[MaturinSettings] = None,
    build_dir: Optional[Path] = None,
    enable_reloading: bool = True,
    force_rebuild: bool = False,
    lock_timeout_seconds: Optional[float] = 120,
    show_warnings: bool = True,
    file_searcher: Optional[ProjectFileSearcher] = None,
    enable_automatic_installation: bool = False,
) -> MaturinProjectImporter:
    """Install an import hook for automatically rebuilding editable installed maturin projects.

    Args:
        settings: settings corresponding to flags passed to maturin.
        build_dir: where to put the compiled artifacts. defaults to `$MATURIN_BUILD_DIR`,
            `sys.exec_prefix / 'maturin_build_cache'` or
            `$HOME/.cache/maturin_build_cache/<interpreter_hash>` in order of preference.
        enable_reloading: enable workarounds to allow the extension modules to be reloaded with `importlib.reload()`
        force_rebuild: whether to always rebuild and skip checking whether anything has changed
        excluded_dir_names: directory names to exclude when determining whether a project has changed
            and so whether the extension module needs to be rebuilt
        lock_timeout_seconds: a lock is required to prevent projects from being built concurrently.
            If the lock is not released before this timeout is reached the import hook stops waiting and aborts
        show_warnings: whether to show compilation warnings
        file_searcher: an object that specifies how to search for the source files and installed files of a project.
        enable_automatic_installation: whether to install detected packages using the import hook even if they
            are not already installed into the virtual environment or are installed in non-editable mode.

    """
    global IMPORTER
    if IMPORTER is not None:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(IMPORTER)
    IMPORTER = MaturinProjectImporter(
        settings=settings,
        build_dir=build_dir,
        enable_reloading=enable_reloading,
        force_rebuild=force_rebuild,
        lock_timeout_seconds=lock_timeout_seconds,
        show_warnings=show_warnings,
        file_searcher=file_searcher,
        enable_automatic_installation=enable_automatic_installation,
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
