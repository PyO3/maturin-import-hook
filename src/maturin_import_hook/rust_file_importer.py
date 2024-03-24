import contextlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from importlib.machinery import ExtensionFileLoader, ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Optional, Union

from maturin_import_hook._building import (
    BuildCache,
    BuildStatus,
    LockedBuildCache,
    build_unpacked_wheel,
    find_maturin,
    get_installation_freshness,
    maturin_output_has_warnings,
    run_maturin,
)
from maturin_import_hook._common import LazySessionTemporaryDirectory
from maturin_import_hook._logging import logger
from maturin_import_hook._resolve_project import ProjectResolver, find_cargo_manifest
from maturin_import_hook.error import ImportHookError
from maturin_import_hook.settings import MaturinSettings

__all__ = ["MaturinRustFileImporter", "install", "uninstall", "IMPORTER"]


class MaturinRustFileImporter(importlib.abc.MetaPathFinder):
    """An import hook for loading .rs files as though they were regular python modules."""

    def __init__(
        self,
        *,
        settings: Optional[MaturinSettings] = None,
        build_dir: Optional[Path] = None,
        enable_reloading: bool = True,
        force_rebuild: bool = False,
        lock_timeout_seconds: Optional[float] = 120,
        show_warnings: bool = True,
    ) -> None:
        self._force_rebuild = force_rebuild
        self._enable_reloading = enable_reloading
        self._resolver = ProjectResolver()
        self._settings = settings
        self._build_cache = BuildCache(build_dir, lock_timeout_seconds)
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

    def get_source_files(self, source_path: Path) -> Iterator[Path]:
        """this method can be overridden to rebuild when changes are made to files other than the main rs file"""
        yield source_path

    def generate_project_for_single_rust_file(
        self,
        module_path: str,
        project_dir: Path,
        rust_file: Path,
        settings: MaturinSettings,
    ) -> Path:
        """This method can be overridden in subclasses to customize project generation."""
        if project_dir.exists():
            shutil.rmtree(project_dir)

        success, output = run_maturin(self.find_maturin(), ["new", "--bindings", "pyo3", str(project_dir)])
        if not success:
            msg = "Failed to generate project for rust file"
            raise ImportHookError(msg)

        if settings.features is not None:
            available_features = [feature for feature in settings.features if "/" not in feature]
            cargo_manifest = project_dir / "Cargo.toml"
            cargo_manifest.write_text(
                "{}\n[features]\n{}".format(
                    cargo_manifest.read_text(),
                    "\n".join(f"{feature} = []" for feature in available_features),
                )
            )

        shutil.copy(rust_file, project_dir / "src/lib.rs")
        return project_dir

    def find_spec(
        self,
        fullname: str,
        path: Optional[Sequence[Union[str, bytes]]] = None,
        target: Optional[ModuleType] = None,
    ) -> Optional[ModuleSpec]:
        already_loaded = fullname in sys.modules
        if already_loaded and not self._enable_reloading:
            return self._handle_no_reload(fullname)

        start = time.perf_counter()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                '%s searching for "%s"%s', type(self).__name__, fullname, " (reload)" if already_loaded else ""
            )

        is_top_level_import = path is None
        if is_top_level_import:
            search_paths = [Path(p) for p in sys.path]
        else:
            assert path is not None
            search_paths = [Path(os.fsdecode(p)) for p in path]

        module_name = fullname.rpartition(".")[2]

        spec = None
        rebuilt = False
        for search_path in search_paths:
            single_rust_file_path = search_path / f"{module_name}.rs"
            if single_rust_file_path.is_file():
                spec, rebuilt = self._import_rust_file(fullname, module_name, single_rust_file_path)
                if spec is not None:
                    break

        if spec is not None:
            if already_loaded and self._enable_reloading:
                assert spec is not None
                spec = self._handle_reload(fullname, spec)

            duration = time.perf_counter() - start
            if rebuilt:
                logger.info('rebuilt and loaded module "%s" in %.3fs', fullname, duration)
            else:
                logger.debug('loaded module "%s" in %.3fs', fullname, duration)

        return spec

    def _handle_no_reload(self, module_path: str) -> Optional[ModuleSpec]:
        module = sys.modules[module_path]
        loader = getattr(module, "__loader__", None)
        if isinstance(loader, _RustFileExtensionFileLoader):
            logger.debug('module "%s" is already loaded and enable_reloading=False', module_path)
            # need to return a spec otherwise the reload will fail because unlike the project import hook,
            # this module cannot be found without support from the hook
            return getattr(module, "__spec__", None)
        else:
            # module not managed by this hook
            return None

    def _handle_reload(self, module_path: str, spec: ModuleSpec) -> ModuleSpec:
        """trick python into reloading the extension module

        see docs/reloading.md for full details
        """
        debug_log_enabled = logger.isEnabledFor(logging.DEBUG)
        if debug_log_enabled:
            logger.debug('handling reload of "%s"', module_path)

        if spec.origin is None:
            logger.error("module spec has no origin. cannot reload")
            return spec
        origin = Path(spec.origin).resolve()
        this_reload_dir = Path(tempfile.mkdtemp(prefix=module_path, dir=self._reload_tmp_path.path))
        # if a symlink is used instead of a copy, if nothing has changed then the module is not re-initialised
        reloaded_module_path = this_reload_dir / origin.name
        shutil.copy(origin, reloaded_module_path)

        if debug_log_enabled:
            logger.debug("reloading %s as '%s'", reloaded_module_path, module_path)
        reloaded_spec = importlib.util.spec_from_loader(
            module_path, _ExtensionModuleReloader(module_path, str(origin), str(reloaded_module_path))
        )
        if reloaded_spec is None:
            logger.error('failed to find module during reload "%s"', module_path)
            return spec

        return reloaded_spec

    def _import_rust_file(
        self, module_path: str, module_name: str, file_path: Path
    ) -> tuple[Optional[ModuleSpec], bool]:
        logger.debug('importing rust file "%s" as "%s"', file_path, module_path)

        with self._build_cache.lock() as build_cache:
            output_dir = build_cache.tmp_project_dir(file_path, module_name)
            logger.debug("output dir: %s", output_dir)
            settings = self.get_settings(module_path, file_path)
            dist_dir = output_dir / "dist"
            package_dir = dist_dir / module_name

            spec, reason = self._get_spec_for_up_to_date_extension_module(
                package_dir, module_path, module_name, file_path, settings, build_cache
            )
            if spec is not None:
                return spec, False
            logger.debug('module "%s" will be rebuilt because: %s', module_path, reason)

            logger.info('building "%s"', module_path)
            logger.debug('creating project for "%s" and compiling', file_path)
            start = time.perf_counter()
            project_dir = self.generate_project_for_single_rust_file(
                module_path, output_dir / file_path.stem, file_path, settings
            )
            manifest_path = find_cargo_manifest(project_dir)
            if manifest_path is None:
                msg = f"cargo manifest not found in the project generated for {file_path}"
                raise ImportHookError(msg)

            maturin_output = build_unpacked_wheel(self.find_maturin(), manifest_path, dist_dir, settings)
            logger.debug(
                'compiled "%s" in %.3fs',
                file_path,
                time.perf_counter() - start,
            )

            if self._show_warnings and maturin_output_has_warnings(maturin_output):
                self._log_build_warnings(module_path, maturin_output, is_fresh=True)
            extension_module_path = _find_extension_module(dist_dir / module_name, module_name, require=True)
            if extension_module_path is None:
                logger.error('cannot find extension module for "%s" after rebuild', module_path)
                return None, True
            build_status = BuildStatus(
                extension_module_path.stat().st_mtime,
                file_path,
                settings.to_args(),
                maturin_output,
            )
            build_cache.store_build_status(build_status)
            return (
                _get_spec_for_extension_module(module_path, extension_module_path),
                True,
            )

    def _get_spec_for_up_to_date_extension_module(
        self,
        search_dir: Path,
        module_path: str,
        module_name: str,
        source_path: Path,
        settings: MaturinSettings,
        build_cache: LockedBuildCache,
    ) -> tuple[Optional[ModuleSpec], Optional[str]]:
        """Return a spec for the given module at the given search_dir if it exists and is newer than the source
        code that it is derived from.
        """
        logger.debug('checking whether the module "%s" is up to date', module_path)

        if self._force_rebuild:
            return None, "forcing rebuild"
        extension_module_path = _find_extension_module(search_dir, module_name, require=False)
        if extension_module_path is None:
            return None, "already built module not found"

        build_status = build_cache.get_build_status(source_path)
        if build_status is None:
            return None, "no build status found"
        if build_status.source_path != source_path:
            return None, "source path in build status does not match the project dir"
        if build_status.maturin_args != settings.to_args():
            return None, "current maturin args do not match the previous build"

        freshness = get_installation_freshness(
            self.get_source_files(source_path), (extension_module_path,), build_status
        )
        if not freshness.is_fresh:
            return None, freshness.reason

        spec = _get_spec_for_extension_module(module_path, extension_module_path)
        if spec is None:
            return None, "module not found"

        logger.debug('module up to date: "%s" (%s)', module_path, spec.origin)

        if self._show_warnings and maturin_output_has_warnings(build_status.maturin_output):
            self._log_build_warnings(module_path, build_status.maturin_output, is_fresh=False)

        return spec, None

    def _log_build_warnings(self, module_path: str, maturin_output: str, is_fresh: bool) -> None:
        prefix = "" if is_fresh else "the last "
        message = '%sbuild of "%s" succeeded with warnings:\n%s'
        if self._show_warnings:
            logger.warning(message, prefix, module_path, maturin_output)
        else:
            logger.debug(message, prefix, module_path, maturin_output)


