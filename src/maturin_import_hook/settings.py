import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO, Any, Literal, Optional, Union

__all__ = [
    "MaturinSettings",
]


@dataclass
class MaturinSettings:
    """Settings common to `maturin build` and `maturin develop` relevant to the import hook.."""

    release: bool = False
    strip: bool = False
    quiet: bool = False
    jobs: Optional[int] = None
    profile: Optional[str] = None
    features: Optional[list[str]] = None
    all_features: bool = False
    no_default_features: bool = False
    target: Optional[str] = None
    ignore_rust_version: bool = False
    color: Optional[bool] = None
    frozen: bool = False
    locked: bool = False
    offline: bool = False
    config: Optional[dict[str, str]] = None
    unstable_flags: Optional[list[str]] = None
    verbose: int = 0
    rustc_flags: Optional[list[str]] = None

    # `maturin build` specific
    auditwheel: Optional[str] = None
    zig: bool = False

    # `maturin develop` specific
    extras: Optional[list[str]] = None
    uv: bool = False
    skip_install: bool = False

    @staticmethod
    def default() -> "MaturinSettings":
        """MaturinSettings() sets no flags but default() corresponds to some sensible defaults."""
        return MaturinSettings(
            color=True,
        )

    def to_args(self, cmd: Literal["develop", "build"]) -> list[str]:
        args: list[str] = []
        if self.release:
            args.append("--release")
        if self.strip:
            args.append("--strip")
        if self.quiet:
            args.append("--quiet")
        if self.jobs is not None:
            args.append("--jobs")
            args.append(str(self.jobs))
        if self.profile is not None:
            args.append("--profile")
            args.append(self.profile)
        if self.features:
            args.append("--features")
            args.append(",".join(self.features))
        if self.all_features:
            args.append("--all-features")
        if self.no_default_features:
            args.append("--no-default-features")
        if self.target is not None:
            args.append("--target")
            args.append(self.target)
        if self.ignore_rust_version:
            args.append("--ignore-rust-version")
        if self.color is not None:
            args.append("--color")
            if self.color:
                args.append("always")
            else:
                args.append("never")
        if self.frozen:
            args.append("--frozen")
        if self.locked:
            args.append("--locked")
        if self.offline:
            args.append("--offline")
        if self.config is not None:
            for key, value in self.config.items():
                args.append("--config")
                args.append(f"{key}={value}")
        if self.unstable_flags is not None:
            for flag in self.unstable_flags:
                args.append("-Z")
                args.append(flag)
        if self.verbose > 0:
            args.append("-{}".format("v" * self.verbose))

        if cmd == "build":
            if self.auditwheel is not None:
                args.append("--auditwheel")
                args.append(self.auditwheel)
            if self.zig:
                args.append("--zig")

        if cmd == "develop":
            if self.extras is not None:
                args.append("--extras")
                args.append(",".join(self.extras))
            if self.uv:
                args.append("--uv")
            if self.skip_install:
                args.append("--skip-install")

        if self.rustc_flags is not None:
            args.append("--")
            args.extend(self.rustc_flags)

        return args

    @staticmethod
    def from_args(raw_args: list[str]) -> "MaturinSettings":
        """Parse command line flags into this data structure"""
        parser = MaturinSettings.parser()
        args = parser.parse_args(raw_args)
        if "--" in args.rustc_flags:
            args.rustc_flags.remove("--")
        if len(args.rustc_flags) == 0:
            args.rustc_flags = None
        return MaturinSettings(**vars(args))

    @staticmethod
    def parser() -> "NonExitingArgumentParser":
        """Obtain an argument parser that can parse arguments related to this class"""
        parser = NonExitingArgumentParser()
        parser.add_argument("-r", "--release", action="store_true")
        parser.add_argument("--strip", action="store_true")
        parser.add_argument("-q", "--quiet", action="store_true")
        parser.add_argument("-j", "--jobs", type=int)
        parser.add_argument("--profile")
        parser.add_argument("-F", "--features", type=lambda arg: re.split(",|[ ]", arg), action="extend")
        parser.add_argument("--all-features", action="store_true")
        parser.add_argument("--no-default-features", action="store_true")
        parser.add_argument("--target")
        parser.add_argument("--ignore-rust-version", action="store_true")

        def parse_color(arg: str) -> Optional[bool]:
            if arg == "always":
                return True
            elif arg == "never":
                return False
            else:
                return None

        parser.add_argument("--color", type=parse_color)
        parser.add_argument("--frozen", action="store_true")
        parser.add_argument("--locked", action="store_true")
        parser.add_argument("--offline", action="store_true")
        parser.add_argument("--config", action=_KeyValueAction)
        parser.add_argument("-Z", dest="unstable_flags", action="append")
        parser.add_argument("-v", "--verbose", action="count", default=0)
        parser.add_argument("rustc_flags", nargs=argparse.REMAINDER)

        # `maturin build` specific
        parser.add_argument("--auditwheel", choices=["repair", "check", "skip"])
        parser.add_argument("--zig", action="store_true")

        # `maturin develop` specific
        parser.add_argument("-E", "--extras", type=lambda arg: arg.split(","), action="extend")
        parser.add_argument("--uv", action="store_true")
        parser.add_argument("--skip-install", action="store_true")

        return parser


class NonExitingArgumentParser(argparse.ArgumentParser):
    """An `ArgumentParser` that does not call `sys.exit` if it fails to parse"""

    def error(self, message: str) -> None:  # type: ignore[override]
        msg = "argument parser error"
        raise ValueError(msg)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:  # type: ignore[override]
        pass

    def _print_message(self, message: str, file: Optional[IO[str]] = None) -> None:
        pass


class _KeyValueAction(argparse.Action):
    """Parse 'key=value' arguments into a dictionary"""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Union[str, Sequence[Any], None],
        option_string: Union[str, Sequence[Any], None] = None,
    ) -> None:
        if values is None:
            values = []
        elif isinstance(values, str):
            values = [values]

        key_value_store = getattr(namespace, self.dest)
        if key_value_store is None:
            key_value_store = {}
            setattr(namespace, self.dest, key_value_store)

        for value in values:
            parts = value.split("=", maxsplit=2)
            if len(parts) == 2:
                key_value_store[parts[0]] = parts[1]
            else:
                msg = f"failed to parse KEY=VALUE from {value!r}"
                raise ValueError(msg)
