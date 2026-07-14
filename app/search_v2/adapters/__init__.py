"""Search Engine V2 source adapters.

Importing this package never performs network calls. Optional sources use the
same contract and fail independently at invocation time.
"""

from .avito import AvitoAdapter
from .base import SourceAdapter, SourceCapabilities, SourceContext, SourceResult
from .citilink import CitilinkAdapter
from .dns import DnsAdapter
from .generic_search import GenericExactSearchAdapter
from .mvideo import MVideoAdapter
from .ozon import OzonAdapter
from .wildberries import WildberriesAdapterV2
from .yandex_market import YandexMarketAdapter

__all__ = [
    "AvitoAdapter",
    "CitilinkAdapter",
    "DnsAdapter",
    "GenericExactSearchAdapter",
    "MVideoAdapter",
    "OzonAdapter",
    "SourceAdapter",
    "SourceCapabilities",
    "SourceContext",
    "SourceResult",
    "WildberriesAdapterV2",
    "YandexMarketAdapter",
]
