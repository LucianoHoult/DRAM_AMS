# stimulus_engine/topology_initializer.py
import os
from typing import Dict, Any

class TopologyInitializer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("topology_initializer", {})
        self.voltages = self.config.get("voltage_levels", {"v_high": 1.2, "v_low": 0.0})

    def _get_voltage(self, state: int) -> float:
        """将逻辑状态 1/0 转换为绝对电压"""
        return self.voltages["v_high"] if state == 1 else self.voltages["v_low"]

    def _get_state(self, row: int, col: int, pattern: str) -> int:
        """
        基于行列坐标生成测试 Pattern。
        支持: solid_1, solid_0, checkerboard, row_stripe, col_stripe
        """
        if pattern == "solid_1": return 1
        if pattern == "solid_0": return 0
        if pattern == "checkerboard": return (row + col) % 2
        if pattern == "row_stripe": return row % 2
        if pattern == "col_stripe": return col % 2
        raise ValueError(f"Unsupported pattern: {pattern}")

    def _parse_range(self, range_list: list) -> range:
        """解析 JSON 中的地址列表为 Python 的 range 对象"""
        if len(range_list) == 1:
            return range(range_list[0], range_list[0] + 1)
        elif len(range_list) == 2:
            # 包含结束地址
            return range(range_list[0], range_list[1] + 1)
        else:
            raise ValueError(f"Invalid address space format: {range_list}")

    def generate(self, output_path: str = None):
        """生成 .ic 文件"""
        out_file = output_path or self.config.get("output_file", "init_storage.ic")
        template = self.config.get("path_template")
        addr = self.config.get("address_space", {})
        pattern = self.config.get("pattern", "solid_0")

        if not template:
            raise ValueError("path_template must be defined in config.")

        # 解析各维度的迭代范围
        sec_range = self._parse_range(addr.get("sec", [0]))
        mat_range = self._parse_range(addr.get("mat", [0]))
        row_range = self._parse_range(addr.get("row", [0]))
        col_range = self._parse_range(addr.get("col", [0]))

        lines = [
            "* ==========================================",
            f"* INITIAL CONDITIONS (.ic)",
            f"* PATTERN: {pattern.upper()}",
            "* ==========================================\n"
        ]

        for s in sec_range:
            for m in mat_range:
                for r in row_range:
                    for c in col_range:
                        state = self._get_state(r, c, pattern)
                        v = self._get_voltage(state)
                        # 将坐标填入预设的绝对路径模板
                        path = template.format(sec=s, mat=m, row=r, col=c)
                        lines.append(f".ic V({path}) = {v}")

        # 确保输出目录存在
        os.makedirs(os.path.dirname(os.path.abspath(out_file)) or '.', exist_ok=True)
        
        with open(out_file, 'w') as f:
            f.write("\n".join(lines) + "\n")
