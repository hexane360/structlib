from __future__ import annotations

from abc import ABC, abstractmethod
import typing as t

import numpy

from .vec import Vec3, BBox


TransformT = t.TypeVar('TransformT', bound='Transform')
PtsLike = t.Union[BBox, Vec3, t.Sequence[Vec3], numpy.ndarray]
PtsT = t.TypeVar('PtsT', bound=PtsLike)
VecLike = t.Union[t.Sequence[float], t.Sequence[int], Vec3, numpy.ndarray]
Num = t.Union[float, int]
NumT = t.TypeVar('NumT', bound=t.Union[float, int])
P = t.ParamSpec('P')
T = t.TypeVar('T')
U = t.TypeVar('U')

AffineSelf = t.TypeVar('AffineSelf', bound=t.Union['AffineTransform', t.Type['AffineTransform']])


def opt_classmethod(func: t.Callable[t.Concatenate[T, P], U]
) -> t.Callable[t.Concatenate[t.Union[T, t.Type[T]], P], U]:

    def inner(cls: t.Union[T, t.Type[T]], *args: P.args, **kwargs: P.kwargs):
        self = t.cast(T, cls() if isinstance(cls, t.Type) else cls)
        return func(self, *args, **kwargs)

    return inner


class Transform(ABC):
	@staticmethod
	@abstractmethod
	def identity() -> Transform:
		...

	@abstractmethod
	def compose(self, other: Transform) -> Transform:
		...

	@t.overload
	@abstractmethod
	def transform(self, points: Vec3) -> Vec3:
		...

	@t.overload
	@abstractmethod
	def transform(self, points: BBox) -> BBox:
		...
	
	@t.overload
	@abstractmethod
	def transform(self, points: t.Union[numpy.ndarray, t.Sequence[Vec3]]) -> numpy.ndarray:
		...

	@abstractmethod
	def transform(self, points: PtsLike) -> t.Union[Vec3, BBox, numpy.ndarray]:
		...

	__call__ = transform
	
	@t.overload
	def __matmul__(self, other: Transform) -> Transform:
		...

	@t.overload
	def __matmul__(self, other: BBox) -> BBox:
		...
	
	@t.overload
	def __matmul__(self, other: t.Union[numpy.ndarray, t.Sequence[Vec3]]) -> numpy.ndarray:
		...

	@t.overload
	def __matmul__(self, other: Vec3) -> Vec3:
		...

	def __matmul__(self, other: t.Union[Transform, PtsLike]) -> t.Union[Transform, numpy.ndarray, BBox]:
		if isinstance(other, Transform):
			return other.compose(self)
		return self.transform(other)

	def __rmatmul__(self, other):
		raise ValueError("Transform must be applied to points, not the other way around.")


