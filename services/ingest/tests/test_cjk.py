"""Chinese, Japanese and Korean text is found by the keyword search (store/cjk.py)."""

import copy

from conftest import DAY
from ingest.ask.retrieve import retrieve, terms
from ingest.store import LifeStore, cjk
from ingest.vault import Vault


def add(vault: Vault, **labels: str) -> None:
    first = vault.events(DAY)[0]
    vault.append_events(DAY, [dict(copy.deepcopy(first), id=ident, label=label) for ident, label in labels.items()])


def found(store: LifeStore, question: str) -> list[str]:
    return [r["id"] for r in retrieve(store, question, limit=5)]


def test_characters_are_set_apart_and_runs_searched_as_pairs() -> None:
    assert cjk.spaced("no CJK here") is None
    assert cjk.spaced("读了机器学习 notes").split() == ["读", "了", "机", "器", "学", "习", "notes"]
    assert cjk.pairs("机器学习") == ["机器", "器学", "学习"] and cjk.pairs("猫") == ["猫"]
    assert cjk.split("gpt机器学习v2") == ["gpt", "机器", "器学", "学习", "v2"]
    assert terms("我昨天看了机器学习 on Bilibili") == [
        "我昨",
        "昨天",
        "天看",
        "看了",
        "了机",
        "机器",
        "器学",
        "学习",
        "bilibili",
    ]
    assert cjk.expression(["机器", "bilibili", "猫"]) == '"机 器" OR "猫"'


def test_a_phrase_inside_a_chinese_sentence_is_found(store: LifeStore, vault: Vault) -> None:
    add(
        vault,
        ev_zh="今天下午读了关于机器学习的论文，然后去喝咖啡",
        ev_other="明天要交数据库作业",
        ev_ja="東京の天気はどうですか",
    )
    assert found(store, "机器学习")[0] == "ev_zh"
    assert found(store, "数据库")[0] == "ev_other"
    assert found(store, "咖啡")[0] == "ev_zh"  # a two-character word: too short for a trigram index
    assert found(store, "天気")[0] == "ev_ja"
    assert found(store, "量子力学") == []


def test_the_record_with_more_of_the_question_ranks_first(store: LifeStore, vault: Vault) -> None:
    add(vault, ev_part="机器很贵", ev_whole="机器学习课程的作业")
    assert found(store, "机器学习课程")[:2] == ["ev_whole", "ev_part"]


def test_words_in_both_scripts_search_both_indexes(store: LifeStore, vault: Vault) -> None:
    add(vault, ev_mixed="Bilibili 上的线性代数视频", ev_english="Linear algebra lecture on YouTube")
    assert found(store, "线性代数 YouTube")[:2] == ["ev_mixed", "ev_english"]


def test_a_replaced_day_and_a_note_keep_the_character_index_in_step(store: LifeStore, vault: Vault) -> None:
    add(vault, ev_old="旧的记录提到火锅")
    assert found(store, "火锅") == ["ev_old"]
    events = [dict(e, label="换成了别的内容") if e["id"] == "ev_old" else e for e in vault.events(DAY)]
    vault.write_lines(vault.day_path(DAY) / "events.jsonl", events)
    assert found(store, "火锅") == [] and found(store, "别的内容") == ["ev_old"]
    notes = vault.root / "notes"
    notes.mkdir(exist_ok=True)
    (notes / "想法.md").write_text("# 想法\n\n周末想去吃火锅，顺便买书。\n", encoding="utf-8")
    store.sync_notes()  # a search reads the notes folder again only now and then
    hits = retrieve(store, "火锅", limit=5)
    assert [h["id"].split("#")[0] for h in hits] == ["note:想法.md"]
    with store.connect() as db:
        db.execute("INSERT INTO units_fts(units_fts, rank) VALUES ('integrity-check', 1)")
