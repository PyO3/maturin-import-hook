import argparse
import importlib.metadata
import json
import platform
import shutil
import site
import subprocess
from pathlib import Path
from typing import Optional

from maturin_import_hook import project_importer, rust_file_importer
from maturin_import_hook._building import get_default_build_dir
from maturin_import_hook._site import (
    get_sitecustomize_path,
    get_usercustomize_path,
    has_automatic_installation,
    insert_automatic_installation,
    remove_automatic_installation,
)


def _action_version(format_name: str) -> None:
    try:
        maturin_import_hook_version = importlib.metadata.version("maturin-import-hook")
    except importlib.metadata.PackageNotFoundError:
        maturin_import_hook_version = "?"

    try:
        maturin_version = subprocess.check_output(["maturin", "--version"]).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        maturin_version = "?"

    try:
        rustc_version = subprocess.check_output(["rustc", "--version"]).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        rustc_version = "?"

    try:
        pip_version = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError:
        pip_version = "?"

    _print_info(
        {
            "OS": platform.platform(),
            "Python": f"{platform.python_implementation()} {platform.python_version()}",
            "maturin-import-hook": maturin_import_hook_version,
            "maturin": maturin_version,
            "rustc": rustc_version,
            "pip": pip_version,
        },
        format_name,
    )


def _action_cache_info(format_name: str) -> None:
    build_dir = get_default_build_dir()
    cache_size_str = _dir_size_mib(build_dir) if build_dir.exists() else None

    _print_info(
        {
            "path": str(build_dir),
            "exists": build_dir.exists(),
            "size": cache_size_str,
        },
        format_name,
    )


def _action_cache_clear(interactive: bool) -> None:
    build_dir = get_default_build_dir()
    if build_dir.exists():
        print(f"clearing '{build_dir}'")
        print(f"This will free {_dir_size_mib(build_dir)}")
        print("please ensure no processes are currently writing to the build cache before continuing")
        if interactive and not _ask_yes_no("are you sure you want to continue"):
            print("not clearing")
            return
        shutil.rmtree(build_dir)
        print("done.")
    else:
        print(f"the cache '{build_dir}' does not exist")


def _action_site_info(format_name: str) -> None:
    sitecustomize_path = get_sitecustomize_path()
    usercustomize_path = get_usercustomize_path()

    _print_info(
        {
            "sitecustomize_path": str(sitecustomize_path),
            "sitecustomize_exists": sitecustomize_path.exists(),
            "sitecustomize_import_hook_installed": has_automatic_installation(sitecustomize_path),
            "user_site_enabled": str(site.ENABLE_USER_SITE),
            "usercustomize_path": str(usercustomize_path),
            "usercustomize_exists": usercustomize_path.exists(),
            "usercustomize_import_hook_installed": has_automatic_installation(usercustomize_path),
            "project_importer_installed": project_importer.is_installed(),
            "rust_file_importer_installed": rust_file_importer.is_installed(),
        },
        format_name,
    )


def _action_site_install(
    *,
    user: bool,
    force: bool,
    args: Optional[str],
    enable_project_importer: bool,
    enable_rs_file_importer: bool,
    detect_uv: bool,
) -> None:
    if user:
        module_path = get_usercustomize_path()
        uninstall_command = "python -m maturin_import_hook site uninstall --user"
    else:
        module_path = get_sitecustomize_path()
        uninstall_command = "python -m maturin_import_hook site uninstall"
    insert_automatic_installation(
        module_path,
        uninstall_command,
        force,
        args,
        enable_project_importer,
        enable_rs_file_importer,
        detect_uv,
    )


def _action_site_uninstall(*, user: bool) -> None:
    module_path = get_usercustomize_path() if user else get_sitecustomize_path()
    remove_automatic_installation(module_path)


def _ask_yes_no(question: str) -> bool:
    while True:
        print(f"{question} (y/n)? ", end="")
        answer = input().strip().lower()

        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        else:
            print("invalid response, please answer y/yes or n/no")


def _dir_size_mib(dir_path: Path) -> str:
    cache_size = sum(p.stat().st_size for p in dir_path.rglob("**/*"))
    return f"{cache_size / (1024 * 1024):.2f} MiB"


