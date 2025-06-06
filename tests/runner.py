from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ruff: noqa: INP001, E402


script_dir = Path(__file__).resolve().parent

sys.path.append(str(script_dir))
from test_import_hook.venv import PackageInstallerBackend, VirtualEnv

log = logging.getLogger("runner")
logging.basicConfig(format="[%(name)s] [%(levelname)s] %(message)s", level=logging.DEBUG)


@dataclass
class TestOptions:
    test_specification: str
    test_suite_name: str
    timeout: int
    max_failures: int | None
    last_failed: bool
    installer_backend: PackageInstallerBackend
    use_lld: bool
    profile: Path | None
    maturin_debug: bool
    html_report: bool
    notify: bool
    clear_workspace: bool


def _run_tests_serial(
    workspace: Path,
    python: Path,
    options: TestOptions,
) -> None:
    log.info("running tests with options: %s", options)

    workspace = workspace.resolve()
    python = python.resolve()
    if workspace.exists() and options.clear_workspace:
        print(f"the workspace directory already exists: '{workspace}'")
        input("Press enter to clear it...")
        shutil.rmtree(workspace)
    _create_ignored_directory(workspace)

    reports_dir = workspace / "reports"
    if reports_dir.exists():
        shutil.rmtree(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace / "report.html"
    report_path.unlink(missing_ok=True)

    venv = _create_test_venv(python, workspace / "venv", options.installer_backend)
    try:
        _run_test_in_environment(venv, workspace / "cache", reports_dir / "results.xml", options)
    finally:
        if options.html_report:
            _create_html_report(venv, reports_dir, report_path)
        if options.notify:
            _notify("tests finished")


def _run_test_in_environment(
    venv: VirtualEnv,
    cache_dir: Path,
    report_output: Path | None,
    options: TestOptions,
) -> None:
    """
    Args:
        cache_dir: a directory that persists to speed up subsequent runs
    """
    cache_dir = cache_dir.resolve()
    env = os.environ.copy()

    venv.activate(env)

    cache_dir.mkdir(parents=True, exist_ok=True)
    env["MATURIN_BUILD_DIR"] = str(cache_dir / "maturin_build_cache")
    env["CARGO_TARGET_DIR"] = str(cache_dir / "target")

    env["MATURIN_IMPORT_HOOK_TEST_PACKAGE_INSTALLER"] = options.installer_backend.value

    if options.maturin_debug:
        env["RUST_LOG"] = "maturin=debug"

    if options.use_lld:
        log.info("using lld")
        # https://stackoverflow.com/a/57817848
        env["RUSTFLAGS"] = "-C link-arg=-fuse-ld=lld"

    cmd = [str(venv.interpreter_path)]
    if options.profile:
        cmd += ["-m", "cProfile", "-o", str(options.profile.resolve())]

    cmd += ["-m", "pytest"]
    if report_output is not None:
        cmd += ["--junit-xml", str(report_output.resolve()), "-o", f"junit_suite_name={options.test_suite_name}"]
    if options.max_failures is not None:
        cmd += ["--maxfail", str(options.max_failures)]
    if options.last_failed:
        cmd += ["--last-failed"]
    cmd += [options.test_specification]
    log.info("running %s", subprocess.list2cmdline(cmd))
    proc = subprocess.run(cmd, env=env, check=False, timeout=options.timeout)
    if proc.returncode != 0:
        log.error("pytest failed with code %i", proc.returncode)
        sys.exit(proc.returncode)


def _create_test_venv(python: Path, venv_dir: Path, installer_backend: PackageInstallerBackend) -> VirtualEnv:
    venv = VirtualEnv.create(venv_dir, python, installer_backend)
    log.info("installing test requirements into virtualenv")
    venv.installer.install_requirements_file(script_dir / "requirements.txt")
    log.info("test environment ready")
    return venv


def _create_ignored_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitignore").write_text("*\n")
    (path / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")


def _create_html_report(venv: VirtualEnv, reports_dir: Path, output_path: Path) -> None:
    reports = [str(p) for p in reports_dir.resolve().glob("*.xml")]
    if not reports:
        log.info("cannot create a report: no files in reports dir")
        return
    cmd = [str(venv.interpreter_path), "-m", "junit2htmlreport", *reports, str(output_path)]
    subprocess.check_call(cmd)
    log.info("report written to %s", output_path)


def _notify(message: str) -> None:
    if platform.system() == "Linux":
        notify_send = shutil.which("notify-send")
        if notify_send is None:
            log.error("notify-send not found. cannot notify")
        else:
            subprocess.call([notify_send, "--", message])

    elif platform.system() == "Darwin":
        sanitised_message = message.replace('"', "'")
        subprocess.call([
            "/usr/bin/osascript",
            "-e",
            f'display notification "{sanitised_message}" with title "Test Runner"',
        ])
    else:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="run the import hook tests in clean virtual environments")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="the path to a python interpreter to use. Defaults to the current interpreter",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=script_dir / "test_workspace",
        help="the location to store the caches and outputs (defaults to test_workspace)",
    )
    parser.add_argument(
        "--name",
        default="Tests",
        help="the name to assign for the suite of tests this run (use to distinguish between OS/python version)",
    )

    parser.add_argument(
        "--timeout",
        default=40 * 60,
        type=int,
        help="the total number of seconds to allow the tests to run for before aborting",
    )
    parser.add_argument(
        "--max-failures",
        default=None,
        type=int,
        help="the maximum number of failures to allow before stopping testing",
    )

    parser.add_argument(
        "--html-report",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="whether to create a html report from the junit test report",
    )
    parser.add_argument(
        "--notify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="send a notification when finished",
    )
    parser.add_argument(
        "--installer",
        choices=list(PackageInstallerBackend),
        type=PackageInstallerBackend,
        default=PackageInstallerBackend.UV,
        help="the package installer to use in the tests",
    )
    parser.add_argument(
        "--lld",
        action="store_true",
        help="use lld for linking (generally faster than the default).",
    )
    parser.add_argument(
        "--maturin-debug",
        action="store_true",
        help="have maturin produce verbose logs",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        required=False,
        help="collect profiling statistics. Note that the majority of the time is spent waiting on subprocesses",
    )
    parser.add_argument(
        "--last-failed",
        action="store_true",
        help="re-run only the tests that failed in the last run",
    )
    parser.add_argument(
        "--clear-workspace",
        action="store_true",
        help="re-create the workspace if it already exists",
    )

    parser.add_argument(
        "test_specification", nargs="?", help="the directory, file or test to run (defaults to running all tests)"
    )
    args = parser.parse_args()

    if args.test_specification is None:
        args.test_specification = "test_import_hook/"

    options = TestOptions(
        test_specification=args.test_specification,
        test_suite_name=args.name,
        timeout=args.timeout,
        max_failures=args.max_failures,
        last_failed=args.last_failed,
        installer_backend=args.installer,
        use_lld=args.lld,
        profile=args.profile,
        maturin_debug=args.maturin_debug,
        html_report=args.html_report,
        notify=args.notify,
        clear_workspace=args.clear_workspace,
    )
    _run_tests_serial(args.workspace, args.python, options)


if __name__ == "__main__":
    main()
