"""Application and data-model version identifiers.

Increment DATA_MODEL_VERSION in compatibility.py for schema/DTO changes.
The server version exposed to clients automatically incorporates it.
"""

from .compatibility import DATA_MODEL_VERSION

__version__ = f"0.2.0+model.{DATA_MODEL_VERSION}"
