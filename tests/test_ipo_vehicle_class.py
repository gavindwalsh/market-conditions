"""§2.4.4 vehicle-class name tagging.

Guards the 2026-08-18 bug: `_SPAC_RE` carried a bare `SPAC` alternative, which
matches inside "SPACE" and "AEROSPACE", so every space company was classified a
blank-check vehicle. Three priced 2026 deals were affected, one of them a
$75bn raise — large enough to distort operating-company issuance on its own.
Run: python tests/test_ipo_vehicle_class.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.pull.ipo import _vehicle_class  # noqa: E402

BIG = 50_000_000   # share count above the bank/thrift heuristic's 10M gate


def test_space_companies_are_not_spacs():
    """The actual regression — these are real priced 2026 deals."""
    for name in ("SPACE EXPLORATION TECHN-CL A",
                 "APPLIED AEROSPACE & DEFENSE",
                 "YORK SPACE SYSTEMS INC",
                 "ROCKET LAB SPACE SYSTEMS",
                 "INTUITIVE MACHINES SPACE",
                 "AEROSPACE HOLDINGS"):
        assert _vehicle_class(name, BIG) == "Operating Co", name
    print("PASS space/aerospace names are operating companies")


def test_real_spacs_still_tagged():
    """The fix must not weaken genuine detection."""
    for name in ("CHURCHILL CAPITAL CORP VII",
                 "AJAX ACQUISITION CORP",
                 "PONO CAPITAL CORP",
                 "XYZ MERGER CORP",
                 "ABC BLANK CHECK CO",
                 "SOME SPAC INC",          # standalone word still matches
                 "SPAC HOLDINGS"):
        assert _vehicle_class(name, BIG) == "SPAC", name
    print("PASS genuine blank-check vehicles still tagged")


def test_other_classes_unaffected():
    for name in ("VANGUARD TOTAL BOND FUND", "SPDR GOLD TRUST"):
        assert _vehicle_class(name, BIG) == "Fund/Vehicle", name
    # bank heuristic is share-gated: small float -> Bank/Thrift, large -> operating
    assert _vehicle_class("HOMETOWN BANCORP", 1_000_000) == "Bank/Thrift"
    assert _vehicle_class("HOMETOWN BANCORP", BIG) == "Operating Co"
    assert _vehicle_class("PLAIN WIDGETS INC", BIG) == "Operating Co"
    print("PASS fund / bank / operating classes unaffected")


if __name__ == "__main__":
    test_space_companies_are_not_spacs()
    test_real_spacs_still_tagged()
    test_other_classes_unaffected()
    print("\nAll vehicle-class tests passed.")
