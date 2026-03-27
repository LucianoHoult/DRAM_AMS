import csv
import json
from pathlib import Path

import pytest

import main
from main import (
    deep_merge_dicts,
    ensure_parent_dir,
    load_merged_config,
    resolve_netlist_input,
    resolve_timing_case,
    run_flow,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_config_helpers_and_resolution(tmp_path):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "general_config.json").write_text(json.dumps({"netlist_engine_io": {"input_cdl": "a.cdl"}}), encoding="utf-8")
    (config_dir / "tb_sim.json").write_text(json.dumps({"testbench_builder": {"includes": {"netlist": "out/net.cdl"}}}), encoding="utf-8")
    (config_dir / "extra.json").write_text(json.dumps({"stimulus_generator": {"timing_cases": {"foo": {}}}}), encoding="utf-8")

    merged = load_merged_config(str(config_dir))
    assert merged["config_meta"]["loaded_files"][0].endswith("general_config.json")
    assert resolve_netlist_input(merged) == "a.cdl"
    assert resolve_timing_case(merged) == "foo"
    assert deep_merge_dicts({"a": {"b": 1}, "c": 1}, {"a": {"d": 2}, "c": 3}) == {"a": {"b": 1, "d": 2}, "c": 3}

    nested_target = tmp_path / "nested" / "dir" / "file.txt"
    ensure_parent_dir(str(nested_target))
    assert nested_target.parent.exists()


def test_resolve_helpers_raise_for_missing_inputs():
    with pytest.raises(KeyError):
        resolve_netlist_input({})
    with pytest.raises(KeyError):
        resolve_timing_case({"stimulus_generator": {"timing_cases": {}}})


