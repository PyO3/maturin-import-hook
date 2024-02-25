import argparse
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ruff: noqa: INP001


script_dir = Path(__file__).resolve().parent

log = logging.getLogger("runner")
logging.basicConfig(format="[%(name)s] [%(levelname)s] %(message)s", level=logging.INFO)


@dataclass
class TestOptions:
    test_specification: str
    timeout: int
    lld: bool
    profile: Optional[Path]
    html_report: bool


def _create_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitignore").write_text("*\n")


def _create_test_venv(python: Path, venv_dir: Path) -> None:
    if venv_dir.exists():
        log.info("removing virtualenv at %s", venv_dir)
        shutil.rmtree(venv_dir)
    if not python.exists():
        raise FileNotFoundError(python)
    log.info("creating test virtualenv at %s", venv_dir)
    cmd = ["virtualenv", "--python", str(python), str(venv_dir)]
    subprocess.run(cmd, capture_output=True, check=True)
    log.info("installing test requirements into virtualenv")
    proc = subprocess.run(
        [
            str(venv_dir / "bin/python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            "requirements.txt",
        ],
        capture_output=True,
        cwd=script_dir,
        check=False,
    )
    if proc.returncode != 0:
        log.error(proc.stdout)
        log.error(proc.stderr)
        msg = "pip install failed"
        raise RuntimeError(msg)
    log.info("test environment ready")


def _get_virtualenv_bin_dir(venv_dir: Path) -> Path:
    return (venv_dir / ("Scripts" if platform.system() == "Windows" else "bin")).resolve()


def _get_interpreter_path(venv_dir: Path) -> Path:
    virtualenv_bin = _get_virtualenv_bin_dir(venv_dir)
    if platform.system() == "Windows":
        interpreter = virtualenv_bin / "python.exe"
        if not interpreter.exists():
            interpreter = virtualenv_bin / "python"
    else:
        interpreter = virtualenv_bin / "python"
    assert interpreter.exists()
    return interpreter


def _run_test_in_environment(
    venv_dir: Path,
    cache_dir: Path,
    report_output: Optional[Path],
    options: TestOptions,
) -> None:
    """
    Args:
        cache_dir: a directory that persists to speed up subsequent runs
    """
    venv_dir = venv_dir.resolve()
    cache_dir = cache_dir.resolve()
    env = os.environ.copy()

    # manually activate the virtual environment
    path = env.get("PATH", "").split(":")
    path.insert(0, str(_get_virtualenv_bin_dir(venv_dir)))
    env["PATH"] = ":".join(path)
    env["VIRTUAL_ENV"] = str(venv_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)
    env["MATURIN_BUILD_DIR"] = str(cache_dir / "maturin_build_cache")
    env["CARGO_TARGET_DIR"] = str(cache_dir / "target")

    if options.lld:
        log.info("using lld")
        # https://stackoverflow.com/a/57817848
        env["RUSTFLAGS"] = "-C link-arg=-fuse-ld=lld"

    interpreter = _get_interpreter_path(venv_dir)

    cmd = [str(interpreter)]
    if options.profile:
        cmd += ["-m", "cProfile", "-o", str(options.profile.resolve())]

    cmd += ["-m", "pytest"]
    if report_output is not None:
        cmd += ["--junit-xml", str(report_output.resolve()), "--junit-prefix", "maturin_import_hook"]
    cmd += [options.test_specification]
    log.info("running %s", subprocess.list2cmdline(cmd))
    proc = subprocess.run(cmd, env=env, check=False, timeout=options.timeout)
    if proc.returncode != 0:
        log.error("pytest failed with code %i", proc.returncode)
        sys.exit(proc.returncode)


def _create_html_report(venv_dir: Path, reports_dir: Path, output_path: Path) -> None:
    interpreter = _get_interpreter_path(venv_dir)
    reports = [str(p) for p in reports_dir.resolve().glob("*.xml")]
    if not reports:
        log.info("cannot create a report: no files in reports dir")
        return
    cmd = [str(interpreter), "-m", "junit2htmlreport", *reports, str(output_path)]
    subprocess.check_call(cmd)
    log.info("report written to %s", output_path)


def _run_tests_serial(
    workspace: Path,
    python: Path,
    options: TestOptions,
) -> None:
    venv_dir = workspace / "venv"

    reports_dir = workspace / "reports"
    if reports_dir.exists():
        shutil.rmtree(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace / "report.html"
    report_path.unlink(missing_ok=True)

    _create_test_venv(python, venv_dir)
    try:
        _run_test_in_environment(venv_dir, workspace / "cache", reports_dir / "results.xml", options)
    finally:
        if options.html_report:
            _create_html_report(venv_dir, reports_dir, report_path)


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
        "--timeout",
        default=40 * 60,
        type=int,
        help="the total number of seconds to allow the tests to run for before aborting",
    )

    parser.add_argument(
        "--html-report",
        action=argparse.BooleanOptionalAction,  # type: ignore[attr-defined]
        default=True,
        help="whether to create a html report from the junit test report",
    )
    parser.add_argument("--lld", action="store_true", help="use lld for linking (generally faster than the default).")
    parser.add_argument(
        "--profile",
        type=Path,
        required=False,
        help="collect profiling statistics. Note that the majority of the time is spent waiting on subprocesses",
    )

    parser.add_argument(
        "test_specification", nargs="?", help="the directory, file or test to run (defaults to running all tests)"
    )
    args = parser.parse_args()

    if args.test_specification is None:
        args.test_specification = "test_import_hook/"

    _create_workspace(args.workspace)
    options = TestOptions(
        test_specification=args.test_specification,
        timeout=args.timeout,
        lld=args.lld,
        profile=args.profile,
        html_report=args.html_report,
    )
    _run_tests_serial(args.workspace, args.python, options)


if __name__ == "__main__":
    main()
