import pytest
from stimulus_engine.topology_initializer import TopologyInitializer

@pytest.fixture
def sample_ic_config():
    """构造一个 2x2 阵列的极简配置用于测试"""
    return {
        "topology_initializer": {
            "output_file": "dummy.ic",
            "voltage_levels": {
                "v_high": 1.2,
                "v_low": 0.0
            },
            "path_template": "X_BANK.X_SEC_{sec}.X_MAT_{mat}.X_CELL_{row}_{col}.SN",
            "address_space": {
                "sec": [0],         # 单地址
                "mat": [0],
                "row": [0, 1],      # 范围地址 (包含 0 和 1)
                "col": [0, 1]
            },
            "pattern": "checkerboard"
        }
    }

def test_voltage_and_range_parsing(sample_ic_config):
    initializer = TopologyInitializer(sample_ic_config)
    
    # 1. 验证电压映射
    assert initializer._get_voltage(1) == 1.2
    assert initializer._get_voltage(0) == 0.0
    
    # 2. 验证地址范围解析 (闭区间转换为 Python 的前闭后开 range)
    assert list(initializer._parse_range([5])) == [5]
    assert list(initializer._parse_range([0, 3])) == [0, 1, 2, 3]
    
    # 异常输入测试
    with pytest.raises(ValueError):
        initializer._parse_range([1, 2, 3])



def test_pattern_logic(sample_ic_config):
    initializer = TopologyInitializer(sample_ic_config)
    
    # 验证各种 Pattern 算法的核心逻辑
    # solid
    assert initializer._get_state(row=5, col=10, pattern="solid_1") == 1
    assert initializer._get_state(row=5, col=10, pattern="solid_0") == 0
    
    # checkerboard (行列和为偶数->0, 奇数->1)
    assert initializer._get_state(0, 0, "checkerboard") == 0
    assert initializer._get_state(0, 1, "checkerboard") == 1
    assert initializer._get_state(1, 0, "checkerboard") == 1
    assert initializer._get_state(1, 1, "checkerboard") == 0
    
    # stripe
    assert initializer._get_state(0, 5, "row_stripe") == 0
    assert initializer._get_state(1, 5, "row_stripe") == 1
    assert initializer._get_state(5, 0, "col_stripe") == 0
    assert initializer._get_state(5, 1, "col_stripe") == 1

    # 异常 Pattern
    with pytest.raises(ValueError):
        initializer._get_state(0, 0, "random_unsupported_pattern")

def test_ic_file_generation(sample_ic_config, tmp_path):
    """
    测试端到端生成逻辑，使用 tmp_path 避免污染本地文件系统
    """
    initializer = TopologyInitializer(sample_ic_config)
    
    # 在临时目录下生成测试文件
    test_output = tmp_path / "test_init.ic"
    
    # 执行生成
    initializer.generate(output_path=str(test_output))
    
    # 验证文件是否成功创建
    assert test_output.exists()
    
    # 读取内容并进行断言
    content = test_output.read_text()
    
    # 验证文件头信息
    assert "PATTERN: CHECKERBOARD" in content
    
    # 验证模板映射和 Checkerboard 的电压结果
    # 坐标 (0,0) -> state 0 -> 0.0V
    assert ".ic V(X_BANK.X_SEC_0.X_MAT_0.X_CELL_0_0.SN) = 0.0" in content
    # 坐标 (0,1) -> state 1 -> 1.2V
    assert ".ic V(X_BANK.X_SEC_0.X_MAT_0.X_CELL_0_1.SN) = 1.2" in content
    # 坐标 (1,0) -> state 1 -> 1.2V
    assert ".ic V(X_BANK.X_SEC_0.X_MAT_0.X_CELL_1_0.SN) = 1.2" in content
    # 坐标 (1,1) -> state 0 -> 0.0V
    assert ".ic V(X_BANK.X_SEC_0.X_MAT_0.X_CELL_1_1.SN) = 0.0" in content
    
    # 确保没有生成超出范围的地址
    assert "X_CELL_0_2" not in content
    assert "X_CELL_2_0" not in content
