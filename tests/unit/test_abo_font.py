"""Direct Python entrypoints must render evidence without system Chinese fonts."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.abo.service import bind_runtime


def test_direct_runtime_uses_project_font_and_renders_an_uncached_evidence_card():
    workspace = bind_runtime()
    import common
    import full_cards
    from full_retrieval import FullRetriever

    with patch.dict(os.environ):
        os.environ.pop("ABO_FONT_PATH", None)
        bind_runtime()
        assert Path(os.environ["ABO_FONT_PATH"]) == workspace / "fonts/NotoSansSC.ttf"
        retriever = FullRetriever()
        try:
            row = dict(
                retriever.db.execute(
                    "SELECT * FROM products WHERE product_type='HEADPHONES' ORDER BY id LIMIT 1"
                ).fetchone()
            )
        finally:
            retriever.close()
        with TemporaryDirectory(dir=workspace / "cache/tmp", prefix="ci-font-") as folder:
            root = Path(folder)
            with (
                patch.object(full_cards, "ROOT", root),
                patch.object(common, "OPERATIONS", root / "operations.txt"),
            ):
                document = full_cards.ensure_card(row, allow_download=False)
                assert document["pages"]
                assert document["raw_sha256"] == row["raw_sha256"]
                assert list(root.glob("cards/*/product.pdf"))


def test_explicit_font_configuration_is_respected():
    with patch.dict(os.environ, {"ABO_FONT_PATH": "explicit-user-font.ttf"}):
        bind_runtime()
        assert os.environ["ABO_FONT_PATH"] == "explicit-user-font.ttf"
