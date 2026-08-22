from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in manufacturer_plus/__init__.py
from manufacturer_plus import __version__ as version

setup(
	name="manufacturer_plus",
	version=version,
	description="Manufacturer Plus",
	author="Totrox Technology",
	author_email="info.totrox.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
