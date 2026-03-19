# tests/test_circuit_reducer.py
import pytest
from netlist_engine.cdl_parser import NetlistIR, Subckt, Instance
from netlist_engine.circuit_reducer import CircuitReducer

@pytest.fixture
def ir_with_inactive_mat():
    ir = NetlistIR()
    subckt = Subckt(name="ARRAY_SECTION", ports=["WL<0>", "VSS", "VDD"])
    # 构造一个待精简的 MAT
    mat_inst = Instance(name="X_MAT_1", ref_model="MAT", ports=["WL<0>", "VSS", "VDD"])
    subckt.instances["X_MAT_1"] = mat_inst
    ir.subckts["ARRAY_SECTION"] = subckt
    return ir

def test_circuit_reduction(ir_with_inactive_mat):
    config = {
        "reduction_models": {
            "mode": "placeholder",
            "targets": [
                {
                    "parent_subckt": "ARRAY_SECTION",
                    "inst_name": "X_MAT_1",
                    "c_eff_fF": 150.0,
                    "i_leak_nA": 10.0,
                    "ignore_ports": ["VSS", "VDD"]
                }
            ]
        }
    }
    
    reducer = CircuitReducer(ir_with_inactive_mat, config)
    reducer.process_all_from_config()
    
    target_subckt = ir_with_inactive_mat.subckts["ARRAY_SECTION"]
    
    # 断言 1: 原 Instance 已被剥离
    assert "X_MAT_1" not in target_subckt.instances
    
    # 断言 2: 生成了连接到 VSS 的等效电容 (仅针对 WL<0>，忽略 VSS/VDD)
    c_inst_name = "C_RED_X_MAT_1_WL_0"
    assert c_inst_name in target_subckt.instances
    c_inst = target_subckt.instances[c_inst_name]
    assert c_inst.ref_model == "150.0f"
    assert set(c_inst.ports) == {"WL<0>", "VSS"}
    
    # 断言 3: 生成了等效漏电电流源
    i_inst_name = "I_RED_X_MAT_1_WL_0"
    assert i_inst_name in target_subckt.instances
    i_inst = target_subckt.instances[i_inst_name]
    assert i_inst.ref_model == "DC"
    assert i_inst.params["DC"] == "10.0n"
