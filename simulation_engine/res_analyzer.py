# execution_engine/log_analyzer.py
import os
import re
from typing import Dict, Any, Union

class LogAnalyzer:
    def __init__(self, run_dir: str, prefix: str):
        self.run_dir = run_dir
        self.prefix = prefix

    def parse_mt0(self) -> Dict[str, Union[float, str]]:
        """
        解析 HSPICE 生成的 .mt0 测量文件。
        采用扁平化 Token 扫描策略，无视恶心的换行和排版。
        """
        mt0_path = os.path.join(self.run_dir, f"{self.prefix}.mt0")
        if not os.path.exists(mt0_path):
            print(f"[Warning] Measurement file not found: {mt0_path}")
            return {"status": "error", "message": "mt0 file missing"}

        with open(mt0_path, 'r') as f:
            lines = f.readlines()

        tokens = []
        for line in lines:
            # 过滤掉注释、环境声明和标题行
            if line.startswith('$') or line.startswith('.TITLE'):
                continue
            
            # 清理 HSPICE 的换行续接符 '+'
            clean_line = line.lstrip('+\t\n ')
            if not clean_line:
                continue
                
            # 切分为基础词汇
            tokens.extend(clean_line.split())

        if 'alter#' not in tokens:
            return {"status": "error", "message": "No alter# found in mt0"}

        start_idx = tokens.index('alter#')
        
        # 寻找数据区域的起点（即 alter# 对应的序号，通常是 '1' 或者 '1.000'）
        # 通过正则匹配纯数字或科学计数法
        val_start_idx = -1
        num_pattern = re.compile(r'^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$')
        
        for i in range(start_idx + 1, len(tokens)):
            if num_pattern.match(tokens[i]):
                val_start_idx = i
                break

        if val_start_idx == -1:
            return {"status": "error", "message": "No numerical data found"}

        # 切分表头和数值数组
        headers = tokens[start_idx : val_start_idx]
        data_vals = tokens[val_start_idx : val_start_idx + len(headers)]

        # 映射成字典
        results = {"status": "success"}
        for h, v in zip(headers, data_vals):
            if h == 'alter#':
                continue
            if v.lower() == 'failed':
                results[h] = 'failed'
            else:
                try:
                    results[h] = float(v)
                except ValueError:
                    results[h] = v

        return results
