#!/usr/bin/env python3
"""Extract a sanitized hardware profile from a Vayu live Flattened Device Tree.

The report intentionally omits /chosen, bootargs, serials, random seeds, and
other per-device properties. The raw DT should remain local and ignored.
"""
import argparse
import json
import struct
import sys


FDT_MAGIC = 0xd00dfeed
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class FdtNode:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.props = {}
        self.children = []
        self.reservations = []

    @property
    def path(self):
        if self.parent is None:
            return "/"
        prefix = self.parent.path.rstrip("/")
        return f"{prefix}/{self.name}"

    def strings(self, name):
        value = self.props.get(name)
        if value is None:
            return []
        return [part.decode("ascii") for part in value.rstrip(b"\0").split(b"\0")]

    def cells(self, name):
        value = self.props.get(name)
        if value is None:
            return []
        if len(value) % 4:
            raise ValueError(f"{self.path}: property {name!r} is not a cell array")
        return list(struct.unpack(f">{len(value) // 4}I", value))

    def cell(self, name, default):
        values = self.cells(name)
        if len(values) > 1:
            raise ValueError(f"{self.path}: property {name!r} has multiple cells")
        return values[0] if values else default


def parse_dtb(data):
    """Parse the FDT structure block into nodes and raw properties."""
    if len(data) < 40:
        raise ValueError("input is shorter than an FDT header")
    (magic, total_size, struct_off, strings_off, reserve_off, version,
     _last_compatible, _boot_cpuid, strings_size, struct_size) = struct.unpack_from(
        ">10I", data
    )
    if magic != FDT_MAGIC:
        raise ValueError("input does not have FDT magic")
    if version < 16:
        raise ValueError(f"unsupported FDT version {version}")
    if total_size > len(data) or total_size < 40:
        raise ValueError("FDT totalsize is outside the input buffer")
    if struct_off + struct_size > total_size or strings_off + strings_size > total_size:
        raise ValueError("FDT structure or strings block is outside totalsize")

    strings = data[strings_off:strings_off + strings_size]
    block = data[struct_off:struct_off + struct_size]
    reservations = []
    reserve_pos = reserve_off
    while reserve_pos + 16 <= total_size:
        address, size = struct.unpack_from(">QQ", data, reserve_pos)
        reserve_pos += 16
        if address == 0 and size == 0:
            break
        reservations.append({"base": f"0x{address:x}", "size": f"0x{size:x}"})
    else:
        raise ValueError("FDT reserve map has no terminator within totalsize")

    offset = 0
    stack = []
    root = None

    def read_string(buf, start):
        end = buf.find(b"\0", start)
        if end < 0:
            raise ValueError("unterminated FDT string")
        return buf[start:end], end + 1

    while offset + 4 <= len(block):
        token, = struct.unpack_from(">I", block, offset)
        offset += 4
        if token == FDT_BEGIN_NODE:
            name, offset = read_string(block, offset)
            offset = (offset + 3) & ~3
            parent = stack[-1] if stack else None
            node = FdtNode(name.decode("ascii"), parent)
            if parent:
                parent.children.append(node)
            elif root is None:
                root = node
            else:
                raise ValueError("multiple FDT root nodes")
            stack.append(node)
        elif token == FDT_END_NODE:
            if not stack:
                raise ValueError("unbalanced FDT end-node token")
            stack.pop()
        elif token == FDT_PROP:
            if not stack or offset + 8 > len(block):
                raise ValueError("FDT property token has no node or header")
            size, name_off = struct.unpack_from(">II", block, offset)
            offset += 8
            if name_off >= len(strings):
                raise ValueError("FDT property name offset is outside strings block")
            prop_name, _ = read_string(strings, name_off)
            if offset + size > len(block):
                raise ValueError("FDT property value is outside structure block")
            if prop_name.decode("ascii") in stack[-1].props:
                raise ValueError(f"{stack[-1].path}: duplicate property {prop_name!r}")
            stack[-1].props[prop_name.decode("ascii")] = block[offset:offset + size]
            offset = (offset + size + 3) & ~3
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            if stack or root is None:
                raise ValueError("FDT ended before the tree was balanced")
            root.reservations = reservations
            return root
        else:
            raise ValueError(f"unknown FDT structure token {token:#x}")
    raise ValueError("FDT structure block has no end token")


def all_nodes(root):
    yield root
    for child in root.children:
        yield from all_nodes(child)


