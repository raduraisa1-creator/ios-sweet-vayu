import json
import struct
import unittest

from tools.vayu_dt import (
    FDT_BEGIN_NODE,
    FDT_END,
    FDT_END_NODE,
    FDT_MAGIC,
    FDT_PROP,
    create_profile,
    parse_dtb,
)


def make_dtb():
    names = bytearray()
    name_offsets = {}
    for name in (
        "#address-cells", "#size-cells", "model", "compatible", "qcom,msm-name",
        "qcom,msm-id", "device_type", "reg", "enable-method", "capacity-dmips-mhz",
        "no-map", "interrupts", "reg-names", "bootargs", "androidboot.serialno",
        "size", "alignment",
    ):
        name_offsets[name] = len(names)
        names.extend(name.encode() + b"\0")

    def cells(*values):
        return struct.pack(f">{len(values)}I", *values)

    def prop(name, value):
        return struct.pack(">III", FDT_PROP, len(value), name_offsets[name]) + value + b"\0" * (-len(value) & 3)

    def begin(name):
        raw = name.encode() + b"\0"
        return struct.pack(">I", FDT_BEGIN_NODE) + raw + b"\0" * (-len(raw) & 3)

    def end_node():
        return struct.pack(">I", FDT_END_NODE)

    root = begin("")
    root += prop("#address-cells", cells(2)) + prop("#size-cells", cells(2))
    root += prop("model", b"VAYU\0")
    root += prop("compatible", b"qcom,sm8150-cdp\0qcom,sm8150\0")
    root += prop("qcom,msm-name", b"SM8150 V2\0")
    root += prop("qcom,msm-id", cells(0x153, 0x20000))
    root += begin("chosen") + prop("bootargs", b"androidboot.serialno=fixture\0")
    root += prop("androidboot.serialno", b"fixture\0")
    root += begin("ufshc@1d84000") + prop("compatible", b"qcom,ufshc\0")
    root += prop("reg", cells(0xdead0000, 0x1000)) + end_node() + end_node()

    root += begin("cpus")
    root += prop("#address-cells", cells(2)) + prop("#size-cells", cells(0))
    root += begin("cpu@0") + prop("device_type", b"cpu\0")
    root += prop("reg", cells(0, 0)) + prop("enable-method", b"psci\0")
    root += prop("capacity-dmips-mhz", cells(0x400)) + end_node() + end_node()

    root += begin("memory") + prop("device_type", b"memory\0")
    root += prop("reg", cells(0, 0x80000000, 0, 0x10000000)) + end_node()
    root += begin("reserved-memory")
    root += prop("#address-cells", cells(2)) + prop("#size-cells", cells(2))
    root += begin("hyp_mem@80000000") + prop("reg", cells(0, 0x80000000, 0, 0x1000))
    root += prop("no-map", b"") + end_node() + end_node()
    root = root[:-4] + begin("dynamic@0") + prop("size", cells(0, 0x2000))
    root += prop("alignment", cells(0, 0x1000)) + end_node() + end_node()

    root += begin("soc") + prop("#address-cells", cells(1)) + prop("#size-cells", cells(1))
    root += begin("interrupt-controller@17a00000")
    root += prop("compatible", b"arm,gic-v3\0")
    root += prop("reg", cells(0x17a00000, 0x10000, 0x17a60000, 0x100000)) + end_node()
    root += begin("ufshc@1d84000") + prop("compatible", b"qcom,ufshc\0")
    root += prop("reg", cells(0x1d84000, 0x2500, 0x1d90000, 0x8000))
    root += prop("reg-names", b"ufs_mem\0ufs_ice\0")
    root += prop("interrupts", cells(0, 0x109, 0)) + end_node() + end_node()
    root += end_node() + struct.pack(">I", FDT_END)

    reserve_map = struct.pack(">QQQQ", 0x90000000, 0x1000, 0, 0)
    struct_off = 40 + len(reserve_map)
    strings_off = struct_off + len(root)
    total_size = strings_off + len(names)
    header = struct.pack(">10I", FDT_MAGIC, total_size, struct_off, strings_off, 40, 17, 16, 0, len(names), len(root))
    return header + reserve_map + root + names


class VayuFdtTests(unittest.TestCase):
    def test_extracts_safe_board_hardware_profile(self):
        profile = create_profile(parse_dtb(make_dtb()))
        self.assertEqual(profile["device_tree"]["model"], "VAYU")
        self.assertEqual(profile["device_tree"]["soc_name"], "SM8150 V2")
        self.assertEqual(profile["device_tree"]["msm_id"], ["0x153", "0x20000"])
        self.assertEqual(profile["cpus"][0]["enable_method"], ["psci"])
        self.assertEqual(profile["cpus"][0]["mpidr"], "0x0")
        self.assertEqual(profile["memory"][0]["base"], "0x80000000")
        self.assertTrue(profile["reserved_memory"][0]["no_map"])
        self.assertEqual(profile["fdt_reservations"][0]["base"], "0x90000000")
        self.assertEqual(profile["dynamic_reserved_memory"][0]["size_cells"], ["0x0", "0x2000"])
        self.assertEqual(profile["gic"][0]["ranges"][0]["base"], "0x17a00000")
        self.assertEqual(profile["ufs"][0]["reg_names"], ["ufs_mem", "ufs_ice"])
        report = json.dumps(profile)
        self.assertNotIn("chosen", report)
        self.assertNotIn("bootargs", report)
        self.assertNotIn("serialno", report)
        self.assertNotIn("dead0000", report)

    def test_rejects_non_vayu_tree(self):
        root = parse_dtb(make_dtb())
        root.props["compatible"] = b"qcom,sm7150\0"
        with self.assertRaisesRegex(ValueError, "not identified as a Vayu"):
            create_profile(root)

    def test_rejects_invalid_fdt_header(self):
        with self.assertRaisesRegex(ValueError, "shorter than an FDT header"):
            parse_dtb(b"not an fdt")


if __name__ == "__main__":
    unittest.main()
