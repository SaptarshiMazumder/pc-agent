"""Turns "sort by this column, that way round" into SQL, without ever trusting the caller.

THE CLIENT NAMES A COLUMN; IT NEVER SUPPLIES ONE. Every sortable column is declared up front as a
key mapped to a fixed SQL expression written here in the source, and the caller's string is only
ever used to look that mapping up. Nothing the client sends reaches the query text. This is the
one place in the admin surface where a request would otherwise choose part of a statement, and
`ORDER BY {user_input}` is a textbook injection -- it sits outside the parameter binding that
protects every other clause, so no amount of quoting in the caller would save it.

AN UNKNOWN COLUMN IS NOT AN ERROR. An older console asking for a column a newer server has
dropped should get the default order and a working table, not a 400 and an empty screen. The
request is answered; it is simply answered in the default order.

EVERY SORT ENDS IN A UNIQUE TIEBREAK, and that is not cosmetic. Paging works by LIMIT/OFFSET over
a sorted set, so if the sort key has duplicates -- a dozen accounts with zero credits, a hundred
payments of the same amount -- the engine may order them differently between two queries, and a
row can appear on page one and again on page two while another is never shown at all. Appending a
unique column makes the order total, which makes paging stable.
"""

from __future__ import annotations

ASC = "ASC"
DESC = "DESC"


class SortOrderResolver:
    """A declared set of sortable columns, and the ORDER BY they produce.

    :param columns: client-facing key -> SQL expression. The expressions are written in the
        caller's source and are the only column text that can ever reach a statement.
    :param default: the key used when the caller names nothing, or names something unknown.
    :param tiebreak: a SQL expression that is unique per row, appended to every order so that
        paging cannot repeat or skip rows. Usually the primary key.
    """

    def __init__(self, columns: dict[str, str], *, default: str, tiebreak: str) -> None:
        if default not in columns:
            # A programming error, caught at construction rather than on the first odd request:
            # a default that names no column would silently degrade every unsorted listing.
            raise ValueError(f"default {default!r} is not one of {sorted(columns)}")
        self._columns = dict(columns)
        self._default = default
        self._tiebreak = tiebreak

    @property
    def keys(self) -> list[str]:
        """The sortable column names, for anything that wants to advertise them."""
        return sorted(self._columns)

    def resolve(self, sort: object, direction: object) -> tuple[str, str, str]:
        """(clause, applied_key, applied_direction).

        The applied key and direction are returned rather than assumed, so the client can render
        the arrow against what the SERVER actually did. A console that draws its arrow from what
        it asked for will point at a column the results are not sorted by the moment the two
        disagree.
        """
        key = str(sort or "").strip().lower()
        if key not in self._columns:
            key = self._default
        way = DESC if str(direction or "").strip().lower() == "desc" else ASC
        # The tiebreak follows the caller's direction so that a page boundary falling inside a
        # run of equal values stays consistent when the sort is reversed.
        clause = f"ORDER BY {self._columns[key]} {way}, {self._tiebreak} {way}"
        return clause, key, way.lower()


__all__ = ["SortOrderResolver", "ASC", "DESC"]
