import os

from simulation_engine.sim_runner import SimRunner


def test_verify_lis_success_distinguishes_success_and_error(tmp_path):
    runner = SimRunner({"sim_runner": {}})
    ok = tmp_path / "ok.lis"
    ok.write_text("header\njob concluded\n", encoding="utf-8")
    bad = tmp_path / "bad.lis"
    bad.write_text("header\nfatal error happened\njob concluded\n", encoding="utf-8")

    assert runner._verify_lis_success(str(ok)) is True
    assert runner._verify_lis_success(str(bad)) is False
    assert runner._verify_lis_success(str(tmp_path / "missing.lis")) is False


def test_run_local_executes_dummy_simulator_and_collects_results(tmp_path):
    dummy_sim = tmp_path / "dummy_hspice"
    dummy_sim.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n\n"
        "args = sys.argv[1:]\n"
        "sp_path = pathlib.Path(args[args.index('-i') + 1])\n"
        "output_prefix = pathlib.Path(args[args.index('-o') + 1])\n"
        "output_prefix.with_suffix('.lis').write_text('job concluded\\n', encoding='utf-8')\n"
        "output_prefix.with_suffix('.mt0').write_text('alter# delay\\n1 9.9e-10\\n', encoding='utf-8')\n"
        "print(f'running {sp_path.name}')\n",
        encoding="utf-8",
    )
    os.chmod(dummy_sim, 0o755)
    sp_path = tmp_path / "tb_case.sp"
    sp_path.write_text("* dummy tb\n.end\n", encoding="utf-8")

    runner = SimRunner(
        {
            "sim_runner": {
                "execution_mode": "local",
                "timeout_seconds": 5,
                "local_settings": {"executable": str(dummy_sim)},
            }
        }
    )

    result = runner._run_local(str(sp_path), str(tmp_path), str(tmp_path / "tb_case"))
    assert result is True
    assert (tmp_path / "runner_stdout.log").exists()
    assert (tmp_path / "tb_case.lis").read_text(encoding="utf-8").strip() == "job concluded"

    batch_result = runner.run_all([str(sp_path)])
    assert batch_result == [
        {"tb": str(sp_path), "success": True, "output_prefix": str(tmp_path / "tb_case")}
    ]
