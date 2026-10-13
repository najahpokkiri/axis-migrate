try:
    from exactextract import exact_extract as zonal
except ImportError:
    from rasterstats import zonal_stats as zonal
