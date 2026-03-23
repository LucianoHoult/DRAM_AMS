# tests/test_rc_inserter.py
from pathlib import Path

import pytest
from netlist_engine.cdl_parser import CDLParser, NetlistIR, Subckt, Instance
from netlist_engine.rc_inserter import RCInserter

@pytest.fixture
def sample_ir():
    """构造一个极简的内存网表用于拓扑测试"""
    ir = NetlistIR()
    # 构造 ARRAY_SECTION: 包含一个 SWD (驱动) 和一个 MAT (负载)
    subckt = Subckt(name="ARRAY_SECTION", ports=["WL<0>", "VSS"])
    
    swd_inst = Instance(name="X_SWD_E", ref_model="SWD_EVEN", ports=["WL<0>", "VSS"])
    mat_inst = Instance(name="X_MAT_0", ref_model="MAT", ports=["WL<0>", "VSS"])
    
    subckt.instances["X_SWD_E"] = swd_inst
    subckt.instances["X_MAT_0"] = mat_inst
    ir.subckts["ARRAY_SECTION"] = subckt
    return ir

def test_star_topology_insertion(sample_ir):
    # 模拟 Config 传入
    config = {
        "rc_extraction": {
            "unit_metrics": {"M1": {"R_per_um": 1.0, "C_per_um": 1e-15}},
            "core_array": {
                "WL<0>": {
                    "layer": "M1", "length_um": 100.0, "pi_stages": 3,
                    "parent_subckt": "ARRAY_SECTION",
                    "driver_inst": "X_SWD_E",
                    "target_insts": ["X_MAT_0"]
                }
            }
        }
    }
    
    inserter = RCInserter(sample_ir, config)
    inserter.process_all_from_config()
    
    target_subckt = sample_ir.subckts["ARRAY_SECTION"]
    
    # 断言 1: RC PI 模型已在顶层生成
    assert "RC_PI_3" in sample_ir.subckts
    
    # 断言 2: 驱动端保持原名，接收端引脚被重命名为 sink
    assert "WL<0>" in target_subckt.instances["X_SWD_E"].ports
    assert "WL<0>_sink" in target_subckt.instances["X_MAT_0"].ports
    assert "WL<0>" not in target_subckt.instances["X_MAT_0"].ports
    
    # 断言 3: RC 实例成功插入并正确桥接
    rc_inst_name = "X_RC_STAR_WL_0"
    assert rc_inst_name in target_subckt.instances
    rc_inst = target_subckt.instances[rc_inst_name]
    assert rc_inst.ports == ["WL<0>", "WL<0>_sink"]
    assert rc_inst.params["R_tot"] == "100.0"


def test_wildcard_star_topology_only_expands_upstream_connected_nets():
    fixture_path = Path(__file__).parent / "fixtures" / "mock_dram_bank.cdl"
    ir = CDLParser(str(fixture_path)).parse()
    config = {
        "rc_extraction": {
            "unit_metrics": {"M1_WL_layer": {"R_per_um": 0.5, "C_per_um": 2e-16}},
            "core_array": {
                "WL<*>": {
                    "layer": "M1_WL_layer",
                    "length_um": 256.0,
                    "pi_stages": 4,
                    "parent_subckt": "ARRAY_SECTION",
                    "driver_inst": "X_SWD_E",
                    "target_insts": ["X_MAT_0"],
                }
            },
        }
    }

    inserter = RCInserter(ir, config)
    inserter.process_all_from_config()

    array_section = ir.subckts["ARRAY_SECTION"]
    assert "X_RC_STAR_WL_0" in array_section.instances
    assert "X_RC_STAR_WL_2" in array_section.instances
    assert "X_RC_STAR_WL_1" not in array_section.instances
    assert "X_RC_STAR_WL_3" not in array_section.instances

    mat_ports = array_section.instances["X_MAT_0"].ports
    assert "WL<0>_sink" in mat_ports
    assert "WL<2>_sink" in mat_ports
    assert "WL<1>" in mat_ports
    assert "WL<3>" in mat_ports
