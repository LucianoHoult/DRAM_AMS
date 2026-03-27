# tb_writer.py
import os
from typing import Dict, Any

class TBWriter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("testbench_builder", {})

    def _format_top_instance(self) -> str:
        """生成顶层 DUT 的实例化语句"""
        top_inst = self.config.get("top_instance", {})
        name = top_inst.get("name", "X_DUT")
        ref_model = top_inst.get("ref_model", "DRAM_BANK")
        ports = top_inst.get("ports", [])
        
        # 自动换行处理以符合 SPICE 格式规范
        port_str = " ".join(ports)
        # 简单处理：如果过长则利用之前 cdl_writer 的换行逻辑，这里为保持独立先直接拼接
        return f"{name} {port_str} {ref_model}"

    def _extract_subckt_ports(self, netlist_path: str, ref_model: str):
        """
        从 CDL/SPICE 网表中提取目标 .SUBCKT 的端口列表（支持 '+' 续行）。
        找不到时返回 None。
        """
        if not netlist_path or not os.path.exists(netlist_path):
            return None

        with open(netlist_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = 0
        while idx < len(lines):
            raw = lines[idx].strip()
            idx += 1
            if not raw:
                continue
            tokens = raw.split()
            if len(tokens) >= 2 and tokens[0].upper() == ".SUBCKT" and tokens[1] == ref_model:
                header_tokens = tokens[2:]
                while idx < len(lines):
                    cont = lines[idx].strip()
                    if not cont.startswith("+"):
                        break
                    header_tokens.extend(cont[1:].strip().split())
                    idx += 1
                return header_tokens

        return None

    def _validate_top_instance_ports(self):
        """
        校验 testbench top_instance 端口列表与被实例化 .SUBCKT 声明是否一致。
        不一致时抛出 ValueError，避免 subckt arity 错误。
        """
        includes = self.config.get("includes", {})
        top_inst = self.config.get("top_instance", {})
        ref_model = top_inst.get("ref_model", "DRAM_BANK")
        top_ports = top_inst.get("ports", [])

        declared_ports = self._extract_subckt_ports(includes.get("netlist", ""), ref_model)
        if declared_ports is None:
            return

        if top_ports != declared_ports:
            raise ValueError(
                "Top instance ports do not match netlist .SUBCKT declaration for "
                f"{ref_model}: expected {declared_ports}, got {top_ports}"
            )

    def generate(self):
        """生成顶层 .sp 测试台文件"""
        out_path = self.config.get("output_tb_path", "top_tb.sp")
        includes = self.config.get("includes", {})
        options = self.config.get("global_options", [])
        temp = self.config.get("temperature_c", 25)
        self._validate_top_instance_ports()

        lines = [
            "* ==========================================",
            "* TOP LEVEL DRAM ARRAY TESTBENCH",
            "* ==========================================\n",
            
            "* --- Global Options & Environment ---",
            f".option {' '.join(options)}",
            f".temp {temp}\n",
            
            "* --- Includes ---"
        ]

        # 写入 Includes
        if "tech_lib" in includes:
            # 假设工艺库带有 corner 标示，实际中可能需要 .lib 'xxx.lib' TT
            # 这里简化为基础 include
            lines.append(f".include '{includes['tech_lib']}'")
            
        for key in ["netlist", "power_supplies", "stimulus", "init_cond"]:
            if key in includes and includes[key]:
                lines.append(f".include '{includes[key]}'")

        lines.extend([
            "\n* --- DUT Instantiation ---",
            self._format_top_instance(),
            "\n.end"
        ])

        # 确保输出目录存在
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
        
        with open(out_path, 'w') as f:
            f.write("\n".join(lines) + "\n")
