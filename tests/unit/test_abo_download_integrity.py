"""A failed or interrupted public download must never replace accepted data."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

import pytest

from app.abo.service import bind_runtime
from app.abo.web_setup import download


def test_download_recovers_from_network_and_checksum_failure_then_reuses_cache():
    workspace = bind_runtime()
    with TemporaryDirectory(dir=workspace / "cache/tmp", prefix="ci-download-") as folder:
        target = Path(folder) / "source.bin"
        digest = sha256(b"accepted source").hexdigest()
        with patch(
            "app.abo.web_setup.urlopen",
            side_effect=[URLError("interrupted"), BytesIO(b"corrupt"), BytesIO(b"accepted source")],
        ) as request:
            download("https://example.test/source", target, digest)
            assert target.read_bytes() == b"accepted source"
            assert request.call_count == 3
            download("https://example.test/source", target, digest)
            assert request.call_count == 3
        assert not target.with_suffix(".bin.part").exists()


def test_exhausted_download_preserves_previous_file_and_cleans_partial():
    workspace = bind_runtime()
    with TemporaryDirectory(dir=workspace / "cache/tmp", prefix="ci-download-") as folder:
        target = Path(folder) / "source.bin"
        target.write_bytes(b"previous source")
        with patch("app.abo.web_setup.urlopen", side_effect=lambda *a, **kw: BytesIO(b"corrupt")):
            with pytest.raises(ValueError, match="Checksum mismatch"):
                download("https://example.test/source", target, sha256(b"new source").hexdigest())
        assert target.read_bytes() == b"previous source"
        assert not target.with_suffix(".bin.part").exists()
