# netlist_engine/pipeline.py
import json
from typing import Dict, Any

from .cdl_parser import CDLParser
from .rc_inserter import RCInserter
from .circuit_reducer import CircuitReducer
from .cdl_writer import CDLWriter

class NetlistEnginePipeline:
    def __init__(self, input_cdl_path: str, config: Dict[str, Any], output_cdl_path: str):
        self.input_cdl_path = input_cdl_path
        self.config = config
        self.output_cdl_path = output_cdl_path
        self.ir = None

    def run(self):
        """执行完整的网表处理流水线"""
        # 1. 解析原始网表 -> 生成 IR
        parser = CDLParser(self.input_cdl_path)
        self.ir = parser.parse()

        # 2. 拓扑修改: 插入 RC 网络
        # 必须在 Reducer 之前执行，确保长走线的命名更改生效
        if "rc_extraction" in self.config:
            rc_inserter = RCInserter(self.ir, self.config)
            rc_inserter.process_all_from_config()

        # 3. 拓扑修改: 静态负载精简替换
        # 依赖已被 RC_Inserter 修改过的引脚连接
        reduction_cfg = self.config.get("reduction_models", {})
        if reduction_cfg and reduction_cfg.get("enabled", True):
            reducer = CircuitReducer(self.ir, self.config)
            reducer.process_all_from_config()

        # 4. 生成新网表
        writer = CDLWriter(self.ir)
        writer.write(self.output_cdl_path)
