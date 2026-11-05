#!/usr/bin/env python3
"""Writes the conformance data in tests/data.

    python3 tools/gen_tests.py RUST_CSV_DIR CSV_SPECTRUM_DIR

RUST_CSV_DIR is a clone of https://github.com/BurntSushi/rust-csv and
CSV_SPECTRUM_DIR one of https://github.com/max-mapper/csv-spectrum.

Files written (every byte string is hex, so any input survives):

  csv_core.txt    the parses_to! cases from csv-core's reader tests
  spectrum.txt    the csv-spectrum files with their expected rows
  python_read.txt random inputs and dialects, parsed by Python's csv.reader
  python_write.txt random rows written by Python's csv.writer
  floats.txt      float64 values with Python's repr and float() answers

Case format:
  case NAME
  set KEY VALUE        dialect settings (bytes as numbers)
  in HEX
  row =HEX =HEX ...    one line per expected record (Python write: fields)
  out HEX              writer cases: Python's output
  end
"""
import csv
import io
import os
import random
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "tests", "data")


def hx(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return b.hex()


def write_case(out, name, sets, data, rows):
    out.write("case %s\n" % name)
    for k, v in sets:
        out.write("set %s %d\n" % (k, v))
    out.write("in %s\n" % hx(data))
    for r in rows:
        out.write("row" + "".join(" =" + hx(f) for f in r) + "\n")
    out.write("end\n")


# ---- csv-core -------------------------------------------------------------

ESC = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "'": "'", "0": "\0"}


def rust_string(s, i):
    """Parses a Rust string literal at s[i:]; returns (value, next index)."""
    if s.startswith("r#\"", i):
        j = s.index("\"#", i + 3)
        return s[i + 3:j], j + 2
    if s.startswith("r\"", i):
        j = s.index("\"", i + 2)
        return s[i + 2:j], j + 1
    assert s[i] == '"', s[i:i + 20]
    i += 1
    out = []
    while s[i] != '"':
        c = s[i]
        if c == "\\":
            n = s[i + 1]
            if n == "u":
                j = s.index("}", i)
                out.append(chr(int(s[i + 3:j], 16)))
                i = j + 1
                continue
            if n == "x":
                out.append(chr(int(s[i + 2:i + 4], 16)))
                i += 4
                continue
            out.append(ESC[n])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), i + 1


def rust_byte(tok):
    m = re.match(r"b'(\\?.)'", tok)
    v = m.group(1)
    if v.startswith("\\"):
        v = ESC[v[1]]
    return ord(v)


def csv_core(rust_dir, out):
    src = open(os.path.join(rust_dir, "csv-core", "src", "reader.rs")).read()
    n = 0
    for m in re.finditer(r"parses_to!\(\s*(\w+),\s*", src):
        name = m.group(1)
        if name == "$name":
            continue
        i = m.end()
        data, i = rust_string(src, i)
        i = src.index("csv![", i) + 5
        rows = []
        depth = 0
        cur = None
        while True:
            c = src[i]
            if c == "[":
                cur = []
                i += 1
            elif c == "]":
                if cur is None:
                    i += 1
                    break
                rows.append(cur)
                cur = None
                i += 1
            elif c in "\"r" and cur is not None:
                v, i = rust_string(src, i)
                cur.append(v)
            else:
                i += 1
        nxt = [k for k in (src.find("parses_to!", i), src.find("macro_rules!", i), len(src)) if k >= 0]
        cfg = src[i:min(nxt)]
        sets = [("header", 0), ("flexible", 1), ("blank", 0), ("bom", 0)]
        for cm in re.finditer(r"\.(\w+)\(([^;]*?)\)(?=[.;]|\s*$)", cfg):
            meth, arg = cm.group(1), cm.group(2)
            if meth == "quote":
                sets.append(("quote", rust_byte(arg)))
            elif meth == "delimiter":
                sets.append(("delimiter", rust_byte(arg)))
            elif meth == "escape":
                sets.append(("escape", rust_byte(arg[5:])))
            elif meth == "double_quote":
                sets.append(("double_quote", 1 if arg == "true" else 0))
            elif meth == "quoting":
                if arg == "false":
                    sets.append(("quote", 0))
            elif meth == "comment":
                sets.append(("comment", rust_byte(arg[5:])))
            elif meth == "terminator":
                sets.append(("terminator", rust_byte(arg[len("Terminator::Any("):])))
            elif meth == "ascii":
                sets.append(("delimiter", 0x1F))
                sets.append(("terminator", 0x1E))
            else:
                raise SystemExit("unknown csv-core setting " + meth)
        write_case(out, name, sets, data, rows)
        n += 1
    return n


# ---- csv-spectrum -----------------------------------------------------------

def spectrum(sdir, out):
    import json
    n = 0
    for fn in sorted(os.listdir(os.path.join(sdir, "csvs"))):
        base = fn[:-4]
        data = open(os.path.join(sdir, "csvs", fn), "rb").read()
        objs = json.load(open(os.path.join(sdir, "json", base + ".json"), encoding="utf-8"))
        if isinstance(objs, dict):
            objs = [objs]
        if base == "location_coordinates":
            # Upstream's JSON has a different phone number than its CSV
            # (2095257564); the CSV holds no other surprise, so use its value.
            objs[0]["Contact Phone Number"] = "2095257564"
        rows = []
        if objs:
            keys = list(objs[0].keys())
            rows.append(keys)
            for o in objs:
                rows.append([o[k] for k in keys])
        write_case(out, base, [("header", 0), ("flexible", 0)], data, rows)
        n += 1
    return n


# ---- Python reader ----------------------------------------------------------

