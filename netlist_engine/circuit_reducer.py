# netlist_engine/circuit_reducer.py
from typing import Dict, Any, List
from .cdl_parser import NetlistIR, Instance

class CircuitReducer:
    def __init__(self, ir: NetlistIR, config: Dict[str, Any]):
        self.ir = ir
        self.config = config

    def _create_equivalent_loads(self, 
                                 target_subckt, 
                                 original_inst: Instance, 
                                 c_eff: float, 
                                 i_leak: float, 
                                 ignore_ports: List[str]):
        """
        为被删除的 Instance 的每个信号引脚生成对地的 C 和 I。
        直接实例化电容和直流电流源。
        """
        inst_base_name = original_inst.name
        
        for port_net in original_inst.ports:
            # 跳过电源/地线网，不对它们施加等效负载
            if port_net in ignore_ports:
                continue

            clean_net = port_net.replace('<', '_').replace('>', '')
            
            # 1. 实例化等效电容 (挂载到 VSS)
            c_inst_name = f"C_RED_{inst_base_name}_{clean_net}"
            c_inst = Instance(
                name=c_inst_name,
                ref_model=f"{c_eff}f",  # 例如 "150.0f"
                ports=[port_net, "VSS"]
            )
            target_subckt.instances[c_inst_name] = c_inst

            # 2. 实例化等效漏电流源 (挂载到 VSS，方向从信号节点流向 VSS)
            i_inst_name = f"I_RED_{inst_base_name}_{clean_net}"
            i_inst = Instance(
                name=i_inst_name,
                ref_model="DC",
                ports=[port_net, "VSS"],
                params={"DC": f"{i_leak}n"} # 例如 "10.0n"
            )
            target_subckt.instances[i_inst_name] = i_inst

    def process_all_from_config(self):
        """主入口：遍历配置，移除 Instance 并替换为等效负载"""
        red_config = self.config.get("reduction_models", {})
        if red_config.get("mode") != "placeholder":
            return # 目前仅支持 placeholder 模式

        targets = red_config.get("targets", [])
        
        for target in targets:
            parent_name = target.get("parent_subckt")
            inst_name = target.get("inst_name")
            c_eff = target.get("c_eff_fF")
            i_leak = target.get("i_leak_nA")
            ignore_ports = target.get("ignore_ports", ["VSS", "VDD", "VPP"])

            target_subckt = self.ir.subckts.get(parent_name)
            if not target_subckt:
                continue

            # 1. 在当前 IR 中定位目标 Instance
            original_inst = target_subckt.instances.get(inst_name)
            if not original_inst:
                continue

            # 2. 生成等效负载 (必须在删除原 Instance 前读取其 ports)
            self._create_equivalent_loads(
                target_subckt, 
                original_inst, 
                c_eff, 
                i_leak, 
                ignore_ports
            )

            # 3. 从网表中剥离原 Instance
            del target_subckt.instances[inst_name]
