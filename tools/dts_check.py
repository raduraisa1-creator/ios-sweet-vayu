#!/usr/bin/env python3
"""Cross-check the constants in tools/mkadt.py against the live vayu FDT dump.

Usage (from repo root):
    python3 tools/dts_check.py [dtb/live/vayu-live.dts]
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NODE_OPEN = re.compile(r'^\s*(?:[\w.]+:\s*)?([\w,.+\-@/]+)\s*\{\s*$')
NODE_CLOSE = re.compile(r'^\s*\};\s*$')
HEX = re.compile(r'0x[0-9a-fA-F]+')


def load_mkadt():
    spec = importlib.util.spec_from_file_location('mkadt', os.path.join(HERE, 'mkadt.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_dts(path):
    nodes, stack = [], []
    pending, buf = None, ''
    for line in open(path, errors='replace'):
        if pending is not None:
            buf += ' ' + line.strip()
            if ';' in line:
                nodes.append((tuple(stack), [int(x, 16) for x in HEX.findall(buf)]))
                pending = None
            continue
        m = NODE_OPEN.match(line)
        if m:
            stack.append(m.group(1))
            continue
        if NODE_CLOSE.match(line):
            if stack:
                stack.pop()
            continue
        s = line.strip()
        if s.startswith('reg = <') or s.startswith('reg ='):
            if ';' in s:
                nodes.append((tuple(stack), [int(x, 16) for x in HEX.findall(s)]))
            else:
                pending, buf = True, s
    return nodes


def regions_2_2(cells):
    out = []
    for i in range(0, len(cells) - 3, 4):
        out.append(((cells[i] << 32) | cells[i + 1], (cells[i + 2] << 32) | cells[i + 3]))
    return out


def overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def main():
    dts = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') \
        else os.path.join(HERE, '..', 'dtb/live/vayu-live.dts')
    mk = load_mkadt()
    nodes = parse_dts(dts)
    fails = 0

    banks = []
    reserved = []
    unit_addrs = {}
    for path, cells in nodes:
        leaf = path[-1] if path else ''
        if '@' in leaf:
            try:
                unit_addrs.setdefault(int(leaf.split('@')[1], 16), []).append(leaf)
            except ValueError:
                pass
        if (leaf == 'memory' or leaf.startswith('memory@')) and len(path) == 2:
            banks += regions_2_2(cells)
        elif 'reserved-memory' in path[:-1]:
            for base, size in regions_2_2(cells):
                reserved.append((leaf, base, size))

    banks.sort()
    merged = []
    for b, sz in banks:
        if merged and b <= merged[-1][0] + merged[-1][1]:
            end = max(merged[-1][0] + merged[-1][1], b + sz)
            merged[-1] = (merged[-1][0], end - merged[-1][0])
        else:
            merged.append((b, sz))
    banks = merged
    for b, sz in banks:
        print(f'  memory {b:#x}-{b + sz:#x}')

    print(f'parsed {len(nodes)} reg nodes, {len(banks)} memory banks, '
          f'{len(reserved)} reserved regions from {dts}')

    k0, k1 = mk.PHYS_BASE, mk.PHYS_BASE + mk.MEM_SIZE
    inside = any(b <= k0 and k1 <= b + s for b, s in banks)
    print(f'[{"ok" if inside else "FAIL"}] kernel RAM {k0:#x}-{k1:#x} inside a memory bank')
    if not inside:
        fails += 1
    for name, b, s in sorted(reserved, key=lambda r: r[1]):
        if overlap(k0, k1, b, b + s):
            tag = 'INFO' if name.startswith('ramoops') else 'FAIL'
            if tag == 'FAIL':
                fails += 1
            print(f'[{tag}] kernel RAM overlaps reserved "{name}" {b:#x}-{b + s:#x}')

    splash = [r for r in reserved if r[0].startswith('cont_splash')]
    if not splash:
        print('[FAIL] no cont_splash_region found in DTS')
        fails += 1
    else:
        _, sb, ss = splash[0]
        ok = mk.FB == sb and mk.FB_SIZE <= ss
        print(f'[{"ok" if ok else "FAIL"}] FB {mk.FB:#x}+{mk.FB_SIZE:#x} vs splash {sb:#x}+{ss:#x}')
        fails += not ok
        need = mk.FB_W * mk.FB_H * 4
        print(f'[{"ok" if need <= mk.FB_SIZE else "FAIL"}] frame {need:#x} bytes fits FB_SIZE; '
              f'stride {mk.FB_STRIDE} (check against ABL if the image looks sheared)')
        fails += need > mk.FB_SIZE

    for label, addr in (('GICD', mk.GICD), ('GICR', mk.GICR), ('UFS', mk.UFS)):
        names = unit_addrs.get(addr)
        if names:
            print(f'[ok] {label} {addr:#x} found as {names[0]}')
        else:
            kind = 'INFO' if label == 'GICR' else 'FAIL'
            fails += kind == 'FAIL'
            print(f'[{kind}] {label} {addr:#x} is not a node unit address in the DTS')
    gic_regs = [c for p, c in nodes if p and p[-1].startswith('interrupt-controller@')]
    if gic_regs:
        flat = {x for c in gic_regs for x in c}
        print(f'[{"ok" if mk.GICR in flat else "FAIL"}] GICR {mk.GICR:#x} appears in a GIC node reg')
        fails += mk.GICR not in flat

    h0, h1 = mk.HIGH_PHYS_BASE, mk.HIGH_PHYS_BASE + mk.HIGH_MEM_SIZE
    ok = any(b <= h0 and h1 <= b + s for b, s in banks)
    print(f'[{"ok" if ok else "FAIL"}] --highmem window {h0:#x}-{h1:#x} inside a memory bank')
    fails += not ok

    print('RESULT:', 'all hard checks passed' if not fails else f'{fails} hard check(s) failed')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
