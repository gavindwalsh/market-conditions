"""Parser test for EDGAR form.idx (§7.6). The 2026-07-08 bug: header-offset
slicing truncated dates to month precision (every filing landed on the 1st).
This test pins the date-anchored parser against a realistic misaligned block.
Run: python tests/test_edgar_parse.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.pull.edgar import _parse_form_idx  # noqa: E402

SAMPLE = """Description:           Form Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    June 30, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------
10-K        WIDGETS INC                                                   123456      2026-06-12  edgar/data/123456/0001193125-26-000001.txt
S-1         Cuprina Holdings (Cayman) LTD                                 1995704     2026-06-15  edgar/data/1995704/0001213900-26-055027.txt
S-1/A       AB Active  Double Space Corp                                  1496608     2026-06-17  edgar/data/1496608/0001213900-26-055123.txt
F-1         Overseas Lister PLC                                           2000001     2026-06-20  edgar/data/2000001/0001104659-26-088888.txt
485APOS     360 Funds                                                     1319067     2026-06-01  edgar/data/1319067/0001580642-26-003333.txt
N-1A        Brand New Trust                                               2222222     2026-06-27  edgar/data/2222222/0001999999-26-000042.txt
S-1MEF      NotWanted Corp                                                3333333     2026-06-30  edgar/data/3333333/0001999999-26-000099.txt
"""


def test_parse():
    df = _parse_form_idx(SAMPLE)
    # 5 target-form rows (10-K and S-1MEF excluded)
    assert len(df) == 5, df
    assert set(df["form"]) == {"S-1", "S-1/A", "F-1", "485APOS", "N-1A"}
    # exact dates preserved — the truncation bug made these all day 01
    s1 = df[df["form"] == "S-1"].iloc[0]
    assert s1["date"] == "2026-06-15" and s1["cik"] == "1995704"
    assert s1["company"] == "Cuprina Holdings (Cayman) LTD"
    # company with internal double spaces still parses (CIK anchored on date)
    s1a = df[df["form"] == "S-1/A"].iloc[0]
    assert s1a["cik"] == "1496608" and s1a["date"] == "2026-06-17"
    # only one first-of-month date in this block (the genuine 485APOS one)
    assert (df["date"].str.endswith("-01")).sum() == 1


if __name__ == "__main__":
    test_parse()
    print("PASS test_parse (edgar form.idx)")
