# Advanced Usage

The import hook classes can be subclassed to further customize to specific use cases.
For example settings can be configured per-project or loaded from configuration files.

```python
import sys
from pathlib import Path
from maturin_import_hook.settings import MaturinSettings
from maturin_import_hook.project_importer import MaturinProjectImporter

class CustomImporter(MaturinProjectImporter):
    def get_settings(self, module_path: str, source_path: Path) -> MaturinSettings:
        return MaturinSettings(
            release=True,
            strip=True,
            # ...
        )

sys.meta_path.insert(0, CustomImporter())
```

## Rebuild Timestamps

After a successful project build, the import hook records the time the build started. This prevents repeated rebuilds
when maturin decides that the compiled extension does not need updating, while still rebuilding conservatively for
source files changed during the build. The installed files' physical timestamp is tracked separately so external
changes to the installation still trigger a rebuild.

To retain the legacy behavior of comparing source files directly with the installed extension timestamp, disable the
logical timestamp reset:

```python
import maturin_import_hook

maturin_import_hook.install(reset_build_timestamp=False)
```
