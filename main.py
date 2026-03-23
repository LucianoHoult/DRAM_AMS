import argparse
import csv
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List

from netlist_engine.pipeline import NetlistEnginePipeline
from simulation_engine.res_analyzer import LogAnalyzer
from simulation_engine.sim_runner import SimRunner
from stimulus_engine.stimulus_generator import StimulusGenerator
from stimulus_engine.tb_writer import TBWriter
from stimulus_engine.topology_initializer import TopologyInitializer

DEFAULT_CONFIG_FILES = [
    "general_config.json",
    "operation_measurement_pwl_config.json",
    "rc.json",
    "reduction.json",
    "tb_sim.json",
]


def deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并多个 JSON 配置，后加载文件覆盖先加载文件。"""
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged



def _resolve_config_files(config_path: str) -> List[Path]:
    path = Path(config_path)
    if path.is_dir():
        ordered = [path / name for name in DEFAULT_CONFIG_FILES if (path / name).exists()]
        remaining = sorted(
            candidate
            for candidate in path.glob("*.json")
            if candidate not in ordered
        )
        files = ordered + remaining
    else:
        files = [path]

    if not files:
        raise FileNotFoundError(f"No JSON configuration files found under: {config_path}")
    return files



def load_merged_config(config_path: str) -> Dict[str, Any]:
    files = _resolve_config_files(config_path)
    merged: Dict[str, Any] = {}

    for file_path in files:
        with file_path.open("r", encoding="utf-8") as handle:
            current = json.load(handle)
        merged = deep_merge_dicts(merged, current)

    merged.setdefault("config_meta", {})["loaded_files"] = [str(path) for path in files]
    return merged



def ensure_parent_dir(file_path: str) -> None:
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)



def resolve_netlist_input(config: Dict[str, Any]) -> str:
    candidates: Iterable[Any] = (
        config.get("netlist_engine_io", {}).get("input_cdl"),
        config.get("netlist_engine_io", {}).get("mimic_cdl"),
        config.get("netlist_rules", {}).get("mimic_cdl"),
        config.get("project_info", {}).get("mimic_cdl"),
    )
    for candidate in candidates:
        if candidate:
            return str(candidate)
    raise KeyError("Missing mimic CDL path. Please set netlist_engine_io.input_cdl or mimic_cdl in config.")



def resolve_timing_case(config: Dict[str, Any]) -> str:
    timing_cases = config.get("stimulus_generator", {}).get("timing_cases", {})
    if "tRAS_measurement" in timing_cases:
        return "tRAS_measurement"
    if timing_cases:
        return next(iter(timing_cases))
    raise KeyError("No timing_cases defined in stimulus_generator config.")



def run_flow(config_path: str):
    print(f"Loading config from: {config_path}")
    try:
        config = load_merged_config(config_path)
    except FileNotFoundError:
        print(f"Error: Config file or directory not found at {config_path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: Failed to parse JSON config: {exc}")
        sys.exit(1)

    print("Merged config files:")
    for file_path in config.get("config_meta", {}).get("loaded_files", []):
        print(f"  - {file_path}")

    print(">>> Stage 0: Resolving mimic CDL input and outputs...")
    nl_input = resolve_netlist_input(config)
    nl_output = config["testbench_builder"]["includes"]["netlist"]
    ensure_parent_dir(nl_output)

    print(">>> Stage 1: Running Netlist Engine (Parse -> RC Insert -> Reduce -> Write)...")
    nl_pipeline = NetlistEnginePipeline(
        input_cdl_path=nl_input,
        config=config,
        output_cdl_path=nl_output,
    )
    nl_pipeline.run()

    print(">>> Stage 2: Generating Stimulus and Measurements...")
    stim_gen = StimulusGenerator(config)
    case_name = resolve_timing_case(config)
    stim_text = stim_gen.process_case(case_name)
    stim_output = config["testbench_builder"]["includes"]["stimulus"]
    ensure_parent_dir(stim_output)
    with open(stim_output, "w", encoding="utf-8") as handle:
        handle.write(stim_text)

    print(">>> Stage 3: Generating Initial Conditions (.ic)...")
    topo_init = TopologyInitializer(config)
    topo_init.generate(output_path=config["testbench_builder"]["includes"]["init_cond"])

    print(">>> Stage 4: Assembling Top-level Testbench...")
    tb_writer = TBWriter(config)
    tb_writer.generate()

    tb_path = config["testbench_builder"]["output_tb_path"]
    print(f"\nFlow completed successfully. Ready to simulate: {tb_path}")

    print("\n>>> Stage 5: Executing Simulation...")
    sim_runner = SimRunner(config)
    sim_results = sim_runner.run_all([tb_path])

    print("\n>>> Stage 6: Extracting Results & Generating Report...")
    report_data = []
    for res in sim_results:
        run_dir = os.path.dirname(res.get("output_prefix", ""))
        prefix = os.path.basename(res.get("output_prefix", ""))
        tb_name = os.path.basename(res["tb"])
        row_data = {"Testbench": tb_name, "Sim_Status": "PASS" if res["success"] else "FAIL"}

        if res["success"]:
            analyzer = LogAnalyzer(run_dir, prefix)
            metrics = analyzer.parse_mt0()
            if metrics.get("status") == "success":
                for key, value in metrics.items():
                    if key != "status":
                        row_data[key] = value
            else:
                row_data["Sim_Status"] = "EXTRACT_ERROR"
                row_data["Error_Msg"] = metrics.get("message", "")

        report_data.append(row_data)

    if report_data:
        all_keys = set().union(*(row.keys() for row in report_data))
        base_headers = ["Testbench", "Sim_Status", "Error_Msg"]
        metric_headers = sorted(key for key in all_keys if key not in base_headers)
        header_order = [header for header in base_headers if header in all_keys] + metric_headers

        report_path = os.path.join(os.path.dirname(tb_path), "timing_evaluation_report.csv")
        ensure_parent_dir(report_path)
        with open(report_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header_order)
            writer.writeheader()
            writer.writerows(report_data)

        print(f"Flow completed. Summary report saved to: {report_path}")
        print("\n--- Quick Summary ---")
        row_format = "{:<25} | {:<12}"
        if metric_headers:
            row_format += " | " + " | ".join(["{:<15}"] * len(metric_headers))
        print(row_format.format("Testbench", "Status", *metric_headers))
        print("-" * (40 + 18 * len(metric_headers)))
        for row in report_data:
            metric_vals = [str(row.get(header, "-")) for header in metric_headers]
            print(row_format.format(row.get("Testbench", ""), row.get("Sim_Status", ""), *metric_vals))
    else:
        print("No valid data extracted for reporting.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DRAM Array Digital Twin Automation Flow")
    parser.add_argument(
        "-c",
        "--config",
        default="config",
        help="Path to a master JSON config file or a directory containing split JSON configs",
    )
    args = parser.parse_args()

    run_flow(args.config)
