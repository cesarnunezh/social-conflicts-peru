# Socio-Environmental Conflicts in Perú's Extractive Industry

## Overview
This project analyzes socio-environmental conflicts in Perú's extractive industry, focusing on disputes related to mining, oil, and gas operations. Using data from official sources and web scraping techniques, the project compiles and visualizes conflict trends over time and across regions, as well as creating a database at a conflict level to compute a Natural Language Processing analysis of the monthly occurrences.

## Data Sources
- **Defensoría del Pueblo**: Monthly reports on social conflicts which are located at their [website](https://www.defensoria.gob.pe/categorias_de_documentos/reportes/).

## Methodology
- **Data Collection**: Automated extraction of reports and structured data processing.
- **Natural Language Processing (NLP)**: Text mining for key conflict topics and actors.
- **Visualization**: Interactive dashboards for data exploration.

## Tools Used
- **Programming Languages**: Python
- **Data Processing**: Pandas, PyPDF2, PyMuPDF(fitz)
- **Web Scraping**: BeautifulSoup
- **Natural Language Processing**: NLTK

## Project Structure
```
├── data/               # Raw and processed datasets
├── notebooks/          # Jupyter/R notebooks for analysis
├── scripts/            # Data extraction and processing scripts
├── reports/            # Analytical reports and insights
├── dashboards/         # Interactive visualizations
└── README.md           # Project documentation
```

## How to use
1. Clone the repository:
```bash
git clone https://github.com/yourusername/socio-env_conflicts_peru.git
```
2. Install dependencies:
```bash
pip install -r requirements.txt
```   
3. Run data processing scripts:
```bash
python scripts/data_extraction.py
```

