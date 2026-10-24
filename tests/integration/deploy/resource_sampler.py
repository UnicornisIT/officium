"""Resource sampling for the free-tier deployment scenario (run as root).

    resource_sampler.py sample OUT.jsonl SLICE_CGROUP_PATH [CONTAINER_ID]   # until killed
    resource_sampler.py report OUT.jsonl MARKERS OUT.json

sample writes one JSON line every 200 ms: the application slice's memory and
CPU (cgroup v2), the MySQL container's memory, the host's used memory
(MemTotal - MemAvailable) and free space of /opt/officium. MARKERS has lines
"<epoch seconds> start|end <step name>"; report gives every step its peaks.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

MIB = 1024 * 1024


def _read_int(path):
    try:
        return int(Path(path).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _cpu_usec(cgroup):
    try:
        for line in (cgroup / 'cpu.stat').read_text().splitlines():
            if line.startswith('usage_usec'):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _host_used():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _sep, rest = line.partition(':')
        values[key] = int(rest.split()[0]) * 1024
    return values['MemTotal'] - values['MemAvailable'], values.get('SwapTotal', 0) - values.get('SwapFree', 0)


def _container_cgroup(container_id):
    for candidate in (
        Path(f'/sys/fs/cgroup/system.slice/docker-{container_id}.scope'),
        Path(f'/sys/fs/cgroup/docker/{container_id}'),
    ):
        if candidate.is_dir():
            return candidate
    return None


def sample(out_path, slice_name, container_id=None):
    # A dash nests slices (officium-budget.slice lives in officium.slice), so
    # the caller passes the path from `systemctl show -p ControlGroup`.
    slice_cgroup = Path(slice_name) if slice_name.startswith('/') else Path('/sys/fs/cgroup') / slice_name
    container = _container_cgroup(container_id) if container_id else None
    with open(out_path, 'a', encoding='utf-8') as out:
        while True:
            used, swap = _host_used()
            record = {
                't': time.time(),
                'app_mem': _read_int(slice_cgroup / 'memory.current'),
                'app_cpu_usec': _cpu_usec(slice_cgroup),
                'mysql_mem': _read_int(container / 'memory.current') if container else None,
                'mysql_cpu_usec': _cpu_usec(container) if container else None,
                'host_used': used,
                'host_swap_used': swap,
                'disk_free': shutil.disk_usage('/opt/officium').free if os.path.isdir('/opt/officium') else None,
            }
            out.write(json.dumps(record) + '\n')
            out.flush()
            time.sleep(0.2)


def report(samples_path, markers_path, out_path):
    samples = [json.loads(line) for line in Path(samples_path).read_text().splitlines() if line.strip()]
    windows = {}
    for line in Path(markers_path).read_text().splitlines():
        stamp, kind, name = line.split(' ', 2)
        windows.setdefault(name, {})[kind] = float(stamp)
    results = []
    for name, window in windows.items():
        if 'start' not in window or 'end' not in window:
            continue
        inside = [item for item in samples if window['start'] <= item['t'] <= window['end']]
        if not inside:
            continue

        def peak(key):
            values = [item[key] for item in inside if item.get(key) is not None]
            return round(max(values) / MIB, 1) if values else None

        def delta(key):
            values = [item[key] for item in inside if item.get(key) is not None]
            return round((values[-1] - values[0]) / 1e6, 2) if len(values) > 1 else None

        disk = [item['disk_free'] for item in inside if item.get('disk_free') is not None]
        results.append({
            'step': name,
            'seconds': round(window['end'] - window['start'], 1),
            'app_slice_peak_mib': peak('app_mem'),
            'mysql_peak_mib': peak('mysql_mem'),
            'host_used_peak_mib': peak('host_used'),
            'host_swap_used_peak_mib': peak('host_swap_used'),
            'app_cpu_seconds': delta('app_cpu_usec'),
            'mysql_cpu_seconds': delta('mysql_cpu_usec'),
            'disk_free_min_mib': round(min(disk) / MIB, 1) if disk else None,
            'disk_free_change_mib': round((disk[-1] - disk[0]) / MIB, 1) if disk else None,
        })
    Path(out_path).write_text(json.dumps(results, indent=2), encoding='utf-8')
    header = ('step', 'seconds', 'app_slice_peak_mib', 'mysql_peak_mib', 'host_used_peak_mib',
              'app_cpu_seconds', 'mysql_cpu_seconds', 'disk_free_min_mib', 'disk_free_change_mib')
    print('| ' + ' | '.join(header) + ' |')
    print('|' + '---|' * len(header))
    for item in results:
        print('| ' + ' | '.join(str(item.get(key)) for key in header) + ' |')


if __name__ == '__main__':
    if sys.argv[1] == 'sample':
        sample(*sys.argv[2:])
    elif sys.argv[1] == 'report':
        report(*sys.argv[2:5])
    else:
        raise SystemExit(__doc__)
