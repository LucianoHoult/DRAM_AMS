from pathlib import Path

from simulation_engine.res_analyzer import LogAnalyzer


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_parse_mt0_handles_wrapped_lines_and_failed_tokens(tmp_path):
    mt0_path = tmp_path / "sample.mt0"
    mt0_path.write_text((FIXTURE_DIR / "dummy_result_wrapped.mt0").read_text(encoding="utf-8"), encoding="utf-8")

    result = LogAnalyzer(str(tmp_path), "sample").parse_mt0()

    assert result["status"] == "success"
    assert result["tRAS_core"] == 1.234e-09
    assert result["t_sense_margin"] == "failed"
    assert result["note"] == "PASS"


def test_parse_mt0_reports_missing_or_malformed_files(tmp_path):
    missing = LogAnalyzer(str(tmp_path), "does_not_exist").parse_mt0()
    assert missing == {"status": "error", "message": "mt0 file missing"}

    bad_path = tmp_path / "broken.mt0"
    bad_path.write_text((FIXTURE_DIR / "dummy_result_invalid.mt0").read_text(encoding="utf-8"), encoding="utf-8")
    malformed = LogAnalyzer(str(tmp_path), "broken").parse_mt0()
    assert malformed == {"status": "error", "message": "No alter# found in mt0"}
