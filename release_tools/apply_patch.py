"""Apply a release patch to a stock IPSW file. Never accesses an iPod."""

import argparse
import copy
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path

FORMAT = "ipod-enhanced-bsdiff-v1"
MAX_IMAGE = 32 * 1024 * 1024
MAX_PATCH = 16 * 1024 * 1024


def identity(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def checked_read(path, limit):
    """Reject devices, pipes and symlinks before reading bounded regular files."""
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Expected a regular file: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError(f"Unsupported file type or size: {path.name}")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File exceeds size limit: {path.name}")
    return data


def require_identity(data, expected, label):
    if identity(data) != expected:
        raise ValueError(f"{label} size/SHA-256 mismatch")


def load_manifest(bundle):
    manifest = json.loads(checked_read(Path(bundle) / "manifest.json", 16384))
    keys = {
        "format",
        "model",
        "revision",
        "member",
        "source_archive",
        "source_firmware",
        "target_archive",
        "target_firmware",
        "patch",
    }
    if not isinstance(manifest, dict) or set(manifest) != keys or manifest["format"] != FORMAT:
        raise ValueError("Unsupported patch manifest")
    for name in ("model", "revision"):
        value = manifest[name]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", value):
            raise ValueError(f"Invalid {name} in manifest")
    member = manifest["member"]
    if not isinstance(member, str) or not re.fullmatch(r"Firmware-[A-Za-z0-9._-]{1,80}", member):
        raise ValueError("Invalid firmware member name")
    for name in ("source_archive", "source_firmware", "target_archive", "target_firmware", "patch"):
        record = manifest[name]
        limit = MAX_PATCH if name == "patch" else MAX_IMAGE
        if not isinstance(record, dict) or set(record) != {"size", "sha256"}:
            raise ValueError(f"Invalid {name} identity")
        if type(record["size"]) is not int or not 0 < record["size"] <= limit:
            raise ValueError(f"Invalid {name} size")
        if not isinstance(record["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", record["sha256"]
        ):
            raise ValueError(f"Invalid {name} SHA-256")
    return manifest


def read_archive(data, member):
    """Read only the two expected members, without extracting filesystem paths."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != [member, "manifest.plist"]:
            raise ValueError("Unsupported IPSW member list")
        infos = archive.infolist()
        for info, limit in zip(infos, (MAX_IMAGE, 16384)):
            if info.file_size > limit or info.flag_bits & 1:
                raise ValueError("Unsupported IPSW member size or encryption")
        members = {info.filename: archive.read(info) for info in infos}
        return infos, members, archive.comment


def repack(infos, members, comment):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(copy.copy(info), members[info.filename])
    return output.getvalue()


def publish_file(output, data):
    """Publish a fully written file without replacing even a racing destination."""
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError(f"Output already exists: {output}")
    descriptor, temporary = tempfile.mkstemp(prefix=".ipod-patch-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard link is atomic and fails if the name exists.
        # Filesystems without hard-link support fail without a final output.
        os.link(temporary, output)
    finally:
        os.unlink(temporary)


def apply_bundle(bundle, source, output, *, firmware_only=False):
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError(f"Output already exists: {output}")
    manifest = load_manifest(bundle)
    stock = checked_read(source, MAX_IMAGE)
    require_identity(stock, manifest["source_archive"], "Stock IPSW (model/version must match)")
    infos, members, comment = read_archive(stock, manifest["member"])
    original = members[manifest["member"]]
    require_identity(original, manifest["source_firmware"], "Stock firmware member")
    delta = checked_read(Path(bundle) / "firmware.bsdiff", MAX_PATCH)
    require_identity(delta, manifest["patch"], "Patch")
    if len(delta) < 32 or delta[:8] != b"BSDIFF40":
        raise ValueError("Expected a BSDIFF40 patch")
    expected_size = manifest["target_firmware"]["size"]
    if delta[24:32] != expected_size.to_bytes(8, "little"):
        raise ValueError("BSDIFF40 target size mismatch")
    try:
        import bsdiff4
    except ImportError as exc:
        raise ValueError(
            "Install the bundled requirements.txt in a Python virtual environment first"
        ) from exc
    try:
        firmware = bsdiff4.patch(original, delta)
    except Exception as exc:
        raise ValueError("Invalid BSDIFF40 patch") from exc
    require_identity(firmware, manifest["target_firmware"], "Patched firmware member")
    if firmware_only:
        result = firmware
    else:
        members[manifest["member"]] = firmware
        result = repack(infos, members, comment)
        require_identity(
            result,
            manifest["target_archive"],
            "Rebuilt IPSW (ZIP compression must reproduce release bytes; "
            "--firmware-only produces the verified raw member instead)",
        )
    publish_file(output, result)
    return {
        "model": manifest["model"],
        "revision": manifest["revision"],
        "output": str(output),
        "kind": "IPSW firmware member" if firmware_only else "IPSW archive",
        **identity(result),
        "device_access": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path, help="Matching stock Apple IPSW file")
    parser.add_argument(
        "--output", type=Path, required=True, help="New output file; never overwritten"
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Extracted patch bundle directory (default: beside this script)",
    )
    parser.add_argument(
        "--firmware-only",
        action="store_true",
        help="Write the raw IPSW Firmware member, not an OSOS image or disk partition",
    )
    args = parser.parse_args()
    try:
        result = apply_bundle(
            args.bundle, args.stock, args.output, firmware_only=args.firmware_only
        )
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Patch refused: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
