"""Virtual types used in annotations.

These stand in for things we deliberately do not depend on: any buffer-like
object (rather than ``numpy.typing.ArrayLike``, which would drag in numpy) and
any canvas object (rendercanvas is optional).
"""

ArrayLike = memoryview | object
CanvasLike = object
