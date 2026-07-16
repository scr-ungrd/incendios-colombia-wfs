# 🔥 Dashboard de incendios en Colombia

> Este repositorio es una copia sincronizada del proyecto original desarrollado por
> [@jorgealpala](https://github.com/jorgealpala) en
> [jorgealpala/incendios-colombia-wfs](https://github.com/jorgealpala/incendios-colombia-wfs).
> Un workflow (`.github/workflows/sync-upstream.yml`) trae automáticamente los cambios
> del repositorio original hacia este.

Visor interactivo de incendios en Colombia a partir de los datos de
**OroraTech WildFire Solution**. Construido con Streamlit + Folium.

El dashboard funciona por **tres niveles**:

- **Nacional** — mapa coroplético: cada departamento coloreado según el número
  de eventos registrados.
- **Departamental** — mapa de calor (densidad) de incendios dentro del
  departamento seleccionado.
- **Municipal** — puntos de incendio coloreados por nivel de confianza
  (verde = baja, amarillo = media, naranja = alta, rojo = muy alta).

La navegación funciona tanto con **menús desplegables** como con **clics en el
mapa** (al hacer clic en un departamento se baja al nivel departamental).

---

## Estructura del proyecto

```
incendios-colombia-wfs/
├── app.py                  # El dashboard (Streamlit)
├── preprocesar.py          # Genera los datos procesados (correr 1 vez)
├── requirements.txt
├── README.md
├── divipola/
│   ├── departamentos.geojson   # Límites DANE (no se suben si pesan mucho)
│   └── municipios.geojson
└── data/
    ├── 2024/                   # GeoJSON de incendios por año
    ├── 2025/
    └── processed/              # <- lo lee el dashboard
        ├── incendios.parquet
        ├── incendios.csv
        ├── departamentos_simplificado.geojson
        └── municipios_simplificado.geojson
```

---

## Uso local

```bash
pip install -r requirements.txt

# 1. Generar los datos procesados (solo cuando agregas/cambias datos)
python preprocesar.py

# 2. Lanzar el dashboard
streamlit run app.py
```

---

## Publicar en Streamlit Community Cloud

1. Sube el repositorio a GitHub (ver nota sobre tamaños abajo).
2. Entra a https://share.streamlit.io e inicia sesión con GitHub.
3. "New app" → elige el repo, la rama y `app.py` como archivo principal.
4. Deploy. La app queda pública en una URL `*.streamlit.app`.

### Qué subir a GitHub

El dashboard **solo necesita la carpeta `data/processed/`** (unos ~14 MB).
Los GeoJSON crudos de incendios y los límites DIVIPOLA sin simplificar son
pesados; puedes mantenerlos fuera del repo con un `.gitignore`:

```gitignore
# Datos crudos y límites pesados (no necesarios para el dashboard)
divipola/municipios.geojson
divipola/departamentos.geojson
data/2024/*.geojson
data/2025/*.geojson

# Mantener los procesados (descomenta la línea siguiente si tu .gitignore
# excluye geojson de forma global):
# !data/processed/*.geojson
```

> Recuerda: GitHub rechaza archivos de más de 100 MB. El preprocesamiento ya
> simplifica los límites para que queden muy por debajo de ese límite.

---

## Niveles de confianza

El campo `confidence` (0 a 1) de OroraTech se mapea así:

| Valor        | Nivel      | Color    |
|--------------|------------|----------|
| 0.0          | Insuficiente | gris   |
| 0.2          | Baja       | 🟢 verde  |
| 0.4          | Media      | 🟡 amarillo |
| 0.6          | Alta       | 🟠 naranja |
| 0.8 – 1.0    | Muy alta   | 🔴 rojo   |

Los incidentes con datos insuficientes están ocultos por defecto y se pueden
mostrar con el checkbox de la barra lateral.

---

## Notas

- **Bogotá** se trata como una unidad propia (igual que la plataforma
  OroraTech), aunque geográficamente pertenezca a Cundinamarca.
- La asignación de cada incendio a su departamento/municipio se hace por la
  ubicación de su centroide (spatial join), no por el nombre del archivo,
  así que es robusta aunque el campo `sub_area_name` venga vacío.
