"""Goodreads Listopia HTML parsing."""

from __future__ import annotations

from endless_library.sources.goodreads_listopia import GoodreadsListopia

# Minimal realistic Listopia HTML
FIXTURE = """
<table class="tableList">
  <tr itemscope itemtype="http://schema.org/Book">
    <td>
      <a class="bookTitle" href="/book/show/2767052.The_Hunger_Games">
        <span itemprop="name">The Hunger Games (The Hunger Games, #1)</span>
      </a>
      <a class="authorName" href="/author/show/153394.Suzanne_Collins">
        <span itemprop="author">Suzanne Collins</span>
      </a>
    </td>
  </tr>
  <tr itemscope itemtype="http://schema.org/Book">
    <td>
      <a class="bookTitle" href="/book/show/3.Harry_Potter_and_the_Sorcerer_s_Stone">
        <span itemprop="name">Harry Potter and the Sorcerer's Stone</span>
      </a>
      <a class="authorName" href="/author/show/1077326.J_K_Rowling">
        <span itemprop="author">J.K. Rowling</span>
      </a>
    </td>
  </tr>
</table>
"""


def test_parse_listopia():
    src = GoodreadsListopia(fetch=lambda _u: FIXTURE)
    out = list(src.list_to_read(identifier="1.Best_Books_Ever", token=None))
    assert len(out) == 2
    assert out[0].title == "The Hunger Games"  # parenthetical stripped
    assert out[0].author == "Suzanne Collins"
    assert out[0].source == "manual"
    assert out[0].source_id == "listopia:2767052"


def test_resolve_url_variants():
    cls = GoodreadsListopia
    assert cls._resolve_url("1.Best_Books_Ever").endswith("1.Best_Books_Ever")
    assert cls._resolve_url("/list/show/1.X") == "https://www.goodreads.com/list/show/1.X"
    assert cls._resolve_url("https://goodreads.com/list/show/1.X").startswith("https://")
