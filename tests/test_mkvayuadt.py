import json
import os
import struct
import unittest

from tools import adt
from tools.mkvayuadt import (
    VAYU_FRAMEBUFFER_BASE,
    VAYU_FRAMEBUFFER_SIZE,
    VAYU_MEMORY_BASE,
    VAYU_MEMORY_SIZE,
    VAYU_UFS_BASE,
    VAYU_UFS_SIZE,
    create_vayu_adt,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VayuAdtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "profiles", "vayu-hardware.json"), encoding="utf-8") as source:
            cls.profile = json.load(source)
        with open(
            os.path.join(ROOT, "profiles", "vayu-recovery-reservations.json"),
            encoding="utf-8",
        ) as source:
            cls.runtime = json.load(source)

    def test_builds_vayu_scoped_adt_with_verified_profile_register_ranges(self):
        root = create_vayu_adt(self.profile, self.runtime)
        parsed, _ = adt.parse(adt.serialize(root))

        compatible = next(value for name, _, value in parsed.props if name == "compatible")
        self.assertIn(b"xiaomi,vayu\0", compatible)
        self.assertIn(b"qcom,sm8150\0", compatible)
        self.assertNotIn(b"xiaomi,sweet\0", compatible)

        chosen = next(child for child in parsed.children if child.name == "chosen")
        props = {name: value for name, _, value in chosen.props}
        self.assertEqual(struct.unpack("<Q", props["dram-base"])[0], VAYU_MEMORY_BASE)
        self.assertEqual(
            struct.unpack("<Q", props["dram-size"])[0],
            VAYU_MEMORY_SIZE - 0x100000,
        )
        self.assertEqual(props["firmware-version"], b"vayu-experimental-shim\0")

        cpus = next(child for child in parsed.children if child.name == "cpus")
        self.assertEqual(len(cpus.children), 8)
        for cpu in cpus.children:
            cpu_props = {name: value for name, _, value in cpu.props}
            self.assertEqual(cpu_props["compatible"], b"arm,armv8\0")
            self.assertNotIn("clock-frequency", cpu_props)
            self.assertIn("cluster-id", cpu_props)
            self.assertIn("cluster-core-id", cpu_props)
            self.assertIn("cluster-type", cpu_props)
        cpu_clusters = [
            struct.unpack("<I", next(v for n, _, v in cpu.props if n == "cluster-id"))[0]
            for cpu in cpus.children
        ]
        self.assertEqual(cpu_clusters, [0, 0, 0, 0, 1, 1, 1, 2])

        fbcon = next(child for child in parsed.children if child.name == "sweet-fbcon")
        self.assertEqual(
            struct.unpack("<QQ", next(v for n, _, v in fbcon.props if n == "reg")),
            (VAYU_FRAMEBUFFER_BASE, VAYU_FRAMEBUFFER_SIZE),
        )
        ufs = next(child for child in parsed.children if child.name == "sweet-ufs")
        self.assertEqual(
            struct.unpack("<QQ", next(v for n, _, v in ufs.props if n == "reg")),
            (VAYU_UFS_BASE, VAYU_UFS_SIZE),
        )

    def test_rejects_high_memory_window_not_contained_in_observations(self):
        runtime = json.loads(json.dumps(self.runtime))
        runtime["cma_pools"][0]["base"] = "0x100000000"
        with self.assertRaisesRegex(ValueError, "not contained"):
            create_vayu_adt(self.profile, runtime)

    def test_rejects_hardware_profile_for_another_board(self):
        profile = json.loads(json.dumps(self.profile))
        profile["device_tree"]["model"] = "SWEET"
        with self.assertRaisesRegex(ValueError, "VAYU / SM8150"):
            create_vayu_adt(profile, self.runtime)


if __name__ == "__main__":
    unittest.main()
