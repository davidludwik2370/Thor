"""Publish contracts, real macOS applications and native dependency relocation."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from thorlang import driver
from thorlang.publishing import Publisher, PublishError, collect_resources, icon_image, relative_path, write_icon
from thorlang.publish_deps import pe_imports, windows_dependencies, linux_dependencies

ROOT = Path(__file__).resolve().parents[1]


def options(**values):
    defaults = dict(app_name=None, app_id=None, app_version=None, app_icon=None,
                    app_type=None, app_resource=[], app_library=[], publish_dir=None)
    return SimpleNamespace(**(defaults | values))


def pe_file(path, imports=(), delayed=()):
    """Small valid PE headers/import sections, sufficient for dependency inspection."""
    data = bytearray(4096)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 132, 0x8664, 1, 0, 0, 0, 240, 0)
    optional = 152
    struct.pack_into('<H', data, optional, 0x20b)
    struct.pack_into('<Q', data, optional + 24, 0x140000000)
    struct.pack_into('<I', data, optional + 108, 16)
    struct.pack_into('<8sIIII', data, optional + 240, b'.rdata\0\0', 0xc00, 0x1000, 0xc00, 0x200)
    for directory, table, names, items, width in [(1, 0x1000, 0x1400, imports, 20), (13, 0x1200, 0x1600, delayed, 32)]:
        if not items:
            continue
        struct.pack_into('<II', data, optional + 112 + directory * 8, table, (len(items) + 1) * width)
        for i, name in enumerate(items):
            encoded = name.encode('ascii') + b'\0'
            data[names - 0x1000 + 0x200:names - 0x1000 + 0x200 + len(encoded)] = encoded
            offset = table - 0x1000 + 0x200 + i * width
            if directory == 1:
                struct.pack_into('<I', data, offset + 12, names)
            else:
                struct.pack_into('<II', data, offset, 1, names)
            names += len(encoded)
    path.write_bytes(data)
    return path


class PublishFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='thor-publish-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.project = self.directory / 'my project'
        self.project.mkdir()
        (self.project / 'main.thor').write_text('package main\nmain::proc(){}\n')
        self.output = self.directory / 'output'
        self.output.mkdir()

    def plan(self, *, system='darwin', flags=(), **settings):
        with contextlib.chdir(self.output), patch('thorlang.publishing.host_target', return_value=(system, 'arm64')):
            return Publisher.prepare(self.project, list(flags), options(**settings))

    def file(self, name, content=b'content'):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def config(self, text):
        (self.project / 'thor.toml').write_text(text)

    def image(self, name='assets/icon.png', size=128):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('PNG/ICO publishing tests require Pillow')
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', (size, size), (20, 80, 200, 128)).save(path)
        return path


class PublishTests(PublishFixture):
    def test_default_names_follow_build_directory_and_selected_platform(self):
        for system, suffix in [('darwin', '.app'), ('windows', '-windows-arm64.zip'), ('linux', '-linux-arm64.tar.gz')]:
            with self.subTest(system=system):
                plan = self.plan(system=system)
                self.assertEqual(plan.name, 'output')
                self.assertEqual(plan.output, self.output / ('output' + suffix))
                self.assertEqual(plan.app_type, 'gui')
                self.assertEqual(plan.executable_name, 'output.exe' if system == 'windows' else 'output')

    def test_explicit_native_output_sets_binary_name_and_package_location(self):
        plan = self.plan(flags=['-out:chosen'], app_name='Readable Name', app_version='2.3')
        self.assertEqual(plan.executable_name, 'chosen')
        self.assertEqual(plan.output, self.output / 'Readable Name.app')
        override = self.directory / 'distribution'
        plan = self.plan(flags=['-out:bin/chosen'], publish_dir=override)
        self.assertEqual(plan.output, override / 'chosen.app')

    def test_config_cli_precedence_and_additive_recursive_resources(self):
        self.file('assets/images/one.txt')
        self.file('data/default.json')
        self.file('extra.txt')
        (self.project / 'assets/empty').mkdir()
        self.config('[publish]\nname="Configured"\nid="com.example.config"\nversion="1.2.3"\ntype="console"\nresources=["data/", "assets/"]\n')
        plan = self.plan(app_name='Override', app_resource=['extra.txt'])
        self.assertEqual((plan.name, plan.identifier, plan.version, plan.app_type), ('Override', 'com.example.config', '1.2.3', 'console'))
        self.assertEqual(set(plan.resources), {Path('assets/images/one.txt'), Path('data/default.json'), Path('extra.txt')})
        self.assertIn(Path('assets/empty'), plan.directories)

    def test_config_is_optional_and_source_file_uses_its_parent(self):
        self.file('assets/test.txt')
        with contextlib.chdir(self.output), patch('thorlang.publishing.host_target', return_value=('darwin', 'arm64')):
            plan = Publisher.prepare(self.project / 'main.thor', [], options())
        self.assertEqual(plan.root, self.project)
        self.assertIn(Path('assets/test.txt'), plan.resources)

    def test_invalid_configuration_is_actionable(self):
        for config, message in [
            ('[publish]\nwat=1', 'Unknown'),
            ('[publish]\nresources="assets"', 'array'),
            ('[publish]\nname=false', 'string'),
            ('[publish]\ntype="guess"', 'gui or console'),
            ('[publish]\nversion="v1"', 'numeric'),
            ('[publish]\nversion="65536.0"', '65535'),
            ('[publish]\nid="bad id"', 'reverse-domain'),
            ('[publish]\nname="../escape"', 'file name'),
            ('[publish]\nname="CON"', 'reserved'),
            ('[publish]\nresources=["missing"]', 'does not exist'),
            ('[publish]\n', None),
        ]:
            with self.subTest(config=config):
                self.config(config)
                if message:
                    with self.assertRaisesRegex(PublishError, message): self.plan()
                else:
                    self.plan()
        self.config('[publish\n')
        with self.assertRaisesRegex(PublishError, 'Cannot read thor.toml'): self.plan()

    def test_resources_reject_traversal_absolute_paths_and_special_files(self):
        for value in ('../secret', '/etc/passwd', 'C:\\secret', 'C:secret', '.', '', 'bad\x00path'):
            with self.subTest(value=value), self.assertRaises(PublishError): relative_path(value, 'Resource')
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.project / 'pipe')
            with self.assertRaisesRegex(PublishError, 'ordinary file'): self.plan(app_resource=['pipe'])

    @unittest.skipIf(os.name == 'nt', 'Requires unprivileged symlinks')
    def test_resources_follow_local_links_but_reject_external_links_and_cycles(self):
        self.file('assets/file.txt')
        (self.project / 'assets/alias.txt').symlink_to('file.txt')
        self.assertIn(Path('assets/alias.txt'), self.plan().resources)
        (self.project / 'assets/outside.txt').symlink_to(self.directory / 'outside.txt')
        (self.directory / 'outside.txt').write_text('outside')
        with self.assertRaisesRegex(PublishError, 'leaves the project'): self.plan()
        (self.project / 'assets/outside.txt').unlink()
        (self.project / 'assets/cycle').symlink_to(self.project / 'assets', target_is_directory=True)
        with self.assertRaisesRegex(PublishError, 'cycle'): self.plan()

    def test_output_cannot_be_inside_resources_or_replace_unmanaged_files(self):
        self.file('assets/test.txt')
        with self.assertRaisesRegex(PublishError, 'outside the included'): self.plan(publish_dir=self.project / 'assets')
        output = self.output / 'output.app'
        output.mkdir()
        (output / 'keep').write_text('keep')
        with self.assertRaisesRegex(PublishError, 'not managed'): self.plan()
        self.assertEqual((output / 'keep').read_text(), 'keep')

    def test_mismatched_platform_and_non_executable_modes_fail_early(self):
        for flag in ['-target:windows_amd64', '-build-mode:dll', '-build-mode:test', '-no-entry-point']:
            with self.subTest(flag=flag), self.assertRaises(PublishError): self.plan(flags=[flag])
        self.assertEqual(self.plan(flags=['-target:darwin_amd64']).arch, 'amd64')

    def test_png_and_ico_inputs_produce_all_platform_formats(self):
        png = self.image(size=256)
        from PIL import Image
        ico = self.project / 'artwork.ico'
        icon_image(png).save(ico, sizes=[(16, 16), (64, 64), (256, 256)])
        for source in (png, ico):
            with self.subTest(source=source.name):
                image = icon_image(source)
                self.assertEqual(image.size, (256, 256))
                for system, suffix in [('darwin', '.icns'), ('windows', '.ico'), ('linux', '.png')]:
                    target = self.directory / ('converted-' + source.stem + suffix)
                    write_icon(image, source, target, system)
                    with Image.open(target) as opened:
                        self.assertEqual(opened.format, {'darwin': 'ICNS', 'windows': 'ICO', 'linux': 'PNG'}[system])

    def test_icon_discovery_and_explicit_override(self):
        png = self.image()
        ico = self.image('assets/icon.ico', 256)
        with self.assertRaisesRegex(PublishError, 'Both assets/icon'): self.plan()
        plan = self.plan(app_icon=Path('assets/icon.ico'))
        self.assertEqual(plan.icon, ico)
        self.config('[publish]\nicon="assets/icon.png"\n')
        self.assertEqual(self.plan().icon, png)
        self.assertEqual(self.plan(app_icon=Path('assets/icon.ico')).icon, ico)

    def test_corrupt_wrong_format_and_missing_icons(self):
        self.image()
        self.file('bad.png')
        self.file('bad.ico', b'not an ico')
        self.file('bad.jpg')
        for name in ('bad.png', 'bad.ico', 'bad.jpg', 'missing.png'):
            with self.subTest(name=name), self.assertRaises(PublishError): self.plan(app_icon=Path(name))

    def test_opaque_rectangular_icons_are_center_cropped_without_stretching(self):
        path = self.image()
        from PIL import Image
        for width, height in [(6, 4), (4, 6)]:
            with self.subTest(size=(width, height)):
                original = Image.new('RGB', (width, height))
                for y in range(height):
                    for x in range(width): original.putpixel((x, y), (x * 20, y * 20, 100))
                original.save(path)
                warnings = []
                normalized = icon_image(path, warn=warnings.append)
                self.assertEqual(normalized.size, (4, 4))
                self.assertEqual(normalized.getpixel((0, 0)), (20 if width == 6 else 0, 20 if height == 6 else 0, 100, 255))
                self.assertEqual(len(warnings), 1)
                self.assertIn('visible pixels were removed', warnings[0])
                self.assertIn(f'{width}x{height} to 4x4', warnings[0])

    def test_transparent_padding_is_cropped_quietly_for_rgba_and_palette_images(self):
        path = self.image()
        from PIL import Image
        for mode in ('RGBA', 'P'):
            with self.subTest(mode=mode):
                if mode == 'RGBA':
                    original = Image.new('RGBA', (8, 4), (255, 255, 255, 0))
                    original.paste((20, 80, 200, 255), (2, 0, 6, 4))
                else:
                    original = Image.new('P', (8, 4), 0)
                    original.putpalette([255, 255, 255, 20, 80, 200] + [0] * 762)
                    original.info['transparency'] = 0
                    original.paste(1, (2, 0, 6, 4))
                original.save(path)
                warnings = []
                normalized = icon_image(path, warn=warnings.append)
                self.assertEqual(normalized.size, (4, 4))
                self.assertEqual(normalized.getpixel((0, 0)), (20, 80, 200, 255))
                self.assertEqual(normalized.getchannel('A').getextrema(), (255, 255))
                self.assertEqual(warnings, [])

    def test_crop_shifts_over_transparent_padding_to_keep_off_center_artwork(self):
        path = self.image()
        from PIL import Image
        for size, box in [((10, 4), (0, 0, 4, 4)), ((10, 4), (6, 0, 10, 4)),
                          ((4, 10), (0, 0, 4, 4)), ((4, 10), (0, 6, 4, 10))]:
            with self.subTest(box=box, size=size):
                original = Image.new('RGBA', size, (255, 100, 50, 0))
                original.paste((10, 20, 30, 255), box)
                original.save(path)
                warnings = []
                normalized = icon_image(path, warn=warnings.append)
                self.assertEqual(normalized.size, (4, 4))
                self.assertEqual(normalized.getchannel('A').getextrema(), (255, 255))
                self.assertEqual(warnings, [])

    def test_fully_transparent_pixels_with_hidden_color_do_not_warn(self):
        path = self.image()
        from PIL import Image
        Image.new('RGBA', (7, 4), (255, 100, 50, 0)).save(path)
        warnings = []
        normalized = icon_image(path, warn=warnings.append)
        self.assertEqual(normalized.size, (4, 4))
        self.assertEqual(normalized.getchannel('A').getextrema(), (0, 0))
        self.assertEqual(warnings, [])

    def test_cropping_a_single_barely_visible_pixel_warns_with_odd_dimensions(self):
        path = self.image()
        from PIL import Image
        original = Image.new('RGBA', (5, 4), (255, 255, 255, 0))
        original.putpixel((0, 1), (0, 0, 0, 255))
        original.putpixel((4, 1), (255, 255, 255, 1))
        original.save(path)
        warnings = []
        self.assertEqual(icon_image(path, warn=warnings.append).size, (4, 4))
        self.assertEqual(len(warnings), 1)

    def test_large_square_icon_scales_quietly_and_keeps_the_source_unchanged(self):
        path = self.image()
        from PIL import Image
        Image.new('L', (4097, 4097), 200).save(path)
        before = path.read_bytes()
        warnings = []
        normalized = icon_image(path, warn=warnings.append)
        self.assertEqual(normalized.size, (1024, 1024))
        self.assertEqual(normalized.getpixel((512, 512)), (200, 200, 200, 255))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(warnings, [])

    def test_rectangular_ico_is_normalized_instead_of_copied_on_windows(self):
        self.image()
        from PIL import Image
        png = io.BytesIO()
        Image.new('RGBA', (32, 16), (20, 80, 200, 255)).save(png, format='PNG')
        encoded = png.getvalue()
        source = self.file('rectangle.ico', struct.pack('<HHH', 0, 1, 1) +
                           struct.pack('<BBBBHHII', 32, 16, 0, 0, 1, 32, len(encoded), 22) + encoded)
        warnings = []
        normalized = icon_image(source, warn=warnings.append)
        self.assertEqual(normalized.size, (16, 16))
        self.assertEqual(len(warnings), 1)
        target = self.directory / 'normalized.ico'
        write_icon(normalized, source, target, 'windows')
        with Image.open(target) as icon:
            self.assertEqual(icon.size, (256, 256))
            self.assertTrue(all(width == height for width, height in icon.ico.sizes()))
        self.assertNotEqual(target.read_bytes(), source.read_bytes())

    def test_square_ico_keeps_its_original_size_variants_on_windows(self):
        source = self.image('assets/icon.ico', 128)
        warnings = []
        normalized = icon_image(source, warn=warnings.append)
        target = self.directory / 'unchanged.ico'
        write_icon(normalized, source, target, 'windows')
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(warnings, [])

    def test_windows_native_flags_set_subsystem_and_embed_icon_and_version(self):
        self.image()
        scratch = self.directory / 'scratch'; scratch.mkdir()
        for app_type, subsystem in [('gui', 'windows'), ('console', 'console')]:
            plan = self.plan(system='windows', app_type=app_type, app_version='2.4.1')
            flags = plan.native_flags(['-o:speed', '-out:ignored'], scratch)
            self.assertIn('-o:speed', flags)
            self.assertIn('-subsystem:' + subsystem, flags)
            self.assertFalse(any(f.startswith('-out:') for f in flags))
            resource = (scratch / 'app.rc').read_text()
            self.assertIn('1 ICON', resource)
            self.assertIn('FILEVERSION 2,4,1,0', resource)
        with self.assertRaisesRegex(PublishError, 'conflicts'):
            self.plan(system='windows').native_flags(['-subsystem:console'], scratch)
        with self.assertRaisesRegex(PublishError, 'custom -resource'):
            self.plan(system='windows', flags=['-resource:custom.rc'])

    def test_archives_preserve_resources_empty_directories_and_ownership(self):
        self.file('assets/message.txt', b'hello')
        (self.project / 'assets/empty').mkdir()
        for system in ('windows', 'linux'):
            with self.subTest(system=system), tempfile.TemporaryDirectory(dir=self.directory) as temporary:
                scratch = Path(temporary)
                executable = scratch / ('app.exe' if system == 'windows' else 'app')
                executable.write_bytes(b'fake native input')
                plan = self.plan(system=system)
                with patch('thorlang.publish_deps.bundle_dependencies', return_value=['library']):
                    artifact = plan.package(executable, scratch)
                if system == 'windows':
                    with zipfile.ZipFile(artifact) as archive:
                        self.assertEqual(archive.read('output/resources/assets/message.txt'), b'hello')
                        self.assertIn('output/resources/assets/empty/', archive.namelist())
                else:
                    with tarfile.open(artifact) as archive:
                        self.assertEqual(archive.extractfile('output/resources/assets/message.txt').read(), b'hello')
                        self.assertTrue(archive.getmember('output/AppRun').mode & 0o111)
                self.plan(system=system).check_output()

    def test_package_names_do_not_collide_with_staging_archives_or_backups(self):
        for system, name in [('windows', 'package.zip'), ('linux', 'package.tar.gz'), ('darwin', 'previous')]:
            for version in range(2):
                with self.subTest(system=system, name=name, version=version), tempfile.TemporaryDirectory(dir=self.directory) as temporary:
                    scratch = Path(temporary)
                    binary = scratch / 'native-input'
                    content = f'input {version}'.encode()
                    binary.write_bytes(content)
                    plan = self.plan(system=system, app_name=name)
                    with patch('thorlang.publish_deps.bundle_dependencies', return_value=[]), patch('thorlang.publish_deps.sign_macos'):
                        artifact = plan.package(binary, scratch)
                    if system == 'windows':
                        with zipfile.ZipFile(artifact) as archive:
                            actual = archive.read(f'{name}/{plan.executable_name}')
                    elif system == 'linux':
                        with tarfile.open(artifact) as archive:
                            actual = archive.extractfile(f'{name}/bin/{plan.executable_name}').read()
                    else:
                        actual = (artifact / 'Contents/MacOS' / plan.executable_name).read_bytes()
                    self.assertEqual(actual, content)
                    plan.check_output()

    def test_linux_desktop_installer_records_absolute_paths_and_gui_choice(self):
        self.image()
        directory = self.directory / 'a path $ with % and quote "'
        directory.mkdir()
        xdg = self.directory / 'xdg'
        plan = self.plan(system='linux', app_name="My App's Name")
        script = directory / 'install-desktop.sh'
        script.write_text(plan.desktop_installer('AppIcon.png'))
        result = subprocess.run(['sh', str(script)], env={**os.environ, 'XDG_DATA_HOME': str(xdg)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (xdg / 'applications' / (plan.identifier + '.desktop')).read_text()
        self.assertIn("Name=My App's Name", text)
        self.assertIn('Terminal=false', text)
        self.assertIn('%%', text)
        self.assertIn('\\$', text)
        self.assertIn('\\"', text)
        self.assertNotIn('resources/"', text)
        quoted = str(directory / 'AppRun').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
        quoted = quoted.replace('\\', '\\\\')
        self.assertIn('Exec="' + quoted + '"', text)

    @unittest.skipIf(os.name == 'nt', 'Requires unprivileged symlinks')
    def test_broken_automatic_asset_link_is_an_error(self):
        (self.project / 'assets').symlink_to('missing')
        with self.assertRaisesRegex(PublishError, 'must be a directory'): self.plan()

    def test_dependency_failure_preserves_previous_package(self):
        plan = self.plan(system='windows')
        old = self.directory / 'old'; old.mkdir()
        binary = old / 'binary'; binary.write_bytes(b'fake')
        with patch('thorlang.publish_deps.bundle_dependencies', return_value=[]): plan.package(binary, old)
        before = plan.output.read_bytes()
        fresh = self.directory / 'fresh'; fresh.mkdir()
        with patch('thorlang.publish_deps.bundle_dependencies', side_effect=PublishError('missing dependency')):
            with self.assertRaisesRegex(PublishError, 'missing dependency'): plan.package(binary, fresh)
        self.assertEqual(plan.output.read_bytes(), before)

    def test_pe_reader_includes_normal_and_delayed_imports(self):
        source = pe_file(self.directory / 'program.exe', ['KERNEL32.dll', 'plugin.dll'], ['lazy.dll'])
        self.assertEqual(pe_imports(source), ['KERNEL32.dll', 'plugin.dll', 'lazy.dll'])
        source.write_bytes(b'not PE')
        with self.assertRaisesRegex(PublishError, 'Cannot inspect Windows'): pe_imports(source)

    def test_windows_dependencies_are_recursive_case_insensitive_and_skip_system(self):
        binary_dir = self.directory / 'binary'; binary_dir.mkdir()
        source = pe_file(self.project / 'app.exe', ['KERNEL32.dll', 'foo.dll'], ['plugin.dll'])
        foo = pe_file(self.project / 'Foo.DLL', ['bar.dll'])
        bar = pe_file(self.project / 'bar.dll')
        plugin = pe_file(self.project / 'plugin.dll')
        binary = binary_dir / 'app.exe'; shutil.copy2(source, binary)
        self.assertEqual(windows_dependencies(source, binary, binary_dir, self.project, []), ['Foo.DLL', 'bar.dll', 'plugin.dll'])
        for path in (foo, bar, plugin): self.assertEqual((binary_dir / path.name).read_bytes(), path.read_bytes())

    def test_missing_windows_dependency_does_not_silently_ship_broken_app(self):
        source = pe_file(self.project / 'app.exe', ['missing-thor-test.dll'])
        with self.assertRaisesRegex(PublishError, 'Cannot locate DLL'):
            windows_dependencies(source, self.output / 'app.exe', self.output, self.project, [])

    def test_windows_import_library_hints_locate_adjacent_runtime_dll(self):
        source = pe_file(self.project / 'main.exe', ['vendor.dll'])
        imports = self.file('external-sdk/vendor.lib')
        dll = pe_file(imports.parent / 'vendor.dll')
        result = windows_dependencies(source, self.output / 'main.exe', self.output, self.project, [], [imports])
        self.assertEqual(result, ['vendor.dll'])
        self.assertEqual((self.output / 'vendor.dll').read_bytes(), dll.read_bytes())

    def test_different_explicit_dlls_with_the_same_name_are_rejected(self):
        first = pe_file(self.file('one/foo.dll'))
        second = pe_file(self.file('two/foo.dll'))
        source = pe_file(self.project / 'main.exe')
        with self.assertRaisesRegex(PublishError, 'same packaged name'):
            windows_dependencies(source, self.output / 'app.exe', self.output, self.project, [first, second])

    def test_explicit_dynamic_plugin_is_packaged_even_without_a_static_import(self):
        source = pe_file(self.project / 'main.exe')
        plugin = pe_file(self.project / 'plugin.dll', ['KERNEL32.dll'])
        result = windows_dependencies(source, self.output / 'app.exe', self.output, self.project, [plugin])
        self.assertEqual(result, ['plugin.dll'])
        self.assertTrue((self.output / 'plugin.dll').is_file())

    def test_native_macos_linker_flags_are_merged_without_affecting_optimization(self):
        plan = self.plan()
        flags = plan.native_flags(['-o:speed', '-extra-linker-flags:-dead_strip', '-out:foo'], self.directory)
        self.assertIn('-o:speed', flags)
        self.assertEqual([f for f in flags if f.startswith('-extra-linker-flags:')],
                         ['-extra-linker-flags:-dead_strip -Wl,-headerpad_max_install_names'])

    def test_publish_defaults_to_speed_and_preserves_explicit_optimization_on_all_platforms(self):
        for system in ('darwin', 'windows', 'linux'):
            for explicit in (None, 'none', 'minimal', 'size', 'speed', 'aggressive'):
                with self.subTest(system=system, explicit=explicit):
                    inputs = ['-debug'] if explicit is None else ['-debug', '-o:' + explicit]
                    original = inputs.copy()
                    flags = self.plan(system=system).native_flags(inputs, self.directory)
                    self.assertEqual([f for f in flags if f.startswith('-o:')], ['-o:' + (explicit or 'speed')])
                    self.assertEqual(inputs, original, 'publish defaults must not mutate flags used by compile-time helpers')

    def test_linux_relocates_non_system_dependencies_without_modifying_sources(self):
        source = self.file('app', b'original exe')
        library = self.file('libexample.so.1.2', b'original library')
        binary = self.output / 'bin/app'; binary.parent.mkdir(); shutil.copy2(source, binary)
        commands = []
        def tool(command):
            command = list(map(str, command)); commands.append(command)
            if command[0] == 'readelf':
                return '(NEEDED) Shared library: [libexample.so.1]\n(NEEDED) Shared library: [libc.so.6]\n' if command[-1] == str(source.resolve()) else '(NEEDED) Shared library: [libm.so.6]\n'
            if command[0] == 'ldd': return f'libexample.so.1 => {library} (0x1)\nlibc.so.6 => /lib/libc.so.6 (0x2)\n'
            return ''
        with patch('thorlang.publish_deps.shutil.which', return_value='readelf'), patch('thorlang.publish_deps.run_tool', side_effect=tool):
            result = linux_dependencies(source, binary, self.output / 'lib', self.project, [])
        self.assertEqual(result, ['libexample.so.1.2'])
        self.assertIn(['patchelf', '--replace-needed', 'libexample.so.1', 'libexample.so.1.2', str(binary)], commands)
        self.assertIn(['patchelf', '--set-rpath', '$ORIGIN/../lib', str(binary)], commands)
        self.assertEqual(source.read_bytes(), b'original exe')
        self.assertEqual(library.read_bytes(), b'original library')

    @unittest.skipIf(os.name == 'nt', 'Requires unprivileged symlinks')
    def test_linux_linker_hints_find_soname_beside_linked_library(self):
        source = self.file('app', b'original exe')
        library = self.file('custom-sdk/libexample.so.1.2', b'original library')
        hint = library.parent / 'libexample.so'; hint.symlink_to(library.name)
        (library.parent / 'libexample.so.1').symlink_to(library.name)
        binary = self.output / 'app'; binary.write_bytes(source.read_bytes())
        def tool(command):
            if command[0] == 'readelf':
                return '(NEEDED) Shared library: [libexample.so.1]\n' if Path(command[-1]) == source else ''
            self.assertNotEqual(command[0], 'ldd', 'linked-library hints should resolve this dependency directly')
            return ''
        with patch('thorlang.publish_deps.shutil.which', return_value='readelf'), patch('thorlang.publish_deps.run_tool', side_effect=tool):
            self.assertEqual(linux_dependencies(source, binary, self.output / 'lib', self.project, [], [hint]), ['libexample.so.1.2'])

    def test_native_linked_library_export_stays_in_scratch_and_does_not_duplicate_user_option(self):
        plan = self.plan()
        flags = plan.native_flags([], self.directory)
        self.assertEqual(plan.linked_file, self.directory / 'linked-libraries.txt')
        self.assertIn('-export-linked-libs-file:' + str(plan.linked_file), flags)
        flag = '-export-linked-libs-file:' + str(self.directory / 'custom.txt')
        flags = plan.native_flags([flag], self.directory)
        self.assertEqual([f for f in flags if f.startswith('-export-linked-libs-file:')], [flag])


@unittest.skipUnless(sys.platform == 'darwin', 'Real macOS application integration')
class MacPublishTests(PublishFixture):
    def invoke(self, command='publish', flags=(), *, source=None, cwd=None):
        if source is not None: (self.project / 'main.thor').write_text(source)
        result = subprocess.run([str(ROOT / 'thor'), command, str(self.project), *flags],
                                cwd=cwd or self.output, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_final_compiler_uses_publish_optimization_defaults_and_leaves_build_alone(self):
        native_run = subprocess.run
        for command, explicit, expected in [('publish', [], ['-o:speed']),
                                             ('publish', ['-o:none'], ['-o:none']),
                                             ('build', [], [])]:
            with self.subTest(command=command, explicit=explicit):
                calls = []
                def run(arguments, *args, **kwargs):
                    calls.append(arguments)
                    return native_run(arguments, *args, **kwargs)
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.chdir(self.output), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                        patch.object(driver.subprocess, 'run', side_effect=run):
                    status = driver._main([command, str(self.project), *explicit])
                self.assertEqual(status, 0, errors.getvalue())
                suffix = '/compiled/program' if command == 'publish' else str(self.output / 'output')
                compiler = [args for args in calls if len(args) > 1 and args[1] == 'build' and
                            any(str(f).startswith('-out:') and str(f).endswith(suffix) for f in args)]
                self.assertEqual(len(compiler), 1, compiler)
                self.assertEqual([f for f in compiler[0] if f.startswith('-o:')], expected)

    def test_visible_icon_crop_warning_is_structured_and_obeys_warning_flags(self):
        path = self.image()
        from PIL import Image
        Image.new('RGB', (12, 6), (20, 80, 200)).save(path)
        before = path.read_bytes()
        for flags, status, severity in [([], 0, 'warning'), (['-ignore-warnings'], 0, None), (['-warnings-as-errors'], 1, 'error')]:
            with self.subTest(flags=flags):
                result = subprocess.run([str(ROOT / 'thor'), 'publish', str(self.project), '-json-errors', *flags],
                                        cwd=self.output, capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, status, result.stderr)
                diagnostics = json.loads(result.stderr)['errors']
                if severity is not None:
                    self.assertEqual(len(diagnostics), 1, diagnostics)
                    self.assertEqual(diagnostics[0]['type'], severity)
                    self.assertIn('visible pixels were removed', str(diagnostics[0]))
                else:
                    self.assertEqual(diagnostics, [])
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(list(self.output.glob('.thor-publish-*')))

    def test_palette_icon_with_only_transparent_padding_publishes_without_warning(self):
        path = self.image()
        from PIL import Image
        original = Image.new('P', (10, 4), 0)
        original.putpalette([255, 255, 255, 20, 80, 200] + [0] * 762)
        original.info['transparency'] = 0
        original.paste(1, (0, 0, 4, 4))
        original.save(path)
        before = path.read_bytes()
        result = self.invoke(flags=['-warnings-as-errors'])
        self.assertEqual(result.stderr, '')
        self.assertTrue((Path(result.stdout.strip()) / 'Contents/Resources/AppIcon.icns').is_file())
        self.assertEqual(path.read_bytes(), before)

    def test_real_gui_app_with_icon_moves_away_from_sources_and_preserves_arguments(self):
        self.image()
        self.file('assets/message.txt', b'copied content')
        self.file('data/defaults.txt', b'configuration')
        self.config('[publish]\nname="My Game"\nid="com.example.game"\nversion="1.2.3"\nresources=["data/"]\n')
        source = '''package main
import "thor:app"
import "core:os"
import "core:fmt"
VALUE :: #run proc() -> int {return 7}()
main :: proc() {
    path := app.resource_path("assets/message.txt")
    defer delete(path)
    data, err := os.read_entire_file(path, context.allocator); assert(err == nil); defer delete(data)
    assert(string(data) == "copied content")
    text := f"value {VALUE}"; defer delete(text)
    fmt.println(text, os.args[1])
}
'''
        # Use an ordinary named direct call, exercising cached #run + publishing.
        source = source.replace('VALUE :: #run proc() -> int {return 7}()', 'value :: fn() -> int {return 7}\nVALUE :: #run value()')
        result = self.invoke(source=source)
        artifact = Path(result.stdout.strip())
        self.assertEqual(artifact, self.output / 'My Game.app')
        info = plistlib.loads((artifact / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleExecutable'], self.output.name)
        self.assertEqual(info['CFBundleIdentifier'], 'com.example.game')
        self.assertEqual(info['CFBundleIconFile'], 'AppIcon.icns')
        self.assertFalse((artifact / 'Contents/MacOS/ThorConsole').exists())
        self.assertEqual((artifact / 'Contents/Resources/data/defaults.txt').read_bytes(), b'configuration')
        moved = self.directory / 'Moved Application.app'; artifact.rename(moved)
        self.project.rename(self.directory / 'hidden-source')
        native = moved / 'Contents/MacOS' / info['CFBundleExecutable']
        run = subprocess.run([str(native), 'argument with spaces'], cwd='/tmp', capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), 'value 7 argument with spaces')
        self.assertFalse(list(self.output.glob('.thor-publish-*')))
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(moved)], check=True, capture_output=True)

    def test_development_resource_lookup_is_independent_of_cwd(self):
        self.file('assets/a.txt')
        result = self.invoke('run', source='''package main
import "thor:app"
import "core:fmt"
main::proc(){path:=app.resource_path("assets/a.txt");defer delete(path);fmt.println(path)}
''')
        self.assertEqual(Path(result.stdout.strip()), self.project / 'assets/a.txt')

    def test_console_mode_is_explicit_and_shell_quotes_executable(self):
        result = self.invoke(flags=['--app-type', 'console', '-out:an executable\'s name'])
        artifact = Path(result.stdout.strip())
        info = plistlib.loads((artifact / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleExecutable'], 'ThorConsole')
        directory = artifact / 'Contents/MacOS'
        from thorlang.publish_deps import macho
        self.assertTrue(macho(directory / 'ThorConsole'))
        command = artifact / 'Contents/Resources/ThorConsole.command'
        self.assertEqual(subprocess.run(['sh', '-n', str(command)]).returncode, 0)
        self.assertEqual(subprocess.run(['sh', str(command)], cwd='/tmp').returncode, 0)

    def test_native_library_dependency_closure_is_copied_and_relocated(self):
        libraries = self.project / 'lib'; libraries.mkdir()
        (libraries / 'b.c').write_text('int from_b(void){return 42;}\n')
        (libraries / 'a.c').write_text('extern int from_b(void); int answer(void){return from_b();}\n')
        commands = [
            ['clang', '-dynamiclib', str(libraries / 'b.c'), '-o', str(libraries / 'libb.dylib'), '-Wl,-install_name,@rpath/libb.dylib', '-Wl,-headerpad_max_install_names'],
            ['clang', '-dynamiclib', str(libraries / 'a.c'), '-o', str(libraries / 'liba.dylib'), '-L' + str(libraries), '-lb', '-Wl,-install_name,' + str(libraries / 'liba.dylib'), '-Wl,-rpath,@loader_path', '-Wl,-headerpad_max_install_names'],
        ]
        for command in commands: subprocess.run(command, check=True, capture_output=True)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in libraries.glob('*.dylib')}
        for path in before:
            path.chmod(0o444)
        source = '''package main
foreign import native "lib/liba.dylib"
foreign native { answer :: proc "c" () -> i32 --- }
main::proc(){assert(answer()==42)}
'''
        result = self.invoke(source=source)
        artifact = Path(result.stdout.strip())
        bundled = artifact / 'Contents/Frameworks'
        self.assertEqual(sorted(p.name for p in bundled.iterdir()), ['liba.dylib', 'libb.dylib'])
        self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items()))
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o444 for p in before))
        self.project.rename(self.directory / 'moved-source')
        binary = artifact / 'Contents/MacOS/output'
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        dependencies = subprocess.check_output(['otool', '-L', str(bundled / 'liba.dylib')], text=True)
        self.assertIn('@loader_path/libb.dylib', dependencies)

    def test_versioned_framework_is_copied_with_internal_links_and_resigned(self):
        framework = self.project / 'Answer.framework'
        version = framework / 'Versions/A'
        resources = version / 'Resources'; resources.mkdir(parents=True)
        info = {'CFBundleExecutable':'Answer','CFBundleIdentifier':'local.thor.answer-framework',
                'CFBundlePackageType':'FMWK','CFBundleVersion':'1.0.0'}
        (resources / 'Info.plist').write_bytes(plistlib.dumps(info))
        (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
        (framework / 'Answer').symlink_to('Versions/Current/Answer')
        (framework / 'Resources').symlink_to('Versions/Current/Resources', target_is_directory=True)
        native = self.project / 'answer.c'; native.write_text('int answer(void){return 42;}\n')
        subprocess.run(['clang','-dynamiclib',str(native),'-o',str(version / 'Answer'),
                        '-Wl,-install_name,' + str(version / 'Answer'),'-Wl,-headerpad_max_install_names'],check=True,capture_output=True)
        source = '''package main
foreign import native "system:Answer.framework"
foreign native { answer :: proc "c" () -> i32 --- }
main::proc(){assert(answer()==42)}
'''
        artifact = Path(self.invoke(source=source, flags=['-extra-linker-flags:-F"' + str(self.project) + '"']).stdout.strip())
        copied = artifact / 'Contents/Frameworks/Answer.framework'
        self.assertTrue((copied / 'Answer').is_symlink())
        self.assertTrue((copied / 'Versions/Current').is_symlink())
        self.project.rename(self.directory / 'hidden-framework-source')
        run = subprocess.run([str(artifact / 'Contents/MacOS/output')],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_declared_dynamic_plugin_loads_from_bundled_rpath(self):
        native = self.file('plugin.c', b'int answer(void){return 42;}\n')
        library = self.project / 'plugin.dylib'
        subprocess.run(['clang','-dynamiclib',str(native),'-o',str(library),
                        '-Wl,-install_name,@rpath/plugin.dylib','-Wl,-headerpad_max_install_names'],check=True,capture_output=True)
        source = '''package main
import "core:dynlib"
main::proc(){
    library,ok:=dynlib.load_library("@rpath/plugin.dylib");assert(ok)
    defer dynlib.unload_library(library)
    pointer,found:=dynlib.symbol_address(library,"answer");assert(found)
    answer:=cast(proc "c" ()->i32)pointer
    assert(answer()==42)
}
'''
        artifact = Path(self.invoke(source=source, flags=['--app-library','plugin.dylib']).stdout.strip())
        self.project.rename(self.directory / 'hidden-plugin-source')
        run = subprocess.run([str(artifact / 'Contents/MacOS/output')],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    @unittest.skipUnless(os.environ.get('THOR_TEST_LAUNCH_SERVICES') == '1',
                         'Set THOR_TEST_LAUNCH_SERVICES=1 outside the shell sandbox for Finder launch integration')
    def test_finder_gui_launch_runs_without_a_terminal_wrapper(self):
        source = '''package main
import "core:os"
main::proc(){err:=os.write_entire_file(os.args[1],"GUI launch succeeded");assert(err==nil)}
'''
        artifact = Path(self.invoke(source=source).stdout.strip())
        marker = self.output / 'finder-result.txt'
        opened = subprocess.run(['open','-n','-W',str(artifact),'--args',str(marker)],capture_output=True,text=True,timeout=30)
        self.assertEqual(opened.returncode,0,opened.stderr)
        self.assertEqual(marker.read_text(),'GUI launch succeeded')
        info = plistlib.loads((artifact / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleExecutable'],'output')

    def test_json_packaging_failure_is_one_structured_diagnostic(self):
        errors = io.StringIO()
        with contextlib.chdir(self.output), contextlib.redirect_stderr(errors), patch.object(Publisher, 'package', side_effect=PublishError('missing dependency')):
            status = driver._main(['publish', str(self.project), '-json-errors'])
        self.assertEqual(status, 1)
        diagnostic = json.loads(errors.getvalue())
        self.assertIn('missing dependency', str(diagnostic))
        self.assertIn('publish', str(diagnostic))
        self.assertFalse(list(self.output.glob('.thor-publish-*')))

    def test_republishing_updates_only_managed_app(self):
        self.file('assets/old.txt')
        first = Path(self.invoke().stdout.strip())
        (self.project / 'assets/old.txt').unlink()
        self.file('assets/new.txt')
        second = Path(self.invoke().stdout.strip())
        self.assertEqual(first, second)
        self.assertFalse((second / 'Contents/Resources/assets/old.txt').exists())
        self.assertTrue((second / 'Contents/Resources/assets/new.txt').exists())

    def test_publish_compiler_errors_keep_original_source_locations_and_previous_app(self):
        artifact = Path(self.invoke().stdout.strip())
        before = (artifact / 'Contents/MacOS/output').read_bytes()
        (self.project / 'main.thor').write_text('package main\nmain::proc(){\n    x:int="wrong"\n    _=x\n}\n')
        result = subprocess.run([str(ROOT / 'thor'), 'publish', str(self.project), '-json-errors'],
                                cwd=self.output, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        diagnostics = json.loads(result.stderr)['errors']
        self.assertTrue(any(d.get('pos', {}).get('file') == str(self.project / 'main.thor') for d in diagnostics), diagnostics)
        self.assertEqual((artifact / 'Contents/MacOS/output').read_bytes(), before)
        self.assertFalse(list(self.output.glob('.thor-publish-*')))

    def test_debug_allocation_tracking_accepts_owned_resource_paths(self):
        result = self.invoke(flags=['-debug', '--no-sanitize'], source='''package main
import "thor:app"
main::proc(){path:=app.resource_path("assets/file.txt");defer delete(path)}
''')
        artifact = Path(result.stdout.strip())
        run = subprocess.run([str(artifact / 'Contents/MacOS/output')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, '')
        self.assertTrue((artifact / 'Contents/Resources/Debug').is_dir())

    def test_bad_publish_config_does_not_affect_ordinary_builds(self):
        self.config('[publish\nmalformed')
        self.invoke('build')
        self.assertTrue((self.output / 'output').is_file())

    def test_resource_helper_checks_on_linux_target(self):
        source = 'package main\nimport "thor:app"\nmain::proc(){p:=app.resource_path("assets/data.txt");delete(p)}\n'
        self.invoke('check', ['-target:linux_amd64'], source=source)

    def test_publish_flags_are_not_accepted_by_build(self):
        result = subprocess.run([str(ROOT / 'thor'), 'build', str(self.project), '--app-type', 'gui'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('only supported by thor publish', result.stderr)
