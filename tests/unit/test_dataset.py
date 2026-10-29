"""Benchmark file integrity and the test-split leakage guard."""

from collections import Counter

import pytest

from evals.dataset import (
    CATEGORIES,
    LeakageError,
    assert_no_leakage,
    few_shot_pool,
    load_benchmark,
    load_split,
)

QUESTIONS = load_benchmark()


def test_size_and_split_ratio():
    assert 80 <= len(QUESTIONS) <= 150
    dev_share = len(load_split("dev")) / len(QUESTIONS)
    assert 0.25 <= dev_share <= 0.35


def test_every_category_in_both_splits():
    for split in ("dev", "test"):
        assert {q.category for q in load_split(split)} == CATEGORIES, split


def test_all_difficulties_in_test():
    assert set(Counter(q.difficulty for q in load_split("test"))) == {"easy", "medium", "hard"}


def test_refusal_and_clarify_questions_exist():
    expected = Counter(q.expected for q in QUESTIONS)
    assert expected["refuse"] >= 5
    assert expected["clarify"] >= 3


def test_question_texts_are_unique():
    texts = [q.question.strip().lower() for q in QUESTIONS]
    assert len(texts) == len(set(texts))


def test_few_shot_pool_is_dev_only():
    pool = few_shot_pool()
    assert pool
    assert all(q.split == "dev" and q.gold_sql for q in pool)


def test_dev_content_passes_leakage_check():
    texts = [q.question for q in few_shot_pool()] + [q.gold_sql for q in few_shot_pool()]
    assert_no_leakage(texts=texts, example_ids=[q.id for q in few_shot_pool()])


def test_leaked_test_question_text_is_caught():
    test_q = load_split("test")[0]
    prompt = f"Example question:\n  {test_q.question.upper()}  \nSQL: ..."
    with pytest.raises(LeakageError, match=test_q.id):
        assert_no_leakage(texts=[prompt])


def test_leaked_test_gold_sql_is_caught():
    test_q = next(q for q in load_split("test") if q.gold_sql and "\n" in q.gold_sql)
    reformatted = " ".join(test_q.gold_sql.split())  # whitespace changes don't hide it
    with pytest.raises(LeakageError, match=test_q.id):
        assert_no_leakage(texts=[f"Example:\n{reformatted}"])


def test_test_ids_as_examples_are_caught():
    test_id = load_split("test")[0].id
    with pytest.raises(LeakageError, match=test_id):
        assert_no_leakage(example_ids=["agg-02", test_id])


def test_baseline_prompt_has_no_test_content():
    from app.agent.baseline import build_system_prompt

    assert_no_leakage(texts=[build_system_prompt()])
