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
        if not target_subckt: return

        r_tot, c_tot = self._calc_rc(params["layer"], params["length_um"], unit_metrics)
        model_name = self._ensure_pi_model_exists(params["pi_stages"])
        
        driver_name = params["driver_inst"]
        target_names = params["target_insts"]

        # 遍历目标 subckt 内部的所有匹配 net (如 WL<0>, WL<1>...)
        # 这里需要一个正则或前缀匹配机制，简单起见，假设 Config 传入的是具体 net 或我们在外层循环展开
        # 此处以处理单个精确 net_name 为例：
        
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
        if not target_subckt: return

        model_name = self._ensure_pi_model_exists(params["pi_stages_per_segment"])
        driver = params["driver_inst"]
        topology = params["topology"]

        # 初始驱动节点。如果是 PORT，则源头直接是该 net_base_name
        current_source_net = net_base_name 
        
        # 如果驱动是内部 Instance，需要先将驱动的输出端改名作为起始点
        if driver != "PORT":
            driver_inst = target_subckt.instances.get(driver)
            current_source_net = f"{net_base_name}_src"
            if driver_inst:
                self._replace_port_in_inst(driver_inst, net_base_name, current_source_net)

        for i, segment in enumerate(topology):
            target_name = segment["target_inst"]
            length = segment["segment_length_um"]
            r_tot, c_tot = self._calc_rc(params["layer"], length, unit_metrics)
            
            # 当前段的输出节点
            segment_out_net = f"{net_base_name}_seg{i+1}"
            
            # 修改目标 Instance 的连线，接通当前段的输出
            t_inst = target_subckt.instances.get(target_name)
            if t_inst:
                # 假设目标原来连在原始的 net_base_name 上
                self._replace_port_in_inst(t_inst, net_base_name, segment_out_net)
            
            # 插入这一段的 RC 模型
            clean_net = net_base_name.replace('<', '_').replace('>', '')
            rc_inst_name = f"X_RC_CHAIN_{clean_net}_seg{i+1}"
            rc_inst = Instance(
                name=rc_inst_name,
                ref_model=model_name,
                ports=[current_source_net, segment_out_net],
                params={"R_tot": f"{r_tot}", "C_tot": f"{c_tot}"}
            )
            target_subckt.instances[rc_inst_name] = rc_inst
            
            # 更新 source，为下一段做准备
            current_source_net = segment_out_net
            
    def _expand_bus_nets(
        self,
        net_pattern: Any,
        target_subckt: Subckt,
        filter_nets: Optional[Set[str]] = None,
    ) -> List[str]:
        """
        根据 Config 中的 net_pattern 寻找实际线名。
        兼容以下格式：
        - "WL<*>" / "BL<0>" 这样的字符串
        - {"pattern": "WL<*>"} 这样的对象
        - {"nets": ["WL<0>", "WL<2>"]} 这样的显式列表
        - ["WL<0>", "WL<2>"] 这样的列表
        可通过 filter_nets 进一步限制为真正连通的 nets。
        """
        if isinstance(net_pattern, dict):
            if "nets" in net_pattern:
                nets = list(net_pattern["nets"])
                return [net for net in nets if filter_nets is None or net in filter_nets]
            net_pattern = net_pattern.get("pattern", "")

        if isinstance(net_pattern, list):
            nets = list(net_pattern)
            return [net for net in nets if filter_nets is None or net in filter_nets]

        if not isinstance(net_pattern, str) or not net_pattern:
            return []

        if "*" not in net_pattern:
            return [net_pattern] if filter_nets is None or net_pattern in filter_nets else []

        regex_str = re.escape(net_pattern).replace(r"\*", r"[^\s]+")
        pattern = re.compile(f"^{regex_str}$")
        
        valid_nets = set()
        
        # 确定合法的线网池：仅从驱动端获取
        if driver_name == "PORT":
            source_ports = target_subckt.ports
        else:
            driver_inst = target_subckt.instances.get(driver_name)
            source_ports = driver_inst.ports if driver_inst else []

        for port in source_ports:
            if pattern.match(port):
                valid_nets.add(port)
                    
        return sorted(list(valid_nets))

    def process_all_from_config(self):
        """主入口：遍历 Config，执行所有 RC 插入任务"""
        rc_config = self.config.get("rc_extraction", {})
        unit_metrics = rc_config.get("unit_metrics", {})

        # 处理 Core Array (星型拓扑)
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

        # 处理 Global Routes (菊花链拓扑)
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
