"""Settings in one place: namespaces (placeholders) and file locations."""
from pathlib import Path

OPM_NS = "https://example.org/opm#"        # ontology and shapes
MODEL_NS = "https://example.org/model/"    # things, states and links of one model

HERE = Path(__file__).resolve().parent
ONTOLOGY = HERE / "ontologie.ttl"
