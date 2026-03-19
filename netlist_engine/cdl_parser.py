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
    top_level_instances: Dict[str, Instance] = field(default_factory=dict) # 用于不在 subckt 内的顶层调用

class CDLParser:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.ir = NetlistIR()
        self._raw_lines = []
        self._logical_lines = []

    def _read_and_preprocess(self):
        """第一步：读取文件，去除注释，合并以 '+' 开头的换行"""
        with open(self.filepath, 'r') as f:
            self._raw_lines = f.readlines()
        
        current_line = ""
        for line in self._raw_lines:
            stripped = line.strip()
            # 忽略空行和以 * 开头的纯注释行
            if not stripped or stripped.startswith('*'):
                continue
            
            if stripped.startswith('+'):
                # 合并多行语句（去掉 '+' 号并追加）
                current_line += " " + stripped[1:].strip()
            else:
                if current_line:
                    self._logical_lines.append(current_line.strip())
                current_line = stripped
        
        if current_line:
            self._logical_lines.append(current_line.strip())

    def parse(self) -> NetlistIR:
        """第二步：将逻辑行转换为 IR 对象 (目前仅搭好框架，待填入具体正则/拆分逻辑)"""
        self._read_and_preprocess()
        
        current_subckt: Optional[Subckt] = None
        
        for line in self._logical_lines:
            tokens = line.split()
            if not tokens: continue
            
            if tokens[0].upper() == '.SUBCKT':
                current_subckt = Subckt(name=tokens[1], ports=tokens[2:])
                self.ir.subckts[current_subckt.name] = current_subckt
            
            elif tokens[0].upper() == '.ENDS':
                current_subckt = None
                
            elif tokens[0].upper().startswith(('X', 'M', 'C', 'R')):
                # 这是一个器件或子电路实例化
                inst_name = tokens[0]
                # TODO: 核心难点在这里。需要区分哪些是端口，哪些是参数 (如 W=1u)，哪个是 ref_model。
                # 暂时用一个占位符将整行存入
                inst = Instance(name=inst_name, ref_model="UNKNOWN", ports=tokens[1:])
                if current_subckt:
                    current_subckt.instances[inst_name] = inst
                else:
                    self.ir.top_level_instances[inst_name] = inst
                    
        return self.ir

    def dump_to_string(self) -> str:
        """测试用：将 IR 重新序列化为 CDL 字符串"""
        out = []
        for sub_name, subckt in self.ir.subckts.items():
            out.append(f".SUBCKT {sub_name} {' '.join(subckt.ports)}")
            for inst_name, inst in subckt.instances.items():
                out.append(f"{inst_name} {' '.join(inst.ports)}") # 简化版dump
            out.append(f".ENDS {sub_name}")
        return "\n".join(out)
