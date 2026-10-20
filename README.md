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
├── preprocesar.py          # Reprocesa TODO desde cero (datos + límites)
├── actualizar_2026.py      # Actualización incremental del año en curso
├── reporte.py              # Módulo que genera el reporte PDF (lo usa app.py)
├── renombrar_dptos.py      # Normaliza los nombres de los GeoJSON exportados
├── requirements.txt
├── README.md
├── logo/                   # Logo institucional (UNGRD)
├── divipola/               # Límites DANE crudos (pesados, no se suben al repo)
│   ├── departamentos.geojson
│   └── municipios.geojson
└── data/
    ├── 2019/ … 2026/          # GeoJSON de incendios por año (crudos, gitignored)
    └── processed/             # <- lo único que lee el dashboard
        ├── incendios.parquet          # tabla principal (se sube a GitHub)
        ├── incendios.csv              # respaldo local (gitignored)
        ├── departamentos_simplificado.geojson
        └── municipios_simplificado.geojson
```

Cobertura actual de datos: **2019 – 2026**.

---

## Scripts

| Script | Qué hace | Cuándo correrlo |
|---|---|---|
| `app.py` | El dashboard (Streamlit). Lee `data/processed/`. | Para visualizar. |
| `preprocesar.py` | Reprocesa **todo** desde cero: spatial join de los incendios contra `divipola/` y genera `data/processed/` (parquet, csv y los dos GeoJSON simplificados). | Al agregar un año nuevo o cuando quieras **regenerar los límites**. |
| `actualizar_2026.py` | Actualización **incremental** solo del año en curso (`data/2026/`): deduplica por `id` y lo fusiona con los años cerrados. **No** regenera los límites. Rápido (~1 min). | Cada vez que descargas nuevos registros del año en curso. |
| `reporte.py` | Genera el PDF ejecutivo (lo invoca `app.py`). | No se ejecuta solo. |
| `renombrar_dptos.py` | Normaliza los nombres de los GeoJSON exportados de OroraTech. Trae `SIMULAR=True` (modo prueba). | Tras exportar archivos nuevos del portal. |

> `preprocesar.py` y `actualizar_2026.py` necesitan la carpeta `divipola/`
> (límites crudos del DANE) presente localmente.

---

## Uso local

```bash
pip install -r requirements.txt
```

**Visualizar el dashboard:**

```bash
python -m streamlit run app.py
```

**Actualizar los datos del año en curso** (lo más frecuente): coloca los nuevos
GeoJSON en `data/2026/` y corre:

```bash
python actualizar_2026.py
```

**Reproceso completo** (agregar un año nuevo o regenerar los límites):

```bash
python preprocesar.py
```

Tras actualizar los datos, en la app desplegada pulsa **🔄 Recargar datos**
(o *Reboot app* en Streamlit Cloud) para que tome el `parquet` nuevo.

---

## Publicar en Streamlit Community Cloud

1. Sube el repositorio a GitHub (ver nota sobre tamaños abajo).
2. Entra a https://share.streamlit.io e inicia sesión con GitHub.
3. "New app" → elige el repo, la rama y `app.py` como archivo principal.
4. Deploy. La app queda pública en una URL `*.streamlit.app`.

### Qué subir a GitHub

El dashboard **solo necesita `data/processed/`**: el `incendios.parquet`
(~35 MB) y los dos GeoJSON simplificados. El `incendios.csv` (respaldo legible,
>100 MB) **no** se sube. Los GeoJSON crudos de incendios y los límites DIVIPOLA
sin simplificar son pesados y tampoco van al repo. El `.gitignore` del proyecto
ya está configurado así:

```gitignore
divipola/                  # límites DANE crudos (pesados)
data/20*/                  # GeoJSON de incendios crudos de todos los años
*copia/                    # carpetas de respaldo "... - copia"
data/processed/incendios.csv   # el CSV pesado (se sube solo el parquet)
```

> Recuerda: GitHub rechaza archivos de más de 100 MB. El preprocesamiento ya
> simplifica los límites y el `parquet` queda muy por debajo de ese límite.

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
- **Mapa base:** Esri *World Light Gray* (gratuito, sin API key).
- El **mapa nacional** ofrece tres métodos de clasificación: *Cortes naturales
  (Jenks)*, *Rangos técnicos (alerta)* —5 niveles fijos con el superior abierto
  (Crítico ≥ 4000)— y *Cuantiles*.
