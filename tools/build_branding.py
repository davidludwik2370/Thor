#!/usr/bin/env python3
"""Export the Thor SVG masters as PNG/ICO assets. Requires Chrome and ImageMagick."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BRANDING = ROOT / 'assets' / 'branding'


def render(chrome, source, target, width, height):
    # Some macOS Chrome versions remain running after writing a screenshot.
    # Wait for a complete PNG, then close this isolated browser explicitly.
    with tempfile.TemporaryDirectory(prefix='thor-brand-render-') as directory:
        temporary = Path(directory)
        output = temporary / 'image.png'
        with (temporary / 'chrome.log').open('w+') as log:
            command = [chrome, '--headless=new', '--disable-gpu', '--disable-background-networking',
                       '--disable-component-update', '--no-first-run', '--no-default-browser-check',
                       '--hide-scrollbars', '--default-background-color=00000000',
                       '--force-device-scale-factor=1', f'--user-data-dir={temporary / "profile"}',
                       f'--window-size={width},{height}', f'--screenshot={output}', source.as_uri()]
            process = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if output.exists() and output.read_bytes().endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(output, target)
                        return
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                log.seek(0)
                raise RuntimeError(f'Could not render {source}:\n{log.read()[-3000:]}')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', default=shutil.which('google-chrome') or shutil.which('chromium')
                        or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    parser.add_argument('--magick', default=shutil.which('magick'))
    parser.add_argument('--preview-dir', type=Path, help='also save PNG previews of both icons')
    options = parser.parse_args()
    if not Path(options.chrome).is_file() or not options.magick:
        parser.error('Chrome and ImageMagick are needed; use --chrome and --magick to specify their paths')
    with tempfile.TemporaryDirectory(prefix='thor-branding-') as directory:
        temporary = Path(directory)
        outputs = [
            ('logo-slim', BRANDING / 'logo-slim.png', 500, 260),
            ('emblem', ROOT / 'editors' / 'vscode' / 'images' / 'emblem.png', 256, 256),
            ('sourcefile', temporary / 'sourcefile.png', 256, 256),
        ]
        for name, target, width, height in outputs:
            render(options.chrome, BRANDING / f'{name}.svg', target, width, height)
            if name != 'logo-slim':
                subprocess.run([options.magick, str(target),
                                '-define', 'icon:auto-resize=256,128,64,48,32,24,16',
                                str(BRANDING / f'{name}.ico')], check=True)
            if options.preview_dir:
                options.preview_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, options.preview_dir / f'{name}.png')
            print(f'Exported {name}', flush=True)


if __name__ == '__main__':
    main()
