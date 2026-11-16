"""Collect symbol image formats without Pillow's optional format backends."""

hiddenimports = ["PIL.BmpImagePlugin", "PIL.PngImagePlugin"]
excludedimports = ["PIL.ImageCms", "PIL.ImageQt", "PIL.ImageShow"]
