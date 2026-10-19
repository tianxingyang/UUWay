"""No apt commands are executed; verify distribution isolation before mutation."""
from pathlib import Path
from unittest.mock import patch

from tests.test_uuway_cli import CliCase, cli


class DebianWineSourceTests(CliCase):
    def source(self, release, files=()):
        with patch.object(cli, 'os_release', return_value=release), \
                patch.object(Path, 'glob', side_effect=lambda *unused: iter(files)):
            return cli.wine_source()

    def test_debian_13_uses_only_the_trixie_debian_repository(self):
        path, url = self.source(dict(ID='debian', VERSION_ID='13', VERSION_CODENAME='trixie'))
        self.assertEqual(path.name, 'winehq-trixie.sources')
        self.assertEqual(url, 'https://dl.winehq.org/wine-builds/debian/dists/trixie/winehq-trixie.sources')
        self.assertNotIn('ubuntu', url)

    def test_unsupported_or_inconsistent_release_stops_before_any_command(self):
        for release in ({}, dict(ID='debian', VERSION_ID='12'),
                        dict(ID='debian', VERSION_ID='13', VERSION_CODENAME='bookworm'),
                        dict(ID='other', ID_LIKE='ubuntu', VERSION_ID='24.04')):
            with self.subTest(release=release), patch.object(cli, 'os_release', return_value=release), \
                    patch.object(cli, 'wine_version', return_value=None):
                ctx = self.record_commands(self.context())
                with self.assertRaises(cli.SetupError):
                    self.quiet(cli.wine_run, ctx)
                self.assertEqual(self.commands, [])

    def test_foreign_and_ambiguous_sources_stop_before_dpkg_or_apt(self):
        source = self.root / 'winehq-existing.sources'
        release = dict(ID='debian', VERSION_ID='13')
        for content in (
                'URIs: https://dl.winehq.org/wine-builds/ubuntu\nSuites: noble\n',
                'URIs: https://dl.winehq.org/wine-builds/debian\nSuites: bookworm\n',
                'URIs: https://dl.winehq.org/wine-builds/debian\nSuites: trixie\n bookworm\n',
                'URIs: https://example.invalid/wine-builds/debian\nSuites: trixie\n',
                'not a source'):
            with self.subTest(content=content), patch.object(cli, 'os_release', return_value=release), \
                    patch.object(cli, 'wine_version', return_value=None), \
                    patch.object(Path, 'glob', side_effect=lambda *unused: iter([source])):
                source.write_text(content)
                ctx = self.record_commands(self.context())
                with self.assertRaises(cli.SetupError):
                    self.quiet(cli.wine_run, ctx)
                self.assertEqual(self.commands, [])
                self.assertEqual(source.read_text(), content)

    def test_matching_sources_are_preserved_including_legacy_format(self):
        for name, content in (
                ('winehq-trixie.sources', 'URIs: https://dl.winehq.org/wine-builds/debian/\nSuites: trixie\n'),
                ('winehq.list', 'deb [signed-by=/etc/apt/keyrings/winehq.asc] '
                              'https://dl.winehq.org/wine-builds/debian trixie main\n')):
            with self.subTest(name=name):
                source = self.root / name
                source.write_text(content)
                self.source(dict(ID='debian', VERSION_ID='13'), [source])
                self.assertEqual(source.read_text(), content)

    def test_debian_install_command_plan_never_mentions_noble(self):
        ctx = self.record_commands(self.context())
        with patch.object(cli, 'os_release', return_value=dict(ID='debian', VERSION_ID='13')), \
                patch.object(cli, 'wine_version', side_effect=[None, 'wine-11.0']), \
                patch.object(Path, 'glob', side_effect=lambda *unused: iter([])):
            self.quiet(cli.wine_run, ctx)
        plan = repr(self.commands)
        self.assertIn('winehq-trixie.sources', plan)
        self.assertNotIn('noble', plan)
        self.assertNotIn('/ubuntu/', plan)
        self.assertIn('winehq-stable=11.0.*', plan)
