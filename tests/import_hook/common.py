import dataclasses
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, TypeVar

script_dir = Path(__file__).resolve().parent

MATURIN_DIR = (script_dir / "../maturin").resolve()
TEST_CRATES_DIR = MATURIN_DIR / "test-crates"


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


def resolved_package_names() -> list[str]:
    return sorted(resolved_packages().keys())


T = TypeVar("T")
U = TypeVar("U")


def map_optional(value: Optional[T], f: Callable[[T], U]) -> Optional[U]:
    return None if value is None else f(value)
