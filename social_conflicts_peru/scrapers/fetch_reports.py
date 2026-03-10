import os
import re
import requests
import httpx
import lxml.html
from urllib.parse import urljoin
import time
from pathlib import Path
from ..config import settings, directories

BASE_URL = "https://www.defensoria.gob.pe/categorias_de_documentos/reportes/"

def extract_numbers(text: str):
    '''
    Helper function to extract the number or id from the file name
    '''
    numbers = re.search(r'(?<=\s)\d+(?=\s)', text).group()
    return numbers

def parse_url(url:str) -> lxml.html.HtmlElement:
    """
    Returns the html of the url parse ready to use
    """
    text = httpx.get(url, follow_redirects = True).text
    return lxml.html.fromstring(text)

def get_pdf_links(page_url: str) -> list:
    '''
    Function to obtain a list of links of the conflicts reports in a page.

    Args:
        - page_url: url from where to extract the .pdf links

    Returns: list of reports' link
    '''
    # Fetching the data
    parse = parse_url(page_url)

    # Getting all the pdf links into a list
    card_lst = parse.xpath('//*[@class="card-body"]')
    text_header = [elem.getchildren()[0].getchildren()[0].text for elem in card_lst]
    pdf_links = [elem.getchildren()[0].get("href") for ix, elem in enumerate(card_lst) if "conflictos" in text_header[ix]]
    
    id_report = [extract_numbers(elem) for elem in text_header if "conflictos" in elem]
    return {id_report[ix]:pdf_links[ix] for ix, _ in enumerate(id_report)}

def get_all_links(base_url):
    '''
    Function to obtain the complete list of links of the conflicts reports.

    Args:
        - base_url: str

    Returns: list of reports' link
    '''
    page_num = 1
    final_lst = []
    while True:
        page_url = base_url.format(page_num)
        response = requests.get(page_url)
        if response.status_code != 200:
            break
        final_lst.extend(get_pdf_links(page_url))
        page_num += 1
        time.sleep(4)
    return final_lst