class AffineTransform(Transform):
	__array_ufunc__ = None

	def __init__(self, array=None):
		if array is None:
			array = numpy.eye(4)
		self.inner = numpy.broadcast_to(array, (4, 4))

	@property
	def __array_interface__(self):
		return self.inner.__array_interface__

	def __repr__(self) -> str:
		return f"AffineTransform(\n{self.inner!r}\n)"

	@classmethod
	def identity(cls: t.Type[TransformT]) -> TransformT:
		return cls()

	@staticmethod
	def from_linear(linear: LinearTransform) -> AffineTransform:
		return AffineTransform(numpy.block([
			[linear.inner, numpy.zeros((3, 1))],
			[numpy.zeros((1, 3)), 1]
		]))  # type: ignore

	def to_linear(self) -> LinearTransform:
		return LinearTransform(self.inner[:3, :3])

	def det(self) -> float:
		return numpy.linalg.det(self.inner[:3, :3])

	def _translation(self) -> numpy.ndarray:
		return self.inner[:3, -1]

	def inverse(self) -> AffineTransform:
		linear_inv = LinearTransform(self.inner[:3, :3]).inverse()
		return linear_inv.compose(AffineTransform().translate(*(linear_inv @ -self._translation())))

	@t.overload
	@classmethod
	def translate(cls, x: VecLike, /) -> AffineTransform:
		...

	@t.overload
	@classmethod
	def translate(cls, x: Num = 0., y: Num = 0., z: Num = 0.) -> AffineTransform:
		...

	@classmethod
	@opt_classmethod
	def translate(cls, x: t.Union[Num, VecLike] = 0., y: Num = 0., z: Num = 0.) -> AffineTransform:
		self = cls() if isinstance(cls, type) else cls

		if isinstance(x, t.Sized) and len(x) > 1:
			if not (y == 0. and z == 0. and len(x) == 3):
				raise ValueError("translate() must be called with a sequence or three numbers.")
			(x, y, z) = map(float, x)

		if isinstance(self, LinearTransform):
			self = AffineTransform.from_linear(self)

		a = self.inner.copy()
		a[:, -1] += [x, y, z, 0]
		return AffineTransform(a)

	@t.overload
	@classmethod
	def scale(cls, x: VecLike, /) -> AffineTransform:
		...

	@t.overload
	@classmethod
	def scale(cls, x: Num = 1., y: Num = 1., z: Num = 1., *,
	          all: Num = 1.) -> AffineTransform:
		...

	@classmethod
	def scale(cls, x: t.Union[Num, VecLike] = 1., y: Num = 1., z: Num = 1., *,
	          all: Num = 1.) -> AffineTransform:
		self = cls() if isinstance(cls, type) else cls
		return self.compose(LinearTransform().scale(x, y, z, all=all))  # type: ignore

	@classmethod
	def rotate(cls, v: VecLike, theta: Num) -> AffineTransform:
		self = cls() if isinstance(cls, type) else cls
		return self.compose(LinearTransform().rotate(v, theta))

	@classmethod
	def rotate_euler(cls, x: Num = 0., y: Num = 0., z: Num = 0.) -> AffineTransform:
		self = cls() if isinstance(cls, type) else cls
		return self.compose(LinearTransform().rotate_euler(x, y, z))

	@t.overload
	@classmethod
	def mirror(cls, a: VecLike, /) -> AffineTransform:
		...
	
	@t.overload
	@classmethod
	def mirror(cls, a: Num, b: Num, c: Num) -> AffineTransform:
		...

	@classmethod
	def mirror(cls, a: t.Union[Num, VecLike],
	           b: t.Optional[Num] = None,
	           c: t.Optional[Num] = None) -> AffineTransform:
		self = cls() if isinstance(cls, type) else cls
		return self.compose(LinearTransform.mirror(a, b, c))  # type: ignore

	@t.overload
	def transform(self, points: Vec3) -> Vec3:
		...

	@t.overload
	def transform(self, points: BBox) -> BBox:
		...
	
	@t.overload
	def transform(self, points: t.Union[numpy.ndarray, t.Sequence[Vec3]]) -> numpy.ndarray:
		...

	def transform(self, points: PtsLike) -> t.Union[numpy.ndarray, BBox]:
		if isinstance(points, BBox):
			return points.from_pts(self.transform(points.corners()))

		points = numpy.atleast_1d(points)
		pts = numpy.concatenate((points, numpy.broadcast_to(1., (*points.shape[:-1], 1))), axis=-1)
		result = (self.inner @ pts.T)[:3].T
		return result.view(Vec3) if isinstance(points, Vec3) else result

	__call__ = transform

	@t.overload
	def compose(self, other: AffineTransform) -> AffineTransform:
		...

	@t.overload
	def compose(self, other: Transform) -> Transform:
		...

	def compose(self, other: Transform) -> Transform:
		if not isinstance(other, Transform):
			raise TypeError(f"Expected a Transform, got {type(other)}")
		if isinstance(other, LinearTransform):
			return self.compose(AffineTransform.from_linear(other))
		if isinstance(other, AffineTransform):
			return AffineTransform(other.inner @ self.inner)
		else:
			raise NotImplementedError()

	@t.overload
	def __matmul__(self, other: AffineTransform) -> AffineTransform:
		...

	@t.overload
	def __matmul__(self, other: Transform) -> Transform:
		...

	@t.overload
	def __matmul__(self, other: numpy.ndarray) -> numpy.ndarray:
		...

	@t.overload
	def __matmul__(self, other: Vec3) -> Vec3:
		...

	@t.overload
	def __matmul__(self, other: BBox) -> BBox:
		...

	@t.overload
	def __matmul__(self, other: t.Union[numpy.ndarray, t.Sequence[Vec3]]) -> numpy.ndarray:
		...

	def __matmul__(self, other: t.Union[Transform, numpy.ndarray, t.Sequence[Vec3], Vec3, BBox]):
		if isinstance(other, Transform):
			return other.compose(self)
		return self.transform(other)


