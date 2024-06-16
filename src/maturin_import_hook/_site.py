import site
from pathlib import Path
from textwrap import dedent

from maturin_import_hook._logging import logger

MANAGED_INSTALL_START = "# <maturin_import_hook>"
MANAGED_INSTALL_END = "# </maturin_import_hook>\n"
MANAGED_INSTALL_COMMENT = """
# the following commands install the maturin import hook during startup.
# see: `python -m maturin_import_hook site`
"""

MANAGED_INSTALLATION_PRESETS = {
    "debug": dedent("""\
        import maturin_import_hook
        maturin_import_hook.install()
    """),
    "release": dedent("""\
        import maturin_import_hook
        from maturin_import_hook.settings import MaturinSettings
        maturin_import_hook.install(MaturinSettings(release=True))
    """),
}


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


def insert_automatic_installation(sitecustomize: Path, preset_name: str, force: bool) -> None:
    if preset_name not in MANAGED_INSTALLATION_PRESETS:
        msg = f"Unknown managed installation preset name: '{preset_name}'"
        raise ValueError(msg)

    logger.info(f"installing automatic activation into '{sitecustomize}'")
    if has_automatic_installation(sitecustomize):
        if force:
            logger.info("already installed, but force=True. Overwriting...")
            remove_automatic_installation(sitecustomize)
        else:
            logger.info("already installed. Aborting install")
            return

    parts = []
    if sitecustomize.exists():
        parts.append(sitecustomize.read_text())
        parts.append("\n")
    parts.extend([
        MANAGED_INSTALL_START,
        MANAGED_INSTALL_COMMENT,
        MANAGED_INSTALLATION_PRESETS[preset_name],
        MANAGED_INSTALL_END,
    ])
    code = "".join(parts)
    sitecustomize.write_text(code)
