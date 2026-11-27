"""Build the 5.5G-style Volume Limit placement over r17; never flash."""
import argparse
from pathlib import Path

from build_native_controls import REPO, build_candidate as build_resources
from settings_order_patch import install

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r17.ipsw'


def build_candidate(source, stock, output):
    return build_resources(source, stock, output, patcher=install)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'{args.output}\nSHA-256 {build_candidate(args.source, args.stock, args.output)}')
