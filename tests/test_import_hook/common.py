import dataclasses
import json
import logging
import multiprocessing
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

script_dir = Path(__file__).resolve().parent
log = logging.getLogger(__name__)

# the CI does not have enough space to keep the outputs.
# When running locally you may set this to False for debugging
CLEAR_WORKSPACE = False

MATURIN_DIR = (script_dir / "../maturin").resolve()
TEST_CRATES_DIR = MATURIN_DIR / "test-crates"

IGNORED_TEST_CRATES = {
    "hello-world",  # not imported as a python module (subprocess only)
    "license-test",  # not imported as a python module (subprocess only)
    "pyo3-bin",  # not imported as a python module (subprocess only)
    "workspace-inverted-order",  # this directory is not a maturin package, only the subdirectory
}


IMPORT_HOOK_HEADER = """
import logging
logging.basicConfig(format='%(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)

import maturin_import_hook
maturin_import_hook.reset_logger()
maturin_import_hook.install()
"""

RELOAD_SUPPORTED = platform.system() != "Windows" and sys.version_info >= (3, 9)
"""
- reloading is not yet supported on Windows
- pyo3 does not support re-initialising modules for
  python < 3.9 (https://github.com/PyO3/pyo3/commit/f17e70316751285340508d0009103570af7e0873)
"""


@dataclass
class ResolvedPackage:
    cargo_manifest_path: Path
    extension_module_dir: Optional[Path]
    module_full_name: str
    python_dir: Path
    python_module: Optional[Path]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ResolvedPackage":
        return ResolvedPackage(
            cargo_manifest_path=Path(data["cargo_manifest_path"]),
            extension_module_dir=map_optional(data["extension_module_dir"], Path),
            module_full_name=data["module_full_name"],
            python_dir=Path(data["python_dir"]),
            python_module=map_optional(data["python_module"], Path),
        )

    def to_json(self) -> str:
        return json.dumps({k: str(v) for k, v in dataclasses.asdict(self).items()}, indent=2, sort_keys=True)


_RESOLVED_PACKAGES: Optional[dict[str, Optional[ResolvedPackage]]] = None


def resolved_packages() -> dict[str, Optional[ResolvedPackage]]:
    global _RESOLVED_PACKAGES
    if _RESOLVED_PACKAGES is None:
        with (script_dir / "../resolved.json").open() as f:
            data = json.load(f)

        commit_hash = data["commit"]
        git_path = shutil.which("git")
        assert git_path is not None
        cmd = [git_path, "rev-parse", "HEAD"]
        current_commit_hash = subprocess.check_output(cmd, cwd=MATURIN_DIR).decode().strip()
        assert current_commit_hash == commit_hash, (
            "the maturin submodule is not in sync with resolved.json. See package_resolver/README.md for details"
        )

        _RESOLVED_PACKAGES = {
            crate_name: None if crate_data is None else ResolvedPackage.from_dict(crate_data)
            for crate_name, crate_data in data["crates"].items()
        }
    return _RESOLVED_PACKAGES


T = TypeVar("T")
U = TypeVar("U")


def map_optional(value: Optional[T], f: Callable[[T], U]) -> Optional[U]:
    return None if value is None else f(value)


def with_underscores(project_name: str) -> str:
    return project_name.replace("-", "_")


def all_usable_test_crate_names() -> list[str]:
    return sorted(
        p.name
        for p in TEST_CRATES_DIR.iterdir()
        if (p / "check_installed/check_installed.py").exists() and (p / "pyproject.toml").exists()
        if p.name not in IGNORED_TEST_CRATES
    )


def mixed_test_crate_names() -> list[str]:
    return [name for name in all_usable_test_crate_names() if "mixed" in name]


class PythonProcessError(RuntimeError):
    def __init__(self, output: str) -> None:
        super().__init__("run_python failed")
        self.output = output


def run_python(
    args: list[str],
    cwd: Path,
    *,
    quiet: bool = False,
    expect_error: bool = False,
    profile: Optional[Path] = None,
    env: Optional[dict[str, Any]] = None,
    interpreter: Optional[Path] = None,
) -> tuple[str, float]:
    start = time.perf_counter()

    interpreter_path = sys.executable if interpreter is None else str(interpreter)
    cmd = [interpreter_path]
    if profile is not None:
        cmd += ["-m", "cProfile", "-o", str(profile.resolve())]
    cmd.extend(args)
    log.info("running python ('%s')", interpreter_path)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            cwd=cwd,
            env=env,
        )
        output = proc.stdout.decode()
    except subprocess.CalledProcessError as e:
        output = e.stdout.decode()
        if not expect_error:
            message = "\n".join([
                "-" * 40,
                "Called Process Error:",
                subprocess.list2cmdline(cmd),
                "Output:",
                output,
                "-" * 40,
            ])
            if not quiet:
                log.info(message)

            # re-raising the CalledProcessError would cause
            # unnecessary output since we are already printing it above
            raise PythonProcessError(message) from None
    duration = time.perf_counter() - start

    output = output.replace("\r\n", "\n")

    if not quiet:
        log.info("-" * 40)
        log.info("cmd: %s", subprocess.list2cmdline(cmd))
        log.info("output:\n%s", output)
        log.info("-" * 40)

    return output, duration


