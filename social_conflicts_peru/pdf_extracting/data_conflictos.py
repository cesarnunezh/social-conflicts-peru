import os
import pandas as pd
import re
import time
import fitz  # PyMuPDF

start_time = time.time()

# 1. Generamos las funciones que utilizaremos para cada pdf
def extract_text_after_target(pdf_path, target_text):
    pdf_document = fitz.open(pdf_path)

    for page_num in range(pdf_document.page_count):
        page = pdf_document.load_page(page_num)
        text = page.get_text()

        if target_text in text:
            target_index = text.index(target_text)
            extracted_text = text[target_index + len(target_text):]
            return extracted_text.strip()

    return None

# 2. Ruta al archivo PDF que deseas procesar y manejo de los pdf que utilizaremos
os.chdir("C:/Users/User/Documents/GitHub/CNHGitHub/Data Perú/Conflictos sociales/")
folder_path = "C:/Users/User/Documents/GitHub/CNHGitHub/Data Perú/Conflictos sociales/pdf_reports_conflictos_sociales/"

files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]

sorted_files = sorted(files, key=lambda x: os.path.getmtime(os.path.join(folder_path, x)))

n_reportes = []
contador = 0
for item in sorted_files:
    name = item[:3]
    try:
        name = int(name)
    except:
        name = item[:2]
        try:
            name = int(name)
        except:
            name = item[:1]
    n_reportes.append(name)
    contador = contador + 1

contador = 0
for pdf in sorted_files:
    try:
        if n_reportes[contador] > 126 :
            pdf_path = folder_path + pdf
            target_text = "PERÚ: CONFLICTOS SOCIOAMBIENTALES ACTIVOS,"
            extracted_text = extract_text_after_target(pdf_path, target_text)

            lines = extracted_text.strip().split('\n')
            month_year = lines[0].rsplit(' ', 3)
            data_lines = lines[5:]
            # Initialize empty lists to store data
            data = []
            columns = ['Actividad', 'Conteo', '%']

            # Loop through each line and split by spaces
            for i in range(0, len(data_lines)-1, 3):
                actividad = data_lines[i]
                conteo = data_lines[i + 1]
                porcentaje = data_lines[i + 2]

                data.append([actividad, conteo, porcentaje])

            locals()[f'df_{n_reportes[contador]}']= pd.DataFrame(data, columns=columns)
            locals()[f'df_{n_reportes[contador]}']['Mes'] = month_year[1]
            locals()[f'df_{n_reportes[contador]}']['Año'] = month_year[2]

            if contador == 0:
                df_final = locals()[f'df_{n_reportes[contador]}']
            else:
                df_final = pd.concat([df_final,locals()[f'df_{n_reportes[contador]}']], ignore_index=True)
            del locals()[f'df_{n_reportes[contador]}']
        else:
            print("otra")
        contador = contador + 1
    except:
        contador = contador + 1
        continue


# Code or process to measure
end_time = time.time()

elapsed_time = end_time - start_time
print("Elapsed time:", elapsed_time, "seconds")

# TEXTO A BUSCAR entre Agosto 2014 a la fecha | 126 a 233 | "PERÚ: CONFLICTOS SOCIOAMBIENTALES ACTIVOS, SEGÚN ACTIVIDAD, "
# Texto a buscar entre Marzo 2013 y Julio 2014 | 109 a 125 | conflictos socioambientales activos de acuerdo
# Texto a buscar entre Noviembre 2012  y Febrero 2013 | 105 a 108 | Conflictos socioambientales según sector
# Antes no hay info de minería

'''
pdf_path = 'C:/Users/User/Documents/GitHub/CNHGitHub/Data Perú/Conflictos sociales/pdf_reports_conflictos_sociales/216_202_2.pdf'
target_text = "PERÚ: CONFLICTOS SOCIOAMBIENTALES ACTIVOS,"
extracted_text = extract_text_after_target(pdf_path, target_text)

lines = extracted_text.strip().split('\n')
month_year = lines[0].rsplit(' ', 3)
data_lines = lines[5:]
# Initialize empty lists to store data
data = []
columns = ['Actividad', 'Conteo', 'percent']

for i in range(0, len(data_lines)-1, 3):
    actividad = data_lines[i]
    conteo = data_lines[i + 1]
    porcentaje = data_lines[i + 2]

    data.append([actividad, conteo, porcentaje])

df_prueba = pd.DataFrame(data, columns=columns)
df_prueba['Mes'] = month_year[1]
df_prueba['Año'] = month_year[2]

'''