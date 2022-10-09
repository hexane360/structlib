from __future__ import annotations

import logging
import typing as t

import numpy
import scipy.spatial
from numpy.typing import ArrayLike
import polars

from .vec import BBox
from .elem import get_elem, get_sym, get_mass
from .transform import Transform, IntoTransform
from .util import map_some


IntoAtoms: t.TypeAlias = t.Union[t.Dict[str, t.Sequence[t.Any]], t.Sequence[t.Any], numpy.ndarray, polars.DataFrame]
"""
A type convertible into an `AtomFrame`.
"""


_COLUMN_DTYPES: t.Mapping[str, t.Type[polars.DataType]] = {
    'x': polars.Float64,
    'y': polars.Float64,
    'z': polars.Float64,
    'v_x': polars.Float64,
    'v_y': polars.Float64,
    'v_z': polars.Float64,
    'elem': polars.Int8,
    'mass': polars.Float32,
}


def _values_to_series(df: polars.DataFrame, selection: AtomSelection, ty: t.Type[polars.DataType]) -> polars.Series:
    if isinstance(selection, polars.Series):
        return selection.cast(ty)
    if isinstance(selection, polars.Expr):
        return df.select(selection.cast(ty)).to_series()

    selection = numpy.broadcast_to(selection, len(df))
    return polars.Series(selection, dtype=ty)


def _values_to_expr(values: AtomValues, ty: t.Type[polars.DataType]) -> polars.Expr:
    if isinstance(values, polars.Expr):
        return values.cast(ty)
    if isinstance(values, polars.Series):
        return polars.lit(values, dtype=ty)
    arr = numpy.asarray(values)
    return polars.lit(polars.Series(arr, dtype=ty) if arr.size > 1 else values)
    

def _selection_to_series(df: polars.DataFrame, selection: AtomSelection) -> polars.Series:
    return _values_to_series(df, selection, ty=polars.Boolean)


