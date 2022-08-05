from __future__ import annotations

import typing as t

import numpy
import polars

from .vec import BBox
from .transform import Transform


IntoAtoms: t.TypeAlias = t.Union[t.Dict[str, t.Sequence[t.Any]], t.Sequence[t.Any], numpy.ndarray, polars.DataFrame]
"""
A type convertible into an `AtomFrame`.
"""


class AtomFrame(polars.DataFrame):
    """
    A collection of atoms, absent any implied coordinate system.
    Implemented as a wrapper around a Polars DataFrame.

    Must contain the following columns:
    - x: x position, float
    - y: y position, float
    - z: z position, float
    - elem: atomic number, int
    - symbol: atomic symbol (may contain charges)

    In addition, it commonly contains the following columns:
    - i: Initial atom number
    - wobble: Isotropic Debye-Waller standard deviation (MSD, <u^2> = B*3/8pi^2, dimensions of [Length^2])
    - frac: Fractional occupancy, [0., 1.]
    """

    def __new__(cls, data: t.Optional[IntoAtoms] = None, columns: t.Optional[t.Sequence[str]] = None) -> AtomFrame:
        if data is None:
            return super().__new__(cls)
        if isinstance(data, polars.DataFrame):
            obj = data.clone()
        else:
            obj = polars.DataFrame(data, columns)
        obj.__class__ = cls

        return t.cast(AtomFrame, obj)

    def __init__(self, data: IntoAtoms, columns: t.Optional[t.Sequence[str]] = None):
        self._validate_atoms()
        self._bbox: t.Optional[BBox] = None

    def _validate_atoms(self):
        missing = set(('x', 'y', 'z', 'elem', 'symbol')) - set(self.columns)
        if len(missing):
            raise ValueError(f"'Atoms' missing column(s) {list(missing)}")

    def coords(self) -> numpy.ndarray:
        """Returns a (N, 3) ndarray of atom coordinates."""
        # TODO find a way to get a view
        return self.select(('x', 'y', 'z')).to_numpy()

    @property
    def bbox(self) -> BBox:
        if self._bbox is None:
            self._bbox = BBox.from_pts(self.coords())

        return self._bbox

    def transform(self, transform: Transform) -> AtomFrame:
        transformed = transform @ self.coords()
        return self.with_columns((
            polars.Series(transformed[:, 0]).alias('x'),
            polars.Series(transformed[:, 1]).alias('y'),
            polars.Series(transformed[:, 2]).alias('z'),
        ))


AtomSelection = t.NewType('AtomSelection', polars.Expr)
"""
Polars expression selecting a subset of atoms from an AtomFrame. Can be used with DataFrame.filter()
"""