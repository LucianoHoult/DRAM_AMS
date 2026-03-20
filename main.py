# main_pipeline.py
import json
import argparse
import sys

from netlist_engine.pipeline import NetlistEnginePipeline
from stimulus_engine.stimulus_generator import StimulusGenerator
from stimulus_engine.topology_initializer import TopologyInitializer
from tb_writer import TBWriter

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DRAM Array Digital Twin Automation Flow")
    parser.add_argument("-c", "--config", required=True, help="Path to the master JSON configuration file")
    args = parser.parse_args()
    
    run_flow(args.config)
