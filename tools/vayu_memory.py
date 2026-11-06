#!/usr/bin/env python3
"""Report Vayu RAM intervals after subtracting known reservations.

The result is an offline inventory, not a boot allocation plan. Linux/TWRP
runtime reservations do not prove which ranges are safe after an ABL handoff.
"""
import argparse
import json
import sys


def number(value):
    return int(value, 0) if isinstance(value, str) else value


def cells_number(cells):
    value = 0
    for cell in cells:
        value = (value << 32) | number(cell)
    return value


def intervals(items):
    result = []
    for item in items:
        base = number(item["base"])
        size = number(item["size"])
        if base < 0 or size <= 0:
            raise ValueError("memory interval has invalid base or size")
        result.append((base, base + size))
    return result


def merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_ranges(memory, reserved):
    remaining = merge_ranges(memory)
    reservations = sorted(reserved)
    for start, end in reservations:
        if not any(mem_start <= start and end <= mem_end
                   for mem_start, mem_end in remaining):
            raise ValueError(
                f"reservation {start:#x}-{end:#x} is outside a memory range"
            )
        next_ranges = []
        for mem_start, mem_end in remaining:
            if end <= mem_start or start >= mem_end:
                next_ranges.append((mem_start, mem_end))
                continue
            if mem_start < start:
                next_ranges.append((mem_start, start))
            if end < mem_end:
                next_ranges.append((end, mem_end))
        remaining = next_ranges
    return remaining


def create_report(profile, runtime):
    dynamic_sizes = sorted(
        cells_number(item["size_cells"])
        for item in profile["dynamic_reserved_memory"]
    )
    observed_pools = runtime["cma_pools"]
    observed_sizes = sorted(number(item["size"]) for item in observed_pools)
    if dynamic_sizes != observed_sizes:
        raise ValueError(
            "runtime CMA pool sizes do not match dynamic reserved-memory sizes"
        )
    cma_total = sum(observed_sizes)
    if "cma_total_kib" in runtime and cma_total != number(runtime["cma_total_kib"]) * 1024:
        raise ValueError("runtime CMA pool sizes do not match CmaTotal")

    memory = intervals(profile["memory"])
    fixed = intervals(profile.get("fdt_reservations", [])) + [
        interval
        for item in profile["reserved_memory"]
        for interval in intervals(item["ranges"])
    ]
    dynamic = intervals(observed_pools)
    reservations = sorted(fixed + dynamic)
    for previous, current in zip(reservations, reservations[1:]):
        if current[0] < previous[1]:
            raise ValueError(
                f"known reservations overlap at {current[0]:#x}"
            )

    remaining = subtract_ranges(memory, reservations)
    ranges = [
        {"base": f"0x{start:x}", "size": f"0x{end - start:x}"}
        for start, end in remaining
    ]
    largest = max(remaining, key=lambda span: span[1] - span[0])
    return {
        "schema_version": 1,
        "status": "analysis_only_not_a_boot_allocation_plan",
        "runtime_source": runtime["source"],
        "runtime_cma_total_kib": cma_total // 1024,
        "ram_after_known_fdt_and_cma_exclusions": ranges,
        "largest_interval_after_known_exclusions": {
            "base": f"0x{largest[0]:x}",
            "size": f"0x{largest[1] - largest[0]:x}",
        },
        "limitations": [
            "Runtime reservations were observed under Linux/TWRP, not after ABL handoff.",
            "This calculation does not subtract the running Linux kernel or other runtime allocations.",
            "It does not account for shim, ramdisk, firmware ownership, or XNU requirements.",
            "Do not use these ranges as an XNU memory map without separate boot-time verification.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", help="sanitized Vayu FDT profile JSON")
    parser.add_argument("runtime", help="sanitized TWRP reservation observation JSON")
    parser.add_argument("-o", "--output", help="write JSON here instead of stdout")
    args = parser.parse_args()
    try:
        with open(args.profile, encoding="utf-8") as source:
            profile = json.load(source)
        with open(args.runtime, encoding="utf-8") as source:
            runtime = json.load(source)
        result = json.dumps(create_report(profile, runtime), indent=2, sort_keys=True) + "\n"
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as output:
            output.write(result)
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
