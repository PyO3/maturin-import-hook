import os
from pathlib import Path
from typing import Optional

from maturin_import_hook import project_importer, rust_file_importer
from maturin_import_hook._logging import logger, reset_logger
from maturin_import_hook.settings import MaturinSettings

__all__ = ["install", "uninstall", "reset_logger"]


def install(
    *,
    enable_project_importer: bool = True,
    enable_rs_file_importer: bool = True,
    enable_reloading: bool = True,
    settings: Optional[MaturinSettings] = None,
    build_dir: Optional[Path] = None,
    force_rebuild: bool = False,
    lock_timeout_seconds: Optional[float] = 120,
    show_warnings: bool = True,
    file_searcher: Optional[project_importer.ProjectFileSearcher] = None,
    enable_automatic_installation: bool = False,
) -> None:
    """Install import hooks for automatically rebuilding and importing maturin projects or .rs files.

    see the guide at <maturin.rs> for more details

    Args:
        enable_project_importer: enable the hook for automatically rebuilding editable installed maturin projects
        enable_rs_file_importer: enable the hook for importing .rs files as though they were regular python modules
        enable_reloading: enable workarounds to allow the extension modules to be reloaded with `importlib.reload()`
        settings: settings corresponding to flags passed to maturin.
        build_dir: where to put the compiled artifacts. defaults to `$MATURIN_BUILD_DIR`,
            `sys.exec_prefix / 'maturin_build_cache'` or
            `$HOME/.cache/maturin_build_cache/<interpreter_hash>` in order of preference
        force_rebuild: whether to always rebuild and skip checking whether anything has changed
        lock_timeout_seconds: a lock is required to prevent projects from being built concurrently.
            If the lock is not released before this timeout is reached the import hook stops waiting and aborts.
            A value of None means that the import hook will wait for the lock indefinitely.
        show_warnings: whether to show compilation warnings
        file_searcher: an object used to find source and installed project files that are used to determine whether
            a project has changed and needs to be rebuilt
        enable_automatic_install: whether to install detected packages using the import hook even if they
            are not already installed into the virtual environment or are installed in non-editable mode.

    """
    if os.environ.get("MATURIN_IMPORT_HOOK_ENABLED") == "0":
        logger.info("maturin import hook disabled by environment variable")
        return

    if enable_rs_file_importer:
        rust_file_importer.install(
            settings=settings,
            build_dir=build_dir,
            enable_reloading=enable_reloading,
            force_rebuild=force_rebuild,
            lock_timeout_seconds=lock_timeout_seconds,
            show_warnings=show_warnings,
        )
    if enable_project_importer:
        project_importer.install(
            settings=settings,
            build_dir=build_dir,
            enable_reloading=enable_reloading,
            force_rebuild=force_rebuild,
            lock_timeout_seconds=lock_timeout_seconds,
            show_warnings=show_warnings,
            file_searcher=file_searcher,
            enable_automatic_installation=enable_automatic_installation,
        )


def uninstall() -> None:
    """Remove the import hooks."""
    project_importer.uninstall()
    rust_file_importer.uninstall()
