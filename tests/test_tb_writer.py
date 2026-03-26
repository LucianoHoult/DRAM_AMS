import json
from pathlib import Path

import pytest

from stimulus_engine.tb_writer import TBWriter


def test_tb_writer_generates_expected_top_level_testbench(tmp_path):
    out_path = tmp_path / "sim" / "top_tb.sp"
    config = {
        "testbench_builder": {
            "output_tb_path": str(out_path),
            "includes": {
                "tech_lib": "models/tt.lib",
                "netlist": "output/netlist.cdl",
                "power_supplies": "output/power_supplies.inc",
                "stimulus": "output/stimulus.sp",
                "init_cond": "output/init.ic",
            },
            "global_options": ["post", "probe"],
            "temperature_c": -40,
            "top_instance": {
                "name": "X_BANK",
                "ref_model": "DRAM_BANK",
                "ports": ["WL<0>", "BL<0>", "VDD", "VSS"],
            },
        }
    }

    TBWriter(config).generate()
    text = out_path.read_text(encoding="utf-8")

    assert ".option post probe" in text
    assert ".temp -40" in text
    assert ".include 'models/tt.lib'" in text
    assert ".include 'output/netlist.cdl'" in text
    assert ".include 'output/power_supplies.inc'" in text
    assert "X_BANK WL<0> BL<0> VDD VSS DRAM_BANK" in text
    assert text.rstrip().endswith(".end")


def test_tb_writer_raises_when_top_instance_ports_mismatch_subckt_ports(tmp_path):
    netlist = tmp_path / "mock_netlist.cdl"
    netlist.write_text(
        ".SUBCKT DRAM_BANK A B C\n"
        ".ENDS DRAM_BANK\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "sim" / "top_tb.sp"
    config = {
        "testbench_builder": {
            "output_tb_path": str(out_path),
            "includes": {"netlist": str(netlist)},
            "top_instance": {
                "name": "X_BANK",
                "ref_model": "DRAM_BANK",
                "ports": ["A", "B"],
            },
        }
    }

    with pytest.raises(ValueError, match="Top instance ports do not match"):
        TBWriter(config).generate()


def test_fixture_dram_bank_port_count_matches_tb_config():
    repo_root = Path(__file__).resolve().parents[1]
    fixture_path = repo_root / "tests" / "fixtures" / "mock_dram_bank.cdl"
    tb_cfg_path = repo_root / "config" / "tb_sim.json"

    tb_config = json.loads(tb_cfg_path.read_text(encoding="utf-8"))
    top_ports = tb_config["testbench_builder"]["top_instance"]["ports"]
    declared_ports = TBWriter({"testbench_builder": {}})._extract_subckt_ports(str(fixture_path), "DRAM_BANK")

    assert declared_ports is not None
    assert len(declared_ports) == len(top_ports)
