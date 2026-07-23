# Nigeria Forest Monitor

A research prototype for detecting unusual Sentinel-1 SAR changes in the Old Oyo–Kwara–Kainji forest corridor and combining them with historical incident proximity for analyst review.

This software produces decision-support indicators. A high risk score is **not evidence of hostile activity** and must be corroborated before operational use.

## Architecture

```text
Sentinel-1 GRD
  -> linear-scale speckle filtering
  -> seasonal/historical baseline
  -> log-ratio change detection
  -> memory-safe SAR feature classifier
  -> grid-level signal fusion
  -> HTML risk map + PDF alert report
```

The classifier uses six compact features (`VV`, `VH`, local means, and local standard deviations). Earth Engine computes them server-side at a configurable analysis scale with class-aware tiling. This avoids downloading raster patches or evaluating a 10 m texture stack over the entire AOI in an interactive request.

## Project layout

```text
configs/config.yaml             validated project parameters
src/config.py                   root-aware configuration and paths
src/pipeline.py                 shared end-to-end orchestration and CLI
src/ingestion/                  Earth Engine, ACLED, and grid utilities
src/preprocessing/              speckle filters, baselines, normalisation
src/detection/                  change detector, feature model, risk fusion
src/dashboard/                  Folium map and ReportLab PDF outputs
notebooks/forest_monitor.ipynb   unified EDA, detection, model, and risk workflow
tests/                          offline regression tests
```

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m ipykernel install --user --name nigeria-forest-monitor --display-name "Nigeria Forest Monitor (.venv)"
earthengine authenticate
```

Create `.env` in the repository root:

```dotenv
GEE_PROJECT=your-google-cloud-project-id
ACLED_EMAIL=your-myacled-email
ACLED_PASSWORD=your-myacled-password
```

ACLED programmatic access uses OAuth credentials. The fetcher follows the documented `year_where=BETWEEN` filter and paginates beyond the 5,000-row response limit.

## Validate the local code

These checks do not contact Earth Engine or ACLED:

```powershell
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Run

Open `notebooks/forest_monitor.ipynb`, select the `.venv (3.11.9)` / `Nigeria Forest Monitor (.venv)` kernel, then use **Restart Kernel and Run All Cells**. Markdown sections separate setup, EDA, change detection, model training, and risk outputs while sharing the same in-memory baseline and monitoring results.

Or run the shared CLI from the repository root:

```powershell
.venv\Scripts\python.exe -m src.pipeline --start 2025-01-01 --end 2025-02-01 --zone old_oyo_core
```

Outputs are written under `data/processed/`, `models/`, and `reports/` regardless of whether execution starts in the root directory or `notebooks/`.

## Configuration notes

- `sentinel1.resolution_m` is the source/display resolution.
- Native 10 m change pixels are max-aggregated to `classifier.sampling_scale_m` (100 m by default), then requested with tiled stratified sampling rather than a full-resolution count.
- `risk.weights` must sum to 1.0 and are validated when configuration loads.
- Missing classifier weights or cached ACLED data do not crash the pipeline; the corresponding signal is set to zero with a warning.

## Data and model limitations

- Notebook 03 uses deterministic weak labels only as a software demonstration. An operational model requires reviewed training labels and independent validation.
- Sentinel-1 change can reflect flooding, soil moisture, agriculture, fire, or acquisition geometry—not only human activity.
- The system should support analyst prioritisation, not automated enforcement or targeting.

## External documentation

- [Google Earth Engine Python setup](https://developers.google.com/earth-engine/guides/python_install)
- [Earth Engine sampling API](https://developers.google.com/earth-engine/apidocs/ee-image-sample)
- [ACLED API getting started](https://acleddata.com/api-documentation/getting-started)