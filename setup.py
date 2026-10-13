"""Optional native parsers; all package metadata lives in pyproject.toml."""

from setuptools import Extension, setup

setup(ext_modules=[
    Extension("pyqld._codec_native", ["pyqld/_codec_native.c"], optional=True),
    Extension("pyqld._dbf_native", ["pyqld/_dbf_native.c"], optional=True),
])
