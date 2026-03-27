# netlist_engine/rc_inserter.py
import re
from typing import Dict, Any, Iterable, List, Optional, Set
from .cdl_parser import NetlistIR, Instance, Subckt

class RCInserter:
    def __init__(self, ir: NetlistIR, config: Dict[str, Any]):
        self.ir = ir
        self.config = config
        self._generated_rc_models = set()

    def _ensure_pi_model_exists(self, stages: int):
        """动态生成指定段数的参数化 PI 模型 (逻辑与之前一致)"""
        model_name = f"RC_PI_{stages}"
        if model_name in self._generated_rc_models:
            return model_name
            
        subckt = Subckt(name=model_name, ports=["IN", "OUT"])
        r_val, c_end_val, c_mid_val = f"'R_tot/{stages}'", f"'C_tot/{2*stages}'", f"'C_tot/{stages}'"
        nodes = ["IN"] + [f"N_{i}" for i in range(1, stages)] + ["OUT"]
        
        for i in range(stages):
            n1, n2 = nodes[i], nodes[i+1]
            r_inst = Instance(name=f"R_{i}", ref_model=r_val, ports=[n1, n2])
            subckt.instances[r_inst.name] = r_inst
            c_inst = Instance(name=f"C_{i}", ref_model=c_end_val if i==0 else c_mid_val, ports=[n1, "VSS"])
            subckt.instances[c_inst.name] = c_inst
            
        c_last = Instance(name=f"C_{stages}", ref_model=c_end_val, ports=["OUT", "VSS"])
        subckt.instances[c_last.name] = c_last
        
        self.ir.subckts[model_name] = subckt
        self._generated_rc_models.add(model_name)
        return model_name

    def _calc_rc(self, layer_name: str, length_um: float, unit_metrics: Dict):
        """根据长度和层信息计算总 R 和总 C"""
        metrics = unit_metrics[layer_name]
        if "metrics" in metrics and isinstance(metrics["metrics"], dict):
            metrics = metrics["metrics"]
        r_tot = metrics["R_per_um"] * length_um
        c_tot = metrics["C_per_um"] * length_um
        return r_tot, c_tot

    def _replace_port_in_inst(self, inst: Instance, old_net: str, new_net: str):
        """在 Instance 的端口列表中替换指定的 net 名称"""
        for i, port in enumerate(inst.ports):
            if port == old_net:
                inst.ports[i] = new_net
                return True
        return False

    def _process_star_topology(self, net_base_name: str, params: Dict, unit_metrics: Dict):
        """处理 Core Array 的单点驱动、多点接收拓扑"""
        subckt_name = params["parent_subckt"]
        target_subckt = self.ir.subckts.get(subckt_name)
        if not target_subckt:
            return

        r_tot, c_tot = self._calc_rc(params["layer"], params["length_um"], unit_metrics)
        model_name = self._ensure_pi_model_exists(params["pi_stages"])
        
        target_names = params["target_insts"]
        sink_net = f"{net_base_name}_sink"
        
        # 1. 驱动端保持 net_base_name 不变，接收端 (targets) 改为 sink_net
        for t_name in target_names:
            t_inst = target_subckt.instances.get(t_name)
            if t_inst:
                self._replace_port_in_inst(t_inst, net_base_name, sink_net)

        # 2. 插入 RC
        clean_net = net_base_name.replace('<', '_').replace('>', '')
        rc_inst_name = f"X_RC_STAR_{clean_net}"
        rc_inst = Instance(
            name=rc_inst_name,
            ref_model=model_name,
            ports=[net_base_name, sink_net],
            params={"R_tot": f"{r_tot}", "C_tot": f"{c_tot}"}
        )
        target_subckt.instances[rc_inst_name] = rc_inst

    def _process_daisy_chain_topology(self, net_base_name: str, params: Dict, unit_metrics: Dict):
        """处理 Global Routes 的级联/菊花链拓扑"""
        subckt_name = params["parent_subckt"]
        target_subckt = self.ir.subckts.get(subckt_name)
        if not target_subckt:
            return

        model_name = self._ensure_pi_model_exists(params["pi_stages_per_segment"])
        driver = params["driver_inst"]
        topology = params["topology"]

        current_source_net = net_base_name 
        
        if driver != "PORT":
            driver_inst = target_subckt.instances.get(driver)
            current_source_net = f"{net_base_name}_src"
            if driver_inst:
                self._replace_port_in_inst(driver_inst, net_base_name, current_source_net)

        for i, segment in enumerate(topology):
            target_name = segment["target_inst"]
            length = segment["segment_length_um"]
            r_tot, c_tot = self._calc_rc(params["layer"], length, unit_metrics)
            
            segment_out_net = f"{net_base_name}_seg{i+1}"
            
            t_inst = target_subckt.instances.get(target_name)
            if t_inst:
                self._replace_port_in_inst(t_inst, net_base_name, segment_out_net)
            
            clean_net = net_base_name.replace('<', '_').replace('>', '')
            rc_inst_name = f"X_RC_CHAIN_{clean_net}_seg{i+1}"
            rc_inst = Instance(
                name=rc_inst_name,
                ref_model=model_name,
                ports=[current_source_net, segment_out_net],
                params={"R_tot": f"{r_tot}", "C_tot": f"{c_tot}"}
            )
            target_subckt.instances[rc_inst_name] = rc_inst
            current_source_net = segment_out_net

    def _get_source_nets(self, target_subckt: Subckt, driver_name: str) -> List[str]:
        """根据 driver_inst 限定可被展开的合法线网集合。"""
        if driver_name == "PORT":
            return list(target_subckt.ports)
        driver_inst = target_subckt.instances.get(driver_name)
        return list(driver_inst.ports) if driver_inst else []

    def _expand_bus_nets(
        self,
        net_pattern: Any,
        target_subckt: Subckt,
        driver_name: str,
        filter_nets: Optional[Set[str]] = None,
    ) -> List[str]:
        """
        根据 Config 中的 net_pattern 寻找实际线名，并以驱动端真实连接为上界。
        """
        allowed_nets = set(self._get_source_nets(target_subckt, driver_name))
        if filter_nets is not None:
            allowed_nets &= set(filter_nets)

        def _filter(nets: Iterable[str]) -> List[str]:
            return [net for net in nets if net in allowed_nets]

        if isinstance(net_pattern, dict):
            if "nets" in net_pattern:
                return _filter(list(net_pattern["nets"]))
            net_pattern = net_pattern.get("pattern", "")

        if isinstance(net_pattern, list):
            return _filter(list(net_pattern))

        if not isinstance(net_pattern, str) or not net_pattern:
            return []

        if "*" not in net_pattern:
            return [net_pattern] if net_pattern in allowed_nets else []

        regex_str = re.escape(net_pattern).replace(r"\*", r"[^\s]+")
        pattern = re.compile(f"^{regex_str}$")
        return sorted(net for net in allowed_nets if pattern.match(net))

    def process_all_from_config(self):
        """主入口：遍历 Config，执行所有 RC 插入任务"""
        rc_config = self.config.get("rc_extraction", {})
        unit_metrics = rc_config.get("unit_metrics", {})

        core_array = rc_config.get("core_array", {})
        for net_pattern, params in core_array.items():
            target_subckt = self.ir.subckts.get(params["parent_subckt"])
            if not target_subckt:
                continue
                
            actual_nets = self._expand_bus_nets(
                net_pattern,
                target_subckt,
                params["driver_inst"],
            )
            for actual_net in actual_nets:
                self._process_star_topology(actual_net, params, unit_metrics)

        global_routes = rc_config.get("global_routes", {})
        for net_pattern, params in global_routes.items():
            target_subckt = self.ir.subckts.get(params["parent_subckt"])
            if not target_subckt:
                continue
                
            actual_nets = self._expand_bus_nets(
                net_pattern,
                target_subckt,
                params["driver_inst"],
            )
            for actual_net in actual_nets:
                self._process_daisy_chain_topology(actual_net, params, unit_metrics)