def _print_info(info: dict[str, object], format_name: str) -> None:
    if format_name == "text":
        for k, v in info.items():
            print(f"{k}: {v}")
    elif format_name == "json":
        print(json.dumps(info))
    else:
        raise ValueError(format_name)


def _main() -> None:
    parser = argparse.ArgumentParser(prog="-m maturin_import_hook", description="maturin import hook")
    subparsers = parser.add_subparsers(dest="action")

    version_action = subparsers.add_parser("version", help="print the version of the import hook and associated tools")
    version_action.add_argument(
        "-f", "--format", choices=["text", "json"], default="text", help="the format to output the data in"
    )

    cache_action = subparsers.add_parser("cache", help="manage the import hook build cache")
    cache_sub_actions = cache_action.add_subparsers(dest="sub_action")
    cache_info = cache_sub_actions.add_parser("info", help="print info about the import hook build cache")
    cache_info.add_argument(
        "-f", "--format", choices=["text", "json"], default="text", help="the format to output the data in"
    )
    cache_clear = cache_sub_actions.add_parser("clear", help="delete the import hook cache")
    cache_clear.add_argument("-y", "--yes", action="store_true", help="do not prompt for confirmation")

    site_action = subparsers.add_parser(
        "site",
        help=(
            "manage installation of the import hook into site-packages/sitecustomize.py "
            "or usercustomize.py (so it starts automatically)"
        ),
    )
    site_sub_actions = site_action.add_subparsers(dest="sub_action")
    site_info = site_sub_actions.add_parser(
        "info", help="information about the current status of installation into sitecustomize/usercustomize"
    )
    site_info.add_argument(
        "-f", "--format", choices=["text", "json"], default="text", help="the format to output the data in"
    )

    install = site_sub_actions.add_parser(
        "install",
        help=(
            "install the import hook into site-packages/sitecustomize.py "
            "or usercustomize.py so that it starts automatically"
        ),
    )
    install.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="whether to overwrite any existing managed import hook installation",
    )
    install.add_argument(
        "--project-importer",
        default=True,
        help="Whether to enable the project importer",
        action=argparse.BooleanOptionalAction,
    )
    install.add_argument(
        "--rs-file-importer",
        default=True,
        help="Whether to enable the rs file importer",
        action=argparse.BooleanOptionalAction,
    )
    install.add_argument(
        "--detect-uv",
        default=True,
        help="Whether to automatically detect and use the --uv flag",
        action=argparse.BooleanOptionalAction,
    )
    install.add_argument(
        "--args",
        help="The arguments to pass to `maturin`. See `maturin develop --help` or `maturin build --help`",
    )
    install.add_argument(
        "--user",
        action="store_true",
        help=(
            "whether to install into usercustomize.py instead of sitecustomize.py. "
            "Note that usercustomize.py is shared between virtualenvs of the same interpreter version and is "
            "not loaded unless the virtualenv is created with the `--system-site-packages` argument. "
            "Use `site info` to check whether usercustomize.py is loaded the current interpreter."
        ),
    )

    uninstall = site_sub_actions.add_parser(
        "uninstall",
        help="uninstall the import hook from site-packages/sitecustomize.py or site-packages/usercustomize.py",
    )
    uninstall.add_argument(
        "--user",
        action="store_true",
        help="whether to uninstall from usercustomize.py instead of sitecustomize.py",
    )

    args = parser.parse_args()

    if args.action == "version":
        _action_version(args.format)

    elif args.action == "cache":
        if args.sub_action == "info":
            _action_cache_info(args.format)
        elif args.sub_action == "clear":
            _action_cache_clear(interactive=not args.yes)
        else:
            cache_action.print_help()

    elif args.action == "site":
        if args.sub_action == "info":
            _action_site_info(args.format)
        elif args.sub_action == "install":
            _action_site_install(
                user=args.user,
                force=args.force,
                args=args.args,
                enable_project_importer=args.project_importer,
                enable_rs_file_importer=args.rs_file_importer,
                detect_uv=args.detect_uv,
            )
        elif args.sub_action == "uninstall":
            _action_site_uninstall(user=args.user)
        else:
            site_action.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
