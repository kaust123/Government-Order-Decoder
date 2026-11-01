"""Tests on text excerpted from real West Bengal Finance Department notifications
(source PDFs on https://comtax.wb.gov.in/GST/GST_Notifications/)."""
from datetime import date

from decoder.amend import analyze, locate
from decoder.checker import filter_text, unsupported
from decoder.dates import parse_date
from decoder.extract import notice_from_text

# 1270-FT_20240807.pdf
N1270 = """GOVERNMENT OF WEST BENGAL
FINANCE DEPARTMENT
REVENUE
NOTIFICATION
No. 1270-F.T. Dated, Howrah, the 07th day of August, 2024
[Corresponding Central Notification No. 15/2024-Central Tax]
In exercise of the powers conferred by sub-section (1) of section 52 of the West Bengal Goods and Services Tax
Act, 2017 (West Ben. Act XXVIII of 2017), the Governor, on the recommendations of the Council, is pleased hereby to
make the following amendment in this Department notification No. 1427-F.T., dated the 28th day of September, 2018
(hereinafter referred to as the said notification), published in the Kolkata Gazette, Extraordinary, Part-I, namely:-
Amendment
In the said notification, for the words "half per cent.", the figure and word "0.25 per cent." shall be substituted."""

# 697-FT_20240503.pdf
N697 = """pleased hereby to make the following amendment in this Department notification No. 198-F.T., dated the 31st January,

2024(hereinafter referred to as the said notification), published in the Kolkata Gazette, Extraordinary, Part-I, namely:–
Amendment
In the said notification, in para 4, for the words and letters "1st day of April, 2024", the words and letters "15th day
of May, 2024" shall be substituted.
2. This notification shall be deemed to have come into force from the 1st day of April, 2024."""

# 1064FT_20190626.pdf
N1064 = """No. 1064-F.T. Dated, Howrah, the 26th day of June, 2019
No. 25/2019-State Tax
In exercise of the powers conferred by section 164 of the West Bengal Goods and Services Tax Act, 2017 (West
Ben. Act XXVIII of 2017), the Governor, on the recommendations of the Council, and on being satisfied that it is
necessary in the public interest so to do, is pleased hereby to make the following amendment in this Department
notification No. 681-F.T., dated the 23rd April, 2019, published in the Kolkata Gazette, Extraordinary, Part I:–
Amendment
In the said notification, for the figures, letters and words “21st day of June, 2019” the figures, letters and word
“21st day of August, 2019” shall be substituted.
2. This notification shall be deemed to have come into force with effect from the 21st day of June, 2019."""

# 1003-FT_20210914.pdf
N1003 = """In the said notification, in the ninth and tenth provisos, for the figures, letters and words "31st day of August,
2021", wherever they occur, the figures, letters and words "30th day of November, 2021" shall be substituted.
2. This notification shall be deemed to have come into force with effect from the 29th day of August, 2021."""

# 1898-FT_20231017.pdf
N1898 = """In the said notification, after the words "is pleased hereby to notify the following special procedure to be followed"
the words, letters and figures "with effect from 1st day of January 2024" shall be inserted and shall be deemed to have
been inserted with effect from the 31st day of July, 2023."""

# 188-FT_20250204.pdf (numeric date format in the reference)
N188 = """No. 188–F.T., the 04th day of February, 2025.— the Governor is pleased hereby to make the following amendment in
this Department’s notification No. 697-F.T., dated 13.07.2021, published in the Kolkata Gazette, Extraordinary, namely:–"""


def test_dates():
    assert parse_date("28th day of September, 2018") == date(2018, 9, 28)
    assert parse_date("31st January, 2024") == date(2024, 1, 31)
    assert parse_date("13.07.2021") == date(2021, 7, 13)
    assert parse_date("02/04/2026") == date(2026, 4, 2)


def test_1270_rate_change():
    am = analyze(N1270)
    assert am.own_number == "1270"
    assert [(r.number, r.date) for r in am.refs] == [("1427", date(2018, 9, 28))]
    assert len(am.changes) == 1
    c = am.changes[0]
    assert (c.kind, c.before, c.after) == ("substitute", "half per cent.", "0.25 per cent.")


def test_697_deadline_change():
    am = analyze(N697)
    assert [(r.number, r.date) for r in am.refs] == [("198", date(2024, 1, 31))]
    c = am.changes[0]
    assert c.before == "1st day of April, 2024" and c.after == "15th day of May, 2024"
    assert "para 4" in c.where
    assert parse_date(am.effective_text) == date(2024, 4, 1)


def test_1064_curly_quotes_no_comma():
    am = analyze(N1064)
    assert am.own_number == "1064"
    assert am.refs[0].number == "681" and am.refs[0].date == date(2019, 4, 23)
    assert am.changes[0].after == "21st day of August, 2019"


def test_1003_wherever_they_occur():
    c = analyze(N1003).changes[0]
    assert c.before == "31st day of August, 2021" and c.after == "30th day of November, 2021"
    assert "provisos" in c.where


def test_1898_insertion():
    c = analyze(N1898).changes[0]
    assert c.kind == "insert" and c.after == "with effect from 1st day of January 2024"


def test_188_numeric_ref_date():
    am = analyze(N188)
    assert am.refs[0].number == "697" and am.refs[0].date == date(2021, 7, 13)


def test_locate_in_old_notice():
    old = 'the operator shall collect an amount calculated at the rate of half per cent. of the net value of taxable supplies'
    before, after = locate(old, analyze(N1270).changes[0])
    assert "half per cent." in before and "0.25 per cent." in after


def test_checker_english_and_bengali():
    src = notice_from_text(N697).full_text
    assert unsupported("The deadline moved to 15 May 2024.", src) == []
    assert unsupported("নতুন তারিখ ১৫ মে, ২০২৪।", src) == []
    assert unsupported("The deadline moved to 30 June 2024.", src) == ["30"]
    kept, dropped = filter_text("Para 4 now says 15 May 2024. The fee is Rs 500.", src)
    assert "15 May 2024" in kept and dropped and dropped[0][1] == ["500"]


def test_checker_ignores_clause_refs():
    src = notice_from_text(N1270).full_text
    assert unsupported("The rate is now 0.25 per cent [C7] (p.1).", src) == []
    assert unsupported("পূর্বে হার ছিল অর্ধ শতাংশ (0.5%), এখন ০.২৫%।", src) == []


def test_clauses_have_ids_and_text():
    n = notice_from_text(N1270)
    assert n.clauses and n.clauses[0].id == "C1"
    assert any("0.25 per cent." in c.text for c in n.clauses)
