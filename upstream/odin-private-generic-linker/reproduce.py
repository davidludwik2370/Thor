"""Build a copied SDK test package with one changed file and a control."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin', required=True, type=Path)
    parser.add_argument('--sdk-internal', required=True, type=Path,
                        help='Clean Odin tests/internal from revision 537fac5')
    options = parser.parse_args()
    compiler = options.odin.resolve()
    sources = options.sdk_internal.resolve()
    bundle = Path(__file__).resolve().parent
    if not (sources / 'test_mem_copy_small.odin').is_file():
        parser.error('--sdk-internal must contain the SDK internal test sources')
    with tempfile.TemporaryDirectory(prefix='odin-private-generic-') as temporary:
        work = Path(temporary)
        package = work / 'internal'
        shutil.copytree(sources, package)
        shutil.copyfile(bundle / 'test_modmod.odin', package / 'test_modmod.odin')
        results = []
        for mode, flags in [('default', []), ('single-module', ['-use-single-module'])]:
            command = [str(compiler), 'build', str(package), '-build-mode:test',
                       '-all-packages', '-thread-count:1',
                       '-define:ODIN_TEST_FANCY=false', '-define:ODIN_TEST_THREADS=1',
                       '-json-errors', '-max-error-count:1000',
                       '-collection:__thor_runtime=' + str(bundle / 'support'),
                       '-out:' + str(work / (mode + '.bin')), *flags]
            result = subprocess.run(command, capture_output=True, text=True)
            print(json.dumps(dict(mode=mode, command=command,
                                  returncode=result.returncode,
                                  stdout=result.stdout, stderr=result.stderr)), flush=True)
            results.append(result)
        reproduced = (results[0].returncode != 0
                      and 'duplicate symbol' in results[0].stderr
                      and results[1].returncode == 0)
        return 0 if reproduced else 1


if __name__ == '__main__':
    raise SystemExit(main())
