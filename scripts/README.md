# Developer scripts

Standalone utilities — **not** part of the Django app. Run them directly
with Python; edit the hard-coded paths inside before use.

- `extract_values.py` — walks a raster folder and writes a `values.json`
  of per-file min/max values (the file the app reads for layer ranges).
- `visualize.py` — quick matplotlib preview of a single raster.
