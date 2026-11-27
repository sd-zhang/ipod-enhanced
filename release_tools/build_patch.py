"""Build a data-only .ipepatch from pinned stock and release IPSWs."""

import argparse
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import bsdiff4
from apply_patch import (
    FORMAT,
    MAX_IMAGE,
    apply_bundle,
    checked_read,
    identity,
    publish_file,
    read_archive,
    repack,
)

HERE = Path(__file__).resolve().parent
PROFILES = {
    "ipod4g": {
        "model": "iPod 4G monochrome",
        "revision": "r50",
        "member": "Firmware-4.3.1.1",
        "stock_name": "iPod_4.3.1.1.ipsw",
        "release_name": "iPod_4.3.1.1-gapless-navigation-complete-r50.ipsw",
        "source_sha256": "576e05def7800e28cfd9a28ec36518964ab7a356f3b035e3fc223b0816be9ed9",
        "target_sha256": "6fc37ae7747831b543b57a2b08ea0bd9dc7a4431db003d308e7c3bf8a3f26a28",
    },
    "ipod55g": {
        "model": "iPod 5.5G late 2006",
        "revision": "r19",
        "member": "Firmware-25.6.3",
        "stock_name": "iPod_25.1.3.ipsw",
        "release_name": "iPod_25.1.3-oemplus-r19.ipsw",
        "source_sha256": "840b2480ad5b692c098cb772c3d2bf8f58bf37557788782b596ac01c27ecdc32",
        "target_sha256": "dabe75b6eccbb460b34f7e6080c3915eccae1f1879e2f2d66dddc61457fe334b",
    },
}


def instructions(profile):
    return f"""iPod Enhanced - {profile["model"]} {profile["revision"]} patch preview

This download contains a BSDIFF40 patch and an offline file patcher.
Supply your own matching stock Apple IPSW. No Apple IPSW, private installer,
device backup, personal library or device identifier is included.

Required stock file: {profile["stock_name"]}
Stock SHA-256: {profile["source_sha256"]}
Output IPSW SHA-256: {profile["target_sha256"]}

Apply from this extracted directory using Python and uv:

  uv run --no-project --with bsdiff4==1.4.4 python apply_patch.py {profile["stock_name"]} --output {profile["release_name"]}

Alternatively, install requirements.txt in your own Python virtual environment
and run apply_patch.py with that interpreter. No compiler or firmware build
fixtures are needed to apply the patch when a bsdiff4 wheel is available.
Use a local output directory with hard-link support (for example APFS, NTFS or
ext4); the tool refuses publication on filesystems without that support.

The patcher checks the stock archive, extracted firmware, patch, patched
firmware and final IPSW against manifest.json. Existing outputs are never
replaced. Use the unmodified stock file each time, not another patched release.
Check the download's published SHA-256 before running bundled code. Bundled
checksums detect corruption; they are not an independent signature.

No network access or iPod access occurs in apply_patch.py. uv may download its
Python dependency during setup. No sudo or administrator privilege is needed.

Advanced raw-member mode:

  uv run --no-project --with bsdiff4==1.4.4 python apply_patch.py {profile["stock_name"]} --firmware-only --output patched-firmware.bin

This emits the exact uncompressed {profile["member"]} member. It is NOT a raw
OSOS image and NOT a device firmware partition. Do not pass it to a tool that
expects either of those formats. Standard bspatch can also apply firmware.bsdiff
to the matching extracted stock member; verify source and target hashes from
manifest.json yourself when using that tool directly.

The default IPSW mode requires ZIP compression to reproduce the published
archive bytes exactly. A different compression implementation may fail the
final hash check; no output is published in that case. Raw-member mode avoids
ZIP recompression and still requires an exact firmware-member hash.

This is a developer preview, not an installer. Reconstruction reproduces a
locally validated candidate; it does not add device-test evidence. Private
flashing scripts are not included. Compatibility with iPodWizard, iPodFirm,
Rockbox ipodpatcher or Finder/iTunes installation is not established here.
Keep device backup/recovery separate from this offline conversion.

LICENSE covers this project's tool source. It does not grant rights to Apple's
software or other third-party material. bsdiff4 is installed separately under
its own license. The binary patch is not a complete Apple firmware image.
"""


def build_bundle(profile, stock_path, release_path, output, *, include_tools=False):
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError(f"Output already exists: {output}")
    stock = checked_read(stock_path, MAX_IMAGE)
    release = checked_read(release_path, MAX_IMAGE)
    if identity(stock)["sha256"] != profile["source_sha256"]:
        raise ValueError("Stock archive does not match the pinned profile")
    if identity(release)["sha256"] != profile["target_sha256"]:
        raise ValueError("Release archive does not match the pinned profile")
    member = profile["member"]
    infos, original, comment = read_archive(stock, member)
    _, target, _ = read_archive(release, member)
    if original["manifest.plist"] != target["manifest.plist"]:
        raise ValueError("Release changes the stock updater manifest")
    if repack(infos, target, comment) != release:
        raise ValueError("Release ZIP cannot be reproduced from stock metadata")
    delta = bsdiff4.diff(original[member], target[member])
    manifest = {
        "format": FORMAT,
        "model": profile["model"],
        "revision": profile["revision"],
        "member": member,
        "source_archive": identity(stock),
        "source_firmware": identity(original[member]),
        "target_archive": identity(release),
        "target_firmware": identity(target[member]),
        "patch": identity(delta),
    }
    files = {
        "firmware.bsdiff": delta,
        "manifest.json": (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    }
    if include_tools:
        files["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        files.update(
            {
                "apply_patch.py": (HERE / "apply_patch.py").read_bytes(),
                "requirements.txt": (HERE / "requirements.txt").read_bytes(),
                "LICENSE": (HERE.parent / "LICENSE").read_bytes(),
                "README.txt": instructions(profile).encode(),
            }
        )
        files["SHA256SUMS"] = "".join(
            f"{identity(data)['sha256']}  {name}\n" for name, data in sorted(files.items())
        ).encode()
    # Prove the exact public manifest and patch work before publishing a bundle.
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for name, data in files.items():
            (directory / name).write_bytes(data)
        applied = directory / "reconstructed.ipsw"
        apply_bundle(directory, stock_path, applied)
        if applied.read_bytes() != release:
            raise ValueError("Reconstruction differs from the pinned release")
    bundle_buffer = io.BytesIO()
    with zipfile.ZipFile(bundle_buffer, "w") as bundle:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, content)
    publish_file(output, bundle_buffer.getvalue())
    return {
        "model": profile["model"],
        "revision": profile["revision"],
        "bundle": str(output),
        **identity(bundle_buffer.getvalue()),
        "patch_bytes": len(delta),
        "stock_sha256": profile["source_sha256"],
        "output_ipsw_sha256": profile["target_sha256"],
        "device_access": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=PROFILES, required=True)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument(
        "--firmware", type=Path, required=True, help="Existing validated release IPSW"
    )
    parser.add_argument("--output", type=Path, required=True, help="New .ipepatch file")
    parser.add_argument(
        "--with-tools",
        action="store_true",
        help="Build a legacy ZIP with the Python helper and instructions",
    )
    args = parser.parse_args()
    try:
        result = build_bundle(
            PROFILES[args.target],
            args.stock,
            args.firmware,
            args.output,
            include_tools=args.with_tools,
        )
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Bundle refused: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
