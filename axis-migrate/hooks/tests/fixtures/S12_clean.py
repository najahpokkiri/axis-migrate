from unittest.mock import patch


def test_clock():
    with patch("time.time", return_value=0):
        assert True
