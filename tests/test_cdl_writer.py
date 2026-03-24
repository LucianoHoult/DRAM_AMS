from netlist_engine.cdl_parser import Instance, NetlistIR, Subckt
from netlist_engine.cdl_writer import CDLWriter


def test_writer_wraps_long_lines_and_serializes_top_level_instances(tmp_path):
    ir = NetlistIR(
        subckts={
            "LONG_SUBCKT": Subckt(
                name="LONG_SUBCKT",
                ports=["IN", "OUT", "VSS"],
                instances={
                    "X_LONG": Instance(
                        name="X_LONG",
                        ref_model="VERY_LONG_MODEL_NAME",
                        ports=["NET_A", "NET_B", "NET_C", "NET_D", "NET_E", "NET_F", "NET_G"],
                        params={"W": "1u", "L": "20n"},
                    )
                },
            )
        },
        top_level_instances={
            "X_TOP": Instance(name="X_TOP", ref_model="LONG_SUBCKT", ports=["A", "B", "0"])
        },
    )

    out_path = tmp_path / "wrapped_output.cdl"
    writer = CDLWriter(ir, max_line_length=36)
    writer.write(str(out_path))

    text = out_path.read_text(encoding="utf-8")
    assert ".SUBCKT LONG_SUBCKT IN OUT VSS" in text
    assert "\n+ NET_E" in text or "\n+ NET_F" in text
    assert "* TOP LEVEL INSTANCES" in text
    assert "X_TOP A B 0 LONG_SUBCKT" in text
