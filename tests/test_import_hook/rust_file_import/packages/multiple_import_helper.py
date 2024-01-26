from .subpackage import my_rust_module  # type: ignore[missing-import]


def foo() -> int:
    return my_rust_module.get_num() + 100
