from __future__ import annotations

from endless_library.sources.bookwyrm import BookWyrm
from endless_library.sources.goodreads import GoodreadsRSS
from endless_library.sources.goodreads_listopia import GoodreadsListopia
from endless_library.sources.goodreads_series import GoodreadsSeries
from endless_library.sources.hardcover import HardcoverGQL
from endless_library.sources.kindlebangla import KindleBangla
from endless_library.sources.manual import ManualEntry
from endless_library.sources.nyt_bestsellers import NYTBestSellers
from endless_library.sources.storygraph import StoryGraph
from endless_library.sources.wikidata_author import WikidataAuthor

_SOURCES = {
    "goodreads": GoodreadsRSS,
    "hardcover": HardcoverGQL,
    "manual": ManualEntry,
    "goodreads_listopia": GoodreadsListopia,
    "goodreads_series": GoodreadsSeries,
    # Phase 6s.3
    "nyt": NYTBestSellers,
    "storygraph": StoryGraph,
    "bookwyrm": BookWyrm,
    "wikidata": WikidataAuthor,
    # Phase 6u
    "kindlebangla": KindleBangla,
}


def build(name: str, **kwargs):
    if name not in _SOURCES:
        raise KeyError(f"unknown source: {name}")
    return _SOURCES[name](**kwargs)
