# Entity Scoring Prototype

A first prototype for configuring weighted due-diligence parameters, validating company observations, calculating historical entity scores with Beta distributions and EWMA, and presenting the results in an interactive Streamlit dashboard.

## 1. Architecture

The implemented prototype follows this flow:

```text
Source document                       Entity data
11 weighted parameters                Facts per company
       |                                      |
       v                                      v
Parser and config                    Data intake
Rules, weights, intervals            Validated, timestamped facts
       |                                      |
       +------------------+-------------------+
                          |
                          v
                   Scoring engine
                          |
       +------------------+------------------+------------------+
       |                  |                  |                  |
       v                  v                  v                  v
   Normalize        Interval check       Beta + EWMA           Rank
   Rules -> 0-1     Inside/outside       Score with memory     Best -> worst
                          |
                          v
              Knockout gate if configured
                          |
                          v
                  JSON result files
                          |
                          v
                    Streamlit UI
          Dashboard / Import / Config / Detail
```

### Main scripts

| File | Purpose |
| --- | --- |
| `parser_config.py` | Reads the Excel rulebook, validates rule specifications and intervals, and generates `parameters_config.json`. |
| `data_intake.py` | Validates raw observations and creates clean, timestamped observations for scoring. |
| `normalize.py` | Converts raw facts to normalized parameter scores in `[0,1]`. |
| `interval_check.py` | Checks whether every normalized parameter score is inside or outside its allowed interval. |
| `beta_ewma.py` | Updates Beta state, calculates the conservative parameter scores, weighted entity score, and EWMA. |
| `rank.py` | Creates the final leaderboard and trend information. |
| `scoring_engine.py` | Orchestrates normalization, interval checking, Beta/EWMA scoring, history generation, and ranking. |
| `ui_app.py` | Streamlit interface for dashboard, imports, configuration, and entity details. |

## 2. Requirements

### Python

Python **3.12** is recommended.

Check your installation:

```text
python --version
```

On some systems use:

```text
py --version
```

### Python libraries

The prototype uses the following external libraries:

| Library | Used for |
| --- | --- |
| `openpyxl` | Reading and validating the Excel rulebook. |
| `scipy` | Beta distribution percentile calculation. |
| `pandas` | UI tables, previews, and result formatting. |
| `plotly` | Interactive dashboard and score-history charts. |
| `streamlit` | Web UI for the local prototype. |
| `pytest` | Automated tests. |

Install all required libraries with:

```text
pip install openpyxl scipy pandas plotly streamlit pytest
```

No installation is needed for standard-library modules such as `json`, `pathlib`, `argparse`, `datetime`, `hashlib`, `dataclasses`, or `collections`.

### Recommended `requirements.txt`

```text
openpyxl
scipy
pandas
plotly
streamlit
pytest
```

Then install with:

```text
pip install -r requirements.txt
```

## 3. First-Time Setup

### 3.1 Clone the repository

```text
git clone <YOUR_REPOSITORY_URL>
cd banka
```

### 3.2 Create a virtual environment

Windows PowerShell:

```text
py -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, either activate from Command Prompt:

```text
.venv\Scripts\activate.bat
```

or allow locally created scripts for the current user:

```text
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Linux/macOS:

```text
python3 -m venv .venv
source .venv/bin/activate
```

### 3.3 Install dependencies

```text
pip install -r requirements.txt
```

or:

```text
pip install openpyxl scipy pandas plotly streamlit pytest
```

## 4. Input Data Format

There are two source data files:

```text
data/entities.json
data/observations_raw.json
```

The same structures are also accepted by the Streamlit **Import data** page.

### 4.1 Entity master data: `entities.json`

Required top-level structure:

```json
{
  "entities": [
    {
      "id": "c01",
      "name": "Example Company",
      "sector": "Fintech",
      "country": "Germany",
      "profile": "Example test company",
      "incorporation_date": "2022-04-10"
    }
  ]
}
```

Important rules:

- `id` must be unique.
- Use stable entity IDs. Historical observations are linked using this ID.
- `name` should be non-empty.
- `incorporation_date` should use ISO format `YYYY-MM-DD`.
- Do not change an entity ID after observations have already been created for it unless you also update every matching observation.

### 4.2 Observation data: `observations_raw.json`

Required top-level structure:

```json
{
  "observations": [
    {
      "entity_id": "c01",
      "checked_at": "2026-09-25",
      "facts": {
        "incorporation_date": "2022-04-10",
        "licenses": ["ISO", "financial"],
        "contracts_count": 20,
        "founder_experience": {
          "bio_experiences": 3,
          "track_records": 2,
          "prior_ventures": 1
        },
        "advisors": {
          "relevant": 4,
          "irrelevant": 1
        },
        "sanctions_hit": false,
        "financials": {
          "has_revenue": true,
          "revenue_growth_years": 3,
          "revenue_decline_years": 0,
          "has_ebitda": true,
          "ebitda_growth_years": 2,
          "ebitda_decline_years": 0
        },
        "debt": "scheduled",
        "audit": "positive_legit",
        "value_proposition": {
          "description": "good",
          "market_analysis": "good",
          "value_proposition": "good"
        }
      }
    }
  ]
}
```

