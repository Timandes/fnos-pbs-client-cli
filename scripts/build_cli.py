"""Verify a pinned official Proxmox APT payload, and stage a private fnOS FPK."""
import argparse
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile


APP = "fnos-pbs-client-cli"
PACKAGE = "proxmox-backup-client-static"
TRIXIE_KEY_SHA256 = "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45"
TEMPLATE = Path(__file__).resolve().parents[1] / "packages" / "cli"
SHA256 = re.compile(r"[0-9a-f]{64}")


class BuildError(Exception):
    pass


def run(argv, description):
    try:
        return subprocess.run(argv, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise BuildError(f"{description}: {detail or exc.returncode}") from exc


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_file(path, sha256, description, size=None):
    if not SHA256.fullmatch(sha256) or digest(path) != sha256:
        raise BuildError(f"{description} SHA-256 mismatch")
    if size is not None and (not size.isascii() or not size.isdecimal() or
                             path.stat().st_size != int(size)):
        raise BuildError(f"{description} size mismatch")


def stanza(text):
    fields = {}
    name = None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and name is not None:
            fields[name] += "\n" + line.strip()
        elif line:
            name, sep, value = line.partition(":")
            if not sep or not name or name in fields:
                raise BuildError("invalid or duplicate repository field")
            fields[name] = value.strip()
    return fields


def verified_release(inrelease, keyring, scratch):
    # The keyring checksum is published in the upstream PBS installation guide.
    verify_file(keyring, TRIXIE_KEY_SHA256, "Proxmox Trixie signing keyring")
    home = scratch / "gnupg"
    home.mkdir(mode=0o700)
    key = home / "proxmox.gpg"
    shutil.copyfile(keyring, key)
    signed = inrelease.read_bytes()
    if not signed.startswith(b"-----BEGIN PGP SIGNED MESSAGE-----") or (
            signed.count(b"-----BEGIN PGP SIGNED MESSAGE-----") != 1 or
            signed.count(b"-----BEGIN PGP SIGNATURE-----") != 1 or
            not signed.rstrip().endswith(b"-----END PGP SIGNATURE-----")):
        raise BuildError("InRelease signature format is invalid")
    process = subprocess.run([
        "gpg", "--no-options", "--homedir", str(home), "--batch",
        "--no-default-keyring", "--keyring", str(key), "--no-auto-key-retrieve",
        "--no-autostart", "--status-fd", "2", "--decrypt", str(inrelease),
    ], capture_output=True)
    status = process.stderr.decode(errors="replace")
    fingerprints = re.findall(r"^\[GNUPG:\] VALIDSIG ([A-Fa-f0-9]{40,64}) ",
                              status, flags=re.MULTILINE)
    if process.returncode or len(fingerprints) != 1:
        raise BuildError("InRelease signature verification failed")
    release = process.stdout
    fields = stanza(release.decode("utf-8"))
    if (fields.get("Origin") != "Proxmox" or fields.get("Codename") != "trixie"
            or "main" not in fields.get("Components", "").split()
            or "amd64" not in fields.get("Architectures", "").split()):
        raise BuildError("InRelease is not the Proxmox Trixie main amd64 repository")
    if "Valid-Until" in fields and parsedate_to_datetime(fields["Valid-Until"]) < datetime.now(timezone.utc):
        raise BuildError("InRelease expired")
    return fields


def verify_index(index, release, version, expected_sha256):
    loaders = {"Packages": open, "Packages.gz": gzip.open, "Packages.xz": lzma.open}
    if index.name not in loaders:
        raise BuildError("provide original Packages, Packages.gz, or Packages.xz filename")
    expected_name = f"main/binary-amd64/{index.name}"
    checks = [line.split() for line in release.get("SHA256", "").splitlines()]
    checks = [row for row in checks if len(row) == 3 and row[2] == expected_name]
    if len(checks) != 1:
        raise BuildError(f"InRelease has no unique SHA-256 for {expected_name}")
    checksum, size, _ = checks[0]
    verify_file(index, checksum, "package index", size)
    with loaders[index.name](index, "rt", encoding="utf-8") as stream:
        paragraphs = re.split(r"\n[ \t]*\n", stream.read())
    matches = [entry for entry in map(stanza, paragraphs)
               if (entry.get("Package"), entry.get("Version"), entry.get("Architecture"))
               == (PACKAGE, version, "amd64")]
    if len(matches) != 1:
        raise BuildError(f"no unique package record for {PACKAGE} {version} amd64")
    entry = matches[0]
    if entry.get("SHA256") != expected_sha256 or not SHA256.fullmatch(expected_sha256):
        raise BuildError("signed payload SHA-256 does not match the requested pin")
    filename = entry.get("Filename", "")
    parts = PurePosixPath(filename).parts
    expected_basename = f"{PACKAGE}_{version}_amd64.deb"
    upstream_flat = ("dists", "trixie", "main", "binary-amd64", expected_basename)
    if (not re.fullmatch(r"[A-Za-z0-9.+~:-]+", version) or
            not ((parts and parts[0] == "pool" and ".." not in parts and
                  parts[-1] == expected_basename) or parts == upstream_flat)):
        raise BuildError("unexpected signed repository payload path")
    if not entry.get("Size"):
        raise BuildError("signed package record has no payload size")
    return entry


def verify_static_elf(path):
    with path.open("rb") as f:
        header = f.read(64)
        if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
            raise BuildError("client is not little-endian ELF64 x86-64")
        typ, machine, _, _, offset, _, _, _, entsize, count, *_ = struct.unpack_from(
            "<HHIQQQIHHHHHH", header, 16)
        if (machine != 62 or typ not in (2, 3) or entsize != 56 or
                not count or offset < 64 or offset + count * entsize > path.stat().st_size):
            raise BuildError("client is not an executable ELF64 x86-64")
        f.seek(offset)
        load = False
        for _ in range(count):
            entry = f.read(entsize)
            segment = struct.unpack_from("<I", entry)[0]
            if segment == 3:
                raise BuildError("client uses a dynamic interpreter")
            if segment == 2:
                _, _, pos, _, _, size, _, _ = struct.unpack("<IIQQQQQQ", entry)
                if size % 16 or pos + size > path.stat().st_size:
                    raise BuildError("invalid ELF dynamic segment")
                current = f.tell()
                f.seek(pos)
                for _ in range(size // 16):
                    tag, _ = struct.unpack("<QQ", f.read(16))
                    if tag == 1:
                        raise BuildError("client needs a dynamic library")
                    if tag == 0:
                        break
                f.seek(current)
            load |= segment == 1
        if not load:
            raise BuildError("ELF executable has no loadable segment")


def stage(*, deb, inrelease, index, keyring, version, expected_sha256, destination):
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise BuildError("stage destination must be new with an existing parent directory")
    with tempfile.TemporaryDirectory(prefix=".cli-build-", dir=destination.parent) as temp:
        scratch = Path(temp)
        # Use a stable private copy from verification through extraction.
        for original in (deb, inrelease, index, keyring):
            if not original.is_file():
                raise BuildError(f"required input not found: {original}")
        # Preserve the index basename, without trusting arbitrary other names.
        copied = {"keyring": scratch / "keyring.gpg", "inrelease": scratch / "InRelease",
                  "index": scratch / "index" / index.name, "deb": scratch / "payload.deb"}
        copied["index"].parent.mkdir()
        for name, original in (("keyring", keyring), ("inrelease", inrelease),
                               ("index", index), ("deb", deb)):
            shutil.copyfile(original, copied[name])
        rel = verified_release(copied["inrelease"], copied["keyring"], scratch)
        record = verify_index(copied["index"], rel, version, expected_sha256)
        deb = copied["deb"]
        verify_file(deb, record["SHA256"], "Debian payload", record["Size"])
        for name, expected in {"Package": PACKAGE, "Version": version, "Architecture": "amd64"}.items():
            found = run(["dpkg-deb", "-f", str(deb), name], "reading Debian metadata").decode().strip()
            if found != expected:
                raise BuildError(f"unexpected Debian {name}: {found}")
        unpack = scratch / "unpack"
        run(["dpkg-deb", "-x", str(deb), str(unpack)], "extracting Debian payload")
        binary = unpack / "usr/bin/proxmox-backup-client"
        notices = unpack / f"usr/share/doc/{PACKAGE}"
        if not binary.is_file() or binary.is_symlink() or not binary.resolve().is_relative_to(unpack):
            raise BuildError("upstream CLI is missing or is not a regular payload file")
        verify_static_elf(binary)
        if (not (notices / "copyright").is_file() or
                not notices.resolve().is_relative_to(unpack) or any(p.is_symlink() for p in notices.rglob("*"))):
            raise BuildError("upstream copyright notices missing or unsupported symlink")
        staged = scratch / "stage"
        shutil.copytree(TEMPLATE, staged)
        (staged / "wizard").mkdir()
        bindir = staged / "app/bin"
        bindir.mkdir(parents=True)
        target = bindir / binary.name
        shutil.copyfile(binary, target)
        target.chmod(0o755)
        target_digest = digest(target)
        if target_digest != digest(binary):
            raise BuildError("staged bytes differ from upstream executable")
        thirdparty = staged / "app/third-party"
        shutil.copytree(notices, thirdparty / PACKAGE)
        (thirdparty / "upstream.json").write_text(json.dumps({
            "package": PACKAGE, "version": version, "architecture": "amd64",
            "distribution": "trixie/main",
            "repository": "https://download.proxmox.com/debian/pbs-client",
            "filename": record["Filename"], "package_sha256": expected_sha256,
            "binary_sha256": target_digest, "inrelease_sha256": digest(copied["inrelease"]),
            "index_sha256": digest(copied["index"]), "distribution_status": "private-test-only",
        }, indent=2) + "\n", encoding="utf-8")
        os.rename(staged, destination)


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("deb", "inrelease", "index", "keyring", "destination"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    try:
        stage(**{k: v.resolve() if isinstance(v, Path) and k != "destination"
                 else v.absolute() if isinstance(v, Path) else v
                 for k, v in vars(args).items()})
    except (OSError, ValueError, BuildError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"Verified staging directory: {args.destination}")


if __name__ == "__main__":
    cli()
