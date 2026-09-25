import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import parser_config 

EXCEL = ROOT / "config" / "Due_Dilligence_Weighed_Parameters.xlsx"
RULE_SPECS = ROOT / "config" / "rule_specs.json"
INTERVALS = ROOT / "config" / "intervals.json"
ENTITIES = ROOT / "data" / "entities.json"
RAW = ROOT / "data" / "observations_raw.json"


@pytest.fixture
def workdir(tmp_path):
    shutil.copy(EXCEL, tmp_path / "rulebook.xlsx")
    shutil.copy(RULE_SPECS, tmp_path / "rule_specs.json")
    shutil.copy(INTERVALS, tmp_path / "intervals.json")
    return tmp_path


@pytest.fixture
def built_config(workdir):
    config = parser_config.build_config(
        workdir / "rulebook.xlsx", workdir / "rule_specs.json", workdir / "intervals.json"
    )
    return parser_config.save_config(config, workdir / "parameters_config.json", versions_dir=None)

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