class LinearTransform(AffineTransform):
	def __init__(self, array=None):
		if array is None:
			array = numpy.eye(3)
		self.inner = numpy.broadcast_to(array, (3, 3))

	@property
	def T(self):
		return LinearTransform(self.inner.T)

	def __repr__(self) -> str:
		return f"LinearTransform(\n{self.inner!r}\n)"

	def _translation(self):
		# not defined for LinearTransform
		raise NotImplementedError()

	@staticmethod
	def identity() -> LinearTransform:
		return LinearTransform()

	def det(self) -> float:
		return numpy.linalg.det(self.inner)

	def inverse(self) -> LinearTransform:
		return LinearTransform(numpy.linalg.inv(self.inner))

	def to_linear(self) -> LinearTransform:
		return self
	
	@t.overload
	@classmethod
	def mirror(cls, a: VecLike, /) -> LinearTransform:
		...
	
	@t.overload
	@classmethod
	def mirror(cls, a: Num, b: Num, c: Num) -> LinearTransform:
		...

	@classmethod
	def mirror(cls, a: t.Union[Num, VecLike],
	           b: t.Optional[Num] = None,
	           c: t.Optional[Num] = None) -> LinearTransform:
		self = cls() if isinstance(cls, type) else cls
		if isinstance(a, t.Sized):
			v = numpy.array(numpy.broadcast_to(a, 3), dtype=float)
			if b is not None or c is not None:
				raise ValueError("mirror() must be passed a sequence or three numbers.")
		else:
			v = numpy.array([a, b, c], dtype=float)
		v /= numpy.linalg.norm(v)
		mirror = numpy.eye(3) - 2 * numpy.outer(v, v)
		return LinearTransform(mirror @ self.inner)

	@classmethod
	def rotate(cls, v: VecLike, theta: Num) -> LinearTransform:
		self = cls() if isinstance(cls, type) else cls
		v = numpy.array(numpy.broadcast_to(v, (3,)), dtype=float)
		v /= numpy.linalg.norm(v)

		# Rodrigues rotation formula
		w = numpy.array([[   0, -v[2],  v[1]],
		                 [ v[2],    0, -v[0]],
		                 [-v[1], v[0],    0]])
		# I + sin(t) W + (1 - cos(t)) W^2 = I + sin(t) W + 2*sin^2(t/2) W^2
		a = numpy.eye(3) + numpy.sin(theta) * w + 2 * (numpy.sin(theta / 2)**2) * w @ w
		return LinearTransform(a @ self.inner)

	@classmethod
	def rotate_euler(cls, x: Num = 0., y: Num = 0., z: Num = 0.) -> LinearTransform:
		"""
		Rotate by the given Euler angles (in radians). Rotation is performed on the x axis
		first, then y axis and z axis.
		"""
		self = cls() if isinstance(cls, type) else cls

		angles = numpy.array([x, y, z], dtype=float)
		c, s = numpy.cos(angles), numpy.sin(angles)
		a = numpy.array([
			[c[1]*c[2], s[0]*s[1]*c[2] - c[0]*s[2], c[0]*s[1]*c[2] + s[0]*s[2]],
			[c[1]*s[2], s[0]*s[1]*s[2] + c[0]*c[2], c[0]*s[1]*s[2] - s[0]*c[2]],
			[-s[1],     s[0]*c[1],                  c[0]*c[1]],
		])
		return LinearTransform(a @ self.inner)

	@t.overload
	@classmethod
	def scale(cls, x: VecLike, /) -> LinearTransform:
		...

	@t.overload
	@classmethod
	def scale(cls, x: Num = 1., y: Num = 1., z: Num = 1., *,
	          all: Num = 1.) -> LinearTransform:
		...

	@classmethod
	def scale(cls, x: t.Union[Num, VecLike] = 1., y: Num = 1., z: Num = 1., *,
	          all: Num = 1.) -> LinearTransform:
		self = cls() if isinstance(cls, type) else cls

		if isinstance(x, t.Sized):
			v = numpy.broadcast_to(x, 3)
			if y != 1. or z != 1.:
				raise ValueError("scale() must be passed a sequence or three numbers.")
		else:
			v = numpy.array([x, y, z])

		a = numpy.zeros((3, 3))
		a[numpy.diag_indices(3)] = all * v
		return LinearTransform(a @ self.inner)
	
	def compose(self, other: TransformT) -> TransformT:
		if not isinstance(other, Transform):
			raise TypeError(f"Expected a Transform, got {type(other)}")
		if isinstance(other, LinearTransform):
			return other.__class__(other.inner @ self.inner)
		if isinstance(other, AffineTransform):
			return AffineTransform.from_linear(self).compose(other)

		raise NotImplementedError()

	@t.overload
	def transform(self, points: Vec3) -> Vec3:
		...

	@t.overload
	def transform(self, points: BBox) -> BBox:
		...
	
	@t.overload
	def transform(self, points: t.Union[numpy.ndarray, t.Sequence[Vec3]]) -> numpy.ndarray:
		...

	def transform(self, points: t.Union[numpy.ndarray, t.Sequence[Vec3], Vec3, BBox]):
		if isinstance(points, BBox):
			return points.from_pts(self.transform(points.corners()))

		points = numpy.atleast_1d(points)
		if points.shape[-1] != 3:
			raise ValueError(f"{self.__class__} works on 3d points only.")

		result = (self.inner @ points.T).T
		return result.view(Vec3) if isinstance(points, Vec3) else result

	@t.overload
	def __matmul__(self, other: TransformT) -> TransformT:
		...

	@t.overload
	def __matmul__(self, other: Vec3) -> Vec3:
		...

	@t.overload
	def __matmul__(self, other: BBox) -> BBox:
		...

	@t.overload
	def __matmul__(self, other: t.Union[numpy.ndarray, t.Sequence[Vec3]]) -> numpy.ndarray:
		...

	def __matmul__(self, other: t.Union[Transform, numpy.ndarray, t.Sequence[Vec3], Vec3, BBox]) -> t.Union[Transform, numpy.ndarray, Vec3, BBox]:
		if isinstance(other, Transform):
			return other.compose(self)
		return self.transform(other)



