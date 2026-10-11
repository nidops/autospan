"""AutoSpan: deterministic composition and execution for test-system plugins."""

from autospan.api.plugin import AUTOSPAN_PLUGIN_API_VERSION

__all__ = ["AUTOSPAN_PLUGIN_API_VERSION"]
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("autospan")
except PackageNotFoundError:  # source tree without installed metadata
    __version__ = "0+unknown"
