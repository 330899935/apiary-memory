#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memory Hive — search benchmark (read-only).

Measures three things against a running Hive:

  1. recall        : for a sample of approved entries, feed the entry's own
                     title as the query and see where that entry lands.
  2. latency       : warm p50 / p95 for limit=5 and limit=20 (includes the
                     HTTP round-trip).
  3. corpus size   : entries / characters / distinct days read straight from
                     the plaintext warehouse files.

Nothing is written back to the Hive. All requests are GET /api/search.

This is the script that produced `results-2026-10-27.txt`. The title-as-query
setup is an EASY set: it proves the index has the entry and can rank it, not
that a human can ask in their own words and find it. Run it on your own Hive
and publish your own numbers — they will differ.

Usage:
    HIVE_DIR=/path/to/hive python3 bench_search.py

Env:
    HIVE_DIR     directory holding warehouse_*.json   (default: cwd)
    HIVE_API     search endpoint  (default: http://127.0.0.1:8768/api/search)
    HIVE_SAMPLE  number of entries to sample          (default: 40)
    HIVE_SEED    RNG seed so runs are comparable      (default: 20261027)
"""
import json
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HIVE_DIR = os.environ.get('HIVE_DIR', '.')
API = os.environ.get('HIVE_API', 'http://127.0.0.1:8768/api/search')
SAMPLE = int(os.environ.get('HIVE_SAMPLE', '40'))
SEED = int(os.environ.get('HIVE_SEED', '20261027'))

PLAIN = ['warehouse_当前主线.json', 'warehouse_参考资料.json',
         'warehouse_待办执行.json', 'warehouse_归档历史.json']

# Eight short topical queries used for the latency loop. Swap them for
# whatever your own hive actually contains.
LATENCY_QUERIES = ['掘金 阅读量', '蜂巢 推广计划', 'App Store 上架', 'MCP 发布',
                   '记忆库 规模', 'GitHub star', '沙箱 隔离', '隐私仓 加密']


def search(q, limit):
    url = f'{API}?q={urllib.parse.quote(q)}&limit={limit}'
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read().decode('utf-8'))
    return data.get('results', []), (time.time() - t0) * 1000


def _entries(fn):
    path = os.path.join(HIVE_DIR, fn)
    if not os.path.exists(path):
        return []
    try:
        data = json.load(open(path, encoding='utf-8'))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def load_pool():
    """Approved entries with a real title and >=300 chars of body."""
    pool = []
    for fn in PLAIN:
        for x in _entries(fn):
            if not isinstance(x, dict) or not x.get('approved'):
                continue
            body = str(x.get('content') or x.get('raw') or '')
            title = str(x.get('title') or '').strip()
            if len(body) < 300 or not title:
                continue
            pool.append({'id': x.get('id'), 'title': title})
    return pool


def clean_title(t):
    """Strip file extensions, long numbers and bracketing noise."""
    t = re.sub(r'\.(md|json|py|txt|csv|png)$', '', t)
    t = re.sub(r'_?\d{8,}', '', t)
    t = re.sub(r'[【】\[\]（）()《》#*`|]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def pct(values, p):
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round((len(v) - 1) * p / 100.0))))
    return v[k]


def main():
    out = []
    w = out.append

    pool = load_pool()
    w(f'approved entries, titled, body>=300 chars, across {len(PLAIN)} plaintext warehouses: {len(pool)}')

    t0 = time.time()
    search('warmup', 5)
    w(f'cold first query: {(time.time() - t0) * 1000:.0f} ms (excluded)')
    for _ in range(3):
        search('warmup', 5)

    # 1. recall -------------------------------------------------------------
    random.seed(SEED)
    sample = random.sample(pool, min(SAMPLE, len(pool)))
    h1 = h5 = h20 = n = 0
    miss = []
    for it in sample:
        q = clean_title(it['title'])[:60]
        if len(q) < 4:
            continue
        n += 1
        res, _ = search(q, 20)
        ids = [x.get('id') for x in res]
        pos = ids.index(it['id']) + 1 if it['id'] in ids else 0
        h1 += pos == 1
        h5 += 1 <= pos <= 5
        h20 += 1 <= pos <= 20
        if not pos:
            miss.append(it['title'][:32])

    w('')
    w(f'1) recall — auto set, n={n}, query = the entry\'s own title terms')
    if n:
        w(f'   top-1 {h1}/{n} = {h1/n*100:.1f}%   top-5 {h5}/{n} = {h5/n*100:.1f}%   top-20 {h20}/{n} = {h20/n*100:.1f}%')
    if miss:
        w(f'   never surfaced ({len(miss)}): ' + '; '.join(miss[:5]))

    # 2. latency ------------------------------------------------------------
    per = {5: [], 20: []}
    for lim in (5, 20):
        for _ in range(4):
            for q in LATENCY_QUERIES:
                _, ms = search(q, lim)
                per[lim].append(ms)

    w('')
    w('2) latency — warm, includes HTTP round-trip')
    for lim in (5, 20):
        v = per[lim]
        w(f'   limit={lim:<2} n={len(v)}  p50={pct(v,50):.0f}ms  p95={pct(v,95):.0f}ms  '
          f'mean={statistics.mean(v):.0f}ms  max={max(v):.0f}ms')

    # 3. corpus size --------------------------------------------------------
    w('')
    w('3) corpus size')
    tot_n = tot_c = 0
    days = set()
    for fn in PLAIN:
        rows = _entries(fn)
        if not rows:
            continue
        c = sum(len(str(x.get('content') or x.get('raw') or '')) for x in rows if isinstance(x, dict))
        tot_n += len(rows)
        tot_c += c
        for x in rows:
            if isinstance(x, dict):
                ts = str(x.get('timestamp') or '')[:10]
                if ts:
                    days.add(ts)
        label = fn.replace('warehouse_', '').replace('.json', '')
        w(f'   {label:12} {len(rows):>6} entries {c:>9} chars')
    w(f'   {"plaintext":12} {tot_n:>6} entries {tot_c:>9} chars, spanning {len(days)} distinct days')

    priv = os.path.join(HIVE_DIR, 'warehouse_隐私.json')
    if os.path.exists(priv):
        w(f'   private warehouse present: {os.path.getsize(priv)} bytes (encrypted, not counted)')

    print('\n'.join(out))


if __name__ == '__main__':
    try:
        main()
    except urllib.error.URLError as e:
        sys.exit(f'cannot reach the Hive at {API} — is it running? ({e})')
