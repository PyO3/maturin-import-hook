from pathlib import Path
from textwrap import dedent

import pytest
from maturin_import_hook._site import (
    has_automatic_installation,
    insert_automatic_installation,
    remove_automatic_installation,
)

from .common import capture_logs


def test_automatic_site_installation(tmp_path: Path) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    header = dedent("""\
    # some existing code
    print(123)
    install()  # another import hook
    """)

    sitecustomize.write_text(header)

    assert not has_automatic_installation(sitecustomize)

    insert_automatic_installation(sitecustomize, preset_name="debug", force=False)

    with capture_logs() as cap:
        insert_automatic_installation(sitecustomize, preset_name="debug", force=False)
        logs = cap.getvalue()
    assert "already installed. Aborting install" in logs

    expected_code = dedent("""\
    # some existing code
    print(123)
    install()  # another import hook

    # <maturin_import_hook>
    # the following commands install the maturin import hook during startup.
    # see: `python -m maturin_import_hook site`
    import maturin_import_hook
    maturin_import_hook.install()
    # </maturin_import_hook>
    """)

    assert sitecustomize.read_text() == expected_code
    assert has_automatic_installation(sitecustomize)

    sitecustomize.write_text(sitecustomize.read_text() + "# some more text\ninstall()\n")

    assert has_automatic_installation(sitecustomize)

    remove_automatic_installation(sitecustomize)

    expected_code = dedent("""\
    # some existing code
    print(123)
    install()  # another import hook

    # some more text
    install()
    """)
    assert sitecustomize.read_text() == expected_code
    assert not has_automatic_installation(sitecustomize)

    with capture_logs() as cap:
        remove_automatic_installation(sitecustomize)
        logs = cap.getvalue()
    assert "no installation found" in logs


def test_automatic_site_installation_force_overwrite(tmp_path: Path) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    header = dedent("""\
    # some existing code
    print(123)
    install()  # another import hook
    """)

    sitecustomize.write_text(header)

    insert_automatic_installation(sitecustomize, preset_name="debug", force=False)

    sitecustomize.write_text(sitecustomize.read_text() + "\n\n# more code")

    with capture_logs() as cap:
        insert_automatic_installation(sitecustomize, preset_name="release", force=True)
        logs = cap.getvalue()
    assert "already installed, but force=True. Overwriting..." in logs

    expected_code = dedent("""\
    # some existing code
    print(123)
    install()  # another import hook



    # more code
    # <maturin_import_hook>
    # the following commands install the maturin import hook during startup.
    # see: `python -m maturin_import_hook site`
    import maturin_import_hook
    from maturin_import_hook.settings import MaturinSettings
    maturin_import_hook.install(MaturinSettings(release=True))
    # </maturin_import_hook>
    """)

    assert sitecustomize.read_text() == expected_code
    assert has_automatic_installation(sitecustomize)


def test_automatic_site_installation_invalid_preset(tmp_path: Path) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    with pytest.raises(ValueError, match="Unknown managed installation preset name: 'foo'"):
        insert_automatic_installation(sitecustomize, preset_name="foo", force=False)
    assert not sitecustomize.exists()


def test_automatic_site_installation_empty(tmp_path: Path) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    insert_automatic_installation(sitecustomize, preset_name="debug", force=False)

    expected_code = dedent("""\
    # <maturin_import_hook>
    # the following commands install the maturin import hook during startup.
    # see: `python -m maturin_import_hook site`
    import maturin_import_hook
    maturin_import_hook.install()
    # </maturin_import_hook>
    """)

    assert sitecustomize.read_text() == expected_code
    assert has_automatic_installation(sitecustomize)

    remove_automatic_installation(sitecustomize)

    assert not has_automatic_installation(sitecustomize)
    assert not sitecustomize.exists()