### Observation identity

An observation is uniquely identified by:

```text
entity_id + checked_at
```

For example:

```text
c01 + 2026-09-25
```

The UI import behavior is:

- new `(entity_id, checked_at)` -> observation is added;
- existing `(entity_id, checked_at)` -> observation is replaced;
- unchanged observation -> no meaningful change.

This makes it possible to both add a new period and correct a previously entered period.

## 5. Allowed Fact Values

The current `data_intake.py` validates the supported fact values before scoring.

### Dates

Use `YYYY-MM-DD`, for example `2026-09-25`.

`incorporation_date` must not be after `checked_at`. The observation `incorporation_date` should also agree with the value stored in the entity master record.

### Counts

Count fields must be non-negative integers.

Valid:

```json
"contracts_count": 12
```

Invalid:

```json
"contracts_count": -1
```

Invalid:

```json
"contracts_count": 2.5
```

### Booleans

Use real JSON booleans:

```text
true
false
```

Do not use strings such as `"yes"`, `"no"`, or `"true"`.

### Licenses

Currently supported values are:

```text
ISO
financial
fintech
other
```

Do not duplicate the same license in one observation.

### Debt

Currently supported values:

```text
none
scheduled
unscheduled
```

### Audit

Currently supported values:

```text
positive_legit
not_legit
negative
none
```

### Value-proposition ratings

Currently supported values:

```text
good
weak
```

### Revenue and EBITDA consistency

If `has_revenue` is `false`, both revenue history counts must be zero.

If `has_ebitda` is `false`, both EBITDA history counts must be zero.

## 6. How to Add a New Company for Testing

The easiest way is through the Streamlit UI.

### Step 1 - Create the entity file

Create, for example, `new_entity.json`:

```json
{
  "entities": [
    {
      "id": "c12",
      "name": "Prototype Test Ltd",
      "sector": "Fintech",
      "country": "Germany",
      "profile": "Manual prototype test",
      "incorporation_date": "2022-01-15"
    }
  ]
}
```

### Step 2 - Create at least one observation

Create `new_observation.json`:

```json
{
  "observations": [
    {
      "entity_id": "c12",
      "checked_at": "2026-09-25",
      "facts": {
        "incorporation_date": "2022-01-15",
        "licenses": ["ISO", "financial"],
        "contracts_count": 25,
        "founder_experience": {
          "bio_experiences": 3,
          "track_records": 2,
          "prior_ventures": 1
        },
        "advisors": {
          "relevant": 4,
          "irrelevant": 1
        },
        "sanctions_hit": false,
        "financials": {
          "has_revenue": true,
          "revenue_growth_years": 3,
          "revenue_decline_years": 0,
          "has_ebitda": true,
          "ebitda_growth_years": 2,
          "ebitda_decline_years": 0
        },
        "debt": "scheduled",
        "audit": "positive_legit",
        "value_proposition": {
          "description": "good",
          "market_analysis": "good",
          "value_proposition": "good"
        }
      }
    }
  ]
}
```

### Step 3 - Start the UI

```text
streamlit run ui_app.py
```

### Step 4 - Open **Import data**

Upload `new_entity.json` and `new_observation.json`.

The UI shows a preview before changing the current data.

### Step 5 - Select **Import and recalculate**

The prototype first stages the merged data in temporary files and runs validation/scoring there.

If validation fails, the current working data is left unchanged.

If validation succeeds, the files are updated and the full pipeline runs again.

The UI then shows:

- entity added/updated;
- observations added/replaced;
- score before and after;
- ranking before and after;
- status before and after;
- current violated parameters.

The new entity will immediately appear on the Dashboard and in Entity Detail.

## 7. How to Add a New Observation to an Existing Company

You do **not** need to upload the entity again if it already exists.

For example, to add a later observation for `c01`:

```json
{
  "observations": [
    {
      "entity_id": "c01",
      "checked_at": "2026-12-31",
      "facts": {
        "incorporation_date": "2021-09-01",
        "licenses": ["ISO", "financial", "fintech"],
        "contracts_count": 38,
        "founder_experience": {
          "bio_experiences": 3,
          "track_records": 2,
          "prior_ventures": 2
        },
        "advisors": {
          "relevant": 5,
          "irrelevant": 0
        },
        "sanctions_hit": false,
        "financials": {
          "has_revenue": true,
          "revenue_growth_years": 4,
          "revenue_decline_years": 0,
          "has_ebitda": true,
          "ebitda_growth_years": 4,
          "ebitda_decline_years": 0
        },
        "debt": "none",
        "audit": "positive_legit",
        "value_proposition": {
          "description": "good",
          "market_analysis": "good",
          "value_proposition": "good"
        }
      }
    }
  ]
}
```

