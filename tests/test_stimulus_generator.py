# tests/test_stimulus_generator.py
import pytest
from stimulus_engine.stimulus_generator import StimulusGenerator

@pytest.fixture
def sample_config():
    """提供一个覆盖各类典型特征的基准配置"""
    return {
        "stimulus_generator": {
            "global_params": {
                "default_tr_ns": 0.1,
                "default_tf_ns": 0.1,
                "voltage_levels": {
                    "VSS": 0.0,
                    "VDD": 1.2,
                    "VPP": 2.5
                }
            },
            "timing_cases": {
                "test_case_1": {
                    "phase_sequence": [
                        {"phase": "IDLE", "duration_ns": 5.0},
                        {"phase": "ACT", "duration_ns": 10.0}
                    ],
                    "pin_stimulus": {
                        "WL_EN": {
                            "init_state": "VSS",
                            "transitions": [
                                # 在 ACT 阶段(5.0ns)偏移 1.0ns 处跳变，自定义 tr=0.5ns
                                {"sync_phase": "ACT", "offset_ns": 1.0, "target_state": "VPP", "tr_ns": 0.5}
                            ]
                        }
                    },
                    "measurements": [
                        {
                            "meas_name": "t_delay",
                            "meas_type": "delay",
                            "trigger": {"node": "CLK", "edge": "RISE", "val_expr": "0.5*VDD"},
                            "target": {"node": "WL_EN", "edge": "FALL", "val_expr": "VPP-0.5"}
                        }
                    ]
                }
            }
        }
    }

def test_voltage_and_math_eval(sample_config):
    generator = StimulusGenerator(sample_config)
    
    # 1. 测试电压解析
    assert generator._resolve_voltage("VDD") == 1.2
    assert generator._resolve_voltage("0.85") == 0.85
    with pytest.raises(ValueError):
        generator._resolve_voltage("UNKNOWN_VDD")
        
    # 2. 测试数学表达式求值
    assert generator._eval_math_expr("0.5*VDD") == "0.6000"
    assert generator._eval_math_expr("VPP - VDD") == "1.3000"
    with pytest.raises(ValueError):
        generator._eval_math_expr("0.5 * UNKNOWN")

def test_time_map_building(sample_config):
    generator = StimulusGenerator(sample_config)
    case = sample_config["stimulus_generator"]["timing_cases"]["test_case_1"]
    
    generator._build_time_map(case["phase_sequence"])
    
    # IDLE 从 0 开始，持续 5ns；ACT 从 5ns 开始，持续 10ns
    assert generator.phase_time_map["IDLE"] == 0.0
    assert generator.phase_time_map["ACT"] == 5.0
    assert generator.total_sim_time_ns == 15.0

def test_pwl_generation(sample_config):
    generator = StimulusGenerator(sample_config)
    case = sample_config["stimulus_generator"]["timing_cases"]["test_case_1"]
    
    # 必须先 build_time_map，因为 PWL 依赖绝对时间查找表
    generator._build_time_map(case["phase_sequence"])
    
    pwl_stmts = generator._generate_pwl_sources(case["pin_stimulus"])
    
    assert len(pwl_stmts) == 1
    stmt = pwl_stmts[0]
    
    # 验证是否包含正确的节点名
    assert "V_WL_EN WL_EN 0" in stmt
    
    # 验证时序计算:
    # 初始: 0n 0.0v
    # 保持到跳变前: 6.0n 0.0v (ACT起始5.0 + offset 1.0)
    # 跳变结束: 6.5n 2.5v (6.0 + 自定义tr 0.5, 目标VPP 2.5)
    # 结束保持: 15.0n 2.5v
    expected_pwl_inner = "0.0n 0.0v 6.0n 0.0v 6.5n 2.5v 15.0n 2.5v"
    
    # 由于浮点数格式化可能有微小差异，这里检查关键时序点是否存在
    assert "0.0n 0.0v" in stmt
    assert "6.0n 0.0v" in stmt
    assert "6.5n 2.5v" in stmt
    assert "15.0n 2.5v" in stmt

def test_measurements_generation(sample_config):
    generator = StimulusGenerator(sample_config)
    case = sample_config["stimulus_generator"]["timing_cases"]["test_case_1"]
    
    meas_stmts = generator._generate_measurements(case["measurements"])
    
    assert len(meas_stmts) == 1
    stmt = meas_stmts[0]
    
    # 验证触发和目标的沿变类型
    assert "rise=1" in stmt
    assert "fall=1" in stmt
    
    # 验证电压表达式是否被正确计算
    # trig: 0.5*VDD = 0.6; targ: VPP-0.5 = 2.0
    assert "val=0.6000" in stmt
    assert "val=2.0000" in stmt

def test_process_case_integration(sample_config):
    generator = StimulusGenerator(sample_config)
    result = generator.process_case("test_case_1")
    
    # 验证核心模块是否都被拼接到最终输出中
    assert "* STIMULUS GENERATED FOR: test_case_1" in result
    assert "PWL(0.0n 0.0v" in result
    assert ".measure tran t_delay" in result
    assert ".tran 5p 15.0n" in result
