"""Build a release-independent browser patcher for IPSW + .ipepatch inputs."""

import argparse
import base64
import hashlib
import html
import json
import os
import re
from pathlib import Path

from apply_patch import identity, publish_file

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
WORKER_FILES = [
    "vendor/pako.min.js",
    "vendor/BinFile.js",
    "vendor/RomPatcher.format.bdf.js",
    "core.js",
    "worker.js",
]


def json_for_html(value):
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def csp_hash(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def render_licenses():
    binfile = (WEB / "vendor/BinFile.js").read_text().split("*/", 1)[0]
    binfile = re.sub(r"^\s*\* ?", "", binfile, flags=re.MULTILINE)
    bdf = (WEB / "vendor/RomPatcher.format.bdf.js").read_text()
    sheetjs = bdf[bdf.index("bz2 (C)") : bdf.index("*/", bdf.index("bz2 (C)"))].strip()
    licenses = [
        ("iPod Firmware Patcher", (HERE.parent / "LICENSE").read_text()),
        ("Rom Patcher JS (modified)", (WEB / "vendor/rom-patcher-LICENSE.txt").read_text()),
        ("BinFile.js (modified)", binfile),
        ("pako", (WEB / "vendor/pako-LICENSE.txt").read_text()),
        ("SheetJS bz2 (modified)", sheetjs),
    ]
    credits = []
    mit_terms = ""
    for name, text in licenses:
        header, marker, terms = text.partition("Permission is hereby granted,")
        # Retain the code's copyright notices. Octicons is named in the upstream
        # repository license, but no Octicons files are bundled in this page.
        copyrights = re.findall(r"^(?:Copyright[^\n]+|bz2 \(C\)[^\n]+)$", header, re.MULTILINE)
        if not marker or not copyrights:
            raise ValueError(f"Missing MIT notice: {name}")
        terms = (marker + terms).strip()
        if mit_terms and terms.split() != mit_terms.split():
            raise ValueError(f"MIT terms differ: {name}; review before consolidating")
        mit_terms = mit_terms or terms
        credits.append(name + "\n" + "\n".join(copyrights))
    mit = "\n\n".join([*credits, mit_terms])
    zlib = (WEB / "vendor/pako-zlib-LICENSE.txt").read_text()
    zlib = re.sub(r"^// ?", "", zlib, flags=re.MULTILINE).strip()
    return (
        "<h3>MIT</h3><pre>"
        + html.escape(mit)
        + "</pre><h3>zlib — pako's zlib port</h3><pre>"
        + html.escape(zlib)
        + "</pre>"
    )


def render_page():
    sources = json.loads((WEB / "vendor/sources.json").read_text())
    for entry in sources["files"]:
        if identity((WEB / "vendor" / entry["file"]).read_bytes())["sha256"] != entry["sha256"]:
            raise ValueError(f"Vendored source hash mismatch: {entry['file']}")
    worker = "\n;\n".join((WEB / name).read_text() for name in WORKER_FILES)
    app = (WEB / "app.js").read_text()
    style = (WEB / "style.css").read_text()
    csp = (
        "default-src 'none'; script-src "
        + csp_hash(app)
        + "; style-src "
        + csp_hash(style)
        + "; worker-src blob:; child-src blob:; frame-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'"
    )
    values = {
        "CSP": html.escape(csp, quote=True),
        "STYLE": style,
        "APP": app,
        "WORKER": json_for_html(worker),
        "LICENSES": render_licenses(),
    }
    template = (WEB / "template.html").read_text()
    placeholders = re.findall(r"\{\{([A-Z]+)\}\}", template)
    if sorted(placeholders) != sorted(values):
        raise ValueError("Unexpected template placeholders")
    # One substitution pass: embedded data cannot introduce further placeholders.
    return re.sub(r"\{\{([A-Z]+)\}\}", lambda match: values[match[1]], template).encode()


def build_site(output):
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError(f"Output already exists: {output}")
    page = render_page()
    output.mkdir()
    try:
        publish_file(output / "index.html", page)
    except OSError:
        # Remove only our new, empty directory; never remove an existing artifact.
        try:
            output.rmdir()
        except OSError:
            pass
        raise
    return {
        "output": str(output / "index.html"),
        **identity(page),
        "patch_format": "ipod-enhanced-bsdiff-v1",
        "network_or_device_access": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    try:
        result = build_site(args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Web build refused: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
