"""Import the product databases of the older KNX tools (``.vd1`` … ``.vd5``).

A ``.vd*`` file holds a relational product database as a table dump. It is converted to the XML
product data a ``.knxprod`` holds: the dump is first brought to the newest database schema, then
catalog, hardware and application programs are written as XML.
"""

from .archive import is_vd_file, read_dump
from .dump import Column, Dump, Record, Table, Value

__all__ = ["Column", "Dump", "Record", "Table", "Value", "is_vd_file", "read_dump"]
