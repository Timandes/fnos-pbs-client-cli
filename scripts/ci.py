"""Build a pinned native FPK in CI and enforce private/public release boundaries."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from scripts import build_cli as b
from scripts.build_cli import BuildError


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_LOCK = ROOT / "upstream/pbs-client-trixie-amd64.lock.json"
SOURCE_LOCK = ROOT / "upstream/release-source.lock.json"
COVERAGE = {"upstream-source", "linked-dependency-source", "packaging-and-build-materials",
            "license-and-copyright-notices"}
FNPACK = "https://static2.fnnas.com/fnpack/fnpack-1.3.3-linux-amd64"
FNPACK_SHA256 = "1464d6aa9b995ebecb263c5ec60f0f4a7cbf45e0ce887836755fd65abd647d1e"
KEYRING = "https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg"
REPO = "https://download.proxmox.com/debian/pbs-client"


def download(url, output):
    # Only explicit vendor URLs; do not follow redirects to another origin.
    b.run(["curl", "--disable", "--fail", "--silent", "--show-error", "--proto", "=https",
           "--connect-timeout", "20", "--max-time", "180", "--max-filesize", str(128 * 1024**2),
           "--output", str(output), url], f"fetching {url}")
    if not output.is_file() or not output.stat().st_size:
        raise BuildError(f"empty vendor download: {url}")


def build_inputs(work):
    lock = json.loads(UPSTREAM_LOCK.read_text())
    version = lock["version"]
    expected = (f"dists/trixie/main/binary-amd64/{b.PACKAGE}_{version}_amd64.deb")
    if not re.fullmatch(r"[A-Za-z0-9.+~-]+", version) or lock["filename"] != expected:
        raise BuildError("unrecognized pinned upstream path")
    for name, url in {
        "fnpack": FNPACK, "keyring.gpg": KEYRING,
        "InRelease": REPO + "/dists/trixie/InRelease",
        "Packages.gz": REPO + "/dists/trixie/main/binary-amd64/Packages.gz",
        "payload.deb": REPO + "/" + expected,
    }.items():
        download(url, work / name)
    fnpack = work / "fnpack"
    b.verify_file(fnpack, FNPACK_SHA256, "official fnpack")
    b.verify_static_elf(fnpack)
    fnpack.chmod(0o700)
    return fnpack, lock


def release_asset(lock_file, tag, package_sha, app_version, package_version="4.0.11-1"):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", app_version) or tag != f"v{app_version}":
        raise BuildError(f"release tag must be v{app_version}")
    if not lock_file.is_file():
        raise BuildError("release blocked: reviewed corresponding source lock is missing")
    source = json.loads(lock_file.read_text())
    asset, sha = source.get("asset", ""), source.get("sha256", "")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,160}\.tar\.(?:gz|xz|zst)", asset)
            or not b.SHA256.fullmatch(sha)
            or source.get("package_sha256") != package_sha
            or source.get("reviewed_for") != package_version
            or not isinstance(source.get("reviewed_by"), str)
            or not source["reviewed_by"].strip()
            or set(source.get("coverage", [])) != COVERAGE):
        raise BuildError("release blocked: invalid corresponding source lock or payload mismatch")
    return asset, sha


def require_signature(fpk):
    # Fail closed. fnpack's release signing path must emit the native signature
    # members, not just exit zero (it can print an error and return zero).
    with tarfile.open(fpk) as outer:
        records = [m for m in outer if m.isfile()]
        by_name = {m.name.removeprefix("./"): m for m in records}
        if not all(name in by_name and by_name[name].size > 0
                   for name in ("signature.json", "signature.sig")):
            raise BuildError("signed output lacks native fnpack signature records")


def verify_fpk(fpk, binary_sha, notice):
    found = set()
    with tarfile.open(fpk) as outer, tempfile.NamedTemporaryFile() as nested:
        archive = outer.getmember("app.tgz")
        if not archive.isfile() or not archive.size:
            raise BuildError("invalid app.tgz in FPK")
        shutil.copyfileobj(outer.extractfile(archive), nested)
        nested.flush()
        with tarfile.open(nested.name) as app:
            expected = {
                "bin/proxmox-backup-client": (binary_sha, 0o755),
                f"third-party/{b.PACKAGE}/copyright": (b.digest(notice), None),
            }
            import hashlib
            for member in app:
                name = member.name.removeprefix("./")
                if name in expected:
                    sha, mode = expected[name]
                    if (name in found or not member.isfile() or
                        (mode is not None and member.mode != mode) or
                            hashlib.file_digest(app.extractfile(member), "sha256").hexdigest() != sha):
                        raise BuildError(f"FPK entry integrity check failed: {name}")
                    found.add(name)
    if found != expected.keys():
        raise BuildError("FPK is missing the verified executable or copyright")


def app_version():
    manifest = (b.TEMPLATE / "manifest").read_text().splitlines()
    m = [line.removeprefix("version=") for line in manifest if line.startswith("version=")]
    if len(m) != 1 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", m[0]):
        raise BuildError("expected a unique numeric manifest version")
    return m[0]


def build(destination, *, key=None, cert=None):
    if (key is None) != (cert is None):
        raise BuildError("provide both developer key and certificate")
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"{b.APP}.fpk"
    sidecar = destination / f"{b.APP}.fpk.sha256"
    if output.exists() or sidecar.exists():
        raise BuildError("refusing to overwrite an existing build")
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        artifact, lock = build_inputs(work)
        stage = work / "stage"
        b.stage(deb=work / "payload.deb", inrelease=work / "InRelease",
                index=work / "Packages.gz", keyring=work / "keyring.gpg",
                version=lock["version"], expected_sha256=lock["package_sha256"],
                destination=stage)
        binary = stage / "app/bin/proxmox-backup-client"
        b.verify_file(binary, lock["binary_sha256"], "pinned client executable")
        args = ["--nosign"] if key is None else ["--key", str(key), "--cert", str(cert)]
        result = subprocess.run([str(artifact), "build", *args], cwd=stage, check=True,
                                capture_output=True)
        fpk = stage / f"{b.APP}.fpk"
        if not fpk.is_file() or not fpk.stat().st_size:
            print(result.stdout.decode(errors="replace"))
            raise BuildError("fnpack did not produce FPK")
        if key:
            require_signature(fpk)
        verify_fpk(fpk, lock["binary_sha256"],
                   stage / f"app/third-party/{b.PACKAGE}/copyright")
        shutil.copyfile(fpk, output)
        sidecar.write_text(f"{b.digest(output)}  {output.name}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--output", type=Path, required=True)
    builder.add_argument("--key", type=Path)
    builder.add_argument("--cert", type=Path)
    gate = sub.add_parser("release-source")
    gate.add_argument("--tag", required=True)
    gate.add_argument("--verify", type=Path, help="downloaded asset to verify; otherwise print its name")
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.output.resolve(), key=args.key.resolve() if args.key else None,
                  cert=args.cert.resolve() if args.cert else None)
        else:
            lock = json.loads(UPSTREAM_LOCK.read_text())
            asset, sha = release_asset(SOURCE_LOCK, args.tag, lock["package_sha256"],
                                       app_version(), lock["version"])
            if args.verify:
                b.verify_file(args.verify, sha, "reviewed corresponding source archive")
            else:
                print(asset)
    except (BuildError, OSError, ValueError, KeyError, subprocess.CalledProcessError,
            tarfile.TarError) as e:
        parser.exit(1, f"error: {e}\n")


if __name__ == "__main__":
    main()
