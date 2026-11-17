"""문서 처리 함수 테스트 (PDF 없이 확인할 수 있는 부분)"""
from app.ingest.parse import looks_like_chart, split_charts
from app.ingest.samples import ranges_to_text, text_to_ranges
from app.ingest.tables import _cluster, _join_dates, is_num, split_table


def test_is_num_accepts_statistics_numbers():
    for t in ["4,056,713", "-74", "(9.0%)", "<86.4>", "△6.1%", "36*", "0.0"]:
        assert is_num(t), t


def test_is_num_rejects_footnote_and_words():
    for t in ["1)", "청년층", "2025.", "15~19세"]:
        assert not is_num(t), t


def test_join_dates_merges_year_and_month():
    # '2025.' 과 '5' 가 따로 추출된 경우 → '2025. 5' 한 단어 (v1 보완: 행 이름이 사라지던 문제)
    words = [(99, 350, 124, 356, "2025."), (130, 350, 135, 356, "5"), (178, 350, 203, 356, "4,175")]
    out = _join_dates(words)
    assert [w[4] for w in out] == ["2025. 5", "4,175"]


def test_join_dates_keeps_far_apart_words():
    words = [(99, 350, 124, 356, "2025."), (160, 350, 165, 356, "5")]
    assert len(_join_dates(words)) == 2


def test_cluster_groups_close_positions():
    assert len(_cluster([100, 103, 105, 200, 202, 300])) == 3


def test_split_table_repeats_header():
    header = "[표] 콘텐츠산업 매출액"
    body = [f"- 산업{i}: 1분기 = {i * 1000}" for i in range(100)]
    chunks = split_table(header, body, max_chars=200)
    assert len(chunks) > 1
    assert all(c.startswith(header) for c in chunks)
    assert all(len(c) <= 200 + 40 for c in chunks)
    assert sum(c.count("- 산업") for c in chunks) == 100      # 행이 빠지거나 중복되지 않음


def test_looks_like_chart():
    assert looks_like_chart(["- : a = 1", "- : b = 2", "- 남자: c = 3"])
    assert not looks_like_chart(["- 전체: a = 1", "- 남자: b = 2", "- : c = 3"])


def test_split_charts_by_title_line():
    caption = "[차트] 사회보험료 부담정도\n- 건강보험 · 2015년 = 64.5%\n- 건강보험 · 2017년 = 61.7%\n\n[차트] 직업별\n- 사무 = 57.2%"
    charts = split_charts(caption)
    assert [t for t, _ in charts] == ["사회보험료 부담정도", "직업별"]
    assert charts[0][1] == ["- 건강보험 · 2015년 = 64.5%", "- 건강보험 · 2017년 = 61.7%"]


def test_split_charts_no_chart():
    assert split_charts("차트 없음") == [("", ["차트 없음"])]


def test_page_ranges_round_trip():
    ranges = [(13, 22), (37, 54)]
    assert text_to_ranges(ranges_to_text(ranges)) == ranges
    assert text_to_ranges("") is None
