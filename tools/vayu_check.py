#!/usr/bin/env python3
"""Cross-check tools/mkadt.py --vayu against the phone's live FDT (pure Python, no dtc).

usage: python3 tools/vayu_check.py path/to/fdt.dtb
Run it after every edit to the vayu constants in mkadt.py. Exit code 1 on any FAIL.
"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkadt

class N:
    def __init__(s, name, parent=None): s.name, s.props, s.kids, s.parent = name, {}, [], parent
    def path(s): return '/' if s.parent is None else s.parent.path().rstrip('/') + '/' + s.name

def load(path):
    d = open(path, 'rb').read()
    magic, _, off_s, off_str = struct.unpack('>4I', d[:16])
    assert magic == 0xd00dfeed, 'not a dtb'
    cs = lambda o: d[o:d.index(b'\0', o)].decode('utf8', 'replace')
    top = N(''); cur = top; p = off_s
    while True:
        t, = struct.unpack('>I', d[p:p+4]); p += 4
        if t == 1:
            nm = cs(p); p = (p + len(nm) + 1 + 3) & ~3
            n = N(nm, cur); cur.kids.append(n); cur = n
        elif t == 2: cur = cur.parent
        elif t == 3:
            ln, no = struct.unpack('>II', d[p:p+8]); p += 8
            cur.props[cs(off_str + no)] = d[p:p+ln]; p = (p + ln + 3) & ~3
        elif t == 9: break
    root = top.kids[0]; root.parent = None
    return root

def walk(n):
    yield n
    for k in n.kids: yield from walk(k)
cells = lambda v: list(struct.unpack('>%dI' % (len(v)//4), v))
def u64s(v):
    c = cells(v); return [(c[i] << 32) | c[i+1] for i in range(0, len(c), 2)]

fails = 0
def check(ok, msg):
    global fails
    print(('PASS  ' if ok else 'FAIL  ') + msg); fails += (not ok)

root = load(sys.argv[1])
by = lambda pred: [n for n in walk(root) if pred(n)]

# ---- build the ADT and pull the values back out
adtroot = mkadt.build(seed=bytes(64), vayu=True)
P = lambda n: dict((k, v) for k, f, v in n.props)
kid = lambda n, name: [c for c in n.children if P(c)['name'] == name.encode() + b'\0'][0]
ch = kid(adtroot, 'chosen')
base, size = struct.unpack('<Q', P(ch)['dram-base'])[0], struct.unpack('<Q', P(ch)['dram-size'])[0]
plog = struct.unpack('<I', P(ch)['embedded-panic-log-size'])[0]
lo, hi = base, base + size + plog

# ---- 1/2: RAM inside memory banks, clear of fixed carve-outs
mem = by(lambda n: n.name == 'memory' and 'device_type' in n.props)[0]
banks = [(u64s(mem.props['reg'])[i], u64s(mem.props['reg'])[i] + u64s(mem.props['reg'])[i+1])
         for i in range(0, len(u64s(mem.props['reg'])), 2)]
inside = any(b0 <= lo and hi <= b1 for b0, b1 in banks) or (
    # banks may be adjacent: merge and retest
    any(lo >= m0 and hi <= m1 for m0, m1 in
        [(min(b[0] for b in banks if b[0] >= 0xc0000000), max(b[1] for b in banks))]))
check(inside, 'kernel RAM %#x-%#x lies inside the FDT memory banks' % (lo, hi))
rm = by(lambda n: n.name == 'reserved-memory')[0]
hit = []
for k in rm.kids:
    if 'reg' in k.props:
        r = u64s(k.props['reg']); a, b = r[0], r[0] + r[1]
        if a < hi and lo < b: hit.append(k.name)
check(not hit, 'no fixed reserved-memory region overlaps kernel RAM' + (' (HIT: %s)' % hit if hit else ''))

# ---- 3: GIC
gic = by(lambda n: n.props.get('compatible', b'').startswith(b'arm,gic-v3'))[0]
g = u64s(gic.props['reg'])  # root #address-cells = #size-cells = 2? gic reg is 64-bit pairs in this DT
gc = cells(gic.props['reg']); gicd, gicr = gc[0], gc[2]
soc = mkadt.SOC_BASE
check(mkadt.GICD == gicd and mkadt.GICR == gicr, 'GICD/GICR %#x/%#x match FDT %#x/%#x' % (mkadt.GICD, mkadt.GICR, gicd, gicr))

# ---- 4: timer
tm = by(lambda n: n.props.get('compatible', b'').startswith(b'arm,armv8-timer'))[0]
tc = cells(tm.props['interrupts']); virt_ppi = tc[7]   # 3rd entry (sec-phys, phys, virt, hyp): <type num flags>
freq = cells(tm.props['clock-frequency'])[0]
check(mkadt.TIMEBASE == freq, 'timebase %d Hz matches FDT %d Hz' % (mkadt.TIMEBASE, freq))
check(mkadt.VTIMER_INTID == 16 + virt_ppi, 'virtual-timer INTID %d == 16 + FDT virt PPI index %d' % (mkadt.VTIMER_INTID, virt_ppi))

# ---- 5: UFS
ufs = by(lambda n: n.props.get('compatible', b'').startswith(b'qcom,ufshc'))[0]
check(mkadt.UFS == cells(ufs.props['reg'])[0], 'UFS host %#x matches FDT %#x' % (mkadt.UFS, cells(ufs.props['reg'])[0]))

# ---- 6: CPUs
fdt_cpus = [cells(n.props['reg'])[-1] for n in by(lambda n: n.name.startswith('cpu@'))]
adt_cpus = [struct.unpack('<I', P(c)['reg'])[0] for c in kid(adtroot, 'cpus').children]
check(sorted(fdt_cpus) == sorted(adt_cpus), 'CPU MPIDRs ADT %s == FDT %s' % ([hex(x) for x in adt_cpus], [hex(x) for x in sorted(fdt_cpus)]))

# ---- 7: framebuffer inside cont_splash_region
cs = [k for k in rm.kids if k.name.startswith('cont_splash')][0]
c0, c1 = u64s(cs.props['reg'])[0], u64s(cs.props['reg'])[0] + u64s(cs.props['reg'])[1]
check(c0 <= mkadt.FB and mkadt.FB + mkadt.FB_SIZE <= c1, 'framebuffer %#x+%#x inside cont_splash_region %#x-%#x' % (mkadt.FB, mkadt.FB_SIZE, c0, c1))
check(mkadt.FB_W * 4 * mkadt.FB_H <= mkadt.FB_SIZE, 'framebuffer %dx%d x4 bytes fits FB_SIZE' % (mkadt.FB_W, mkadt.FB_H))

print('\n%s' % ('ALL CHECKS PASSED' if not fails else '%d CHECK(S) FAILED' % fails))
sys.exit(1 if fails else 0)
