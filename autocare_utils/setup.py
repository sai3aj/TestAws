from setuptools import setup, find_packages

setup(
    name="autocare_utils",
    version="0.1.0",
    author="Arbaz",
    author_email="arbaz.khan@gmail.com",
    description="Utility functions for automotive service appointment validation",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=[
        'datetime',
    ],
) 