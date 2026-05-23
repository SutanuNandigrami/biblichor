"""Vendored maxdjohnson/stkclient @ v0.1.1, MIT.

Re-export only the symbols biblichor uses. Internal modules are
underscore-prefixed so it's obvious which code is upstream.
"""
from ._stkclient import Client, OAuth2
from ._model import OwnedDevice

__all__ = ["Client", "OAuth2", "OwnedDevice"]
