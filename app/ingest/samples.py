"""기본 문서 3개(D01~D03)의 정보. 출처·이용 조건: docs/data_sources.md

page_ranges: 원본 PDF를 자르지 않고(변경금지 조건) 코드에서 사용할 쪽 범위만 지정합니다.
"""
SAMPLE_DOCS = [
    {
        "code": "D01",
        "file": "2026_youth_employment_survey.pdf",
        "title": "2026년 5월 경제활동인구조사 청년층 부가조사 결과",
        "source_org": "국가데이터처",
        "source_url": "https://mods.go.kr/board.es?mid=a10301010000&bid=210&act=view&list_no=446080",
        "license": "공공누리 제1유형",
        "page_ranges": [(1, 40)],
    },
    {
        "code": "D02",
        "file": "2025_content_industry_trend.pdf",
        "title": "2025년 4분기 및 연간 콘텐츠산업 동향분석 보고서",
        "source_org": "한국콘텐츠진흥원",
        "source_url": "https://www.kocca.kr/kocca/bbs/view/B0158949/2011489.do?menuNo=204157",
        "license": "공공누리 제4유형",
        "page_ranges": [(13, 22), (37, 54)],
    },
    {
        "code": "D03",
        "file": "2025_social_survey.pdf",
        "title": "2025년 사회조사 결과",
        "source_org": "국가데이터처",
        "source_url": "https://mods.go.kr/board.es?mid=a10301010000&bid=219&act=view&list_no=439196",
        "license": "공공누리 제3유형",
        "page_ranges": [(1, 56)],
    },
]


def ranges_to_text(ranges):
    """[(13, 22), (37, 54)] → '13-22,37-54'  (DB에 글로 저장)"""
    return ",".join(f"{a}-{b}" for a, b in ranges) if ranges else ""


def text_to_ranges(text):
    """'13-22,37-54' → [(13, 22), (37, 54)].  빈 값이면 None(전체 쪽)"""
    if not text:
        return None
    return [tuple(int(x) for x in part.split("-")) for part in text.split(",")]
