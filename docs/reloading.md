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
being reloaded. The hook can identify the reload case by whether the module being queried is already in `sys.modules` or
by whether the `target` argument to `importlib.abc.MetaPathFinder.find_spec()` is not `None`.



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


## File Importer
When `importlib.reload()` is called on a module backed directly by an extension module the situation is more challenging
as the module cannot be removed from `sys.modules` to force it to be reloaded (doing so will break the reload process).

[PEP-489] states that
> Reloading an extension module using importlib.reload() will continue to have no effect, except re-setting import-related attributes.

and the documentation for [importlib.reload](https://docs.python.org/3/library/importlib.html#importlib.reload) states that
> The init function of extension modules is not called a second time

The experimentation in `tests/reference/reloading` verifies that nothing about the module
(functions or globals) are reset when a normal extension module is reloaded.


[pyximport](https://github.com/cython/cython/blob/master/pyximport/pyximport.py) (import hook for cython) has a
`reload_support` parameter that controls whether the hook will create copies of the extension module. This approach
inspired the project importer workaround, however this feature of pyximport appears to now be broken as this test case
fails for me (prints 10 twice):

```python
import importlib
from pathlib import Path
import pyximport
pyximport.install(reload_support=True)

Path("thing.pyx").write_text("def get_num(): return 10")
import thing
print(f"get_num before {thing.get_num()}")
Path("thing.pyx").write_text("def get_num(): return 20")
importlib.reload(thing)
print(f"get_num after {thing.get_num()}")
```

Another possibility is trick the interpreter into thinking the new extension module is a completely different module,
then copying the state (`__dict__`) over to the already loaded module.
This works mostly as expected for simple use cases but has some edge cases/drawbacks:

- it is not possible to persist state that is initially assigned during module initialisation because it will be overwritten with the fresh data from the reloaded module
  - one workaround for this could be to initialise global data after module initialisation, either with an explicit `init()` function or constructing lazily on the first use. Because the data will not exist in the fresh copy of the module it will not be overwritten during the reload

it would be possible to create a custom import hook that calls a pre-determined function (e.g. `_reload_init`) and the hook could pass in the state of the old module, but requiring full reload support should be a rare requirement.

In CPython, [`_PyImport_LoadDynamicModuleWithSpec`](https://github.com/python/cpython/blob/59057ce55a443f35bfd685c688071aebad7b3671/Python/importdl.c#L97) identifies the extension module init function then immediately calls
it so there is no way to pass state to the newly loaded module using the current built-in mechanisms.


## Summary

In summary, reload support for extension modules and packages containing extension modules is possible with support
from an import hook, but hacks are required and there are some edge cases.

- Project Importer
  - triggered by reloading the root module (not the extension module) (i.e. `reload(some_package)` not `reload(some_package.extension_module)`)
  - behaviour is close to reloading regular python modules
    - global data is not reset if nothing has changed (this is different to reloading a python module)
    - global data is reset if the module was recompiled
    - imports of the type `import <extension_module>` use the reloaded functionality
  - modules other than the extension module are not reloaded by reloading the package
  - `__path__` and `__file__` are set to the temporary location required for reloading
- File Importer
  - triggered by reloading an extension module originally imported by the file importer
  - behaviour is different from reloading regular python modules.
    - Extension is loaded fresh so state assigned at load-time does not persist
    - global data is always reset even if nothing has changed
  - imports of the type `import <extension_module>` use the reloaded functionality
  - `__file__` remains pointing at the original extension module location
