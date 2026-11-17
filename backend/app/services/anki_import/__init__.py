"""Anki deck import (Requirement 2).

The import is a two-step flow: upload -> Import_Preview, then confirm ->
Import_Summary. Every decision is a pure function in this package; only the
upload reader, the package reader, and the final database write touch I/O.
"""
