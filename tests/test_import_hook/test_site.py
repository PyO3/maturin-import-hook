from pathlib import Path
from textwrap import dedent

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

    insert_automatic_installation(sitecustomize)

    with capture_logs() as cap:
        insert_automatic_installation(sitecustomize)
        logs = cap.getvalue()
    assert "already installed" in logs

    expected_code = dedent("""\
    # some existing code
    print(123)
    install()  # another import hook

    # <maturin_import_hook>
    # this section of code installs the maturin import hook into every interpreter.
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


def test_automatic_site_installation_empty(tmp_path: Path) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    insert_automatic_installation(sitecustomize)

    expected_code = dedent("""\
    # <maturin_import_hook>
    # this section of code installs the maturin import hook into every interpreter.
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