def _selection_to_expr(selection: AtomSelection) -> polars.Expr:
    return _values_to_expr(selection, ty=polars.Boolean)


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
    - mass: Atomic mass, in g/mol (approx. Da)
    - v_[xyz]: Atom velocities, dimensions of length/time
    - atom_type: Numeric atom type, as used by programs like LAMMPS
    """

    def __new__(cls, data: t.Optional[IntoAtoms] = None, columns: t.Optional[t.Sequence[str]] = None,
                orient: t.Union[t.Literal['row'], t.Literal['col'], None] = None,
                _unchecked: bool = False) -> AtomFrame:
        if data is None:
            return super().__new__(cls)
        if isinstance(data, polars.DataFrame):
            obj = data.clone()
        else:
            obj = polars.DataFrame(data, columns, orient=orient)

        if not _unchecked:
            missing: t.Tuple[str] = tuple(set(('symbol', 'elem')) - set(obj.columns))  # type: ignore
            if len(missing) > 1:
                raise ValueError("'AtomFrame' missing columns 'elem' and/or 'symbol'.")
            if missing == ('symbol',):
                obj = polars.DataFrame.with_columns(obj, get_sym(obj['elem']))
            elif missing == ('elem',):
                obj = polars.DataFrame.with_columns(obj, get_elem(obj['symbol']))

            # cast to standard dtypes
            obj = polars.DataFrame.with_columns(obj, [
                polars.DataFrame.get_column(obj, col).cast(dtype)
                for (col, dtype) in _COLUMN_DTYPES.items() if col in obj
            ])

        obj.__class__ = cls
        return t.cast(AtomFrame, obj)

    def __init__(self, data: IntoAtoms, columns: t.Optional[t.Sequence[str]] = None,
                 orient: t.Union[t.Literal['row'], t.Literal['col'], None] = None,
                 _unchecked: bool = False):
        self._bbox: t.Optional[BBox] = None
        if not _unchecked:
            self._validate_atoms()

    def _validate_atoms(self):
        missing = [col for col in ['x', 'y', 'z', 'elem', 'symbol'] if col not in self]
        if len(missing):
            raise ValueError(f"'AtomFrame' missing column(s) {', '.join(map(repr, missing))}")

    @staticmethod
    def empty() -> AtomFrame:
        data = [
            polars.Series('x', (), dtype=polars.Float64),
            polars.Series('y', (), dtype=polars.Float64),
            polars.Series('z', (), dtype=polars.Float64),
            polars.Series('elem', (), dtype=polars.Int8),
            polars.Series('symbol', (), dtype=polars.Utf8),
        ]
        return AtomFrame(data)

    def with_column(self, column: t.Union[polars.Series, polars.Expr]) -> AtomFrame:
        return AtomFrame(super(AtomFrame, self).with_column(column), _unchecked=True)

    def with_columns(self, exprs: t.Union[t.Literal[None], polars.Series, polars.Expr, t.Sequence[t.Union[polars.Series, polars.Expr]]],
                     **named_exprs: t.Union[polars.Expr, polars.Series]) -> AtomFrame:
        return AtomFrame(super(AtomFrame, self).with_columns(exprs, **named_exprs), _unchecked=True)

    def try_get_column(self, name: str) -> t.Optional[polars.Series]:
        try:
            return self.get_column(name)
        except polars.NotFoundError:
            return None

    def coords(self, selection: t.Optional[AtomSelection] = None) -> numpy.ndarray:
        """Returns a (N, 3) ndarray of atom coordinates."""
        # TODO find a way to get a view
        if selection is not None:
            self = self.filter(_selection_to_expr(selection))
        return self.select(('x', 'y', 'z')).to_numpy()

    def velocities(self, selection: t.Optional[AtomSelection] = None) -> t.Optional[numpy.ndarray]:
        """Returns a (N, 3) ndarray of atom velocities."""
        if selection is not None:
            self = self.filter(_selection_to_expr(selection))
        try:
            return self.select(('v_x', 'v_y', 'v_z')).to_numpy()
        except polars.NotFoundError:
            return None

    def types(self) -> t.Optional[polars.Series]:
        return self.try_get_column('type')

    def masses(self) -> t.Optional[polars.Series]:
        return self.try_get_column('mass')

    def with_index(self, index: t.Optional[AtomValues] = None) -> AtomFrame:
        if index is None and 'i' in self:
            return self
        if index is None:
            index = numpy.arange(len(self), dtype=numpy.int64)
        return self.with_column(_values_to_expr(index, polars.Int64).alias('i'))

    def with_wobble(self, wobble: t.Optional[AtomValues] = None) -> AtomFrame:
        """
        Return self with the given displacements. If `wobble` is not specified,
        defaults to the already-existing wobbles or 0.
        """
        if wobble is None and 'wobble' in self:
            return self
        wobble = 0. if wobble is None else wobble
        return self.with_column(_values_to_expr(wobble, polars.Float64).alias('wobble'))

    def with_occupancy(self, frac_occupancy: t.Optional[AtomValues] = None) -> AtomFrame:
        """
        Return self with the given fractional occupancies. If `frac_occupancy` is not specified,
        defaults to the already-existing occupancies or 1.
        """
        if frac_occupancy is None and 'frac_occupancy' in self:
            return self
        frac_occupancy = 1. if frac_occupancy is None else frac_occupancy
        return self.with_column(_values_to_expr(frac_occupancy, polars.Float64).alias('frac_occupancy'))

    def apply_wobble(self, rng: t.Union[numpy.random.Generator, int, None] = None) -> AtomFrame:
        """
        Displace the atoms in `self` by the amount in the `wobble` column.
        `wobble` is interpretated as a mean-squared displacement, which is distributed
        equally over each axis.
        """
        if 'wobble' not in self:
            return self
        rng = numpy.random.default_rng(seed=rng)

        stddev = self.select((polars.col('wobble') / 3.).sqrt()).to_series().to_numpy()
        coords = self.coords()
        coords += stddev[:, None] * rng.standard_normal(coords.shape)
        return self.with_coords(coords)

    def apply_occupancy(self, rng: t.Union[numpy.random.Generator, int, None] = None) -> AtomFrame:
        """
        For each atom in `self`, use its `frac_occupancy` to randomly decide whether to remove it.
        """
        if 'frac_occupancy' not in self:
            return self
        rng = numpy.random.default_rng(seed=rng)

        frac = self.select('frac_occupancy').to_series().to_numpy()
        choice = rng.binomial(1, frac).astype(numpy.bool_)
        return self.filter(polars.lit(choice))

    def with_type(self, types: t.Optional[AtomValues] = None) -> AtomFrame:
        """
        Return `self` with the given atom types in column 'types'.
        If `types` is not specified, use the already existing types or auto-assign them.

        When auto-assigning, each symbol is given a unique value, case-sensitive.
        Values are assigned from lowest atomic number to highest.
        For instance: ["Ag+", "Na", "H", "Ag"] => [3, 11, 1, 2]
        """
        if types is not None:
            return self.with_column(_values_to_expr(types, polars.Int32).alias('type'))
        if 'type' in self:
            return self

        unique = self.unique(maintain_order=False, subset=['elem', 'symbol']).sort(['elem', 'symbol'])
        new = self.with_column(polars.Series('type', values=numpy.zeros(len(self)), dtype=polars.Int32))

        logging.warning("Auto-assigning element types")
        for (i, (elem, sym)) in enumerate(unique.select(('elem', 'symbol')).rows()):
            print(f"Assigning type {i+1} to element '{sym}'")
            new = new.with_column(polars.when((polars.col('elem') == elem) & (polars.col('symbol') == sym))
                                        .then(polars.lit(i+1))
                                        .otherwise(polars.col('type'))
                                        .alias('type'))

        assert (new.get_column('type') == 0).sum() == 0
        return new

    def with_mass(self, mass: t.Optional[ArrayLike] = None) -> AtomFrame:
        """
        Return `self` with the given atom masses in column 'mass'.
        If `mass` is not specified, use the already existing masses or auto-assign them.
        """
        if mass is not None:
            return self.with_column(_values_to_expr(mass, polars.Float32).alias('mass'))
        if 'mass' in self:
            return self

        unique_elems = self.get_column('elem').unique()
        new = self.with_column(polars.Series('mass', values=numpy.zeros(len(self)), dtype=polars.Float32))

        logging.warning("Auto-assigning element masses")
        for elem in unique_elems:
            new = new.with_column(polars.when(polars.col('elem') == elem)
                                        .then(polars.lit(get_mass(elem)))
                                        .otherwise(polars.col('mass'))
                                        .alias('mass'))

        assert (new.get_column('mass').abs() < 1e-10).sum() == 0
        return new

    def with_coords(self, pts: ArrayLike, selection: t.Optional[AtomSelection] = None) -> AtomFrame:
        """
        Return `self` replaced with the given atomic positions.
        """
        if selection is not None:
            selection = _selection_to_series(self, selection).to_numpy()
            new_pts = self.coords()
            pts = numpy.atleast_2d(pts)
            assert pts.shape[-1] == 3
            new_pts[selection] = pts
            pts = new_pts

        pts = numpy.broadcast_to(pts, (len(self), 3))
        return self.__class__(self.with_columns((
            polars.Series(pts[:, 0], dtype=polars.Float64).alias('x'),
            polars.Series(pts[:, 1], dtype=polars.Float64).alias('y'),
            polars.Series(pts[:, 2], dtype=polars.Float64).alias('z'),
        )))

    def with_velocity(self, pts: t.Optional[ArrayLike] = None, selection: t.Optional[AtomSelection] = None) -> AtomFrame:
        """
        Return `self` replaced with the given atomic velocities.
        If `pts` is not specified, use the already existing velocities or zero.
        """
        if pts is None:
            if all(col in self for col in ('v_x', 'v_y', 'v_z')):
                return self
            pts = numpy.zeros((len(self), 3))
        else:
            pts = numpy.broadcast_to(pts, (len(self), 3))

        if selection is None:
            return self.__class__(self.with_columns((
                polars.Series(pts[:, 0], dtype=polars.Float64).alias('v_x'),
                polars.Series(pts[:, 1], dtype=polars.Float64).alias('v_y'),
                polars.Series(pts[:, 2], dtype=polars.Float64).alias('v_z'),
            )))

        selection = _selection_to_series(self, selection)
        return self.__class__(self.with_columns((
            self['v_x'].set_at_idx(selection, pts[:, 0]),  # type: ignore
            self['v_y'].set_at_idx(selection, pts[:, 1]),  # type: ignore
            self['v_z'].set_at_idx(selection, pts[:, 2]),  # type: ignore
        )))

    @property
    def bbox(self) -> BBox:
        if self._bbox is None:
            self._bbox = BBox.from_pts(self.coords())

        return self._bbox

    def transform(self, transform: IntoTransform, selection: t.Optional[AtomSelection] = None, *, transform_velocities: bool = False) -> AtomFrame:
        transform = Transform.make(transform)
        selection = map_some(lambda s: _selection_to_series(self, s), selection)
        transformed = self.with_coords(Transform.make(transform) @ self.coords(selection), selection)
        # try to transform velocities as well
        if transform_velocities and (velocities := self.velocities(selection)) is not None:
            return transformed.with_velocity(transform.transform_vec(velocities), selection)
        return transformed

    def round_near_zero(self, tolerance: float = 1e-14) -> AtomFrame:
        return self.with_columns(tuple(
            polars.when(col.abs() >= tolerance).then(col).otherwise(polars.lit(0.))
            for col in map(polars.col, ('x', 'y', 'z'))
        ))

    def deduplicate(self, tolerance: float = 1e-3, cols: t.Iterable[str] = ('x', 'y', 'z', 'symbol')) -> AtomFrame:
        """
        De-duplicate atoms in `self`. Atoms of the same `symbol` that are closer than `tolerance`
        to each other (by Euclidian distance) will be removed, leaving only the first atom.

        If `cols` is specified, only those columns will be included while assessing duplicates.
        Floating point columns other than 'x', 'y', and 'z' will not by toleranced.
        """

        cols = set((cols,) if isinstance(cols, str) else cols)

        indices = numpy.arange(len(self))

        spatial_cols = cols.intersection(('x', 'y', 'z'))
        cols -= spatial_cols
        if len(spatial_cols) > 0:
            coords = self.select(list(spatial_cols)).to_numpy()
            print(coords.shape)
            tree = scipy.spatial.KDTree(coords)

            # TODO This is a bad algorithm
            while True:
                changed = False
                for (i, j) in tree.query_pairs(tolerance, 2.):
                    # whenever we encounter a pair, ensure their index matches
                    i_i, i_j = indices[[i, j]]
                    if i_i != i_j:
                        indices[i] = indices[j] = min(i_i, i_j)
                        changed = True
                if not changed:
                    break

        self = self.with_column(polars.Series('_unique_pts', indices))
        cols.add('_unique_pts')
        return self.unique(subset=list(cols)).drop('_unique_pts')

    def __eq__(self, other) -> bool:
        if not isinstance(other, AtomFrame):
            return False
        if not self.schema == other.schema:
            return False
        for (col, dtype) in self.schema.items():
            if dtype in (polars.Float32, polars.Float64):
                eq = numpy.allclose(self[col].view(True), other[col].view(True))
            else:
                eq = self[col] == other[col]
            if not eq:
                return False
        return True


AtomSelection = t.Union[polars.Series, polars.Expr, ArrayLike]
"""
Polars expression selecting a subset of atoms from an AtomFrame.
Can be used with many AtomFrame methods.
"""

AtomValues = t.Union[polars.Series, polars.Expr, ArrayLike]
"""
Array, value, or polars expression mapping atoms to values.
Can be used with `with_*` methods on AtomFrame
"""

__ALL__ = [
    'AtomFrame', 'IntoAtoms', 'AtomSelection', 'AtomValues',
]
