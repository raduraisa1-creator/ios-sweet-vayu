import unittest

from tools.vayu_memory import create_report


class VayuMemoryTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "memory": [
                {"base": "0x1000", "size": "0x5000"},
                {"base": "0x6000", "size": "0x2000"},
            ],
            "reserved_memory": [
                {
                    "ranges": [
                        {"base": "0x2000", "size": "0x1000"},
                    ],
                },
            ],
            "fdt_reservations": [
                {"base": "0x7000", "size": "0x1000"},
            ],
            "dynamic_reserved_memory": [
                {"size_cells": ["0x0", "0x1000"]},
            ],
        }
        self.runtime = {
            "source": "synthetic test fixture",
            "cma_total_kib": 4,
            "cma_pools": [
                {"base": "0x5000", "size": "0x1000"},
            ],
        }

    def test_subtracts_fixed_and_runtime_reservations_from_merged_ram(self):
        report = create_report(self.profile, self.runtime)
        self.assertEqual(report["status"], "analysis_only_not_a_boot_allocation_plan")
        self.assertEqual(
            report["ram_after_known_fdt_and_cma_exclusions"],
            [
                {"base": "0x1000", "size": "0x1000"},
                {"base": "0x3000", "size": "0x2000"},
                {"base": "0x6000", "size": "0x1000"},
            ],
        )
        self.assertEqual(
            report["largest_interval_after_known_exclusions"],
            {"base": "0x3000", "size": "0x2000"},
        )

    def test_rejects_unmatched_dynamic_pool_sizes(self):
        self.runtime["cma_pools"][0]["size"] = "0x2000"
        with self.assertRaisesRegex(ValueError, "sizes do not match"):
            create_report(self.profile, self.runtime)

    def test_rejects_cma_total_mismatch(self):
        self.runtime["cma_total_kib"] = 8
        with self.assertRaisesRegex(ValueError, "do not match CmaTotal"):
            create_report(self.profile, self.runtime)

    def test_rejects_overlapping_reservations(self):
        self.profile["reserved_memory"][0]["ranges"][0]["base"] = "0x4c00"
        with self.assertRaisesRegex(ValueError, "reservations overlap"):
            create_report(self.profile, self.runtime)

    def test_rejects_reservation_outside_reported_ram(self):
        self.runtime["cma_pools"][0]["base"] = "0x9000"
        with self.assertRaisesRegex(ValueError, "outside a memory range"):
            create_report(self.profile, self.runtime)


if __name__ == "__main__":
    unittest.main()