def _find_extension_module(dir_path: Path, module_name: str, *, require: bool = False) -> Optional[Path]:
    # the suffixes include the platform tag and file extension eg '.cpython-311-x86_64-linux-gnu.so'
    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        extension_path = dir_path / f"{module_name}{suffix}"
        if extension_path.exists():
            return extension_path
    if require:
        msg = f'could not find module "{module_name}" in "{dir_path}"'
        raise ImportHookError(msg)
    return None


class _RustFileExtensionFileLoader(ExtensionFileLoader):
    pass


def _get_spec_for_extension_module(module_path: str, extension_module_path: Path) -> Optional[ModuleSpec]:
    return importlib.util.spec_from_loader(
        module_path, _RustFileExtensionFileLoader(module_path, str(extension_module_path))
    )


class _ExtensionModuleReloader(_RustFileExtensionFileLoader):
    """A loader that can be used to force a new version of an extension module to be loaded.

    See docs/reloading.md for details
    """

    def __init__(self, name: str, path: str, reload_path: str) -> None:
        # the module being reloaded will have its loader set to this object and __file__ set to the given path.
        # here the choice is made to keep __file__ pointing to the main shared object, not the temporary one used
        # only for reloading. The reload path can be accessed with `some_module.__loader__.reload_path` if necessary
        super().__init__(name, path)
        self.reload_path = reload_path

    if TYPE_CHECKING:
        name: str
        path: str

    def exec_module(self, module: ModuleType) -> None:
        if sys.modules[self.name] is not module:
            msg = f"failed to reload {self.name}. Module not in sys.modules"
            raise ImportHookError(msg)

        reload_name = f"maturin_import_hook._reload.{self.name}"
        try:
            logger.debug("creating new module then moving into %s", self.name)

            loader = ExtensionFileLoader(reload_name, self.reload_path)
            spec = importlib.util.spec_from_loader(reload_name, loader)
            if spec is None:
                msg = f"failed to create spec for {self.name} during reload"
                raise ImportHookError(msg)

            reloaded_module = importlib.util.module_from_spec(spec)
            if reloaded_module is module:
                msg = f"failed to create new module for {self.name} during reload"
                raise ImportHookError(msg)

            if sys.modules[self.name] is reloaded_module:
                msg = f"expected a new module to be created for {self.name}"
                raise ImportHookError(msg)

            excluded_names = {"__name__", "__file__", "__package__", "__loader__", "__spec__"}

            for k, v in reloaded_module.__dict__.items():
                if k not in excluded_names:
                    module.__dict__[k] = v
        finally:
            if reload_name in sys.modules:
                del sys.modules[reload_name]


