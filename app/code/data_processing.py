#cambio el link de url gardner y la url de Area de estudio a local
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

IMAGENES = BASE_DIR / "imagenes"

BARRAS_AE = IMAGENES / "barras AE"
PRESENCIA = IMAGENES / "barras presencia"

# barras presencia
ALTA = PRESENCIA / "Presencia_ALTA.png"
MEDIA = PRESENCIA / "Presencia_MEDIA.png"
BAJA = PRESENCIA / "Presencia_BAJA.png"

# barras AE
BARRAS = {
        0: BARRAS_AE / "Progreso_0%.png",
       10:  BARRAS_AE / "Progreso_10%.png",
       20:  BARRAS_AE / "Progreso_20%.png",
       30:  BARRAS_AE / "Progreso_30%.png",
       40:  BARRAS_AE / "Progreso_40%.png",
       50:  BARRAS_AE / "Progreso_50%.png",
       60:  BARRAS_AE / "Progreso_60%.png",
       70:  BARRAS_AE / "Progreso_70%.png",
       80:  BARRAS_AE / "Progreso_80%.png",
       90:  BARRAS_AE / "Progreso_90%.png",
      100:  BARRAS_AE / "Progreso_100%.png"
    }


def process_data(data):
    with open(data, "r", encoding="utf-8") as f:
            students = json.load(f)

    for student in students:

        for i in range(1, 9):

            #gardner
            Gindex = "% Gardner 0" + str(i)
            Gindex_url = "URL Gardner 0" + str(i)
            level = student[Gindex]
            if level == "ALTA":
                student[Gindex_url] = {"image": str(ALTA)}
            elif level == "MEDIA":
                student[Gindex_url] = {"image": str(MEDIA)}
            else:
                student[Gindex_url] = {"image": str(BAJA)}

            #areas de estudio
            AEindex =  "% AE 0" + str(i)
            AEindex_url = "URL AE 0" + str(i)

            #casos que no hay nada en ese campo
            if student[AEindex] == "":
                student[AEindex_url] = ""
                break

            porcentaje = student[AEindex]

            if porcentaje < 0.05:
                 student[AEindex_url] = {"image": str(BARRAS[0])}
            elif porcentaje < 0.15 :
                student[AEindex_url] = {"image": str(BARRAS[10])}
            elif porcentaje < 0.25 :
                student[AEindex_url] = {"image": str(BARRAS[20])}
            elif porcentaje < 0.35 :
                student[AEindex_url] = {"image": str(BARRAS[30])}
            elif porcentaje < 0.45 :
                student[AEindex_url] = {"image": str(BARRAS[40])}
            elif porcentaje < 0.55 :
                student[AEindex_url] = {"image": str(BARRAS[50])}
            elif porcentaje < 0.65 :
                student[AEindex_url] = {"image": str(BARRAS[60])}
            elif porcentaje < 0.75 :
                student[AEindex_url] = {"image": str(BARRAS[70])}
            elif porcentaje < 0.85 :
                student[AEindex_url] = {"image": str(BARRAS[80])}
            elif porcentaje < 0.95 :
                student[AEindex_url] = {"image": str(BARRAS[90])}
            else:
                student[AEindex_url] = {"image": str(BARRAS[100])}


    ruta = BASE_DIR /"data" /"data_url_actualizadas.json"

    with open(ruta, "w", encoding = "utf-8") as f:
        json.dump(students, f, ensure_ascii=False, indent=2)


data = BASE_DIR / "data" / "data.json"

if __name__ == "__main__":
    process_data(data)





