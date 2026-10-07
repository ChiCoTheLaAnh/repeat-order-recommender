"""Local closed-loop HTTP benchmark; starts and stops one real API subprocess."""
import argparse
import asyncio
from collections import Counter
from itertools import count
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from .audit_raw import ROOT
from .local_api import resolve_bundle
from .model_selection import hardware, percentiles


def summarize(latencies,statuses,seconds):
    successes = [value for value,status in zip(latencies,statuses) if status == 200]
    return {'completed_requests':len(statuses),'successful_requests':len(successes),
        'error_rate':1-len(successes)/len(statuses) if statuses else 1.,
        'status_counts':dict(Counter(map(str,statuses))),
        'throughput_requests_per_second':len(statuses)/seconds,
        'successful_requests_per_second':len(successes)/seconds,
        'latency_all_attempts':percentiles(latencies) if latencies else None,
        'latency_successful':percentiles(successes) if successes else None,
        'elapsed_seconds':seconds}


def memory(pid):
    values = {}
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith(('VmRSS:','VmHWM:')):
            name,value,*_ = line.split()
            values[name.rstrip(':')] = int(value)/1024
    return {'rss_mib':values['VmRSS'],'peak_rss_mib':values['VmHWM']}


def request_mode(number,unknown_fraction):
    if not 0 <= unknown_fraction <= 1:
        raise ValueError('Unknown fraction must be between zero and one')
    unknown = math.floor((number+1)*unknown_fraction) > math.floor(number*unknown_fraction)
    return 'popularity_fallback' if unknown else 'personalized'


async def measure(base,customer_ids,concurrency,seconds,config):
    latencies,statuses,modes = [],[],[]
    sequence = count()
    async with httpx.AsyncClient(base_url=base,timeout=10.,trust_env=False,
        limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        for number in range(config['warmup_requests']):
            response = await client.get('/recommendations/'+customer_ids[number%len(customer_ids)],params={'k':config['k']})
            response.raise_for_status()
        started = time.perf_counter()
        deadline = started+seconds

        async def worker(offset):
            while time.perf_counter() < deadline:
                number = next(sequence)
                # Fixed 90/10 identified/fallback traffic. IDs never appear in reports/logs.
                expected = request_mode(number,config['unknown_fraction'])
                customer = 'unknown-load-test-customer' if expected == 'popularity_fallback' else customer_ids[number%len(customer_ids)]
                request_start = time.perf_counter()
                try:
                    result = await client.get('/recommendations/'+customer,params={'k':config['k']})
                    status = result.status_code
                    if status == 200:
                        body = result.json()
                        if body['recommendation_mode'] != expected or len(body['items']) != config['k']:
                            status = -1
                except httpx.HTTPError:
                    status = 0
                latencies.append(1000*(time.perf_counter()-request_start))
                statuses.append(status)
                modes.append(expected)

        await asyncio.gather(*(worker(offset) for offset in range(concurrency)))
        result = summarize(latencies,statuses,time.perf_counter()-started)
        result['concurrency'] = concurrency
        result['attempted_mode_counts'] = dict(Counter(modes))
        result['actual_unknown_fraction'] = modes.count('popularity_fallback')/len(modes) if modes else None
        return result


def run_load_test(bundle_path=None,output=ROOT/'outputs/milestone3',seconds=None):
    bundle_path,output = resolve_bundle(bundle_path),Path(output)
    output.mkdir(parents=True,exist_ok=True)
    config = json.loads((ROOT/'config/release_v1.json').read_text())['load_test']
    customer_ids = sorted(json.loads((bundle_path/'customer_index.json').read_text()))
    manifest = json.loads((bundle_path/'manifest.json').read_text())
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    process_started = time.perf_counter()
    with (output/'api_requests.jsonl').open('w') as logs:
        process = subprocess.Popen([sys.executable,'-m','scripts.local_api','--bundle',str(bundle_path),
            '--port',str(port)],cwd=ROOT,stdout=logs,stderr=logs)
        try:
            with httpx.Client(base_url=base,timeout=2.,trust_env=False) as client:
                ready = None
                while time.perf_counter()-process_started < 45:
                    if process.poll() is not None:
                        raise RuntimeError('Local API exited before readiness')
                    try:
                        response = client.get('/ready')
                        if response.status_code == 200:
                            ready = response.json()
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.05)
                if ready is None:
                    raise RuntimeError('Local API did not become ready; inspect API logs')
            startup_ms = 1000*(time.perf_counter()-process_started)
            startup_memory = memory(process.pid)
            results = []
            for concurrency in config['concurrency']:
                result = asyncio.run(measure(base,customer_ids,concurrency,
                    seconds or config['seconds_per_level'],config))
                result['api_process_memory'] = memory(process.pid)
                result['meets_250ms_p95_engineering_target'] = (result['latency_all_attempts'] is not None and
                    result['latency_all_attempts']['p95_ms'] <= config['target_p95_ms_at_5_clients'] and result['error_rate'] == 0)
                results.append(result)
                print(json.dumps(result),flush=True)
            report = {'release_id':manifest['release_id'],'model_version':manifest['model_version'],
                'data_as_of':manifest['data_as_of'],'hardware':hardware(),'startup_process_to_ready_ms':startup_ms,
                'bundle_validation_and_load_ms':ready['bundle_load_ms'],'startup_memory':startup_memory,
                'conditions':{'config':config,'duration_override_seconds':seconds,'transport':'HTTP over loopback, keepalive',
                    'service':'One uvicorn process; sync FastAPI routes in thread pool; four native threads configured once',
                    'clients':'Closed-loop 1 and 5 asynchronous clients in a separate process, sharing the same CPU quota',
                    'workload':'All stored eligible customers round-robin; 90% 200-candidate personalized scoring, 10% unknown fallback; top10',
                    'timing':'Client end-to-end wall time including HTTP and JSON response validation; 50 warmup calls per level excluded',
                    'memory':'Linux /proc API-process RSS and lifetime high-water RSS; excludes benchmark client',
                    'limitations':'Short local warm runs; no remote network, TLS, sustained soak, cloud, or test-split quality evaluation. Engineering target is not a guarantee.'},
                'results':results,'test_evaluated':False}
            (output/'load_test_report.json').write_text(json.dumps(report,indent=2)+'\n')
            lines = ['# Local warm load test','',f'Release: {manifest["release_id"]}',
                '',f'Process-to-ready: {startup_ms:.1f} ms; validation/load: {ready["bundle_load_ms"]:.1f} ms.',
                '', '| Clients | Requests | p50 ms | p95 ms | Requests/sec | Error rate | Peak API RSS MiB |',
                '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
            for result in results:
                lines.append(f'| {result["concurrency"]} | {result["completed_requests"]} | {result["latency_all_attempts"]["p50_ms"]:.3f} | {result["latency_all_attempts"]["p95_ms"]:.3f} | {result["throughput_requests_per_second"]:.1f} | {result["error_rate"]:.4%} | {result["api_process_memory"]["peak_rss_mib"]:.1f} |')
            lines += ['', 'Conditions and hardware:', '```json',json.dumps({'hardware':report['hardware'],'conditions':report['conditions']},indent=2),'```']
            (output/'load_test_report.md').write_text('\n'.join(lines)+'\n')
            return report
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/milestone3')
    parser.add_argument('--seconds',type=float)
    args = parser.parse_args()
    if args.seconds is not None and args.seconds <= 0:
        parser.error('--seconds must be positive')
    run_load_test(args.bundle,args.output,args.seconds)
