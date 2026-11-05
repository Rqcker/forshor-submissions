"""sto_site engine glue: binds the vendored Qualtran, then exposes the library, the two counting paths and the
frontier. Import this package before `qualtran`, `tcost` or `forshor_qualtran`."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
DATA = ENGINE / "data"
QUALTRAN_TREE = ENGINE / "vendor" / "Qualtran-main" / "Qualtran-main"

if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
os.environ.setdefault("TCOST_QUALTRAN_PATH", str(QUALTRAN_TREE))
os.environ.setdefault("ONECLICK_QUALTRAN", str(QUALTRAN_TREE))

import warnings  # noqa: E402

warnings.filterwarnings("ignore", message=".*Graphviz drawing is unavailable.*")

import tcost  # noqa: E402,F401  (binds the vendored Qualtran first)
import forshor_qualtran  # noqa: E402,F401

from sto_currency import DEFAULT_DELTA, SITE_MODEL_VERSION  # noqa: E402,F401

#: The fixed tags. The table-trust tag (Lean-verified, replay-checked, ...) is computed per number: see `trust`.
TRUST_TAGS = ("Python-verified", "counts cross-checked")