Upload only this observation file on the **Import data** page and recalculate.

Because Beta and EWMA are sequential, adding a later observation can change:

- parameter-level `alpha` and `beta`;
- conservative Beta scores;
- entity raw score;
- EWMA score;
- current status;
- rank;
- trend;
- recent violation count.

## 8. UI Overview

The prototype UI contains four pages.

### Dashboard

Shows:

- total entities;
- compliant entities;
- violated entities;
- knockout entities;
- interactive entity scatter plot;
- ranked leaderboard;
- current violated parameters;
- score/rank trend information.

#### Interactive dot graph

Each dot represents one entity.

- **X-axis:** current EWMA score (%).
- **Y-axis:** number of currently violated parameters.
- **Dot size:** number of historical observations.
- **Green:** currently compliant.
- **Red:** currently violated.
- **Dark red:** knockout/gate violation.

Hover over a company dot to see:

- company name and ID;
- rank;
- current EWMA score;
- status;
- trend;
- observation count;
- recent violated-observation count;
- latest observation date;
- all currently violated parameters.

### Import data

Supports:

- new entity JSON;
- entity updates;
- new observations;
- replacement/correction of an existing observation;
- validation preview;
- safe staged import;
- immediate rescoring;
- before/after comparison.

### Configuration

Supports:

- displaying the current rulebook/configuration;
- replacing the Excel source document;
- editing allowed lower/upper intervals;
- rebuilding configuration;
- rescoring after an interval change;
- viewing current scoring settings.

### Entity detail

Shows:

- current rank;
- current EWMA score;
- status;
- observation count;
- currently violated parameters;
- knockout warning when applicable;
- raw-score and EWMA history chart;
- latest parameter breakdown;
- normalized score;
- allowed interval;
- compliant/violated status;
- current Alpha and Beta values;
- conservative Beta score;
- compliance log.

## 9. Scoring Logic

### 9.1 Normalization

Every raw parameter is translated into a normalized value:

```text
0 <= score <= 1
```

The rule implementation comes from `rule_specs.json`.

### 9.2 Interval check

For parameter `i`:

```text
compliant_i = lower_i <= score_i <= upper_i
```

The check is binary. Once the normalized value is outside the configured interval, that parameter is marked as violated.

### 9.3 Beta update

Every entity has a separate Beta state for every parameter.

For each new observation:

```text
alpha_t = gamma * alpha_(t-1) + 1 if compliant else 0
beta_t  = gamma * beta_(t-1)  + 1 if violated else 0
```

With `gamma < 1`, old evidence gradually loses influence.

### 9.4 Conservative Beta score

The prototype does not rank directly with:

```text
alpha / (alpha + beta)
```

Instead it uses a lower percentile of the Beta distribution:

```text
Beta.ppf(credible_level, alpha, beta)
```

With `credible_level = 0.10`, this is a cautious lower estimate of compliance.

This prevents an entity with very little evidence from immediately receiving the same confidence as an entity with a long clean history.

### 9.5 Weighted raw entity score

For parameter weight `w_i` and conservative Beta score `q_i`:

```text
raw_score = sum(w_i * q_i) / sum(w_i)
```

unless a gate is active.

### 9.6 EWMA

The historical entity score is updated using:

```text
EWMA_t = lambda * raw_score_t + (1 - lambda) * EWMA_(t-1)
```

For the first observation, there is no previous EWMA, so the raw score is used directly.

### 9.7 Knockout

If a gate parameter is violated:

```text
raw_score  = 0
ewma_score = 0
```

### 9.8 Ranking

The leaderboard primarily sorts by current EWMA score from highest to lowest.

For equal scores, the prototype uses additional deterministic tie-breaking such as observation count, recent violations, and entity ID.

## 10. Prototype Limitations

This is intentionally a first prototype.

Current limitations include:

- JSON files are used instead of a database.
- There is no REST/API layer.
- The Streamlit UI directly calls the Python processing modules.
- The application is intended primarily for local/single-user testing.
- Concurrent edits to the same JSON files are not a supported production scenario.
- Rule specifications are manually defined; arbitrary natural-language rules from Excel are not executed automatically.
- Authentication and role-based access are not implemented.
- Production-grade audit logging and transactional persistence are not implemented.

A later version can replace file-based persistence with a database and expose the same scoring logic through an API without changing the core normalization, compliance, Beta/EWMA, and ranking concepts.

## 11. Quick Start

For an already configured repository:

```text
# 1. Create/activate environment
py -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Build/validate config
python parser_config.py --strict

# 4. Validate observations
python data_intake.py --strict

# 5. Calculate scores/ranking
python scoring_engine.py

# 6. Start UI
streamlit run ui_app.py
```

Then open the local Streamlit URL shown in the terminal, typically:

```text
http://localhost:8501
```

To test changing results, open **Import data**, upload a new entity and/or a later observation, then select **Import and recalculate**.
