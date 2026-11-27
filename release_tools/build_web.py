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


def render_page():
    sources = json.loads((WEB / "vendor/sources.json").read_text())
    for entry in sources["files"]:
        if identity((WEB / "vendor" / entry["file"]).read_bytes())["sha256"] != entry["sha256"]:
            raise ValueError(f"Vendored source hash mismatch: {entry['file']}")
    worker = "\n;\n".join((WEB / name).read_text() for name in WORKER_FILES)
    app = (WEB / "app.js").read_text()
    style = (WEB / "style.css").read_text()
    licenses = [
        ("iPod Enhanced", HERE.parent / "LICENSE"),
        ("Rom Patcher JS", WEB / "vendor/rom-patcher-LICENSE.txt"),
        ("pako", WEB / "vendor/pako-LICENSE.txt"),
        ("pako · zlib", WEB / "vendor/pako-zlib-LICENSE.txt"),
    ]
    # SheetJS's full license is retained in the BDF module; expose it in the UI too.
    bdf = (WEB / "vendor/RomPatcher.format.bdf.js").read_text()
    sheetjs = bdf[bdf.index("bz2 (C)") : bdf.index("*/", bdf.index("bz2 (C)"))].strip()
    notices = [(name, path.read_text()) for name, path in licenses] + [("SheetJS bz2", sheetjs)]
    modifications = (
        "Rom Patcher JS commit "
        + sources["rom_patcher"]["commit"]
        + "; pako "
        + sources["pako"]["version"]
        + ". Local fixes: "
        + " ".join(item["change"] for item in sources["local_modifications"])
    )
    license_html = "<p>" + html.escape(modifications) + "</p>"
    license_html += "".join(
        "<h3>" + html.escape(name) + "</h3><pre>" + html.escape(text) + "</pre>"
        for name, text in notices
    )
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
        "LICENSES": license_html,
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
