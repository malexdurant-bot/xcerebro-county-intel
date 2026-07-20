"""
Shelby County, TN — build configuration.

County-specific constants used across all pipeline modules.
No county/state literals appear in framework code — only here and in adapters.
"""

from pathlib import Path

COUNTY_ID = "shelby_tn"
COUNTY_NAME = "Shelby County"
STATE = "TN"
STATE_FULL = "Tennessee"
FIPS_CODE = "47157"
TIMEZONE = "America/Chicago"

# State rule family — Tennessee non-judicial foreclosure
# Unlike AZ, in TN the Notice of Trustee Sale is published in a newspaper
# (not recorded with Register of Deeds).  The Appointment of Successor
# Trustee (ASOT) IS recorded and is our pre-foreclosure signal.
STATE_RULE_FAMILY = "TN_non_judicial_foreclosure"

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_DIR = Path(__file__).parent

RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
CACHE_DIR = REPO_ROOT / "data" / "cache"
OUTPUT_DIR = THIS_DIR / "pipeline_output"

# Raw JSONL files written by scrapers
TAX_SALE_JSONL = RAW_DATA_DIR / "trustee_tax_sale_shelby.jsonl"
REGISTER_JSONL = RAW_DATA_DIR / "register_shelby.jsonl"
CHANCERY_JSONL = RAW_DATA_DIR / "chancery_court_shelby.jsonl"
GENERAL_SESSIONS_JSONL = RAW_DATA_DIR / "general_sessions_shelby.jsonl"
PROBATE_JSONL = RAW_DATA_DIR / "probate_court_shelby.jsonl"

# Parcel cache — keyed by PARCELID
PARCEL_CACHE_PATH = CACHE_DIR / "shelby_tn_parcel_cache.json"

# ---------------------------------------------------------------------------
# ArcGIS parcel enrichment
# ---------------------------------------------------------------------------

ARCGIS_PARCEL_URL = (
    "https://scgis.shelbycountytn.gov"
    "/serverhigh/rest/services/Parcel/CurrentParcels/MapServer/0/query"
)

# ---------------------------------------------------------------------------
# Source IDs (must match config/counties/shelby_tn.json)
# ---------------------------------------------------------------------------

SOURCE_TAX_SALE = "trustee_tax_sale"
SOURCE_REGISTER = "register_shelby"
SOURCE_CHANCERY = "chancery_court_shelby"
SOURCE_GENERAL_SESSIONS = "general_sessions_civil_shelby"
SOURCE_PROBATE = "probate_court_shelby"
SOURCE_PARCEL_MASTER = "parcel_master_shelby"
