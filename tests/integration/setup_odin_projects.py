"""Fetch the pinned upstream integration working trees without replacing edits."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'integration-projects' / 'odin'
MANIFEST = Path(__file__).with_name('odin_projects.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects', default='.*')
    options = parser.parse_args()
    environment = {**os.environ, 'GIT_LFS_SKIP_SMUDGE': '1'}
    for project in json.loads(MANIFEST.read_text()):
        if not re.search(options.projects, project['repo']):
            continue
        target = WORK / project['repo'].replace('/', '__')
        git = ['git', '-c', 'url.https://github.com/.insteadOf=git@github.com:']
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([*git, 'clone', '--filter=blob:none', '--no-checkout',
                            'https://github.com/'+project['repo']+'.git', str(target)],
                           env=environment, check=True)
            subprocess.run([*git, 'fetch', '--depth=1', 'origin', project['commit']],
                           cwd=target, env=environment, check=True)
            subprocess.run([*git, 'checkout', '--detach', project['commit']],
                           cwd=target, env=environment, check=True)
        revision = subprocess.check_output([*git, 'rev-parse', 'HEAD'], cwd=target, text=True).strip()
        if revision != project['commit']:
            raise RuntimeError(f'{target} is at {revision}, expected {project["commit"]}')
        subprocess.run([*git, 'submodule', 'update', '--init', '--recursive', '--depth=1'],
                       cwd=target, env=environment, check=True)
        print(project['repo'], revision, flush=True)


if __name__ == '__main__':
    main()
