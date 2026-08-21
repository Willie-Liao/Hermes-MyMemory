from __future__ import annotations

from recall.tools import expand_memory, recall_memory

SEED = "mem-2026-08-12-event-9625547B667B"
HOP1 = "mem-2026-08-09-procedure-C1DD5CAA2A26"
HOP2 = "mem-2026-08-08-procedure-2B93F6F55D12"
OVERLAP = "mem-2026-08-13-event-48AB7607830B"


def test_recall_sentence_hits_seed_cluster(staging):
    fts = recall_memory(
        "Did we already fix the semicolon digest bug, and what else broke in that stretch?",
        staging=staging,
    )
    assert SEED in fts
    assert HOP1 in fts or "channel=fts5" in fts
    ent = recall_memory("what did we do about memory digest?", staging=staging)
    assert "key=memorydigest" in ent or SEED in ent


def test_recall_typo_id_one_hex_off(staging):
    typo = "mem-2026-08-12-event-9625547B667C"
    text = recall_memory(typo, staging=staging)
    assert SEED in text
    assert "channel=id" in text


def test_recall_channels(staging):
    fts = recall_memory("semicolon", staging=staging)
    assert "channel=fts5" in fts
    assert SEED in fts
    ent = recall_memory("memory digest", staging=staging)
    assert "channel=entity_key" in ent
    assert "key=memorydigest" in ent
    ident = recall_memory(SEED, staging=staging)
    assert "channel=id" in ident
    assert "Beginning:" in ident or "event body" in ident.lower()


def test_expand_ppr_order_and_depth_clamp(staging):
    text = expand_memory(SEED, depth=5, staging=staging)
    assert "depth=2" in text
    assert "Beginning:" not in text
    assert HOP1 in text and HOP2 in text
    pos1 = text.find(HOP1)
    pos2 = text.find(HOP2)
    pos_o = text.find(OVERLAP)
    assert pos1 != -1 and pos2 != -1
    if pos_o != -1:
        assert min(pos1, pos2) < pos_o
