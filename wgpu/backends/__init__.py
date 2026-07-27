"""Kept so ``wgpu.backends.wgpu_native`` keeps resolving.

wgpu-py used to select a backend at runtime; there is only one now, statically
linked into the package. The import path survives because downstream code --
pygfx above all -- imports wgpu-native's extra features from it.
"""
