"""Goodreads Series HTML parsing (main-series-only filter)."""

from __future__ import annotations

from endless_library.sources.goodreads_series import GoodreadsSeries

FIXTURE = """
<html><body>
<div class="listWithDividers__item">
  <h3>Book 1</h3>
  <a itemprop="url" href="/book/show/2767052.The_Hunger_Games">
    <span itemprop="name">The Hunger Games (The Hunger Games, #1)</span>
  </a>
  <span itemprop="author">Suzanne Collins</span>
</div>
<div class="listWithDividers__item">
  <h3>Book 1.5</h3>
  <a itemprop="url" href="/book/show/999.Novella">
    <span itemprop="name">Companion Novella (The Hunger Games)</span>
  </a>
  <span itemprop="author">Suzanne Collins</span>
</div>
<div class="listWithDividers__item">
  <h3>Book 2</h3>
  <a itemprop="url" href="/book/show/6148028.Catching_Fire">
    <span itemprop="name">Catching Fire (The Hunger Games, #2)</span>
  </a>
  <span itemprop="author">Suzanne Collins</span>
</div>
</body></html>
"""


def test_main_series_only():
    src = GoodreadsSeries(fetch=lambda _u: FIXTURE)
    out = list(src.list_to_read(identifier="73758-the-hunger-games", token=None))
    titles = [b.title for b in out]
    assert "The Hunger Games" in titles
    assert "Catching Fire" in titles
    # Novella (Book 1.5) skipped
    assert "Companion Novella" not in titles
