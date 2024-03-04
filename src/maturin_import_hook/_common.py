import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from maturin_import_hook._logging import logger


class LazySessionTemporaryDirectory:
    """A temporary directory that is created on first use and usually removed when the program exits (not guaranteed)"""

    def __init__(self, *, prefix: str) -> None:
        self._prefix = prefix
        self._tmp_path: Optional[Path] = None

    def __del__(self) -> None:
        self._cleanup()
        atexit.unregister(self._cleanup)

    def _cleanup(self) -> None:
        if self._tmp_path is not None:
            logger.debug("removing temporary directory: %s", self._tmp_path)
            try:
                shutil.rmtree(self._tmp_path)
            except OSError as e:
                logger.debug("failed to remove temporary directory %s: %r", self._tmp_path, e)
            self._tmp_path = None

    @property
    def path(self) -> Path:
        if self._tmp_path is None:
            self._tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._prefix}_"))
            atexit.register(self._cleanup)
        return self._tmp_path
