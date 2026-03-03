import camelot
from camelot.core import TableList
from pathlib import Path
import re
import json
from loguru import logger

_COMMENTS_RE = re.compile(r"^Comments?$", re.MULTILINE)


def all_camelot_tables(path: Path, line_scale: int = 60) -> TableList:
    return camelot.read_pdf(
        str(path),
        pages="all",
        flavor="lattice",
        process_background=True,
        line_scale=line_scale,
    )


def number_comments(text: str) -> int:
    return len(_COMMENTS_RE.findall(text))


def number_of_comment_tables(tables: TableList) -> int:
    return sum(
        1
        for t in tables
        if not t.df.empty and str(t.df.iat[0, 0]).strip() in {"Comments", "Comment"}
    )


def find_all_tables(pdf: Path, text: str) -> TableList:
    n_comments = number_comments(text)

    for ls in range(60, 111):
        tables = all_camelot_tables(pdf, line_scale=ls)
        n_comment_tables = number_of_comment_tables(tables)

        if n_comment_tables == n_comments:
            logger.info(f"Got it! {pdf}: Found at line_scale {ls}")
            return tables

    return TableList()


def pdf_to_raw_tables(pdf: Path, text: str) -> str:
    """Converts a TableList into a jsonify object to insert into RawDB

    Args:
        tables (TableList): TableList with comments

    Returns:
        str: string storing the tables in a json format
    """
    tables = find_all_tables(pdf, text)

    return json.dumps(
        [
            json.loads(t.df.to_json())
            for t in tables
            if t.df.iloc[0, 0] in {"Comments", "Comment"}
        ]
    )


def json_to_tables(json_string: str) -> TableList:
    return TableList(json.loads(json_string))
