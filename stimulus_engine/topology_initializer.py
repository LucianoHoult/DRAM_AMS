# stimulus_engine/topology_initializer.py
import os
import re
import string
import warnings
from typing import Any, Dict, List

from netlist_engine.cdl_parser import CDLParser, NetlistIR

class TopologyInitializer:
    def __init__(self, config: Dict[str, Any]):
        self.root_config = config
        self.config = config.get("topology_initializer", {})
        self.global_voltages = config.get("power_domains", {}).get("voltage_levels", {})
        self.voltages = self.config.get("voltage_levels", {"v_high": 1.2, "v_low": 0.0})

    def _resolve_voltage(self, value: Any) -> float:
        """支持直接电压值或全局电压别名（如 VARY/VSS）。"""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            if value in self.global_voltages:
                return float(self.global_voltages[value])
            try:
                return float(value)
            except ValueError as exc:
                raise ValueError(f"Unknown voltage alias: {value}") from exc
        raise ValueError(f"Invalid voltage value: {value}")

    def _get_voltage(self, state: int) -> float:
        """将逻辑状态 1/0 转换为绝对电压"""
        raw = self.voltages["v_high"] if state == 1 else self.voltages["v_low"]
        return self._resolve_voltage(raw)

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

    def _extract_template_fields(self, template: str) -> List[str]:
        fields = []
        for _, field_name, _, _ in string.Formatter().parse(template):
            if field_name:
                fields.append(field_name)
        return fields

    def _collect_hierarchical_ports(
        self,
        ir: NetlistIR,
        subckt_name: str,
        prefix: str,
        out_paths: set,
        depth: int = 0,
        max_depth: int = 16,
    ) -> None:
        if depth > max_depth:
            return
        subckt = ir.subckts.get(subckt_name)
        if not subckt:
            return
        for inst in subckt.instances.values():
            inst_path = f"{prefix}.{inst.name}" if prefix else inst.name
            child = ir.subckts.get(inst.ref_model)
            if child:
                for port in child.ports:
                    out_paths.add(f"{inst_path}.{port}")
                self._collect_hierarchical_ports(
                    ir=ir,
                    subckt_name=inst.ref_model,
                    prefix=inst_path,
                    out_paths=out_paths,
                    depth=depth + 1,
                    max_depth=max_depth,
                )

    def _discover_cell_nodes(
        self,
        ir: NetlistIR,
        subckt_name: str,
        prefix: str,
        out_targets: List[Dict[str, int]],
        depth: int = 0,
        max_depth: int = 16,
    ) -> None:
        if depth > max_depth:
            return
        subckt = ir.subckts.get(subckt_name)
        if not subckt:
            return
        for inst in subckt.instances.values():
            inst_path = f"{prefix}.{inst.name}" if prefix else inst.name
            child = ir.subckts.get(inst.ref_model)

            cell_match = re.match(r"^X_CELL_(\d+)_(\d+)$", inst.name, flags=re.IGNORECASE)
            if cell_match and child:
                sn_port = next((port for port in child.ports if port.upper() == "SN"), None)
                if sn_port:
                    sec_match = re.search(r"X_(?:ARRAY_)?SEC_(\d+)", inst_path, flags=re.IGNORECASE)
                    mat_match = re.search(r"X_MAT_(\d+)", inst_path, flags=re.IGNORECASE)
                    out_targets.append(
                        {
                            "path": f"{inst_path}.{sn_port}",
                            "sec": int(sec_match.group(1)) if sec_match else 0,
                            "mat": int(mat_match.group(1)) if mat_match else 0,
                            "row": int(cell_match.group(1)),
                            "col": int(cell_match.group(2)),
                        }
                    )

            if child:
                self._discover_cell_nodes(
                    ir=ir,
                    subckt_name=inst.ref_model,
                    prefix=inst_path,
                    out_targets=out_targets,
                    depth=depth + 1,
                    max_depth=max_depth,
                )

    def _render_template_candidates(self, template: str, addr: Dict[str, List[int]]) -> List[Dict[str, int]]:
        fields = set(self._extract_template_fields(template))
        sec_range = self._parse_range(addr.get("sec", [0])) if "sec" in fields else range(1)
        mat_range = self._parse_range(addr.get("mat", [0])) if "mat" in fields else range(1)
        row_range = self._parse_range(addr.get("row", [0])) if "row" in fields else range(1)
        col_range = self._parse_range(addr.get("col", [0])) if "col" in fields else range(1)

        rendered = []
        for s in sec_range:
            for m in mat_range:
                for r in row_range:
                    for c in col_range:
                        rendered.append(
                            {
                                "path": template.format(sec=s, mat=m, row=r, col=c),
                                "sec": s if "sec" in fields else 0,
                                "mat": m if "mat" in fields else 0,
                                "row": r if "row" in fields else 0,
                                "col": c if "col" in fields else 0,
                            }
                        )
        return rendered

    def _discover_init_targets_from_netlist(self) -> List[Dict[str, int]]:
        includes = self.root_config.get("testbench_builder", {}).get("includes", {})
        netlist_path = includes.get("netlist")
        if not netlist_path or not os.path.exists(netlist_path):
            return []

        parser = CDLParser(netlist_path)
        ir = parser.parse()

        top_subckt = (
            self.root_config.get("netlist_rules", {}).get("top_subckt")
            or self.root_config.get("testbench_builder", {}).get("top_instance", {}).get("ref_model")
        )
        if not top_subckt:
            return []

        cell_targets: List[Dict[str, int]] = []
        self._discover_cell_nodes(ir=ir, subckt_name=top_subckt, prefix="", out_targets=cell_targets)
        if cell_targets:
            return cell_targets

        existing_paths = set()
        self._collect_hierarchical_ports(ir=ir, subckt_name=top_subckt, prefix="", out_paths=existing_paths)

        node_cfg = self.config.get("node_discovery", {})
        strict_mode = node_cfg.get("missing_template_policy", "lenient").lower() == "strict"
        fallback_addr = node_cfg.get("fallback_address_space", self.config.get("address_space", {}))

        templates = []
        if node_cfg.get("fallback_path_template"):
            templates.append(node_cfg["fallback_path_template"])
        templates.extend(node_cfg.get("fallback_path_templates", []))
        if not templates and self.config.get("path_template"):
            templates.append(self.config["path_template"])

        dedup = set()
        matched_targets = []
        for template in templates:
            for candidate in self._render_template_candidates(template, fallback_addr):
                if candidate["path"] in existing_paths and candidate["path"] not in dedup:
                    dedup.add(candidate["path"])
                    matched_targets.append(candidate)

        if not matched_targets and templates:
            message = (
                f"Topology initializer could not find any valid init node from template(s): {templates}. "
                "Please check node_discovery.fallback_path_template(s) against processed netlist."
            )
            if strict_mode:
                raise ValueError(message)
            warnings.warn(message)
        return matched_targets

    def _has_netlist_for_discovery(self) -> bool:
        includes = self.root_config.get("testbench_builder", {}).get("includes", {})
        netlist_path = includes.get("netlist")
        return bool(netlist_path and os.path.exists(netlist_path))

    def generate(self, output_path: str = None):
        """生成 .ic 文件"""
        out_file = output_path or self.config.get("output_file", "init_storage.ic")
        template = self.config.get("path_template")
        addr = self.config.get("address_space", {})
        pattern = self.config.get("pattern", "solid_0")

        discovery_enabled = self._has_netlist_for_discovery()
        discovered_targets = self._discover_init_targets_from_netlist() if discovery_enabled else []
        fallback_targets = []
        if not discovered_targets and not discovery_enabled:
            if not template:
                raise ValueError("path_template must be defined in config.")
            # 兼容旧行为（无网表或未匹配发现结果时）
            for s in self._parse_range(addr.get("sec", [0])):
                for m in self._parse_range(addr.get("mat", [0])):
                    for r in self._parse_range(addr.get("row", [0])):
                        for c in self._parse_range(addr.get("col", [0])):
                            fallback_targets.append(
                                {
                                    "path": template.format(sec=s, mat=m, row=r, col=c),
                                    "sec": s,
                                    "mat": m,
                                    "row": r,
                                    "col": c,
                                }
                            )

        targets = discovered_targets or fallback_targets

        lines = [
            "* ==========================================",
            f"* INITIAL CONDITIONS (.ic)",
            f"* PATTERN: {pattern.upper()}",
            "* ==========================================\n"
        ]

        for target in targets:
            state = self._get_state(target["row"], target["col"], pattern)
            v = self._get_voltage(state)
            lines.append(f".ic V({target['path']}) = {v}")

        # 确保输出目录存在
        os.makedirs(os.path.dirname(os.path.abspath(out_file)) or '.', exist_ok=True)
        
        with open(out_file, 'w') as f:
            f.write("\n".join(lines) + "\n")
