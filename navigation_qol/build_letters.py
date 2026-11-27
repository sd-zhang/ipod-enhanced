"""Build letter navigation without changing the existing two-feature artifact."""
import argparse
from pathlib import Path

from build import REPO, build
from letters_patch import install

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO / "build/navigation-qol/iPod_4.3.1.1-gapless-navigation.ipsw")
    parser.add_argument("--stock", type=Path, default=REPO / "iPod_4.3.1.1.ipsw")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    digest = build(args.source, args.stock, args.output,
                   installer=lambda image: install(image, args.output.with_suffix(".artifacts")))
    print(f"{args.output}\nSHA-256 {digest}")
