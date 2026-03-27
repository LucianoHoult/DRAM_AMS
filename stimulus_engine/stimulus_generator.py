# stimulus_engine/stimulus_generator.py
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple

class StimulusGenerator:
    def __init__(self, config: Dict[str, Any]):
        self.root_config = config
        self.config = config.get("stimulus_generator", {})
        self.global_params = self.config.get("global_params", {})
        self.voltages = self.global_params.get("voltage_levels", {})
        if not self.voltages:
            self.voltages = self.root_config.get("power_domains", {}).get("voltage_levels", {})
        if self.voltages:
            self.global_params["voltage_levels"] = dict(self.voltages)
        
        # 内部状态
        self.phase_time_map: Dict[str, float] = {}
        self.total_sim_time_ns: float = 0.0

    def generate_power_supplies_file(self) -> str:
        """基于 power_domains 生成全局电压源定义文件，并回填到 TB includes。"""
        power_cfg = self.root_config.get("power_domains", {})
        voltages = power_cfg.get("voltage_levels", {})
        if not voltages:
            return ""

        includes = self.root_config.setdefault("testbench_builder", {}).setdefault("includes", {})
        output_path = power_cfg.get("supply_output") or includes.get("power_supplies") or "output/power_supplies.inc"

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "* ==========================================",
            "* GLOBAL POWER SUPPLIES",
            "* ==========================================",
        ]
        for name, value in voltages.items():
            lines.append(f"V_{name} {name} 0 DC {value}")

        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        includes["power_supplies"] = output_path
        return output_path

    def _resolve_voltage(self, v_str: str) -> float:
        """解析电压状态，例如将 'VDD' 转换为 1.2，支持直接输入数字字符串"""
        if v_str in self.voltages:
            return self.voltages[v_str]
        try:
            return float(v_str)
        except ValueError:
            raise ValueError(f"Unknown voltage level or invalid number: {v_str}")

    def _eval_math_expr(self, expr: str) -> str:
        """
        安全评估测量语句中的数学表达式，如 '0.5*VPP'。
        替换变量为具体数值后进行计算。
        """
        parsed_expr = expr
        # 按长度降序替换，防止 VDD 替换 VDD_CORE 的前缀
        for v_name in sorted(self.voltages.keys(), key=len, reverse=True):
            if v_name in parsed_expr:
                parsed_expr = parsed_expr.replace(v_name, str(self.voltages[v_name]))
        try:
            # 仅允许基础数学运算的 eval
            result = eval(parsed_expr, {"__builtins__": None}, {})
            return f"{result:.4f}"
        except Exception as e:
            raise ValueError(f"Failed to evaluate expression '{expr}': {e}")

    def _build_time_map(self, phase_sequence: List[Dict[str, Any]]):
        """扫描 phase_sequence，建立绝对时间映射表"""
        current_time = 0.0
        self.phase_time_map.clear()
        
        for phase in phase_sequence:
            name = phase["phase"]
            duration = phase["duration_ns"]
            self.phase_time_map[name] = current_time
            current_time += duration
            
        self.total_sim_time_ns = current_time

    def _generate_pwl_sources(self, pin_stimulus: Dict[str, Any]) -> List[str]:
        """将引脚行为转换为 HSPICE PWL 电压源语句"""
        pwl_statements = []
        default_tr = self.global_params.get("default_tr_ns", 0.1)
        default_tf = self.global_params.get("default_tf_ns", 0.1)

        for pin_name, behavior in pin_stimulus.items():
            current_v = self._resolve_voltage(behavior["init_state"])
            # PWL 坐标对: [(time, voltage)]
            pwl_points: List[Tuple[float, float]] = [(0.0, current_v)]
            
            transitions = behavior.get("transitions", [])
            # 确保按绝对时间排序，防止 Config 编写乱序导致 SPICE 报错
            transitions.sort(key=lambda t: self.phase_time_map[t["sync_phase"]] + t["offset_ns"])

            for trans in transitions:
                sync_time = self.phase_time_map[trans["sync_phase"]]
                t_start = sync_time + trans["offset_ns"]
                target_v = self._resolve_voltage(trans["target_state"])
                
                # 决定边沿时间
                if target_v > current_v:
                    edge_time = trans.get("tr_ns", default_tr)
                else:
                    edge_time = trans.get("tf_ns", default_tf)
                
                t_end = t_start + edge_time
                
                # 保持前一状态直到跳变开始
                if pwl_points and t_start > pwl_points[-1][0]:
                    pwl_points.append((t_start, current_v))
                
                # 写入跳变后的状态
                pwl_points.append((t_end, target_v))
                current_v = target_v
            
            # 保持最终状态直到仿真结束
            if pwl_points[-1][0] < self.total_sim_time_ns:
                pwl_points.append((self.total_sim_time_ns, current_v))

            # 格式化为 HSPICE 语句
            # V_EQ_CTRL EQ_CTRL 0 PWL(0n 1.2v 5.0n 1.2v 5.1n 0.0v ...)
            pts_str = " ".join([f"{t}n {v}v" for t, v in pwl_points])
            pwl_statements.append(f"V_{pin_name} {pin_name} 0 PWL({pts_str})")

        return pwl_statements

    def _generate_measurements(self, measurements: List[Dict[str, Any]]) -> List[str]:
        """生成 .measure tran 语句"""
        meas_statements = []
        
        for meas in measurements:
            m_name = meas["meas_name"]
            # 仅处理 delay 类型的测算
            if meas["meas_type"] == "delay":
                trig = meas["trigger"]
                targ = meas["target"]
                
                trig_val = self._eval_math_expr(trig["val_expr"])
                targ_val = self._eval_math_expr(targ["val_expr"])
                
                trig_edge = 1 if trig["edge"].upper() == "RISE" else 0
                targ_edge = 1 if targ["edge"].upper() == "RISE" else 0
                # 若为 FALL，HSPICE 中 fall=1，rise/fall 互斥。这里简化为直接写明 rise=1 或 fall=1
                trig_edge_str = "rise=1" if trig["edge"].upper() == "RISE" else "fall=1"
                targ_edge_str = "rise=1" if targ["edge"].upper() == "RISE" else "fall=1"

                stmt = (f".measure tran {m_name} "
                        f"trig v({trig['node']}) val={trig_val} {trig_edge_str} "
                        f"targ v({targ['node']}) val={targ_val} {targ_edge_str}")
                meas_statements.append(stmt)
                
        return meas_statements

    def process_case(self, case_name: str) -> str:
        """处理指定的 timing_case，返回完整的激励文本"""
        timing_case = self.config.get("timing_cases", {}).get(case_name)
        if not timing_case:
            raise ValueError(f"Timing case '{case_name}' not found in config.")

        # 1. 构建时间轴
        self._build_time_map(timing_case.get("phase_sequence", []))
        
        out_lines = []
        out_lines.append(f"* ==========================================")
        out_lines.append(f"* STIMULUS GENERATED FOR: {case_name}")
        out_lines.append(f"* TOTAL SIMULATION TIME: {self.total_sim_time_ns} ns")
        out_lines.append(f"* ==========================================\n")

        # 2. 生成 PWL 波形
        out_lines.append("* --- Voltage Sources (PWL) ---")
        pwl_stmts = self._generate_pwl_sources(timing_case.get("pin_stimulus", {}))
        out_lines.extend(pwl_stmts)
        out_lines.append("\n")

        # 3. 生成 Measure 语句
        out_lines.append("* --- Timing Measurements ---")
        meas_stmts = self._generate_measurements(timing_case.get("measurements", []))
        out_lines.extend(meas_stmts)
        out_lines.append("\n")

        # 4. 生成 Tran 仿真控制语句
        # 默认步长设为总时间的 1/1000，上限限制在 1p 到 10p 之间
        out_lines.append("* --- Simulation Control ---")
        out_lines.append(f".tran 5p {self.total_sim_time_ns}n")
        
        return "\n".join(out_lines)
