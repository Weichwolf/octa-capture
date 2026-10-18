# SPDX-License-Identifier: GPL-2.0-only
"""Run ALSA test utilities with bounded, unprivileged RTKit scheduling."""

import os
import resource
import sys
import threading

PRIORITY = 5
MAX_CPU_US = 200000
SERVICE = 'org.freedesktop.RealtimeKit1'


def rtkit_call(interface, method, signature, values):
    from gi.repository import Gio, GLib

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = connection.call_sync(SERVICE, '/org/freedesktop/RealtimeKit1', interface, method,
                                     GLib.Variant(signature, values), None,
                                     Gio.DBusCallFlags.NONE, 3000, None)
        return reply.unpack() if reply is not None else ()
    except GLib.Error as error:
        raise RuntimeError(f'RTKit request failed: {error.message}') from error


def rtkit_property(name):
    result = rtkit_call('org.freedesktop.DBus.Properties', 'Get', '(ss)', (SERVICE, name))
    if len(result) != 1 or type(result[0]) is not int or result[0] <= 0:
        raise RuntimeError(f'Invalid RTKit property: {name}.')
    return result[0]


def scheduling_parameters(pid, require_realtime=False):
    policy = os.sched_getscheduler(pid)
    priority = os.sched_getparam(pid).sched_priority
    base = policy & ~os.SCHED_RESET_ON_FORK
    names = {getattr(os, 'SCHED_' + name): 'SCHED_' + name for name in ('OTHER', 'FIFO', 'RR', 'BATCH', 'IDLE')}
    result = {'policy': names.get(base, str(base)), 'priority': priority,
              'reset_on_fork': bool(policy & os.SCHED_RESET_ON_FORK)}
    if require_realtime:
        if base != os.SCHED_RR or priority != PRIORITY or not result['reset_on_fork']:
            raise RuntimeError(f'Audio process {pid} lost the requested realtime scheduling: {result}')
        soft, hard = resource.prlimit(pid, resource.RLIMIT_RTTIME)
        if not 0 < soft <= hard <= MAX_CPU_US:
            raise RuntimeError(f'Audio process {pid} has an invalid realtime CPU budget: {(soft, hard)}')
        result.update(cpu_soft_limit_us=soft, cpu_hard_limit_us=hard)
    return result


def enable_realtime():
    if rtkit_property('MaxRealtimePriority') < PRIORITY:
        raise RuntimeError('RTKit does not allow the requested audio priority.')
    budget = min(MAX_CPU_US, rtkit_property('RTTimeUSecMax'))
    for limit in resource.getrlimit(resource.RLIMIT_RTTIME):
        if limit != resource.RLIM_INFINITY:
            budget = min(budget, limit)
    if budget <= 0:
        raise RuntimeError('The current realtime CPU limit prevents an RTKit request.')
    resource.setrlimit(resource.RLIMIT_RTTIME, (budget, budget))
    rtkit_call(SERVICE, 'MakeThreadRealtime', '(tu)', (threading.get_native_id(), PRIORITY))
    return scheduling_parameters(os.getpid(), require_realtime=True)


def main(arguments):
    if not arguments or arguments[0] not in ('arecord', 'aplay'):
        raise ValueError('Usage: pcm_realtime.py arecord|aplay [ALSA arguments]')
    enable_realtime()
    os.execvp(arguments[0], arguments)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ImportError, RuntimeError, ValueError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        raise SystemExit(1) from error
