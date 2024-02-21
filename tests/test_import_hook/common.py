import dataclasses
import json
import logging
import multiprocessing
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

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
}


IMPORT_HOOK_HEADER = """
import logging
logging.basicConfig(format='%(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)

import maturin_import_hook
maturin_import_hook.reset_logger()
maturin_import_hook.install()
"""


@dataclass
class ResolvedPackage:
    cargo_manifest_path: str
    extension_module_dir: Optional[str]
    module_full_name: str
    python_dir: str
    python_module: Optional[str]

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)


_RESOLVED_PACKAGES: Optional[Dict[str, Optional[ResolvedPackage]]] = None


def resolved_packages() -> Dict[str, Optional[ResolvedPackage]]:
    global _RESOLVED_PACKAGES
    if _RESOLVED_PACKAGES is None:
        with (script_dir / "../resolved.json").open() as f:
            data = json.load(f)

        commit_hash = data["commit"]
        cmd = ["git", "rev-parse", "HEAD"]
        current_commit_hash = subprocess.check_output(cmd, cwd=MATURIN_DIR).decode().strip()
        assert (
            current_commit_hash == commit_hash
        ), "the maturin submodule is not in sync with resolved.json. See package_resolver/README.md for details"

        _RESOLVED_PACKAGES = {
            crate_name: None if crate_data is None else ResolvedPackage(**crate_data)
            for crate_name, crate_data in data["crates"].items()
        }
    return _RESOLVED_PACKAGES


T = TypeVar("T")
U = TypeVar("U")


def map_optional(value: Optional[T], f: Callable[[T], U]) -> Optional[U]:
    return None if value is None else f(value)


def with_underscores(project_name: str) -> str:
    return project_name.replace("-", "_")


def all_usable_test_crate_names() -> List[str]:
    return sorted(
        p.name
        for p in TEST_CRATES_DIR.iterdir()
        if (p / "check_installed/check_installed.py").exists() and (p / "pyproject.toml").exists()
        if p.name not in IGNORED_TEST_CRATES
    )


def mixed_test_crate_names() -> List[str]:
    return [name for name in all_usable_test_crate_names() if "mixed" in name]


def run_python(
    args: List[str],
    cwd: Path,
    *,
    quiet: bool = False,
    expect_error: bool = False,
    profile: Optional[Path] = None,
    env: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float]:
    start = time.perf_counter()

    cmd = [sys.executable]
    if profile is not None:
        cmd += ["-m", "cProfile", "-o", str(profile.resolve())]
    cmd.extend(args)
    log.info("running python")
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
        if not quiet and not expect_error:
            message = "\n".join([
                "-" * 40,
                "Called Process Error:",
                subprocess.list2cmdline(cmd),
                "Output:",
                output,
                "-" * 40,
            ])
            log.info(message)
        if not expect_error:
            # re-raising the CalledProcessError would cause
            # unnecessary output since we are already printing it above
            msg = "run_python failed"
            raise RuntimeError(msg) from None
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
    args: Optional[List[str]] = None,
    cwd: Optional[Path] = None,
    quiet: bool = False,
    expect_error: bool = False,
    env: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float]:
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
    num: int, func: Callable[..., Tuple[str, float]], args: Dict[str, Any]
) -> List[PythonProcessOutput]:
    outputs: List[PythonProcessOutput] = []
    with multiprocessing.Pool(processes=num) as pool:
        processes = [pool.apply_async(func, kwds=args) for _ in range(num)]

        for proc in processes:
            try:
                output, duration = proc.get()
            except subprocess.CalledProcessError as e:
                stdout = "None" if e.stdout is None else e.stdout.decode()
                stderr = "None" if e.stderr is None else e.stderr.decode()
                output = "\n".join(["-" * 50, "Stdout:", stdout, "Stderr:", stderr, "-" * 50])
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
