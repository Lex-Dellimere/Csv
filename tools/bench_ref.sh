#!/bin/sh
# Reference speeds for the file `mbuild bench` reads (bench/data.csv by default):
# Python's csv module, and zsv if it is on PATH (https://github.com/liquidaty/zsv).
#
#   sh tools/bench_ref.sh [FILE]
FILE=${1:-bench/data.csv}
python3 - "$FILE" <<'PY'
import csv, io, os, sys, time
path = sys.argv[1]
data = open(path, newline="").read()
size = os.path.getsize(path)
best = 0
for _ in range(3):
    t = time.time()
    n = 0
    for row in csv.reader(io.StringIO(data, newline="")):
        n += len(row)
    best = max(best, size / (time.time() - t) / 1e6)
print("python csv.reader: %d MB/s (%d fields)" % (best, n))
rows = list(csv.reader(io.StringIO(data, newline="")))
out = io.StringIO()
t = time.time()
csv.writer(out).writerows(rows)
print("python csv.writer: %d MB/s" % (len(out.getvalue()) / (time.time() - t) / 1e6))
PY
if command -v zsv >/dev/null 2>&1; then
  for i in 1 2 3; do
    s=$(date +%s.%N)
    zsv count "$FILE" >/dev/null
    e=$(date +%s.%N)
    python3 -c "import os; print('zsv count: %d MB/s' % (os.path.getsize('$FILE') / ($e - $s) / 1e6))"
  done
fi
