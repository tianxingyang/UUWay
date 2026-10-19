"""Execute the source installer's dependency/Wine stage with command recording.

Never execute sudo/apt or the remaining install/service/display stages.
The distribution/source parser itself is tested in test_debian_wine_source.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceInstallerTests(unittest.TestCase):
    def stage(self, preflight):
        script = (ROOT / 'install.sh').read_text()
        start = script.index('if should_run 1; then')
        end = script.index('\nif [[ -z "$gpu_backend"', start)
        packages_start = script.index('packages=(')
        packages_end = script.index(')\n', packages_start) + 1
        with tempfile.TemporaryDirectory(prefix='uuway-installer-plan-') as temporary:
            directory = Path(temporary)
            helper = directory / 'python'
            helper.write_text('#!/bin/sh\n' + preflight)
            helper.chmod(0o700)
            harness = '''set -Eeuo pipefail
repo_dir="$1"
python="$2"
wine_dir="$3/missing-wine"
__PACKAGES__
dry_run=1
should_run() { return 0; }
info() { :; }
ok() { :; }
confirm() { return 0; }
die() { printf 'REFUSED %s\\n' "$*"; exit 88; }
dpkg-query() { return 1; }
compgen() { return 1; }
run() { printf 'PLAN %s\\n' "$*"; }
'''
            harness = harness.replace('__PACKAGES__', script[packages_start:packages_end])
            return subprocess.run(['bash', '-c', harness + script[start:end], 'fixture',
                                   str(ROOT), str(helper), str(directory)],
                                  capture_output=True, text=True, timeout=10)

    def test_debian_plan_uses_trixie_and_pins_every_wine_package(self):
        result = self.stage("printf '%s\\n' '/etc/apt/sources.list.d/winehq-trixie.sources' "
                            "'https://dl.winehq.org/wine-builds/debian/dists/trixie/winehq-trixie.sources'\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('winehq-trixie.sources', result.stdout)
        self.assertNotIn('noble', result.stdout)
        self.assertNotIn('/ubuntu/', result.stdout)
        self.assertIn('libva-dev vainfo libwine-dev', result.stdout)
        self.assertNotIn('libva-utils', result.stdout)
        for package in ('winehq-stable', 'wine-stable', 'wine-stable-amd64', 'wine-stable-i386:i386'):
            self.assertIn(package + '=11.0.*', result.stdout)
        self.assertLess(result.stdout.index('preferences.d/uuway-wine'),
                        result.stdout.index('apt-get install -y --install-recommends winehq-stable'))

    def test_failed_preflight_runs_no_privileged_command(self):
        result = self.stage('exit 1\n')
        self.assertEqual(result.returncode, 88)
        self.assertNotIn('PLAN ', result.stdout)

    def test_malformed_preflight_runs_no_privileged_command(self):
        result = self.stage("printf 'only-one-line\\n'\n")
        self.assertEqual(result.returncode, 88)
        self.assertNotIn('PLAN ', result.stdout)
