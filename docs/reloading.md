# Reloading

Regular python modules can be reloaded with [`importlib.reload()`](https://docs.python.org/3/library/importlib.html#importlib.reload)
to load changes to the source code without restarting the python interpreter.
This mechanism is used by the [`%autoreload` IPython extension](https://ipython.readthedocs.io/en/stable/config/extensions/autoreload.html)
to automatically trigger reloads whenever something from the module is accessed.

Reloading modules should generally be avoided in production, but may be useful in some situations such as prototyping and interactive
development like Jupyter notebooks. These are situations where import hooks are also useful so supporting reloading would be
beneficial.

As [PEP-489](https://peps.python.org/pep-0489/#module-reloading) mentions,
calling `importlib.reload()` on an extension module does not reload the module even if it has changed:

> Reloading an extension module using importlib.reload() will continue to have no effect, except re-setting import-related attributes.
> Due to limitations in shared library loading (both dlopen on POSIX and LoadModuleEx on Windows),
> it is not generally possible to load a modified library after it has changed on disk.

Some import hooks such as [`pyximport` (for cython)](https://github.com/cython/cython/blob/master/pyximport/pyximport.py)
support reloading by compiling to a different path each time the module is loaded.

when `importlib.reload()` is called, import hooks get called to find the spec for the module
being reloaded. The hook can identify the reload case by whether the module being queried is already in `sys.modules`.



## Project Importer
When `importlib.reload()` is called on a maturin package the default behaviour *without the import hook* is that the python module
(eg `my_project/__init__.py`) gets reloaded, but modules inside the package (like the compiled extension module) do
not get reloaded. calling `importlib.reload()` on the extension module directly also has no effect due to the
reasons outlined above.

when `enable_reloading = True`, the import hook triggers a rebuild like normal, but then unloads submodules any
extension modules package by deleting them from `sys.modules` and creates a symlink of the project and returns a module
spec pointing to the symlink. This is enough to trigger the python interpreter to load the extension module again.

This behaviour doesn't follow [the semantics of `importlib.reload()`](https://docs.python.org/3/library/importlib.html#importlib.reload) exactly:

- Reloading the package root module should leave submodules unaffected
- The module being reloaded is not supposed to be re-initialised and the global data for the module is supposed to persist.
- each time the package reloads the extension module is located at a different place on the filesystem.
  This is necessary for the workaround to function

But hopefully the chosen behaviour is more useful for general use. Reloading can always be disabled if it causes problems in some edge cases.
