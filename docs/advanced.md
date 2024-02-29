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
