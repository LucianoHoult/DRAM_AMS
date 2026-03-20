# main_pipeline.py
import json
import argparse
import sys
import os
import csv

from netlist_engine.pipeline import NetlistEnginePipeline
from stimulus_engine.stimulus_generator import StimulusGenerator
from stimulus_engine.topology_initializer import TopologyInitializer
from tb_writer import TBWriter
from execution_engine.sim_runner import SimRunner
from execution_engine.log_analyzer import LogAnalyzer

def run_flow(config_path: str):
    print(f"Loading config from: {config_path}")
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)

    # 1. 网表处理 (Netlist Engine)
    # 此处假设 config 中包含 netlist_engine 需要的输入输出路径
    print(">>> Stage 1: Running Netlist Engine (Parse -> RC Insert -> Reduce -> Write)...")
    # 为了兼容之前的代码，这里需要从 config 中提取 netlist 相关的 I/O
    nl_input = config.get("netlist_engine_io", {}).get("input_cdl", "input.cdl")
    nl_output = config["testbench_builder"]["includes"]["netlist"]
    
    nl_pipeline = NetlistEnginePipeline(
        input_cdl_path=nl_input,
        config=config,
        output_cdl_path=nl_output
    )
    nl_pipeline.run()

    # 2. 激励与测量生成 (Stimulus & Measure)
    print(">>> Stage 2: Generating Stimulus and Measurements...")
    stim_gen = StimulusGenerator(config)
    # 假设我们要跑 config 中定义的 "tRAS_measurement"
    stim_text = stim_gen.process_case("tRAS_measurement")
    stim_output = config["testbench_builder"]["includes"]["stimulus"]
    with open(stim_output, 'w') as f:
        f.write(stim_text)

    # 3. 阵列存储初始化 (Topology Initializer)
    print(">>> Stage 3: Generating Initial Conditions (.ic)...")
    topo_init = TopologyInitializer(config)
    topo_init.generate(output_path=config["testbench_builder"]["includes"]["init_cond"])

    # 4. 顶层 TB 组装 (Testbench Writer)
    print(">>> Stage 4: Assembling Top-level Testbench...")
    tb_writer = TBWriter(config)
    tb_writer.generate()

    print(f"\nFlow completed successfully. Ready to simulate: {config['testbench_builder']['output_tb_path']}")
    
    # 假设 Stage 4 生成的顶层 TB 路径
    tb_path = config["testbench_builder"]["output_tb_path"]
    
    # --- Stage 5: Execution (仿真调度) ---
    print("\n>>> Stage 5: Executing Simulation...")
    sim_runner = SimRunner(config)
    # 实际项目中这里可能是一个 tb_path 的列表（多 Corner/Case 并发）
    sim_results = sim_runner.run_all([tb_path])

    # --- Stage 6: Post-processing (结果提取与报告) ---
    print("\n>>> Stage 6: Extracting Results & Generating Report...")
    report_data = []
    
    for res in sim_results:
        run_dir = os.path.dirname(res["output_prefix"])
        prefix = os.path.basename(res["output_prefix"])
        tb_name = os.path.basename(res["tb"])
        
        row_data = {"Testbench": tb_name, "Sim_Status": "PASS" if res["success"] else "FAIL"}
        
        if res["success"]:
            analyzer = LogAnalyzer(run_dir, prefix)
            metrics = analyzer.parse_mt0()
            
            if metrics.get("status") == "success":
                # 合并测量指标到当前行数据中
                for k, v in metrics.items():
                    if k != "status":
                        row_data[k] = v
            else:
                row_data["Sim_Status"] = "EXTRACT_ERROR"
                row_data["Error_Msg"] = metrics.get("message", "")
                
        report_data.append(row_data)

    # 汇总写入 CSV
    if report_data:
        # 提取所有出现过的列名（处理不同 TB 可能测了不同指标的情况）
        all_keys = set().union(*(d.keys() for d in report_data))
        
        # 强制排序列名，保证基础信息在前，测量数据在后
        base_headers = ["Testbench", "Sim_Status", "Error_Msg"]
        metric_headers = sorted([k for k in all_keys if k not in base_headers])
        header_order = [h for h in base_headers if h in all_keys] + metric_headers
        
        report_path = os.path.join(os.path.dirname(tb_path), "timing_evaluation_report.csv")
        
        with open(report_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=header_order)
            writer.writeheader()
            writer.writerows(report_data)
            
        print(f"Flow completed. Summary report saved to: {report_path}")
        
        # 在终端打印简易表格供工程师快速确认
        print("\n--- Quick Summary ---")
        row_format = "{:<25} | {:<12} | " + " | ".join(["{:<15}"] * len(metric_headers))
        print(row_format.format("Testbench", "Status", *metric_headers))
        print("-" * (40 + 18 * len(metric_headers)))
        for row in report_data:
            metric_vals = [str(row.get(h, "-")) for h in metric_headers]
            print(row_format.format(row.get("Testbench", ""), row.get("Sim_Status", ""), *metric_vals))
    else:
        print("No valid data extracted for reporting.")
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DRAM Array Digital Twin Automation Flow")
    parser.add_argument("-c", "--config", required=True, help="Path to the master JSON configuration file")
    args = parser.parse_args()
    
    run_flow(args.config)
