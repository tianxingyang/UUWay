"""Synthetic IDD -> guardian -> virtual negotiation -> capture ACK regressions.

Uses the production display planner and confirmation gates with a fake Mutter
bus and synthetic allocation evidence. No UU client, pixels, encoder or live
desktop is involved; ACK is not proof of remote decoding/presentation.
"""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_display_guardian as guardian
import native_display_topology as topology
from native_display_config import decode_monitors
from native_display_confirmation import confirmation_request
from native_runtime_state import read_private
from native_virtual_display import VirtualDisplays, VirtualSelectedDisplay, mode_entry
from tests.test_native_display_topology import FakeBus

spec = importlib.util.spec_from_file_location('adaptive_capture_broker', ROOT / 'scripts/uu-native-capture-broker.py')
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class Selected(VirtualSelectedDisplay):
    def __init__(self, connector, manager, bus):
        self.connector, self.manager, self.bus, self.owner = connector, manager, bus, 'fixture-owner'

    def identity(self):
        screen = self.manager.find(self.connector)
        return ['fixture-boot', self.owner, self.connector, screen['session'] if screen else 'physical']

    def _reply(self):
        return copy.deepcopy(self.bus.reply)


class AdaptiveResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        flags = SimpleNamespace(DBusCallFlags=SimpleNamespace(NO_AUTO_START=0))
        override = patch.object(topology, 'Gio', flags)
        override.start()
        self.addCleanup(override.stop)
        self.bus = FakeBus()
        # RecordVirtual advertises only its currently negotiated native mode.
        first = mode_entry((1920, 1080, 60))['id']
        self.bus.reply[1][1][1][:] = [(first, 1920, 1080, 60., 1., [1.], {'is-current': True})]
        self.manager = VirtualDisplays.__new__(VirtualDisplays)
        self.screen = dict(slot=0, connector='Virtual-fixture', session='session-one', history=[(1920, 1080, 60)])
        self.manager.screens = {0: self.screen}
        self.manager.modes = {0: [(1920, 1080, 60), (1280, 720, 60)]}
        self.manager._event = Mock()
        self.manager.backend = SimpleNamespace(monitors=lambda: decode_monitors(self.bus.reply))
        self.negotiated = []

        def negotiate(screen, mode):
            self.assertIs(screen, self.screen)
            self.negotiated.append(mode)
            physical = copy.deepcopy(self.bus.reply[1])
            modes = physical[1][1]
            entry = mode_entry(mode)
            modes[:] = [(entry['id'], mode[0], mode[1], float(mode[2]), 1., [1.], {'is-current': True})]
            self.bus.reply = (self.bus.reply[0] + 1, physical, self.bus.reply[2], self.bus.reply[3])
            if mode not in screen['history']:
                screen['history'].append(mode)
                del screen['history'][:-32]

        self.manager._negotiate = negotiate
        self.router = guardian.DisplayRouter(self.manager.backend, self.parent,
            lambda connector: Selected(connector, self.manager, self.bus))
        self.manager.router = self.router

    def select(self, width, height, refresh=60):
        # Reviewed vendor IDD select-mode wire; this is not a made-up viewport RPC.
        return self.manager.ioctl(0x30c00c, list(struct.pack('<5I', 0, 1, width, height, refresh)))

    def record(self):
        return read_private(self.router.journal(1))

    def evidence(self, width, height, started=None):
        return dict(width=width, height=height, accepted_ns=time.monotonic_ns(),
                    capture_started_ns=started or time.monotonic_ns(), screen_id=1)

    def confirm(self, width, height):
        evidence = self.evidence(width, height)
        evidence['accepted_ns'] = time.monotonic_ns()
        request = confirmation_request(self.record(), {}, evidence, time.monotonic_ns())
        self.assertIsNotNone(request)
        self.assertEqual(self.router.request(request), dict(confirmed=True))

    def test_landscape_portrait_and_repeated_resize_use_guarded_capture_recreation(self):
        original_started = time.monotonic_ns()
        physical = self.manager.backend.monitors()[0]
        for width, height in ((1280, 720), (1080, 1920), (2560, 1440), (720, 1280)):
            with self.subTest(size=(width, height)):
                self.select(width, height)
                record = self.record()
                self.assertEqual(record['confirmation_policy'], 'gpu_frame')
                self.assertTrue(broker.transaction_requires_new_capture(self.parent, 1, original_started))
                self.assertFalse(broker.transaction_requires_new_capture(self.parent, 0, original_started))
                fresh_started = time.monotonic_ns()
                self.assertFalse(broker.transaction_requires_new_capture(self.parent, 1, fresh_started))
                # Metadata-only negotiation cannot confirm; a new selected
                # allocation must carry exactly the requested dimensions.
                bad = self.evidence(height, width, fresh_started)
                self.assertIsNone(confirmation_request(record, {}, bad, time.monotonic_ns()))
                self.confirm(width, height)
                native = self.manager.backend.monitors()[1]
                self.assertEqual((native['width'], native['height']), (width, height))
                self.assertEqual((native['x'], native['y'], native['scale']), (1920, 0, 1.))
                self.assertFalse(broker.transaction_requires_new_capture(self.parent, 1, original_started))
                self.assertEqual(self.manager.backend.monitors()[0], physical)
        self.assertEqual(self.negotiated, [(1280, 720, 60), (1080, 1920, 60),
                                           (2560, 1440, 60), (720, 1280, 60)])

    def test_resize_burst_rejects_old_same_size_ack_and_rolls_back_to_confirmed_baseline(self):
        self.select(1280, 720)
        old = self.record()
        old_started = time.monotonic_ns()
        self.select(1080, 1920)
        self.select(1280, 720)
        latest = self.record()
        self.assertNotEqual(old['transaction'], latest['transaction'])
        self.assertEqual(latest['pending']['before']['mode'], mode_entry((1920, 1080, 60))['id'])
        evidence = self.evidence(1280, 720, old_started)
        self.assertIsNone(confirmation_request(latest, {}, evidence, time.monotonic_ns()))
        with self.assertRaisesRegex(ValueError, 'Stale display confirmation'):
            self.router.request(dict(version=1, op='confirm', output=1,
                transaction=old['transaction'], serial=latest['pending']['observed']['serial']))
        selected = self.router.guardian(1)
        selected.deadline = 0
        self.assertEqual(selected.expire(), 'rolled_back')
        self.assertEqual(self.manager.backend.monitors()[1]['width'], 1920)
        self.assertIsNone(selected.transaction.pending)
        self.assertIsNone(selected.registry_default)

    def test_retarget_checkpoint_failure_retains_original_deadline_and_confirmation(self):
        self.select(1280, 720)
        selected = self.router.guardian(1)
        previous = self.record()
        deadline = selected.deadline
        catalogue = copy.deepcopy(self.manager.modes)
        with patch.object(guardian, 'write_private', side_effect=OSError('fixture checkpoint failure')):
            with self.assertRaises(guardian.DisplayDefaultWriteError):
                self.select(1080, 1920)
        self.assertEqual(self.record(), previous)
        self.assertEqual(selected.identifier, previous['transaction'])
        self.assertEqual(selected.deadline, deadline)
        self.assertEqual(self.manager.modes, catalogue)
        self.assertEqual(self.negotiated, [(1280, 720, 60)])
        self.confirm(1280, 720)

    def test_later_local_change_is_preserved_and_retarget_is_rejected(self):
        self.select(1280, 720)
        self.bus.reply = (self.bus.reply[0] + 1, *self.bus.reply[1:])
        for width, height in ((1280, 720), (1080, 1920)):
            with self.subTest(size=(width, height)), self.assertRaisesRegex(RuntimeError, 'observation is no longer current'):
                self.select(width, height)
        selected = self.router.guardian(1)
        selected.deadline = 0
        self.assertEqual(selected.expire(), 'later_display_change_preserved')
        self.assertEqual(self.manager.backend.monitors()[1]['width'], 1280)

    def test_reconnect_does_not_reuse_previous_virtual_sessions_default(self):
        self.select(1280, 720)
        self.confirm(1280, 720)
        self.assertIsNotNone(self.router.guardian(1).registry_default)
        self.screen['session'] = 'session-two'
        self.assertIsNone(self.router.guardian(1).registry_default)
        self.select(1080, 1920)
        self.confirm(1080, 1920)

    def test_physical_and_manual_pending_transactions_cannot_be_superseded(self):
        for index, confirmation in ((0, 'gpu_frame'), (1, None)):
            with self.subTest(index=index, confirmation=confirmation):
                request = dict(version=1, op='apply', output=index, serial=self.bus.reply[0],
                               width=1280, height=720)
                if confirmation:
                    request['confirmation'] = confirmation
                self.router.request(request)
                with self.assertRaisesRegex(RuntimeError, 'still requires confirmation'):
                    self.router.request(dict(request, serial=self.bus.reply[0], width=1920, height=1080))
                selected = self.router.guardian(index)
                selected.deadline = 0
                self.assertEqual(selected.expire(), 'rolled_back')

    def test_invalid_mode_or_missing_guardian_never_negotiates(self):
        for mode in ((1921, 1080), (0, 720), (5000, 720)):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.select(*mode)
        self.manager.router = None
        with self.assertRaisesRegex(RuntimeError, 'guarded owned virtual display'):
            self.select(1280, 720)
        self.assertEqual(self.negotiated, [])
        self.assertEqual(self.bus.calls, [])

    def test_long_resize_burst_and_full_catalogue_cannot_evict_rollback_mode(self):
        for index in range(35):
            self.select(1280 + index * 2, 720)
        modes = [(2000 + index * 2, 1000, 60) for index in range(64)]
        packet = struct.pack('<2I', 0, 64) + b''.join(struct.pack('<3I', *mode) for mode in modes)
        self.manager.ioctl(0x30c010, list(packet))
        self.assertEqual(len(self.manager.entries(self.screen)), 64)
        selected = self.router.guardian(1)
        selected.deadline = 0
        self.assertEqual(selected.expire(), 'rolled_back')
        self.assertEqual(self.manager.backend.monitors()[1]['width'], 1920)

    def test_ambiguous_negotiation_failure_is_retained_without_replay(self):
        negotiate = self.manager._negotiate

        def lost_reply(screen, mode):
            negotiate(screen, mode)
            raise RuntimeError('fixture lost reply after mutation')

        self.manager._negotiate = lost_reply
        with self.assertRaisesRegex(RuntimeError, 'lost reply after mutation'):
            self.select(1280, 720)
        selected = self.router.guardian(1)
        selected.deadline = 0
        self.assertEqual(selected.expire(), 'ambiguous_apply_requires_review')
        self.assertEqual(self.negotiated, [(1280, 720, 60)])
        self.assertIsNone(selected.transaction.pending['observed'])
