#!/usr/bin/env python3
"""Replace blacktop's KDK download (public github.com link, which is returning 504)
with `gh release download` (API route), keeping a retrying curl as fallback."""
import re, sys, textwrap
p = sys.argv[1]
s = open(p).read()
pat = re.compile(r'^([ \t]*)curl --progress-bar --max-time 900 --connect-timeout 60 -L -o /tmp/KDK\.dmg "\$\{KDK_URL\}"[ \t]*$', re.M)
hits = pat.findall(s)
if len(hits) != 1:
    sys.exit("patch-kdk: expected exactly one KDK curl line in %s, found %d" % (p, len(hits)))
m = pat.search(s)
block = r"""KDK_TAG=$(printf '%s' "${KDK_URL}" | sed -E 's|.*/releases/download/([^/]+)/.*|\1|')
KDK_FILE=$(basename "${KDK_URL}")
rm -f /tmp/KDK.dmg
gh release download "${KDK_TAG}" -R dortania/KdkSupportPkg -p "${KDK_FILE}" -O /tmp/KDK.dmg --clobber \
  || curl --fail --retry 8 --retry-all-errors --retry-delay 20 --progress-bar --connect-timeout 60 -L -o /tmp/KDK.dmg "${KDK_URL}" \
  || { echo "KDK download failed"; exit 1; }
[ "$(stat -f%z /tmp/KDK.dmg)" -gt 100000000 ] || { echo "KDK download is not a disk image"; exit 1; }"""
new = textwrap.indent(block, m.group(1))
s = s[:m.start()] + new + s[m.end():]
open(p, "w").write(s)
print("patch-kdk: KDK download now uses gh release download")
