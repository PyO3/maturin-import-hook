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

# ruff: noqa: INP001


script_dir = Path(__file__).resolve().parent

log = logging.getLogger("runner")
logging.basicConfig(format="[%(name)s] [%(levelname)s] %(message)s", level=logging.DEBUG)


@dataclass
class TestOptions:
    test_specification: str
    test_suite_name: str
    timeout: int
    max_failures: int | None
    use_lld: bool
    profile: Path | None
    html_report: bool
    notify: bool


def _run_tests_serial(
    workspace: Path,
    python: Path,
    options: TestOptions,
) -> None:
    log.info("running tests with options: %s", options)

    workspace = workspace.resolve()
    python = python.resolve()
    _create_ignored_directory(workspace)

    reports_dir = workspace / "reports"
    if reports_dir.exists():
        shutil.rmtree(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace / "report.html"
    report_path.unlink(missing_ok=True)

    venv = _create_test_venv(python, workspace / "venv")
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
    cmd += [options.test_specification]
    log.info("running %s", subprocess.list2cmdline(cmd))
    proc = subprocess.run(cmd, env=env, check=False, timeout=options.timeout)
    if proc.returncode != 0:
        log.error("pytest failed with code %i", proc.returncode)
        sys.exit(proc.returncode)


def _create_test_venv(python: Path, venv_dir: Path) -> VirtualEnv:
    venv = VirtualEnv.new(venv_dir, python)
    log.info("installing test requirements into virtualenv")
    proc = subprocess.run(
        [
            str(venv.interpreter_path),
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
        log.error(proc.stdout.decode())
        log.error(proc.stderr.decode())
        msg = "pip install failed"
        raise RuntimeError(msg)
    log.debug("%s", proc.stdout.decode())
    log.info("test environment ready")
    return venv


class VirtualEnv:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._is_windows = platform.system() == "Windows"

    @staticmethod
    def new(root: Path, interpreter_path: Path) -> VirtualEnv:
        if root.exists():
            log.info("removing virtualenv at %s", root)
            shutil.rmtree(root)
        if not interpreter_path.exists():
            raise FileNotFoundError(interpreter_path)
        log.info("creating test virtualenv at '%s' from '%s'", root, interpreter_path)
        cmd = ["virtualenv", "--python", str(interpreter_path), str(root)]
        proc = subprocess.run(cmd, capture_output=True, check=True)
        log.debug("%s", proc.stdout.decode())
        assert root.is_dir()
        return VirtualEnv(root)

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def bin_dir(self) -> Path:
        return self._root / ("Scripts" if self._is_windows else "bin")

    @property
    def interpreter_path(self) -> Path:
        if self._is_windows:
            interpreter = self.bin_dir / "python.exe"
            if not interpreter.exists():
                interpreter = self.bin_dir / "python"
        else:
            interpreter = self.bin_dir / "python"
        assert interpreter.exists()
        return interpreter

    def activate(self, env: dict[str, str]) -> None:
        """set the environment as-if venv/bin/activate was run"""
        path = env.get("PATH", "").split(os.pathsep)
        path.insert(0, str(self.bin_dir))
        env["PATH"] = os.pathsep.join(path)
        env["VIRTUAL_ENV"] = str(self.root_dir)


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


# TODO(matt): remove when 3.9 is the minimum supported version
if sys.version_info < (3, 9):
    from typing import Any
    # ruff: noqa: ANN401

    class _BooleanOptionalAction(argparse.Action):
        """a copy of argparse.BooleanOptionAction. This class is only available in python 3.9+"""

        def __init__(
            self,
            option_strings: Any,
            dest: Any,
            default: Any = None,
            type: Any = None,  # noqa: A002
            choices: Any = None,
            required: Any = False,
            help: Any = None,  # noqa: A002
            metavar: Any = None,
        ) -> None:
            _option_strings = []
            for option_string in option_strings:
                _option_strings.append(option_string)

                if option_string.startswith("--"):
                    option_string = "--no-" + option_string[2:]  # noqa: PLW2901
                    _option_strings.append(option_string)

            super().__init__(
                option_strings=_option_strings,
                dest=dest,
                nargs=0,
                default=default,
                type=type,
                choices=choices,
                required=required,
                help=help,
                metavar=metavar,
            )

        def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None) -> None:
            if option_string in self.option_strings:
                setattr(namespace, self.dest, not option_string.startswith("--no-"))

        def format_usage(self) -> str:
            return " | ".join(self.option_strings)

    argparse.BooleanOptionalAction = _BooleanOptionalAction  # type: ignore[attr-defined]


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
        help="the name for the suite of tests this run (use to distinguish between OS/python version)",
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
        action=argparse.BooleanOptionalAction,  # type: ignore[attr-defined]
        default=True,
        help="whether to create a html report from the junit test report",
    )
    parser.add_argument(
        "--notify",
        action=argparse.BooleanOptionalAction,  # type: ignore[attr-defined]
        default=True,
        help="send a notification when finished",
    )
    parser.add_argument(
        "--lld",
        action="store_true",
        help="use lld for linking (generally faster than the default).",
    )
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

    options = TestOptions(
        test_specification=args.test_specification,
        test_suite_name=args.name,
        timeout=args.timeout,
        max_failures=args.max_failures,
        use_lld=args.lld,
        profile=args.profile,
        html_report=args.html_report,
        notify=args.notify,
    )
    _run_tests_serial(args.workspace, args.python, options)


if __name__ == "__main__":
    main()
