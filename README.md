# PBS 客户端（CLI）for fnOS

A CLI-only, third-party fnOS packaging project. This package does not start a service,
add an APT source, run Debian maintainer scripts, register a global command, or contact
the network at install time. The intended fnOS binary path is
`/var/apps/fnos-pbs-client-cli/target/bin/proxmox-backup-client`.
The GUI is a separate future package. This is not an official Proxmox or fnOS release.

## Current status

The private x86-64 local-debug FPK for package version 0.1.0 has been built
with official fnpack v1.3.3 from the authenticated, user-supplied Proxmox
Backup Client static Debian package 4.0.11-1. The locked metadata and hashes
are in `upstream/pbs-client-trixie-amd64.lock.json`. The client binary read
back from the FPK matches the original payload bytes and ran `version` as
a non-root user in an isolated Linux home. It has not been installed or
tested on fnOS. Do not publish or distribute the unsigned debug package.

## Build from verified offline inputs

Use Linux x86-64 with Python 3.11+, GnuPG, `dpkg-deb`, `tar`, and [official
fnpack](https://developer.fnnas.com/docs/cli/fnpack/) v1.3.3. Nothing needs root
or changes the host's APT setup.

Place four original files already acquired from the [official Proxmox client
repository](https://pbs.proxmox.com/docs/installation.html) under `inputs/`:

1. `proxmox-archive-keyring-trixie.gpg` from
   [the upstream keyring download](https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg).
2. [Trixie InRelease](https://download.proxmox.com/debian/pbs-client/dists/trixie/InRelease),
   named `InRelease`.
3. One original `Packages`, `Packages.gz`, or `Packages.xz` from
   `dists/trixie/main/binary-amd64/`, matching the listed file and checksum in that
   InRelease. Do not re-compress or rename the index.
4. The chosen `proxmox-backup-client-static_*_amd64.deb` from the matching
   signed Packages stanza's `Filename`, relative to
   `https://download.proxmox.com/debian/pbs-client/`.

Select an exact version and SHA-256 from the authenticated index; use both as
explicit pins. Do not treat a hash computed from an unverified `.deb` as its
provenance. The script validates the keyring checksum against upstream documentation,
checks the OpenPGP signature on InRelease, verifies the index's path, SHA-256 and
size, then verifies the pinned package digest, size, and Debian package identity
before unpacking. It refuses a non-x86-64 or dynamically linked CLI and checks for
the upstream copyright. No untrusted binary is executed by the staging script.

From the project root, substituting the exact filenames and pins from the lock file:

```sh
mkdir -p dist
python3 scripts/build_cli.py \
  --deb inputs/proxmox-backup-client-static_VERSION_amd64.deb \
  --inrelease inputs/InRelease \
  --index inputs/Packages.gz \
  --keyring inputs/proxmox-archive-keyring-trixie.gpg \
  --version VERSION --expected-sha256 SHA256 \
  --destination dist/cli-stage
(
  cd dist/cli-stage
  /absolute/path/to/official/fnpack build --nosign
  # fnpack's expected output must exist even if it exits with status zero.
  test -s fnos-pbs-client-cli.fpk || exit 1
  tar -tzf fnos-pbs-client-cli.fpk
  tar -xOzf fnos-pbs-client-cli.fpk app.tgz | tar -tzf -
  sha256sum fnos-pbs-client-cli.fpk > fnos-pbs-client-cli.fpk.sha256
)
```

`--nosign` is only for local debugging. Do not publish an unsigned debug package.
Without this flag, fnpack v1.3.3 requires a configured developer certificate;
signing and release are separate from this build. Do not upload any signing private
key for troubleshooting.

Check the archived CLI SHA-256 against `app/third-party/upstream.json` and the
staged binary via:

```sh
cd dist/cli-stage
tar -xOzf fnos-pbs-client-cli.fpk app.tgz | tar -xOzf - bin/proxmox-backup-client | sha256sum
sha256sum app/bin/proxmox-backup-client
```

Before any binary distribution, preserve upstream legal and dependency notices
and verify/offer the exact corresponding Proxmox and incorporated dependency
sources and required build materials alongside the package. The staging script
retains upstream Debian notices and records binary provenance, but neither
asserts source compliance nor performs any distribution.

## Verification

```sh
FNPACK_TEST_BIN=/absolute/path/to/official/fnpack python3 -m unittest discover -s tests -v
```

Omit `FNPACK_TEST_BIN` to skip only the real fnpack fixture. The fixture tests
intentionally synthesize the index and payload after the signature boundary;
they also separately test rejection of an invalid signature and keyring. This is
not verification of any specific upstream release. No package maintainer hook
is executed in tests or staging. The fixture binary is a synthetic ELF header,
not a usable client.

On an authorized test fnOS, manually install a real package offline, run
`/var/apps/fnos-pbs-client-cli/target/bin/proxmox-backup-client version` as the
actual package account, test its DNS resolution and TLS validation independently,
and back up a permitted fixture to an isolated namespace. Restore to an empty
directory, compare checksums, and check uninstall/upgrade behavior. Do not
grant root or weaken TLS or file permissions to make the verification pass.

## GitHub Actions and Release

The CLI FPK workflow verifies pull requests and pushes to `main` without
release secrets and never uploads its unsigned debug package. It downloads
official fnpack v1.3.3 and checks its pinned SHA-256 before executing it,
verifies live Proxmox repository signatures and the pinned 4.0.11-1
Debian payload, then re-reads the native FPK and checks the archived binary
and copyright. Vendor download failures, metadata changes incompatible with
the pinned package, and validation errors fail the job; there is no fallback.

Publishing a GitHub release triggers a separate, fail-closed upload job.
Before enabling public binary releases:

1. Independently review the exact corresponding source, linked dependency
   source, packaging/build materials and license/copyright notices for the
   pinned package. Prepare a matching public source archive with a clear
   build/reproduction guide. A generic upstream repository link is not enough.
2. After review, add `upstream/release-source.lock.json` to the tagged
   source. Its fields are `asset` (strict basename ending in `.tar.gz`,
   `.tar.xz`, or `.tar.zst`), `sha256` (source archive digest),
   `package_sha256` (same digest as the Debian payload lock),
   `reviewed_for` (exact Debian package version), `reviewed_by` (review
   record or responsible maintainer), and a `coverage` array with
   `upstream-source`, `linked-dependency-source`,
   `packaging-and-build-materials`, and `license-and-copyright-notices`.
   The machine checks this review metadata and archive hash; it cannot
   judge source completeness itself. No unreviewed source lock is shipped.
3. Configure the GitHub `fpk-release` environment for protected release
   tags and require a release reviewer where supported. Add only the
   officially issued fnOS developer key and cert as environment secrets
   `FNPACK_KEY_B64` and `FNPACK_CERT_B64` (base64 of each complete file,
   not encryption). Never paste these in logs, issues or chat or commit
   them. Official fnpack v1.3.3's `build --help` documents
   `build --key ... --cert ...`; a self-signed test cert is not accepted.
4. Use tag `v` plus the matching fnOS manifest version, attach the reviewed
   source archive to the draft release before publishing it, and then
   publish. The workflow validates the already-present asset and its
   checksum before signing. It requires native signature records in the
   output and uploads only the signed FPK and its SHA-256 sidecar, without
   overwriting existing assets.

The unsigned path and release guards are locally exercised. A release
signed by an officially issued certificate and its acceptance on an actual
fnOS device are not yet tested; confirm the signed output layout and its
installation in a controlled run before any public release. If the signing
tool or archive layout changes, the upload gate intentionally fails closed.
