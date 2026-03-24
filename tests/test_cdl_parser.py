# tests/test_cdl_parser.py
import os
import pytest
from netlist_engine.cdl_parser import CDLParser

def normalize_cdl_string(cdl_str: str) -> set:
    """
    清洗并标准化 CDL 字符串，用于无视排版格式的比对。
    返回逻辑行的集合（Set），这样甚至可以无视语句的绝对先后顺序（在同一个 scope 内）。
    """
    lines = cdl_str.split('\n')
    normalized_lines = []
    current_line = ""
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('*'):
            continue
        if stripped.startswith('+'):
            current_line += " " + stripped[1:].strip()
        else:
            if current_line:
                # 统一转大写，并将多个空格替换为单个空格，消除格式差异
                clean_line = " ".join(current_line.upper().split())
                normalized_lines.append(clean_line)
            current_line = stripped
            
    if current_line:
        clean_line = " ".join(current_line.upper().split())
        normalized_lines.append(clean_line)
        
    return set(normalized_lines)

def test_cdl_roundtrip():
    # 1. 确定 mock_dram_bank.cdl 的路径
    current_dir = os.path.dirname(__file__)
    fixture_path = os.path.join(current_dir, 'fixtures', 'mock_dram_bank.cdl')
    
    # 2. 读取原始文件内容
    with open(fixture_path, 'r') as f:
        original_content = f.read()
        
    # 3. 运行解析器 -> 生成 IR -> 再 Dump 回字符串
    parser = CDLParser(fixture_path)
    parser.parse()
    generated_content = parser.dump_to_string()
    
    # 4. 执行标准化比对
    original_set = normalize_cdl_string(original_content)
    generated_set = normalize_cdl_string(generated_content)
    
    # 如果两者不一致，找出差异并报错
    missing_in_generated = original_set - generated_set
    extra_in_generated = generated_set - original_set
    
    assert not missing_in_generated, f"Generated CDL is missing: {missing_in_generated}"
    assert not extra_in_generated, f"Generated CDL has extra unexpected lines: {extra_in_generated}"
