# Nigeria Forest Monitor

> SAR-based forest anomaly detection for counter-terrorism and security monitoring across the Old Oyo National Park corridor, Southwest Nigeria.

## Background

In May 2026, armed bandits kidnapped 46 students and teachers from schools in Oriire LGA, Oyo State, and retreated into the Old Oyo National Park. Security sources confirmed the attackers used a forest corridor linking **Kainji National Park (Niger State) → Kwara State → Old Oyo National Park (Oyo State)** — a largely unmonitored green belt spanning three states.

This project proposes an ML-powered satellite monitoring system to detect unusual human activity in this corridor before and during such incidents, providing actionable intelligence to security agencies.

---

## Approach

**Primary data source:** Sentinel-1 SAR (Synthetic Aperture Radar)
- Free via Google Earth Engine
- Penetrates cloud cover — critical for Nigeria's forest belt
- 5–12 day revisit time, 10m resolution

**Pipeline:**
```
Sentinel-1 SAR → Preprocessing → Change Detection → CNN Classifier → Risk Scorer → Alert Dashboard
```

---

## Project Structure

```
nigeria-forest-monitor/
├── configs/
│   └── config.yaml          # All parameters — AOI, dates, model settings
├── data/
│   ├── raw/                 # Downloaded SAR imagery (gitignored)
│   ├── processed/           # Filtered, calibrated composites
│   ├── labels/              # Training labels
│   └── acled/               # ACLED incident data for Nigeria
├── models/                  # Saved model weights (gitignored)
├── notebooks/
│   ├── 01_eda.ipynb
│   ├── 02_change_detection.ipynb
│   ├── 03_model.ipynb
│   └── 04_risk_map.ipynb
├── src/
│   ├── ingestion/           # GEE download, ACLED fetch, grid builder
│   ├── preprocessing/       # Speckle filter, baseline, normalise
│   ├── detection/           # Change detection, CNN classifier, risk scorer
│   └── dashboard/           # Map builder, PDF alert report
├── reports/                 # Generated alerts and figures (gitignored)
├── requirements.txt
└── .gitignore
```

---

## Setup

### 1. Clone and create environment

```bash
git clone https://github.com/yourusername/nigeria-forest-monitor.git
cd nigeria-forest-monitor
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### 2. Authenticate Google Earth Engine

```bash
earthengine authenticate
```

This opens a browser — sign in with a Google account that has GEE access (free at [signup.earthengine.google.com](https://signup.earthengine.google.com)).

### 3. Set up environment variables

Create a `.env` file in the project root:

```
GEE_PROJECT=your-gee-project-id
ACLED_API_KEY=your-acled-api-key
```

### 4. Run the pipeline

Start with the notebooks in order:
```
01_eda.ipynb             → explore the AOI and baseline imagery
02_change_detection.ipynb → build and validate the change detector
03_model.ipynb           → train the CNN patch classifier
04_risk_map.ipynb        → generate risk scores and alerts
```

---

## Target Region

| Zone | States | Key Area |
|---|---|---|
| Core | Oyo | Old Oyo National Park |
| Buffer | Kwara | Kaiama / Woro corridor |
| Source | Niger | Kainji National Park edge |

---

## Data Sources

| Source | Type | Access |
|---|---|---|
| Sentinel-1 GRD | SAR imagery | Free — Google Earth Engine |
| ACLED | Incident records | Free — [acleddata.com](https://acleddata.com) |
| SRTM DEM | Terrain elevation | Free — Google Earth Engine |

---

## Status

- [x] Repo structure
- [x] Config
- [ ] GEE ingestion pipeline
- [ ] Preprocessing
- [ ] Change detection
- [ ] CNN classifier
- [ ] Risk mapper
- [ ] Alert dashboard

---

## Proposal Target

Oyo State Ministry of Security / Nigerian Army Intelligence — demonstrating proactive forest corridor monitoring as a scalable, low-cost complement to ground patrols.