"""Bounded read-only 10s/1s comparison, without persisting message bodies."""
import json
from pathlib import Path
import statistics
import sys
import time
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/wechat_monitor'))
from core import configuration
from clients import AlphaReader

def cpu_snapshot():
    result = {}
    for process in psutil.process_iter(['pid', 'name', 'cpu_times']):
        if (process.info['name'] or '').lower().startswith('alpha'):
            times = process.info['cpu_times']
            result[process.pid] = times.user + times.system
    return result

def phase(reader, config, interval, count):
    starts, latencies, messages = [], [], []
    cpu_before, wall = cpu_snapshot(), time.monotonic()
    for index in range(count):
        target = wall + index * interval
        time.sleep(max(0, target - time.monotonic()))
        started = time.monotonic()
        starts.append(started - wall)
        before = int(time.time() * 1000)
        found = 0
        for source in config['sources']:
            records = reader.messages(source, config['maxPollMessagesPerGroup'], after_ms=before - 300000,
                                      before_ms=before, require_complete=True)
            found += len(records)
        latencies.append((time.monotonic() - started) * 1000)
        messages.append(found)
        if latencies[-1] > 2000:
            raise RuntimeError('BENCHMARK_STOPPED_SLOW_READ')
    elapsed = time.monotonic() - wall
    cpu_after = cpu_snapshot()
    return {'intervalSeconds': interval, 'cycles': count, 'seconds': round(elapsed, 3),
            'medianCycleMs': round(statistics.median(latencies), 2),
            'p95CycleMs': round(sorted(latencies)[max(0, int(len(latencies) * .95) - 1)], 2),
            'maximumCycleMs': round(max(latencies), 2), 'maxMessagesPerCycle': max(messages),
            'alphaCpuCorePercent': round(sum(max(0, cpu_after[pid] - old) for pid, old in cpu_before.items() if pid in cpu_after) / elapsed * 100, 2),
            'cycleStartSeconds': [round(s, 3) for s in starts]}

config = configuration()
reader = AlphaReader(config)
reader.verify_sources()
result = {'liveResidentUnchanged': True, 'phases': [], 'sendCalls': 0, 'modelCalls': 0, 'inputActions': 0,
          'limitations': 'Short sequential quiet-data measurement; not an account-risk or long-running stability test.'}
directory = ROOT / 'artifacts' / ('wechat-poll-benchmark-' + uuid.uuid4().hex[:10])
directory.mkdir()
try:
    for interval, count in ((10, 3), (1, 30)):
        result['phases'].append(phase(reader, config, interval, count))
    result['result'] = 'READ_ONLY_BENCHMARK_OK'
except Exception as error:
    result['result'] = 'READ_ONLY_BENCHMARK_FAILED'
    result['errorType'] = type(error).__name__
    raise
finally:
    result['alphaReadCalls'] = reader.calls
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'evidence': str(directory / 'result.json'), **result}, ensure_ascii=True), flush=True)
