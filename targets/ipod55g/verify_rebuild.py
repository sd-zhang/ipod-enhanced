"""Rebuild from a candidate's frozen sources into a fresh immutable directory."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from verify_package import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--stock', type=Path, default=Path('iPod_25.1.3.ipsw'))
    args = parser.parse_args()
    candidate = args.candidate.resolve(); artifacts = candidate.with_suffix('.artifacts')
    verify(candidate, args.stock)
    destination = artifacts/'source-rebuild'
    destination.mkdir(exist_ok=False)
    output = destination/candidate.name
    command = [sys.executable, str(artifacts/'source/targets/ipod55g/build.py'),
               '--stock', str(args.stock.resolve()), '--output', str(output)]
    with (destination/'build.txt').open('x') as log:
        run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    result = {'passed': False, 'returncode': run.returncode}
    if run.returncode == 0:
        original, rebuilt = candidate.read_bytes(), output.read_bytes()
        result.update(archive_equal=original == rebuilt,
                      archive_sha256=hashlib.sha256(rebuilt).hexdigest(),
                      elf_equal=(artifacts/'extension.elf').read_bytes() ==
                      (output.with_suffix('.artifacts')/'extension.elf').read_bytes())
        result['passed'] = result['archive_equal'] and result['elf_equal']
    with (artifacts/'source-rebuild.json').open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))
    if not result['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
