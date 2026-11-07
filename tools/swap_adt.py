#!/usr/bin/env python3
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt
src, new_adt, out = sys.argv[1:4]
d = bytearray(open(src, 'rb').read())
if d[:8] != b'SWEETXNU':
    sys.exit('not a SWEETXNU payload')
adt_off, adt_size = struct.unpack_from('<2Q', d, 0x20)
if adt_off + adt_size > len(d) or len(d) - (adt_off + adt_size) > 16:
    sys.exit('ADT is not the last piece of this payload; refusing')
blob = open(new_adt, 'rb').read()
root, _ = adt.parse(blob)
chosen = next(c for c in root.children if c.name == 'chosen')
base = struct.unpack('<Q', chosen.prop('dram-base'))[0]
size = struct.unpack('<Q', chosen.prop('dram-size'))[0]
old_base, old_size = struct.unpack_from('<2Q', d, 0x38)
print('phys_base  %#x -> %#x' % (old_base, base))
print('mem_size   %#x -> %#x' % (old_size, size))
d = d[:adt_off] + blob + b'\0' * (-len(blob) & 0xf)
struct.pack_into('<Q', d, 0x28, len(blob))
struct.pack_into('<2Q', d, 0x38, base, size)
open(out, 'wb').write(d)
print('wrote', out, len(d), 'bytes')
