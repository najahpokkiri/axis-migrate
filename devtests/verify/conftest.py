"""Lux devtests for compare.py (06 §6.2, L1-L18). Every mutation happens on a tmp copy; the fixture is never touched."""
import os
import sys
from pathlib import Path

os.environ["GDAL_PAM_ENABLED"] = "NO"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

import lux_lib as L  # noqa: E402
import axis_common as ac  # noqa: E402


@pytest.fixture(scope="session")
def fixture_hash():
    return ac.tree_hash(L.FIXTURE)


@pytest.fixture(scope="session", autouse=True)
def fixture_untouched(fixture_hash):
    yield
    assert ac.tree_hash(L.FIXTURE) == fixture_hash, "a devtest modified fixtures/lux-exposure-legacy"


@pytest.fixture(scope="session")
def work(tmp_path_factory):
    """Session work dir with the clean independent implementation in A1_clean (built once)."""
    import lux_independent as I
    w = tmp_path_factory.mktemp("luxwork")
    I.run_all(L.FIXTURE / "03_INPUT", w / "A1_clean")
    return w


_VARIANTS = {}


@pytest.fixture(scope="session")
def variant(work):
    """variant(name) -> output tree of a wrong (or legitimate) recipe, chained downstream from its step."""
    import measure_lux as M

    def get(name):
        if name not in _VARIANTS:
            spec = M.WRONG.get(name) or M.LEGIT.get(name)
            start, kw = spec[0], spec[1]
            fm = spec[3] if len(spec) > 3 else None
            _VARIANTS[name] = M.run_variant(work, name, start, kw, fm)
        return _VARIANTS[name]
    return get


@pytest.fixture
def legacy_copy(tmp_path):
    return L.copy_fixture(tmp_path)