def run_python_code(
    python_script: str,
    *,
    args: Optional[list[str]] = None,
    cwd: Optional[Path] = None,
    quiet: bool = False,
    expect_error: bool = False,
    env: Optional[dict[str, Any]] = None,
    interpreter: Optional[Path] = None,
) -> tuple[str, float]:
    with tempfile.TemporaryDirectory("run_python_code") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        tmp_script_path = tmpdir / "script.py"
        tmp_script_path.write_text(python_script)

        python_args = [str(tmp_script_path)]
        if args is not None:
            python_args.extend(args)

        return run_python(
            python_args,
            cwd=cwd or tmpdir,
            quiet=quiet,
            expect_error=expect_error,
            env=env,
            interpreter=interpreter,
        )


def remove_ansii_escape_characters(text: str) -> str:
    """Remove escape characters (eg used to color terminal output) from the given string.

    based on: https://stackoverflow.com/a/14693789
    """
    return re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)


def check_match(text: str, pattern: str, *, flags: int = 0) -> None:
    matches = re.fullmatch(pattern, text, flags=flags) is not None
    if not matches:
        log.error("text = %s", repr(text))
    assert matches, f'text does not match pattern:\npattern: "{pattern}"\ntext:\n{text}'


def get_string_between(text: str, start: str, end: str) -> Optional[str]:
    start_index = text.find(start)
    if start_index == -1:
        return None
    end_index = text.find(end)
    if end_index == -1:
        return None
    return text[start_index + len(start) : end_index]


def missing_entrypoint_error_message_pattern(name: str) -> str:
    if platform.python_implementation() == "CPython":
        return f"dynamic module does not define module export function \\(PyInit_{name}\\)"
    elif platform.python_implementation() == "PyPy":
        return f"function _cffi_pypyinit_{name} or PyInit_{name} not found in library .*"
    else:
        raise NotImplementedError(platform.python_implementation())


@dataclass
class PythonProcessOutput:
    output: str
    duration: Optional[float]
    success: bool


def run_concurrent_python(
    num: int, func: Callable[..., tuple[str, float]], args: dict[str, Any]
) -> list[PythonProcessOutput]:
    outputs: list[PythonProcessOutput] = []
    with multiprocessing.Pool(processes=num) as pool:
        processes = [pool.apply_async(func, kwds=args) for _ in range(num)]

        for proc in processes:
            try:
                output, duration = proc.get()
            except PythonProcessError as e:
                output = e.output
                success = False
                duration = None
            else:
                success = True
            outputs.append(PythonProcessOutput(output, duration, success))

        for i, o in enumerate(outputs):
            log.info("# Subprocess %i", i)
            log.info("success: %s", o.success)
            log.info("duration: %s", o.duration)
            log.info("output:\n%s", o.output)

    return outputs


def get_file_times(path: Path) -> tuple[float, float]:
    s = path.stat()
    times = (s.st_atime, s.st_mtime)
    if platform.system() == "Windows" and platform.python_implementation() == "PyPy":
        # workaround for https://github.com/pypy/pypy/issues/4916
        for _ in range(10):
            set_file_times(path, times)
            if path.stat().st_mtime == times[1]:
                break
    return times


def set_file_times_recursive(path: Path, times: tuple[float, float]) -> None:
    for p in path.rglob("*"):
        os.utime(p, times)


def set_file_times(path: Path, times: tuple[float, float]) -> None:
    os.utime(path, times)


@contextmanager
def capture_logs(log: Optional[logging.Logger] = None, level: int = logging.INFO) -> Iterator[StringIO]:
    out = StringIO()
    if log is None:
        log = logging.getLogger()
    handler = logging.StreamHandler(out)
    handler.setLevel(level)
    log.addHandler(handler)
    try:
        yield out
    finally:
        log.removeHandler(handler)


def remove_executable_from_path(path: str, executable_name: str) -> str:
    """filter out the elements of the PATH environment variable that contain an executable with the given name"""
    log.info("removing %s from PATH = '%s'", executable_name, path)
    executable_path = shutil.which(executable_name, path=path)
    while executable_path is not None:
        executable_dir = Path(executable_path).parent
        log.info("removing '%s' from PATH", executable_dir)
        path = os.pathsep.join(path for path in path.split(os.pathsep) if path != str(executable_dir))
        executable_path = shutil.which(executable_name, path=path)
    log.info("filtered PATH = '%s'", path)
    return path


def create_echo_script(path: Path, message: str) -> None:
    """create a file that when executed, prints the given message"""
    if platform.system() == "Windows":
        # scripts cannot be run directly on windows without shell=True
        # which is not a good idea, so have to create an exe
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "main.rs"
            script_path.write_text(f'fn main() {{ println!("{message}") }}')
            subprocess.check_call(["rustc", "-o", path.with_suffix(".exe"), str(script_path)])
    else:
        path.write_text(f'#!/bin/sh\necho "{message}"')
        path.chmod(0o777)
