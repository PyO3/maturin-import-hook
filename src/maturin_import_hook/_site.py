import dataclasses
import importlib
import shlex
import shutil
import site
from pathlib import Path
from typing import Optional

from maturin_import_hook._logging import logger
from maturin_import_hook.settings import MaturinSettings

MANAGED_INSTALL_START = "# <maturin_import_hook>\n"
MANAGED_INSTALL_END = "# </maturin_import_hook>\n"
MANAGED_INSTALL_TEMPLATE = """\
# the following installs the maturin import hook during startup.
# see: `python -m maturin_import_hook site`
try:
    import maturin_import_hook
    from maturin_import_hook.settings import MaturinSettings
    maturin_import_hook.install(
        settings=MaturinSettings(
            {settings}
        ),
        enable_project_importer={enable_project_importer},
        enable_rs_file_importer={enable_rs_file_importer},
    )
except Exception as e:
    raise RuntimeError(
        f"{{e}}\\n>> ERROR in managed maturin_import_hook installation. "
        "Remove with `{uninstall_command}`\\n",
    )
"""


def get_sitecustomize_path() -> Path:
    site_packages = site.getsitepackages()
    if not site_packages:
        msg = "could not find sitecustomize.py (site-packages not found)"
        raise FileNotFoundError(msg)
    for path in site_packages:
        sitecustomize_path = Path(path) / "sitecustomize.py"
        if sitecustomize_path.exists():
            return sitecustomize_path
    return Path(site_packages[0]) / "sitecustomize.py"


def get_usercustomize_path() -> Path:
    user_site_packages = site.getusersitepackages()
    if user_site_packages is None:
        msg = "could not find usercustomize.py (user site-packages not found)"
        raise FileNotFoundError(msg)
    return Path(user_site_packages) / "usercustomize.py"


def has_automatic_installation(module_path: Path) -> bool:
    if not module_path.is_file():
        return False
    code = module_path.read_text()
    return MANAGED_INSTALL_START in code


def remove_automatic_installation(module_path: Path) -> None:
    logger.info(f"removing automatic activation from '{module_path}'")
    if not has_automatic_installation(module_path):
        logger.info("no installation found")
        return

    code = module_path.read_text()
    managed_start = code.find(MANAGED_INSTALL_START)
    if managed_start == -1:
        msg = f"failed to find managed install start marker in '{module_path}'"
        raise RuntimeError(msg)
    managed_end = code.find(MANAGED_INSTALL_END)
    if managed_end == -1:
        msg = f"failed to find managed install start marker in '{module_path}'"
        raise RuntimeError(msg)
    code = code[:managed_start] + code[managed_end + len(MANAGED_INSTALL_END) :]

    if code.strip():
        module_path.write_text(code)
    else:
        logger.info("module is now empty. Removing file.")
        module_path.unlink(missing_ok=True)


def _should_use_uv() -> bool:
    """Whether the `--uv` flag should be used when installing into this environment.

    virtual environments managed with `uv` do not have `pip` installed so the `--uv` flag is required.
    """
    try:
        importlib.import_module("pip")
    except ModuleNotFoundError:
        if shutil.which("uv") is not None:
            return True
        else:
            logger.warning("neither `pip` nor `uv` were found. `maturin develop` may not work...")
            return False
    else:
        # since pip is a more established program, use it even if uv may be installed
        return False


def insert_automatic_installation(
    module_path: Path,
    uninstall_command: str,
    force: bool,
    args: Optional[str],
    enable_project_importer: bool,
    enable_rs_file_importer: bool,
    detect_uv: bool,
) -> None:
    if args is None:
        parsed_args = MaturinSettings.default()
    else:
        parsed_args = MaturinSettings.from_args(shlex.split(args))
        if parsed_args.color is None:
            parsed_args.color = True
    if detect_uv and not parsed_args.uv and _should_use_uv():
        parsed_args.uv = True
        logger.info(
            "using `--uv` flag as it was detected to be necessary for this environment. "
            "Use `site install --no-detect-uv` to set manually."
        )

    logger.info(f"installing automatic activation into '{module_path}'")
    if has_automatic_installation(module_path):
        if force:
            logger.info("already installed, but force=True. Overwriting...")
            remove_automatic_installation(module_path)
        else:
            logger.info("already installed. Aborting install.")
            return

    parts: list[str] = []
    if module_path.exists():
        parts.append(module_path.read_text())
        parts.append("\n")

    defaults = MaturinSettings()
    non_default_settings = {k: v for k, v in dataclasses.asdict(parsed_args).items() if getattr(defaults, k) != v}

    parts.extend([
        MANAGED_INSTALL_START,
        MANAGED_INSTALL_TEMPLATE.format(
            settings=",\n            ".join(f"{k}={v!r}" for k, v in non_default_settings.items()),
            enable_project_importer=repr(enable_project_importer),
            enable_rs_file_importer=repr(enable_rs_file_importer),
            uninstall_command=uninstall_command,
        ),
        MANAGED_INSTALL_END,
    ])
    code = "".join(parts)
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(code)
    logger.info("automatic activation written successfully.")
