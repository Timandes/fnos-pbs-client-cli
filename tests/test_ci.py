import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import ci


class ReleaseGateTests(unittest.TestCase):
    def test_requires_source_lock_and_matching_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "source.json"
            with self.assertRaisesRegex(ci.BuildError, "source"):
                ci.release_asset(missing, "v0.1.0", "a" * 64, "0.1.0")
            with self.assertRaisesRegex(ci.BuildError, "tag"):
                ci.release_asset(missing, "vother", "a" * 64, "0.1.0")

    def test_source_lock_rejects_wrong_payload_digest_and_unreviewed_lock(self):
        asset = {"asset": "complete-corresponding-source.tar.gz",
                 "sha256": "b" * 64, "package_sha256": "a" * 64,
                 "reviewed_for": "4.0.11-1"}
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "source.json"
            lock.write_text(ci.json.dumps(asset))
            with self.assertRaisesRegex(ci.BuildError, "source"):
                ci.release_asset(lock, "v0.1.0", "c" * 64, "0.1.0")
            with self.assertRaisesRegex(ci.BuildError, "source"):
                ci.release_asset(lock, "v0.1.0", "a" * 64, "0.1.0")

    def test_signed_release_requires_signature_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "test.fpk"
            with tarfile.open(archive, "w:gz") as f:
                member = tarfile.TarInfo("app.tgz")
                member.size = 6
                f.addfile(member, io.BytesIO(b"nested"))
            with self.assertRaisesRegex(ci.BuildError, "signature"):
                ci.require_signature(archive)
