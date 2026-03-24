# execution_engine/sim_runner.py
import os
import time
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List

class SimRunner:
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("sim_runner", {})
        self.mode = self.config.get("execution_mode", "local")
        self.max_workers = self.config.get("max_parallel_jobs", 4)
        self.timeout = self.config.get("timeout_seconds", 3600)

    def _verify_lis_success(self, lis_path: str) -> bool:
        """扫描 .lis 文件末尾，确认是否包含 job concluded 且无 fatal error"""
        if not os.path.exists(lis_path):
            return False
        
        # 仅读取最后 50 行以提高效率
        try:
            with open(lis_path, 'r') as f:
                lines = f.readlines()
                tail_lines = "".join(lines[-50:]).lower()
                if "job concluded" in tail_lines and "error" not in tail_lines:
                    return True
        except Exception:
            pass
        return False

    def _run_local(self, sp_path: str, run_dir: str, output_prefix: str) -> bool:
        """本地执行 HSPICE"""
        exe = self.config.get("local_settings", {}).get("executable", "hspice")
        cmd = [exe, "-i", sp_path, "-o", output_prefix]
        
        log_path = os.path.join(run_dir, "runner_stdout.log")
        
        try:
            with open(log_path, 'w') as log_file:
                subprocess.run(
                    cmd, cwd=run_dir, stdout=log_file, stderr=subprocess.STDOUT, 
                    timeout=self.timeout, check=True
                )
            return self._verify_lis_success(f"{output_prefix}.lis")
        except subprocess.TimeoutExpired:
            print(f"[Error] Local run timeout for {sp_path}")
            return False
        except subprocess.CalledProcessError:
            print(f"[Error] Local run failed for {sp_path}")
            return False
            
    def _run_cluster(self, sp_path: str, run_dir: str, output_prefix: str) -> bool:
        """通过集群系统提交任务并轮询状态"""
        c_settings = self.config.get("cluster_settings", {})
        submit_cmd_template = c_settings.get("submit_cmd", "asub")
        poll_cmd_base = c_settings.get("poll_cmd", "ajob")
        poll_interval = c_settings.get("poll_interval_seconds", 15)
        job_id_regex = c_settings.get("job_id_regex", r"(?m)^(\d+)\s+Submit")
        job_done_keyword = c_settings.get("job_done_keyword", "query successfully, no matches.")

        job_name = os.path.basename(output_prefix)
        # asub 命令组装
        full_submit_cmd = f"{submit_cmd_template.format(job_name=job_name)} hspice -i {sp_path} -o {output_prefix}"

        # 1. 提交任务
        try:
            submit_res = subprocess.run(
                full_submit_cmd, shell=True, cwd=run_dir, 
                capture_output=True, text=True, check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"[Error] Failed to submit job: {full_submit_cmd}\n{e.stderr}")
            return False

        # 2. 精准提取 Job ID
        match = re.search(job_id_regex, submit_res.stdout)
        if not match:
            print(f"[Error] Could not extract Job ID from stdout:\n{submit_res.stdout}")
            return False
        job_id = match.group(1)
        print(f"[Info] Submitted {job_name} with Job ID: {job_id}")

        # 3. 轮询任务状态
        start_time = time.time()
        # 组装查询命令，直接查该 ID 可以减小服务器调度器压力
        poll_cmd = f"{poll_cmd_base} | grep {job_id}"
        
        while True:
            if time.time() - start_time > self.timeout:
                print(f"[Error] Cluster job {job_id} timeout. Killing job...")
                # 超时强杀逻辑预留
                subprocess.run(f"akill {job_id}", shell=True, capture_output=True)
                return False
                
            time.sleep(poll_interval)
            
            # 执行 ajob 查询
            poll_res = subprocess.run(
                poll_cmd, shell=True, 
                capture_output=True, text=True
            )
            
            stdout_text = poll_res.stdout.strip()
            
            # 判断逻辑：如果 grep 抓不到任何内容，或者系统明确返回了 no matches 关键字，均视为退出队列
            if not stdout_text or job_done_keyword in stdout_text:
                break

        # 4. 确认结果合法性
        return self._verify_lis_success(f"{output_prefix}.lis")
    
    def _worker(self, sp_path: str) -> Dict[str, Any]:
        """单任务调度入口"""
        run_dir = os.path.dirname(os.path.abspath(sp_path)) or "."
        base_name = os.path.splitext(os.path.basename(sp_path))[0]
        output_prefix = os.path.join(run_dir, base_name)
        
        if self.mode == "cluster":
            success = self._run_cluster(sp_path, run_dir, output_prefix)
        else:
            success = self._run_local(sp_path, run_dir, output_prefix)
            
        return {"tb": sp_path, "success": success, "output_prefix": output_prefix}

    def run_all(self, sp_file_list: List[str]) -> List[Dict[str, Any]]:
        """并发调度入口"""
        print(f"--- Starting Simulation Execution Engine ---")
        print(f"Mode: {self.mode.upper()}, Max Workers: {self.max_workers}, Total Jobs: {len(sp_file_list)}")
        
        results = []
        # 使用线程池进行并发调度
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_sp = {executor.submit(self._worker, sp): sp for sp in sp_file_list}
            
            # 收集结果
            for future in as_completed(future_to_sp):
                sp_path = future_to_sp[future]
                try:
                    res = future.result()
                    results.append(res)
                    status = "PASS" if res["success"] else "FAIL"
                    print(f"[{status}] Completed: {sp_path}")
                except Exception as exc:
                    print(f"[FAIL] {sp_path} generated an exception: {exc}")
                    results.append({"tb": sp_path, "success": False})
                    
        return results
