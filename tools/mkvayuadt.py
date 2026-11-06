#!/usr/bin/env python3
"""Generate an experimental Vayu ADT from sanitized hardware observations.

This only writes an offline ADT artifact. The high-memory candidate comes from
TWRP and is not verified against ABL's handoff, so explicit opt-in is required.
"""
import argparse
import hashlib
import os
import struct
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import adt
from mkadt import build as build_sweet_tree
from vayu_memory import create_report, intervals, merge_ranges


VAYU_MEMORY_BASE = 0x100000000
VAYU_MEMORY_SIZE = 0xfcc00000
VAYU_FRAMEBUFFER_BASE = 0x9c000000
VAYU_FRAMEBUFFER_SIZE = 0x2400000
VAYU_UFS_BASE = 0x1d84000
VAYU_UFS_SIZE = 0x2500


def find_node(root, name):
    if root.name == name:
        return root
    for child in root.children:
        found = find_node(child, name)
        if found is not None:
            return found
    return None


def set_property(node, name, value):
    for index, (prop_name, flags, _) in enumerate(node.props):
        if prop_name == name:
            node.props[index] = (name, flags, value)
            return
    node.props.append((name, 0, value))


def remove_property(node, name):
    node.props = [prop for prop in node.props if prop[0] != name]


def get_observed_range(profile, node_path, range_index=0):
    for node in profile.get(node_path[0], []):
        if node.get("path") == node_path[1] and len(node.get("ranges", [])) > range_index:
            item = node["ranges"][range_index]
            return int(item["base"], 0), int(item["size"], 0)
    raise ValueError(f"hardware profile is missing {node_path[1]} range {range_index}")


def validate_hardware_profile(profile):
    device_tree = profile.get("device_tree", {})
    compatibles = device_tree.get("compatible", [])
    if device_tree.get("model") != "VAYU" or "qcom,sm8150" not in compatibles:
        raise ValueError("hardware profile does not identify a VAYU / SM8150 device")

    cpus = profile.get("cpus", [])
    if len(cpus) != 8 or any(cpu.get("enable_method") != ["psci"] for cpu in cpus):
        raise ValueError("hardware profile does not confirm eight PSCI-enabled CPUs")
    if not any(
        "arm,psci-1.0" in item.get("compatible", [])
        and item.get("method") == ["smc"]
        for item in profile.get("psci", [])
    ):
        raise ValueError("hardware profile does not confirm PSCI SMC")

    gic_base, gic_size = get_observed_range(
        profile, ("gic", "/soc/interrupt-controller@17a00000"), 0
    )
    gicr_base, gicr_size = get_observed_range(
        profile, ("gic", "/soc/interrupt-controller@17a00000"), 1
    )
    if (gic_base, gic_size, gicr_base, gicr_size) != (
        0x17a00000,
        0x10000,
        0x17a60000,
        0x100000,
    ):
        raise ValueError("Vayu GIC ranges do not match the ADT template")

    timer = profile.get("timer", [])
    if len(timer) != 1 or int(timer[0]["clock_frequency_hz"][0], 0) != 0x124f800:
        raise ValueError("Vayu timer frequency does not match the ADT template")
    timer_interrupts = [int(value, 0) for value in timer[0].get("interrupts", [])]
    if len(timer_interrupts) != 12 or timer_interrupts[6:9] != [1, 3, 0xf08]:
        raise ValueError("Vayu virtual timer PPI does not match the ADT template")

    fb_base, fb_size = get_observed_range(
        profile,
        ("reserved_memory", "/reserved-memory/cont_splash_region@9c000000"),
    )
    if (fb_base, fb_size) != (VAYU_FRAMEBUFFER_BASE, VAYU_FRAMEBUFFER_SIZE):
        raise ValueError("Vayu splash reservation does not match the ADT template")

    ufs_base, ufs_size = get_observed_range(profile, ("ufs", "/soc/ufshc@1d84000"))
    if (ufs_base, ufs_size) != (VAYU_UFS_BASE, VAYU_UFS_SIZE):
        raise ValueError("Vayu UFS register range does not match the ADT template")

    fdt_ram = merge_ranges(intervals(profile.get("memory", [])))
    if not any(
        start <= VAYU_MEMORY_BASE
        and VAYU_MEMORY_BASE + VAYU_MEMORY_SIZE <= end
        for start, end in fdt_ram
    ):
        raise ValueError("Vayu experimental memory window is outside the FDT RAM ranges")


