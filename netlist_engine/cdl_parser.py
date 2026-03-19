# netlist_engine/cdl_parser.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class Instance:
    name: str
    ref_model: str
    ports: List[str] = field(default_factory=list)
    params: Dict[str, str] = field(default_factory=dict)

@dataclass
class Subckt:
    name: str
    ports: List[str] = field(default_factory=list)
    instances: Dict[str, Instance] = field(default_factory=dict)

@dataclass
class NetlistIR:
    subckts: Dict[str, Subckt] = field(default_factory=dict)
    top_level_instances: Dict[str, Instance] = field(default_factory=dict)

class CDLParser:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.ir = NetlistIR()
        self._raw_lines = []
        self._logical_lines = []

    def _read_and_preprocess(self):
        with open(self.filepath, 'r') as f:
            self._raw_lines = f.readlines()
        
        current_line = ""
        for line in self._raw_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('*'):
                continue
            
            if stripped.startswith('+'):
                current_line += " " + stripped[1:].strip()
            else:
                if current_line:
                    self._logical_lines.append(current_line.strip())
                current_line = stripped
        
        if current_line:
            self._logical_lines.append(current_line.strip())

    def parse(self) -> NetlistIR:
        self._read_and_preprocess()
        current_subckt: Optional[Subckt] = None
        
        for line in self._logical_lines:
            tokens = line.split()
            if not tokens: continue
            
            keyword = tokens[0].upper()
            
            if keyword == '.SUBCKT':
                current_subckt = Subckt(name=tokens[1], ports=tokens[2:])
                self.ir.subckts[current_subckt.name] = current_subckt
                
            elif keyword == '.ENDS':
                current_subckt = None
                
            elif keyword.startswith(('X', 'M', 'C', 'R', 'D', 'Q')):
                inst_name = tokens[0]
                params = {}
                
                # 逆向扫描提取参数
                end_idx = len(tokens)
                for i in range(len(tokens)-1, 0, -1):
                    if '=' in tokens[i]:
                        # 处理诸如 W=0.4u 的参数
                        key, val = tokens[i].split('=', 1)
                        params[key] = val
                        end_idx = i
                    else:
                        break # 遇到非参数Token，停止扫描
                
                # 剩余的Tokens列表结构为: [inst_name, port1, port2, ..., ref_model]
                remaining_tokens = tokens[:end_idx]
                
                if len(remaining_tokens) >= 2:
                    ref_model = remaining_tokens[-1]
                    ports = remaining_tokens[1:-1]
                else:
                    ref_model = "UNKNOWN"
                    ports = []
                    
                inst = Instance(name=inst_name, ref_model=ref_model, ports=ports, params=params)
                
                if current_subckt:
                    current_subckt.instances[inst_name] = inst
                else:
                    self.ir.top_level_instances[inst_name] = inst
                    
        return self.ir

    def dump_to_string(self) -> str:
        out = []
        for sub_name, subckt in self.ir.subckts.items():
            out.append(f".SUBCKT {sub_name} {' '.join(subckt.ports)}")
            for inst_name, inst in subckt.instances.items():
                # 重组Instance行
                param_str = ' '.join([f"{k}={v}" for k, v in inst.params.items()])
                port_str = ' '.join(inst.ports)
                
                line_parts = [inst_name]
                if port_str: line_parts.append(port_str)
                line_parts.append(inst.ref_model)
                if param_str: line_parts.append(param_str)
                
                out.append(" ".join(line_parts))
            out.append(f".ENDS {sub_name}")
            out.append("") # 增加空行以提高可读性
            
        # 处理顶层实例化（如果存在）
        for inst_name, inst in self.ir.top_level_instances.items():
            param_str = ' '.join([f"{k}={v}" for k, v in inst.params.items()])
            port_str = ' '.join(inst.ports)
            line_parts = [inst_name]
            if port_str: line_parts.append(port_str)
            line_parts.append(inst.ref_model)
            if param_str: line_parts.append(param_str)
            out.append(" ".join(line_parts))
            
        return "\n".join(out)
