# SPDX-License-Identifier: GPL-2.0-only
"""Exercise RTKit refusal, bounded resource limits and independent policy checks."""

import os
from pathlib import Path
import resource
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import pcm_realtime as realtime


class Policy(unittest.TestCase):
    def setUp(self):
        for target, value in (('sched_getscheduler', os.SCHED_RR | os.SCHED_RESET_ON_FORK),
                              ('sched_getparam', os.sched_param(5))):
            mock = patch.object(realtime.os, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        limits = patch.object(realtime.resource, 'prlimit', return_value=(200000, 200000))
        limits.start()
        self.addCleanup(limits.stop)

    def test_verified_rr_priority_reset_and_bounded_cpu_time(self):
        result = realtime.scheduling_parameters(123, require_realtime=True)
        self.assertEqual(result, {'policy': 'SCHED_RR', 'priority': 5, 'reset_on_fork': True,
                                  'cpu_soft_limit_us': 200000, 'cpu_hard_limit_us': 200000})

    def test_normal_scheduling_is_reported_without_claiming_realtime(self):
        with patch.object(realtime.os, 'sched_getscheduler', return_value=os.SCHED_OTHER), \
                patch.object(realtime.os, 'sched_getparam', return_value=os.sched_param(0)):
            self.assertEqual(realtime.scheduling_parameters(123),
                             {'policy': 'SCHED_OTHER', 'priority': 0, 'reset_on_fork': False})
            with self.assertRaisesRegex(RuntimeError, 'requested realtime scheduling'):
                realtime.scheduling_parameters(123, require_realtime=True)

    def test_unexpected_priority_or_inheritable_realtime_is_rejected(self):
        for policy, priority in ((os.SCHED_RR, 5), (os.SCHED_FIFO | os.SCHED_RESET_ON_FORK, 5),
                                 (os.SCHED_RR | os.SCHED_RESET_ON_FORK, 6)):
            with self.subTest(policy=policy, priority=priority), \
                    patch.object(realtime.os, 'sched_getscheduler', return_value=policy), \
                    patch.object(realtime.os, 'sched_getparam', return_value=os.sched_param(priority)), \
                    self.assertRaisesRegex(RuntimeError, 'requested realtime scheduling'):
                realtime.scheduling_parameters(123, require_realtime=True)

    def test_unlimited_excessive_or_invalid_cpu_budget_is_rejected(self):
        for limits in ((-1, -1), (0, 200000), (200001, 200001), (200000, 100000)):
            with self.subTest(limits=limits), patch.object(realtime.resource, 'prlimit', return_value=limits), \
                    self.assertRaisesRegex(RuntimeError, 'invalid realtime CPU budget'):
                realtime.scheduling_parameters(123, require_realtime=True)


class Request(unittest.TestCase):
    def setUp(self):
        properties = patch.object(realtime, 'rtkit_property', side_effect=(20, 200000))
        self.properties = properties.start()
        self.addCleanup(properties.stop)
        for name, value in (('getrlimit', (-1, -1)), ('setrlimit', None)):
            mock = patch.object(realtime.resource, name, return_value=value)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)
        request = patch.object(realtime, 'rtkit_call', return_value=())
        self.request = request.start()
        self.addCleanup(request.stop)
        policy = patch.object(realtime, 'scheduling_parameters', return_value={'policy': 'SCHED_RR'})
        self.policy = policy.start()
        self.addCleanup(policy.stop)

    def test_budget_is_set_before_request_and_policy_is_independently_verified(self):
        def accepted(*_args):
            self.setrlimit.assert_called_once_with(resource.RLIMIT_RTTIME, (200000, 200000))
        self.request.side_effect = accepted
        self.assertEqual(realtime.enable_realtime(), {'policy': 'SCHED_RR'})
        self.request.assert_called_once_with(realtime.SERVICE, 'MakeThreadRealtime', '(tu)',
                                             (realtime.threading.get_native_id(), 5))
        self.policy.assert_called_once_with(os.getpid(), require_realtime=True)

    def test_stricter_existing_soft_and_hard_limits_are_preserved(self):
        self.getrlimit.return_value = (50000, 100000)
        realtime.enable_realtime()
        self.setrlimit.assert_called_once_with(resource.RLIMIT_RTTIME, (50000, 50000))

    def test_service_and_local_limits_cannot_be_exceeded(self):
        self.properties.side_effect = (20, 100000)
        realtime.enable_realtime()
        self.setrlimit.assert_called_once_with(resource.RLIMIT_RTTIME, (100000, 100000))

    def test_a_permissive_service_cannot_raise_the_test_cpu_budget(self):
        self.properties.side_effect = (20, 1000000)
        realtime.enable_realtime()
        self.setrlimit.assert_called_once_with(resource.RLIMIT_RTTIME, (200000, 200000))

    def test_disabled_cpu_budget_or_insufficient_priority_cannot_request_realtime(self):
        self.getrlimit.return_value = (0, 0)
        with self.assertRaisesRegex(RuntimeError, 'current realtime CPU limit'):
            realtime.enable_realtime()
        self.properties.side_effect = (4,)
        with self.assertRaisesRegex(RuntimeError, 'does not allow'):
            realtime.enable_realtime()
        self.request.assert_not_called()

    def test_request_refusal_cannot_be_reported_as_success(self):
        self.request.side_effect = RuntimeError('RTKit refused')
        with self.assertRaisesRegex(RuntimeError, 'RTKit refused'):
            realtime.enable_realtime()
        self.policy.assert_not_called()

    def test_only_the_two_audio_utilities_are_execed_after_successful_setup(self):
        with patch.object(realtime, 'enable_realtime') as enabled, patch.object(realtime.os, 'execvp') as execute:
            for command in ('arecord', 'aplay'):
                realtime.main([command, '-c12'])
                execute.assert_called_with(command, [command, '-c12'])
            self.assertEqual(enabled.call_count, 2)
            for arguments in ([], ['sh'], ['sudo', 'arecord']):
                with self.assertRaises(ValueError):
                    realtime.main(arguments)
            self.assertEqual(enabled.call_count, 2)

    def test_refused_setup_never_executes_the_audio_utility(self):
        with patch.object(realtime, 'enable_realtime', side_effect=RuntimeError('RTKit refused')), \
                patch.object(realtime.os, 'execvp') as execute:
            with self.assertRaisesRegex(RuntimeError, 'RTKit refused'):
                realtime.main(['arecord', '-c12'])
            execute.assert_not_called()


class Properties(unittest.TestCase):
    def test_malformed_or_nonpositive_service_properties_fail(self):
        for response in ((), (True,), (0,), (-1,), ('20',), (20, 20)):
            with self.subTest(response=response), patch.object(realtime, 'rtkit_call', return_value=response), \
                    self.assertRaisesRegex(RuntimeError, 'Invalid RTKit property'):
                realtime.rtkit_property('MaxRealtimePriority')


if __name__ == '__main__':
    unittest.main()