def create_vayu_adt(profile, runtime):
    validate_hardware_profile(profile)
    memory_report = create_report(profile, runtime)
    candidate_end = VAYU_MEMORY_BASE + VAYU_MEMORY_SIZE
    candidate_is_observed = any(
        int(item["base"], 0) <= VAYU_MEMORY_BASE
        and candidate_end <= int(item["base"], 0) + int(item["size"], 0)
        for item in memory_report["ram_after_known_fdt_and_cma_exclusions"]
    )
    if not candidate_is_observed:
        raise ValueError(
            "experimental 4 GiB memory window is not contained in the "
            "observed post-reservation intervals"
        )

    seed = hashlib.sha512(b"ios-sweet-vayu-experimental-adt").digest()
    root = build_sweet_tree(seed=seed, highmem=True)
    set_property(
        root,
        "compatible",
        b"D22AP\0iPhone10,3\0xiaomi,vayu\0qcom,sm8150\0AppleARM\0",
    )

    chosen = find_node(root, "chosen")
    if chosen is None:
        raise ValueError("base ADT is missing the chosen node")
    set_property(chosen, "firmware-version", b"vayu-experimental-shim\0")
    set_property(chosen, "system-firmware-version", b"vayu-experimental-shim\0")
    set_property(
        chosen,
        "unique-chip-id",
        struct.pack("<Q", 0x5641595500000001),
    )

    cpus = find_node(root, "cpus")
    if cpus is None or len(cpus.children) != 8:
        raise ValueError("base ADT does not contain the expected eight CPU nodes")
    for cpu in cpus.children:
        set_property(cpu, "compatible", b"arm,armv8\0")
        for frequency in (
            "clock-frequency",
            "bus-frequency",
            "memory-frequency",
            "peripheral-frequency",
            "fixed-frequency",
        ):
            remove_property(cpu, frequency)

    fbcon = find_node(root, "sweet-fbcon")
    ufs = find_node(root, "sweet-ufs")
    if fbcon is None or ufs is None:
        raise ValueError("base ADT is missing the framebuffer or UFS node")
    set_property(
        fbcon,
        "reg",
        struct.pack("<QQ", VAYU_FRAMEBUFFER_BASE, VAYU_FRAMEBUFFER_SIZE),
    )
    set_property(
        ufs,
        "reg",
        struct.pack("<QQ", VAYU_UFS_BASE, VAYU_UFS_SIZE),
    )
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="path for the experimental ADT output")
    parser.add_argument(
        "--profile",
        default=os.path.join("profiles", "vayu-hardware.json"),
        help="sanitized Vayu FDT profile JSON",
    )
    parser.add_argument(
        "--runtime",
        default=os.path.join("profiles", "vayu-recovery-reservations.json"),
        help="sanitized TWRP reservation observation JSON",
    )
    parser.add_argument(
        "--experimental-highmem",
        action="store_true",
        help="explicitly permit the unverified TWRP-derived 4 GiB memory candidate",
    )
    args = parser.parse_args()
    if not args.experimental_highmem:
        parser.error(
            "refusing to generate a boot-oriented ADT without "
            "--experimental-highmem; TWRP memory is not ABL verification"
        )
    try:
        import json

        with open(args.profile, encoding="utf-8") as source:
            profile = json.load(source)
        with open(args.runtime, encoding="utf-8") as source:
            runtime = json.load(source)
        blob = adt.serialize(create_vayu_adt(profile, runtime))
        with open(args.output, "wb") as output:
            output.write(blob)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        f"{args.output}: {len(blob)} bytes (experimental only; "
        "not verified for boot)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
