from __future__ import annotations

from recall.lexical import TOKENIZER_PLAIN, rebuild_lexical, search_lexical


SEED = "mem-2026-08-12-event-9625547B667B"


def test_porter_semicolon_hits_seed_unicode61_misses(staging, tmp_path):
    porter = tmp_path / "porter.db"
    plain = tmp_path / "plain.db"
    ms = rebuild_lexical(staging, db_path=porter)
    assert ms < 100
    rebuild_lexical(staging, tokenizer=TOKENIZER_PLAIN, db_path=plain)
    hits = search_lexical("semicolon", staging=staging, db_path=porter)
    assert hits, hits
    assert hits[0]["id"] == SEED
    miss = search_lexical(
        "semicolon", staging=staging, tokenizer=TOKENIZER_PLAIN, db_path=plain
    )
    assert miss == []
    again = search_lexical("semicolon", k=1, staging=staging, db_path=porter)
    assert again and again[0]["id"] == SEED


def test_sentence_query_ors_to_semicolon_seed(staging, tmp_path):
    porter = tmp_path / "porter-sent.db"
    rebuild_lexical(staging, db_path=porter)
    q = "Did we already fix the semicolon digest bug, and what else broke in that stretch?"
    hits = search_lexical(q, staging=staging, db_path=porter)
    assert hits, hits
    ids = [h["id"] for h in hits]
    assert SEED in ids
