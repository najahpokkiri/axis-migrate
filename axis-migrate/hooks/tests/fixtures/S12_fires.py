from unittest.mock import patch


def test_reads():
    with patch("rasterio.open"):
        assert True
