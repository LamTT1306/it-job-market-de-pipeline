import os
import sys
import types
import hashlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

for mod_name in ("boto3", "psycopg2", "psycopg2.extras"):
    if mod_name not in sys.modules:
        try:
            __import__(mod_name)
        except ImportError:
            dummy = types.ModuleType(mod_name)
            dummy.execute_values = None
            sys.modules[mod_name] = dummy

from transform.bronze_to_silver import (
    hash_pii_sha256,
    mask_phone,
    parse_salary_to_vnd,
    USD_TO_VND_RATE,
)


def test_hash_pii_sha256():
    expected = hashlib.sha256("hr@company.com".encode("utf-8")).hexdigest()
    assert hash_pii_sha256("  HR@Company.com ") == expected
    assert hash_pii_sha256("") is None
    assert hash_pii_sha256(None) is None


def test_mask_phone():
    assert mask_phone("0909888777") == "090****777"
    assert mask_phone("000") == "***"
    assert mask_phone(None) == "***"


def test_parse_salary_to_vnd():
    assert parse_salary_to_vnd("20 - 35 triệu") == (20_000_000, 35_000_000)
    assert parse_salary_to_vnd("$2,000 - $3,000 / month") == (
        2000 * USD_TO_VND_RATE,
        3000 * USD_TO_VND_RATE,
    )
    assert parse_salary_to_vnd("$60,000 - $120,000 / year") == (
        5000 * USD_TO_VND_RATE,
        10000 * USD_TO_VND_RATE,
    )
    assert parse_salary_to_vnd(None) == (15_000_000, 25_000_000)


if __name__ == "__main__":
    test_hash_pii_sha256()
    test_mask_phone()
    test_parse_salary_to_vnd()
    print("All transformation tests passed.")
