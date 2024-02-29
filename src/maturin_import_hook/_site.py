import site
from pathlib import Path

from maturin_import_hook._logging import logger

MANAGED_INSTALL_START = "# <maturin_import_hook>"
MANAGED_INSTALL_END = "# </maturin_import_hook>\n"
MANAGED_INSTALLATION = """
# this section of code installs the maturin import hook into every interpreter.
# see: `python -m maturin_import_hook site`
import maturin_import_hook
maturin_import_hook.install()
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


def has_automatic_installation(sitecustomize: Path) -> bool:
    if not sitecustomize.is_file():
        return False
    code = sitecustomize.read_text()
    return MANAGED_INSTALL_START in code


def remove_automatic_installation(sitecustomize: Path) -> None:
    logger.info(f"removing automatic activation from '{sitecustomize}'")
    if not has_automatic_installation(sitecustomize):
        logger.info("no installation found")
        return

    code = sitecustomize.read_text()
    managed_start = code.find(MANAGED_INSTALL_START)
    if managed_start == -1:
        msg = f"failed to find managed install start marker in '{sitecustomize}'"
        raise RuntimeError(msg)
    managed_end = code.find(MANAGED_INSTALL_END)
    if managed_end == -1:
        msg = f"failed to find managed install start marker in '{sitecustomize}'"
        raise RuntimeError(msg)
    code = code[:managed_start] + code[managed_end + len(MANAGED_INSTALL_END) :]

    if code.strip():
        sitecustomize.write_text(code)
    else:
        logger.info("module is now empty. Removing file.")
        sitecustomize.unlink(missing_ok=True)


def insert_automatic_installation(sitecustomize: Path) -> None:
    logger.info(f"installing automatic activation into '{sitecustomize}'")
    if has_automatic_installation(sitecustomize):
        logger.info("already installed")
        return

    parts = []
    if sitecustomize.exists():
        parts.append(sitecustomize.read_text())
        parts.append("\n")
    parts.extend([MANAGED_INSTALL_START, MANAGED_INSTALLATION, MANAGED_INSTALL_END])
    code = "".join(parts)
    sitecustomize.write_text(code)
