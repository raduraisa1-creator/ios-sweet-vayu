#!/usr/bin/env python3
import sys
OFF, LEN = 0x48, 608
d = bytearray(open(sys.argv[1], 'rb').read())
if d[:8] != b'SWEETXNU':
    sys.exit('not a SWEETXNU payload')
cur = bytes(d[OFF:OFF+LEN]).split(b'\0')[0].decode()
print('current:', cur)
if len(sys.argv) >= 4:
    new = sys.argv[3].encode()
    if len(new) >= LEN:
        sys.exit('too long (max %d)' % (LEN - 1))
    d[OFF:OFF+LEN] = new + b'\0' * (LEN - len(new))
    open(sys.argv[2], 'wb').write(d)
    print('new:    ', sys.argv[3])
