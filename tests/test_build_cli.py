import gzip
import hashlib
import io
import json
import os
import pathlib
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_cli as b


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def elf(machine=62):
    # Minimal synthetic ELF64 executable header with one PT_LOAD entry.
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    ehdr = struct.pack("<HHIQQQIHHHHHH", 2, machine, 1, 0, 64, 0, 0,
                       64, 56, 1, 0, 0, 0)
    phdr = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 120, 120, 4096)
    return ident + ehdr + phdr


class Fixture(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = pathlib.Path(scratch.name)
        self.keyring = self.root / "keyring.gpg"
        self.keyring.write_bytes(b"Test keyring stub; not a trusted production key")

    def inputs(self, *, package_arch="amd64", machine=62, suffix=""):
        d = self.root / self._testMethodName
        d.mkdir()
        p = d / "contents"
        (p / "DEBIAN").mkdir(parents=True)
        (p / "DEBIAN/control").write_text(f"""Package: proxmox-backup-client-static
Version: 4.2.0-1
Architecture: {package_arch}
Maintainer: Test <test@example.invalid>
Description: Synthetic fixture only
""")
        (p / "DEBIAN/postinst").write_text("#!/bin/sh\nexit 91\n")
        (p / "DEBIAN/postinst").chmod(0o755)
        binary = p / "usr/bin/proxmox-backup-client"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(elf(machine))
        binary.chmod(0o755)
        notice = p / "usr/share/doc/proxmox-backup-client-static/copyright"
        notice.parent.mkdir(parents=True)
        notice.write_text("Synthetic fixture copyright only\n")
        deb = d / "payload.deb"
        subprocess.run(["dpkg-deb", "--build", str(p), str(deb)],
                       check=True, capture_output=True)
        payload = deb.read_bytes()
        digest = sha(payload)
        index = gzip.compress(f"""Package: proxmox-backup-client-static
Version: 4.2.0-1
Architecture: amd64
Filename: dists/trixie/main/binary-amd64/proxmox-backup-client-static_4.2.0-1_amd64.deb
SHA256: {digest}
Size: {len(payload)}
{suffix}

""".encode())
        (d / "Packages.gz").write_bytes(index)
        release = d / "Release"
        release.write_text(f"""Origin: Proxmox
Codename: trixie
Components: main
Architectures: amd64
SHA256:
 {sha(index)} {len(index)} main/binary-amd64/Packages.gz
""")
        (d / "InRelease").write_bytes(b"synthetic test input")
        return d, digest

    def stage(self, d, digest):
        with patch.object(b, "verified_release", return_value=b.stanza((d / "Release").read_text())):
            b.stage(deb=d / "payload.deb", inrelease=d / "InRelease",
                    index=d / "Packages.gz", keyring=self.keyring,
                    version="4.2.0-1", expected_sha256=digest, destination=d / "stage")

    def test_index_verified_payload_byte_identical_and_notice_retained(self):
        d, digest = self.inputs()
        self.stage(d, digest)
        self.assertEqual((d / "stage/app/bin/proxmox-backup-client").read_bytes(), elf())
        self.assertTrue((d / "stage/app/third-party/proxmox-backup-client-static/copyright").exists())
        self.assertFalse((d / "stage/DEBIAN").exists())
        self.assertFalse((d / "stage/app/ui").exists())

    def test_staging_has_offline_unprivileged_fnpack_structure(self):
        d, digest = self.inputs()
        self.stage(d, digest)
        stage = d / "stage"
        for name, size in [("ICON.PNG", 64), ("ICON_256.PNG", 256)]:
            png = (stage / name).read_bytes()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack_from(">II", png, 16), (size, size))
        self.assertEqual(json.loads((stage / "config/privilege").read_text()),
                         {"defaults": {"run-as": "package"}})
        self.assertEqual(json.loads((stage / "config/resource").read_text()),
                         {"usr-local-linker": {"bin": ["bin/proxmox-backup-client"]}})
        manifest = (stage / "manifest").read_text()
        self.assertIn("appname=fnos-pbs-client-cli", manifest.splitlines())
        self.assertIn("display_name=Proxmox Backup Client", manifest.splitlines())
        self.assertIn("platform=x86", manifest)
        self.assertIn("ctl_stop=false", manifest)
        self.assertTrue((stage / "wizard").is_dir())
        cmd = stage / "cmd"
        for path in cmd.iterdir():
            self.assertTrue(path.stat().st_mode & 0o111)
            if path.name != "main":
                self.assertEqual(path.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual(subprocess.run([cmd / "main", "status"]).returncode, 3)

    @unittest.skipUnless(os.getenv("FNPACK_TEST_BIN"), "optional official fnpack fixture")
    def test_official_fnpack_preserves_payload_bytes_and_mode(self):
        d, digest = self.inputs()
        self.stage(d, digest)
        stage = d / "stage"
        subprocess.run([os.environ["FNPACK_TEST_BIN"], "build", "--nosign"],
                       cwd=stage, check=True, capture_output=True)
        self.assertEqual(list(stage.glob("*.fpk")), [stage / f"{b.APP}.fpk"])
        with tarfile.open(stage / f"{b.APP}.fpk") as fpk:
            self.assertTrue({"app.tgz", "manifest", "ICON.PNG", "ICON_256.PNG"}.issubset(
                fpk.getnames()))
            manifest = dict(tuple(field.strip() for field in line.split("=", 1))
                            for line in fpk.extractfile("manifest").read().decode().splitlines()
                            if "=" in line)
            self.assertEqual(manifest["appname"], "fnos-pbs-client-cli")
            self.assertEqual(manifest["display_name"], "Proxmox Backup Client")
            self.assertEqual(json.load(fpk.extractfile("config/resource")),
                             {"usr-local-linker": {"bin": ["bin/proxmox-backup-client"]}})
            compressed_app = fpk.extractfile("app.tgz").read()
        with tarfile.open(fileobj=io.BytesIO(compressed_app)) as app:
            client = "bin/proxmox-backup-client"
            self.assertEqual(app.extractfile(client).read(), elf())
            self.assertEqual(app.getmember(client).mode, 0o755)
            self.assertIn(f"third-party/{b.PACKAGE}/copyright", app.getnames())
            self.assertIn("third-party/upstream.json", app.getnames())

    def test_digest_mismatch_does_not_stage(self):
        d, digest = self.inputs()
        with (d / "payload.deb").open("ab") as f:
            f.write(b"corruption")
        with self.assertRaisesRegex(b.BuildError, "payload"):
            self.stage(d, digest)
        self.assertFalse((d / "stage").exists())

    def test_control_arch_mismatch_does_not_stage(self):
        d, digest = self.inputs(package_arch="arm64")
        with self.assertRaisesRegex(b.BuildError, "Architecture"):
            self.stage(d, digest)
        self.assertFalse((d / "stage").exists())

    def test_elf_machine_mismatch_does_not_stage(self):
        d, digest = self.inputs(machine=183)
        with self.assertRaisesRegex(b.BuildError, "x86-64"):
            self.stage(d, digest)
        self.assertFalse((d / "stage").exists())

    def test_unsigned_index_changes_do_not_stage(self):
        d, digest = self.inputs()
        with (d / "Packages.gz").open("ab") as f:
            f.write(b"X")
        with self.assertRaisesRegex(b.BuildError, "index"):
            self.stage(d, digest)
        self.assertFalse((d / "stage").exists())

    def test_invalid_signature_does_not_stage(self):
        d, digest = self.inputs()
        (d / "InRelease").write_bytes(
            b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\ninvalid\n"
            b"-----BEGIN PGP SIGNATURE-----\n\nincomplete\n-----END PGP SIGNATURE-----\n")
        with patch.object(b, "TRIXIE_KEY_SHA256", sha(self.keyring.read_bytes())):
            with self.assertRaisesRegex(b.BuildError, "signature"):
                b.stage(deb=d / "payload.deb", inrelease=d / "InRelease",
                        index=d / "Packages.gz", keyring=self.keyring,
                        version="4.2.0-1", expected_sha256=digest, destination=d / "stage")
        self.assertFalse((d / "stage").exists())

    def test_untrusted_keyring_does_not_stage(self):
        d, digest = self.inputs()
        with self.assertRaisesRegex(b.BuildError, "signing keyring"):
            b.stage(deb=d / "payload.deb", inrelease=d / "InRelease",
                    index=d / "Packages.gz", keyring=self.keyring,
                    version="4.2.0-1", expected_sha256=digest, destination=d / "stage")
        self.assertFalse((d / "stage").exists())


if __name__ == "__main__":
    unittest.main()