def decode_reg(node):
    parent = node.parent
    if parent is None:
        return []
    address_cells = parent.cell("#address-cells", 2)
    size_cells = parent.cell("#size-cells", 1)
    width = address_cells + size_cells
    values = node.cells("reg")
    if not values:
        return []
    if width == 0 or len(values) % width:
        raise ValueError(f"{node.path}: reg does not match parent cell widths")

    def number(cells):
        value = 0
        for cell in cells:
            value = (value << 32) | cell
        return value

    ranges = []
    for i in range(0, len(values), width):
        address = number(values[i:i + address_cells])
        size = number(values[i + address_cells:i + width]) if size_cells else None
        item = {"base": f"0x{address:x}"}
        if size is not None:
            item["size"] = f"0x{size:x}"
        ranges.append(item)
    return ranges


def create_profile(root):
    compatible = root.strings("compatible")
    msm_id = root.cells("qcom,msm-id")
    profile = {
        "schema_version": 1,
        "device_tree": {
            "model": root.strings("model")[0] if root.strings("model") else None,
            "compatible": compatible,
            "soc_name": root.strings("qcom,msm-name")[0] if root.strings("qcom,msm-name") else None,
            "msm_id": [f"0x{value:x}" for value in msm_id],
        },
        "cpus": [],
        "memory": [],
        "reserved_memory": [],
        "dynamic_reserved_memory": [],
        "fdt_reservations": root.reservations,
        "gic": [],
        "timer": [],
        "ufs": [],
    }

    board_names = root.strings("model") + root.strings("qcom,product-name")
    if "qcom,sm8150" not in compatible or not any("vayu" in item.lower() for item in board_names):
        raise ValueError("FDT is not identified as a Vayu / Qualcomm SM8150 tree")

    for node in all_nodes(root):
        if node.path == "/chosen" or node.path.startswith("/chosen/"):
            continue
        node_compatible = node.strings("compatible")
        device_type = node.strings("device_type")
        if node.parent and node.parent.name == "cpus" and device_type == ["cpu"]:
            mpidr = decode_reg(node)
            if len(mpidr) != 1:
                raise ValueError(f"{node.path}: expected exactly one CPU MPIDR in reg")
            profile["cpus"].append({
                "path": node.path,
                "mpidr": mpidr[0]["base"],
                "enable_method": node.strings("enable-method"),
                "capacity_dmips_mhz": [f"0x{x:x}" for x in node.cells("capacity-dmips-mhz")],
            })
        if device_type == ["memory"]:
            profile["memory"].extend({"path": node.path, **rng} for rng in decode_reg(node))
        if node.parent and node.parent.name == "reserved-memory":
            if node.props.get("reg") is not None:
                profile["reserved_memory"].append({
                    "path": node.path,
                    "ranges": decode_reg(node),
                    "no_map": "no-map" in node.props,
                })
            elif node.props.get("size") is not None:
                profile["dynamic_reserved_memory"].append({
                    "path": node.path,
                    "size_cells": [f"0x{x:x}" for x in node.cells("size")],
                    "alignment_cells": [f"0x{x:x}" for x in node.cells("alignment")],
                    "no_map": "no-map" in node.props,
                })
        if "arm,gic-v3" in node_compatible:
            profile["gic"].append({"path": node.path, "ranges": decode_reg(node)})
        if any("armv8-timer" in item for item in node_compatible):
            profile["timer"].append({
                "path": node.path,
                "compatible": node_compatible,
                "interrupts": [f"0x{x:x}" for x in node.cells("interrupts")],
            })
        if "qcom,ufshc" in node_compatible:
            profile["ufs"].append({
                "path": node.path,
                "ranges": decode_reg(node),
                "reg_names": node.strings("reg-names"),
                "interrupts": [f"0x{x:x}" for x in node.cells("interrupts")],
            })

    if not profile["cpus"] or not profile["memory"] or not profile["gic"] or not profile["ufs"]:
        raise ValueError("FDT is missing one or more required CPU, memory, GIC, or UFS nodes")
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fdt", help="local live FDT blob (keep device dumps out of git)")
    parser.add_argument("-o", "--output", help="write sanitized JSON here instead of stdout")
    args = parser.parse_args()
    try:
        with open(args.fdt, "rb") as fdt_file:
            profile = create_profile(parse_dtb(fdt_file.read()))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    result = json.dumps(profile, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as output:
            output.write(result)
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
