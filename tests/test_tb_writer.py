from stimulus_engine.tb_writer import TBWriter


def test_tb_writer_generates_expected_top_level_testbench(tmp_path):
    out_path = tmp_path / "sim" / "top_tb.sp"
    config = {
        "testbench_builder": {
            "output_tb_path": str(out_path),
            "includes": {
                "tech_lib": "models/tt.lib",
                "netlist": "output/netlist.cdl",
                "stimulus": "output/stimulus.sp",
                "init_cond": "output/init.ic",
            },
            "global_options": ["post", "probe"],
            "temperature_c": -40,
            "top_instance": {
                "name": "X_BANK",
                "ref_model": "DRAM_BANK",
                "ports": ["WL<0>", "BL<0>", "VDD", "VSS"],
            },
        }
    }

    TBWriter(config).generate()
    text = out_path.read_text(encoding="utf-8")

    assert ".option post probe" in text
    assert ".temp -40" in text
    assert ".include 'models/tt.lib'" in text
    assert ".include 'output/netlist.cdl'" in text
    assert "X_BANK WL<0> BL<0> VDD VSS DRAM_BANK" in text
    assert text.rstrip().endswith(".end")