IMPORTER: Optional[MaturinRustFileImporter] = None


def install(
    *,
    settings: Optional[MaturinSettings] = None,
    build_dir: Optional[Path] = None,
    enable_reloading: bool = True,
    force_rebuild: bool = False,
    lock_timeout_seconds: Optional[float] = 120,
    show_warnings: bool = True,
) -> MaturinRustFileImporter:
    """Install the 'rust file' importer to import .rs files as though
    they were regular python modules.

    Args:
        settings: settings corresponding to flags passed to maturin.
        build_dir: where to put the compiled artifacts. defaults to `$MATURIN_BUILD_DIR`,
            `sys.exec_prefix / 'maturin_build_cache'` or
            `$HOME/.cache/maturin_build_cache/<interpreter_hash>` in order of preference
        enable_reloading: enable workarounds to allow the extension modules to be reloaded with `importlib.reload()`
        force_rebuild: whether to always rebuild and skip checking whether anything has changed
        lock_timeout_seconds: a lock is required to prevent projects from being built concurrently.
            If the lock is not released before this timeout is reached the import hook stops waiting and aborts
        show_warnings: whether to show compilation warnings

    """
    global IMPORTER
    if IMPORTER is not None:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(IMPORTER)
    IMPORTER = MaturinRustFileImporter(
        settings=settings,
        build_dir=build_dir,
        enable_reloading=enable_reloading,
        force_rebuild=force_rebuild,
        lock_timeout_seconds=lock_timeout_seconds,
        show_warnings=show_warnings,
    )
    sys.meta_path.insert(0, IMPORTER)
    return IMPORTER


def uninstall() -> None:
    """Uninstall the rust file importer import hook."""
    global IMPORTER
    if IMPORTER is not None:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(IMPORTER)
        IMPORTER = None