def python_read(out, rng, count):
    n = 0
    while n < count:
        delim = rng.choice([",", ";", "\t", "|"])
        quote = rng.choice(['"', "'"])
        esc = rng.choice([None, None, "\\"])
        dq = rng.random() < 0.8
        sis = rng.random() < 0.3
        qnone = rng.random() < 0.1
        alphabet = [delim] * 4 + [quote] * 3 + ["\r", "\n", " ", "a", "b", "c", "#", "é"] + ["x"] * 4
        if esc:
            alphabet += [esc] * 2
        size = rng.choice([0, 1, 2, 3, 5, 8, 13, 21, 40, 80])
        data = "".join(rng.choice(alphabet) for _ in range(size))
        if esc and data.endswith(esc):
            continue
        kw = dict(delimiter=delim, quotechar=quote, escapechar=esc, doublequote=dq,
                  skipinitialspace=sis, strict=False)
        if qnone:
            kw["quoting"] = csv.QUOTE_NONE
        try:
            rows = list(csv.reader(io.StringIO(data, newline=""), **kw))
        except csv.Error:
            continue
        sets = [("header", 0), ("flexible", 1), ("blank", 1), ("bom", 1),
                ("delimiter", ord(delim)), ("quote", 0 if qnone else ord(quote)),
                ("escape", ord(esc) if esc else 0), ("double_quote", 1 if dq else 0),
                ("skip_space", 1 if sis else 0)]
        write_case(out, "py%d" % n, sets, data, rows)
        n += 1
    return n


# ---- Python writer ----------------------------------------------------------

def python_write(out, rng, count):
    n = 0
    styles = [(csv.QUOTE_MINIMAL, 0), (csv.QUOTE_ALL, 1), (csv.QUOTE_NONNUMERIC, 2)]
    while n < count:
        delim = rng.choice([",", ";", "\t", "|"])
        quote = rng.choice(['"', "'"])
        style, sid = rng.choice(styles)
        eol = rng.choice(["\r\n", "\n"])
        alphabet = [delim, quote, "\r", "\n", " ", "a", "b", "#", "é", "x", "y"]
        rows = []
        for _ in range(rng.randint(1, 4)):
            rows.append(["".join(rng.choice(alphabet) for _ in range(rng.choice([0, 1, 2, 4, 9])))
                         for _ in range(rng.randint(1, 4))])
        buf = io.StringIO(newline="")
        w = csv.writer(buf, delimiter=delim, quotechar=quote, quoting=style, lineterminator=eol)
        for r in rows:
            w.writerow(r)
        sets = [("delimiter", ord(delim)), ("quote", ord(quote)), ("style", sid),
                ("eol", 0 if eol == "\r\n" else 1)]
        out.write("case pyw%d\n" % n)
        for k, v in sets:
            out.write("set %s %d\n" % (k, v))
        for r in rows:
            out.write("row" + "".join(" =" + hx(f) for f in r) + "\n")
        out.write("out %s\n" % hx(buf.getvalue()))
        out.write("end\n")
        n += 1
    return n


# ---- floats -----------------------------------------------------------------

def floats(out, rng, count):
    """Lines: 'f BITS REPR' (format and parse back) and 'p TEXT BITS'."""
    n = 0
    special = [0.0, -0.0, 1.0, 0.1, 1e16, 1e-5, 5e-324, 2.2250738585072014e-308,
               1.7976931348623157e308, 9007199254740993.0, 123456.789, 1e22, 1e23]
    vals = list(special)
    while len(vals) < count:
        k = rng.random()
        if k < 0.5:
            bits = rng.getrandbits(64)
            v = struct.unpack("<d", struct.pack("<Q", bits))[0]
            if v != v or v in (float("inf"), float("-inf")):
                continue
        elif k < 0.75:
            v = rng.uniform(-1e6, 1e6)
        else:
            v = float("%.*g" % (rng.randint(1, 17), rng.uniform(0, 1) * 10 ** rng.randint(-30, 30)))
        vals.append(v)
    for v in vals:
        bits = struct.unpack("<Q", struct.pack("<d", v))[0]
        out.write("f %d %s\n" % (bits, repr(v)))
        n += 1
    # Decimal strings near halfway points and with many digits.
    for _ in range(count):
        digits = "".join(rng.choice("0123456789") for _ in range(rng.choice([1, 5, 16, 17, 18, 19, 25, 40, 120, 800] if rng.random() < 0.1 else [1, 5, 16, 17, 18, 19, 25])))
        frac = rng.randint(0, len(digits))
        text = digits[:frac] + "." + digits[frac:] if frac < len(digits) else digits
        text += "e%d" % rng.randint(-340, 320)
        if rng.random() < 0.3:
            text = "-" + text
        v = float(text)
        bits = struct.unpack("<Q", struct.pack("<d", v))[0]
        out.write("p %s %d\n" % (text, bits))
        n += 1
    return n


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    os.makedirs(DATA, exist_ok=True)
    rng = random.Random(4180)
    with open(os.path.join(DATA, "csv_core.txt"), "w") as f:
        print("csv_core", csv_core(sys.argv[1], f))
    with open(os.path.join(DATA, "spectrum.txt"), "w") as f:
        print("spectrum", spectrum(sys.argv[2], f))
    with open(os.path.join(DATA, "python_read.txt"), "w") as f:
        print("python_read", python_read(f, rng, 10000))
    with open(os.path.join(DATA, "python_write.txt"), "w") as f:
        print("python_write", python_write(f, rng, 3000))
    with open(os.path.join(DATA, "floats.txt"), "w") as f:
        print("floats", floats(f, rng, 5000))


if __name__ == "__main__":
    main()
