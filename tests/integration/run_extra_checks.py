"""Compare upstream conformance and browser journeys across built artifacts."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

if __package__:
    from .run_odin_projects import execute
else:
    from run_odin_projects import execute


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'integration-projects'
STAGES = ('baseline', 'migrated', 'vetted')


def toml():
    source = WORK / 'odin' / 'Up05__toml_parser'
    output = WORK / 'results' / 'Up05__toml_parser'
    skips = re.findall(r"-skip '([^']+)'", (source / 'run-tests.bash').read_text())
    results = {}
    for stage in STAGES:
        arguments = [str(WORK / 'tools/bin/toml-test'), 'test', '-json', '-v',
                     '-toml=1.1.0', '-parallel=2', '-skip-must-err', '-encoder=',
                     '-decoder='+str(output / f'{stage}-2-program')]
        for skip in skips:
            arguments.extend(['-skip', skip])
        result = execute(arguments, source, output / f'{stage}-conformance', os.environ.copy(), 180)
        packet = json.loads(Path(result['stdout']).read_text())
        counts = {key: value for key, value in packet.items()
                  if key.startswith(('passed_', 'failed_')) or key == 'skipped'}
        results[stage] = {'status': result['status'], 'counts': counts,
                          'tests': packet.get('tests', [])}
        print('TOML', stage, result['status'], counts, flush=True)
    comparable = {stage: (result['status'], result['counts'],
        sorted((case['path'], case['failure'], case['skipped']) for case in result['tests']))
        for stage, result in results.items()}
    results['matches_baseline'] = all(value == comparable['baseline'] for value in comparable.values())
    (output / 'conformance-comparison.json').write_text(json.dumps(results, indent=2)+'\n')
    return results['matches_baseline'] and comparable['baseline'][0] == 0


def browsers():
    source = WORK / 'odin' / 'hubastard__speedrun'
    output = WORK / 'results' / 'hubastard__speedrun'
    results = {}
    with (output / 'fixture-server.log').open('w') as log:
        server = subprocess.Popen(['python3', '-m', 'http.server', '8781', '--bind',
            '127.0.0.1', '--directory', str(source / 'dev/flows/web/site')],
            stdout=log, stderr=log)
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError('Browser fixture server exited, see fixture-server.log')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8781/', timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('Browser fixture server did not become ready')
            for stage in STAGES:
                results[stage] = {}
                for browser in ('chromium', 'firefox'):
                    report = output / f'{stage}-web-{browser}.xml'
                    report.unlink(missing_ok=True)
                    arguments = [str(output / f'{stage}-0-program'), 'test', '--headless',
                        '--device', browser, 'dev/flows/web/', '--format', 'junit',
                        '--output', str(report), '--debug-output',
                        str(output / f'{stage}-web-{browser}-debug'), '--flatten-debug-output']
                    result = execute(arguments, source, output / f'{stage}-web-{browser}',
                        os.environ.copy(), 600)
                    cases = []
                    if report.exists():
                        cases = sorted((case.get('name'), case.get('status'),
                            tuple(child.tag for child in case))
                            for case in ET.parse(report).iter('testcase'))
                    results[stage][browser] = {'status': result['status'], 'cases': cases}
                    print('Browser', stage, browser, result['status'], len(cases), flush=True)
        finally:
            server.terminate()
            server.wait(timeout=10)
    results['matches_baseline'] = all(results[stage] == results['baseline'] for stage in STAGES)
    (output / 'browser-comparison.json').write_text(json.dumps(results, indent=2)+'\n')
    return results['matches_baseline'] and all(
        value['status'] == 0 and value['cases'] for value in results['baseline'].values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['toml', 'browsers'])
    args = parser.parse_args()
    raise SystemExit(0 if (toml() if args.suite == 'toml' else browsers()) else 1)


if __name__ == '__main__':
    main()
