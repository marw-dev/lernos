from setuptools import find_packages, setup

setup(
    name="lernos",
    version="1.8.6",
    description="Intelligentes Lern-Betriebssystem — Spaced Repetition + Wissensgraph",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "requests>=2.28",
        "colorama>=0.4",
        # PDF-Text-Extraktion
        "pdfplumber>=0.9",
        "pdfminer.six>=20221105",
        # Vision-Pipeline (optional aber empfohlen)
        "pdf2image>=1.16",  # benötigt Poppler (system package)
        "Pillow>=9.0",
    ],
    extras_require={
        "vision": [
            "pdf2image>=1.16",
            "Pillow>=9.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "lernos=lernos.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
