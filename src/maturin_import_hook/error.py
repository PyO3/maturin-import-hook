class ImportHookError(ImportError):
    """An error raised by the import hook.

    All errors raised by the import hook should inherit this class so that they can easily be caught and handled.
    for example:

        try:
            import my_maturin_project as runner
        except ImportHookError:  # perhaps maturin is not installed?
            import pure_python_fallback as runner

    """


class MaturinError(ImportHookError):
    """An error from the import hook involving maturin"""
