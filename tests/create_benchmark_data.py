import argparse
import logging
import random
import string
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from runner import VirtualEnv

script_dir = Path(__file__).resolve().parent
repo_root = script_dir.parent

log = logging.getLogger("runner")
logging.basicConfig(format="[%(name)s] [%(levelname)s] %(message)s", level=logging.DEBUG)


@dataclass
class BenchmarkConfig:
    seed: int
    filename_length: int
    depth: int
    num_python_editable_packages: int

    @staticmethod
    def default() -> "BenchmarkConfig":
        return BenchmarkConfig(
            seed=0,
            filename_length=10,
            depth=10,
            num_python_editable_packages=100,
        )


def random_name(rng: random.Random, length: int) -> str:
    return "".join(rng.choices(string.ascii_lowercase, k=length))


def random_path(rng: random.Random, root: Path, depth: int, name_length: int) -> Path:
    path = root
    for _ in range(depth):
        path = path / random_name(rng, name_length)
    return path


def create_python_package(root: Path) -> tuple[str, Path]:
    root.mkdir(parents=True, exist_ok=False)
    src_dir = root / "src" / root.name
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text(
        textwrap.dedent(f"""\
    def get_name():
        return "{root.name}"
    """)
    )
    (root / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
    [project]
    name = "{root.name}"
    version = "0.1.0"

    [tool.setuptools.packages.find]
    where = ["src"]

    [build-system]
    requires = ["setuptools", "wheel"]
    build-backend = "setuptools.build_meta"
    """)
    )
    return root.name, src_dir


def create_benchmark_environment(root: Path, config: BenchmarkConfig) -> None:
    rng = random.Random(config.seed)

    log.info("creating benchmark environment at %s", root)
    root.mkdir(parents=True, exist_ok=False)
    venv = VirtualEnv.create(root / "venv", Path(sys.executable))

    venv.install_editable_package(repo_root)

    python_package_names = []
    python_package_paths = []

    packages_root = random_path(rng, root, config.depth, config.filename_length)
    name, src_dir = create_python_package(packages_root)
    python_package_names.append(name)
    python_package_paths.append(src_dir)

    for _ in range(config.num_python_editable_packages):
        path = random_path(rng, packages_root, config.depth, config.filename_length)
        name, src_dir = create_python_package(path)
        python_package_names.append(name)
        python_package_paths.append(src_dir)

    python_package_paths_str = ", ".join(f'"{path.parent}"' for path in python_package_paths)
    import_python_packages = "\n".join(f"import {name}" for name in python_package_names)
    (root / "run.py").write_text(f"""\
import time
import logging
import sys
import maturin_import_hook

sys.path.extend([{python_package_paths_str}])

# logging.basicConfig(format='%(asctime)s %(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)
# maturin_import_hook.reset_logger()

maturin_import_hook.install()

start = time.perf_counter()

{import_python_packages}

end = time.perf_counter()
print(f'took {{end - start:.6f}}s')
""")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="the location to write the benchmark data to")
    args = parser.parse_args()

    config = BenchmarkConfig.default()
    create_benchmark_environment(args.root, config)


if __name__ == "__main__":
    main()