def test_run_flow_end_to_end_generates_outputs_and_report(tmp_path, monkeypatch):
    src_cdl = FIXTURE_DIR / "mock_dram_bank.cdl"
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    merged_config = {
        "netlist_engine_io": {"input_cdl": str(src_cdl)},
        "rc_extraction": {
            "unit_metrics": {
                "WL": {"comment": "M1_WL_layer", "R_per_um": 0.5, "C_per_um": 0.2e-15},
                "CSL": {"comment": "M3_global_layer", "R_per_um": 0.1, "C_per_um": 0.1e-15},
            },
            "core_array": {
                "WL<*>": {
                    "layer": "WL",
                    "length_um": 128.0,
                    "pi_stages": 2,
                    "parent_subckt": "ARRAY_SECTION",
                    "driver_inst": "X_SWD_E",
                    "target_insts": ["X_MAT_0"],
                }
            },
            "global_routes": {
                "CSL": {
                    "layer": "CSL",
                    "pi_stages_per_segment": 2,
                    "parent_subckt": "DRAM_BANK",
                    "driver_inst": "PORT",
                    "topology": [
                        {"target_inst": "X_SA_SEC_0", "segment_length_um": 120.0},
                        {"target_inst": "X_CROSS_HOLE_0", "segment_length_um": 20.0},
                    ],
                }
            },
        },
        "reduction_models": {
            "enabled": True,
            "mode": "placeholder",
            "targets": [
                {
                    "parent_subckt": "ARRAY_SECTION",
                    "inst_name": "X_MAT_0",
                    "c_eff_fF": 99.0,
                    "i_leak_nA": 7.0,
                    "ignore_ports": ["VSS", "VPP", "VDD"],
                }
            ],
        },
        "stimulus_generator": {
            "global_params": {
                "default_tr_ns": 0.1,
                "default_tf_ns": 0.2,
            },
            "timing_cases": {
                "tRAS_measurement": {
                    "phase_sequence": [
                        {"phase": "IDLE", "duration_ns": 1.0},
                        {"phase": "ACT", "duration_ns": 2.0},
                    ],
                    "pin_stimulus": {
                        "MWL_EVEN": {
                            "init_state": "VSS",
                            "transitions": [
                                {"sync_phase": "ACT", "offset_ns": 0.5, "target_state": "VPP", "tr_ns": 0.1}
                            ],
                        }
                    },
                    "measurements": [
                        {
                            "meas_name": "t_core",
                            "meas_type": "delay",
                            "trigger": {"node": "MWL_EVEN", "edge": "RISE", "val_expr": "0.5*VPP"},
                            "target": {"node": "MWL_EVEN", "edge": "FALL", "val_expr": "0.5*VPP"},
                        }
                    ],
                }
            },
        },
        "topology_initializer": {
            "output_file": str(tmp_path / "output" / "init.ic"),
            "voltage_levels": {"v_high": "VARY", "v_low": "VSS"},
            "path_template": "X_BANK.X_ARRAY_SEC_{sec}.X_MAT_{mat}.X_CELL_{row}_{col}.SN",
            "address_space": {"sec": [0], "mat": [0], "row": [0, 1], "col": [0, 1]},
            "pattern": "checkerboard",
        },
        "testbench_builder": {
            "output_tb_path": str(tmp_path / "sim_workspace" / "top_tb.sp"),
            "includes": {
                "netlist": str(tmp_path / "output" / "modified_dram_bank.cdl"),
                "power_supplies": str(tmp_path / "output" / "power_supplies.inc"),
                "stimulus": str(tmp_path / "output" / "stimulus.sp"),
                "init_cond": str(tmp_path / "output" / "init.ic"),
            },
            "global_options": ["post", "probe"],
            "temperature_c": 25,
            "top_instance": {
                "name": "X_DUT",
                "ref_model": "DRAM_BANK",
                "ports": [
                    "MWL_E",
                    "MWL_O",
                    "SAN",
                    "SAP",
                    "EQ",
                    "CSL",
                    "LIO",
                    "LIO_B",
                    "VPP",
                    "VSS",
                    "BL<0>",
                    "BL<1>",
                    "BL_B<0>",
                    "BL_B<1>",
                    "VDD",
                    "VARY",
                    "VBLP",
                    "VBB",
                ],
            },
        },
        "power_domains": {
            "voltage_levels": {"VDD": 1.1, "VPP": 1.8, "VARY": 1.0, "VBLP": 0.5, "VBB": -0.3, "VSS": 0.0},
            "supply_output": str(tmp_path / "output" / "power_supplies.inc"),
        },
        "sim_runner": {"execution_mode": "local", "max_parallel_jobs": 1, "timeout_seconds": 5},
    }

    for name, payload in {
        "general_config.json": {"netlist_engine_io": merged_config["netlist_engine_io"]},
        "operation_measurement_pwl_config.json": {"stimulus_generator": merged_config["stimulus_generator"]},
        "rc.json": {"rc_extraction": merged_config["rc_extraction"]},
        "reduction.json": {"reduction_models": merged_config["reduction_models"]},
        "tb_sim.json": {
            "topology_initializer": merged_config["topology_initializer"],
            "testbench_builder": merged_config["testbench_builder"],
            "sim_runner": merged_config["sim_runner"],
            "power_domains": merged_config["power_domains"],
        },
    }.items():
        (config_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    def fake_run_all(self, tb_paths):
        prefix = Path(tb_paths[0]).with_suffix("")
        prefix.with_suffix(".mt0").write_text(
            "alter# t_core energy\n1 1.111e-09 2.50e-12\n",
            encoding="utf-8",
        )
        return [{"tb": tb_paths[0], "success": True, "output_prefix": str(prefix)}]

    monkeypatch.setattr(main.SimRunner, "run_all", fake_run_all)

    run_flow(str(config_dir))

    netlist_text = (tmp_path / "output" / "modified_dram_bank.cdl").read_text(encoding="utf-8")
    assert "X_RC_STAR_WL_0" in netlist_text
    assert "X_RC_CHAIN_CSL_seg1" in netlist_text
    assert "C_RED_X_MAT_0_WL_1" in netlist_text

    stim_text = (tmp_path / "output" / "stimulus.sp").read_text(encoding="utf-8")
    assert ".measure tran t_core" in stim_text
    assert ".tran 5p 3.0n" in stim_text

    tb_text = (tmp_path / "sim_workspace" / "top_tb.sp").read_text(encoding="utf-8")
    assert ".include '" + str(tmp_path / "output" / "modified_dram_bank.cdl") + "'" in tb_text
    assert ".include '" + str(tmp_path / "output" / "power_supplies.inc") + "'" in tb_text

    supplies_text = (tmp_path / "output" / "power_supplies.inc").read_text(encoding="utf-8")
    assert "V_VPP VPP 0 DC 1.8" in supplies_text

    report_path = tmp_path / "sim_workspace" / "timing_evaluation_report.csv"
    rows = list(csv.DictReader(report_path.open(encoding="utf-8")))
    assert rows == [{"Testbench": "top_tb.sp", "Sim_Status": "PASS", "energy": "2.5e-12", "t_core": "1.111e-09"}]
