"""
============================================================================
DASHBOARD DE INCENDIOS - COLOMBIA  (OroraTech WildFire Solution)
============================================================================
Visor de 3 niveles (Nacional / Departamental / Municipal).
Metrica de confianza: confidence (valores numericos originales 0..1).
Filtro temporal: rango de fechas por 'oldest_acquisition' (inicio del incendio).

EJECUTAR LOCAL (Windows):
    python -m pip install -r requirements.txt
    python -m streamlit run app.py
============================================================================
"""

import json
import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st
import altair as alt
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium

import reporte  # modulo local para generar el PDF

st.set_page_config(page_title="Incendios Colombia | OroraTech", page_icon=":fire:", layout="wide")

RAIZ = Path(__file__).resolve().parent
DIR_PROC = RAIZ / "data" / "processed"
F_PARQUET = DIR_PROC / "incendios.parquet"
F_CSV     = DIR_PROC / "incendios.csv"
F_DEPTOS = DIR_PROC / "departamentos_simplificado.geojson"
F_MUNIS  = DIR_PROC / "municipios_simplificado.geojson"

# --- Mapa base (tiles) ---
# CARTO (cartodbpositron) pasó a exigir API key al servirse desde dominios como
# streamlit.app, mostrando la marca de agua "API KEY REQUIRED". Usamos el
# basemap claro de Esri (World Light Gray), gratuito y sin clave, que conserva
# el mismo estilo limpio. Centralizado aquí para que los 3 mapas usen el mismo.
TILES_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
             "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}")
TILES_ATTR = ("Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ · "
              "Datos © OpenStreetMap contributors")

# --- confidence (unica metrica): valor numerico -> color en escala verde->rojo ---
ORDEN_CONF = ["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]
COLOR_CONF = {
    "0.0": "#95a5a6",  # gris (datos insuficientes)
    "0.2": "#2ecc71",  # verde
    "0.4": "#f1c40f",  # amarillo
    "0.6": "#e67e22",  # naranja
    "0.8": "#e74c3c",  # rojo
    "1.0": "#c0392b",  # rojo oscuro
    "Sin dato": "#95a5a6",
}
ETIQUETAS = {"0.0": "0.0 (insuficiente)", "0.2": "0.2", "0.4": "0.4",
             "0.6": "0.6", "0.8": "0.8", "1.0": "1.0"}

# --- Colores FIJOS por año (consistentes en todas las gráficas) ---
# Cada año tiene su color asignado de forma estable: aunque se agreguen años
# nuevos (p. ej. 2019-2023 más adelante), los existentes NO cambian de color.
PALETA_ANIOS = ["#27ae60", "#2980b9", "#e74c3c", "#8e44ad", "#f39c12",
                "#16a085", "#c0392b", "#2c3e50", "#d35400", "#7f8c8d",
                "#e67e22", "#1abc9c"]
# Asignacion estable por año concreto (2019 en adelante)
ANIOS_REF = list(range(2019, 2031))
COLOR_ANIO = {a: PALETA_ANIOS[i % len(PALETA_ANIOS)] for i, a in enumerate(ANIOS_REF)}

def escala_color_anios(anios):
    """Devuelve (domain, range) de colores para una lista de años dada,
    usando el color FIJO de cada año (consistente entre todas las gráficas)."""
    dom = [str(a) for a in sorted(anios)]
    rng = [COLOR_ANIO.get(int(a), "#95a5a6") for a in sorted(anios)]
    return dom, rng

# --- Clasificacion del mapa nacional: rangos tecnicos y Jenks ---
# Umbrales tecnicos (niveles de alerta) definidos con criterio institucional.
# Son los 5 BORDES INFERIORES de los 5 niveles (Bajo..Critico). El nivel
# superior (Critico) es ABIERTO: >= 4000. Su cierre se calcula con el maximo
# real del periodo, asi el mapa funciona igual para "Todos los años" (valores
# acumulados grandes) que para un solo mes, sin crear una clase extra sin
# etiqueta. Los 4 cortes internos (100, 500, 1500, 4000) son FIJOS y por eso
# comparables en el tiempo.
RANGOS_TECNICOS = [0, 100, 500, 1500, 4000]
ETIQUETAS_ALERTA = ["Bajo", "Moderado", "Alto", "Muy alto", "Crítico"]

def jenks_breaks(valores, n_clases=5):
    """Cortes naturales de Jenks (Fisher-Jenks) en NumPy puro, sin dependencias."""
    import numpy as np
    v = np.sort(np.asarray(valores, dtype=float))
    n = len(v)
    if n <= n_clases:
        return list(np.unique(v))
    mat1 = np.zeros((n + 1, n_clases + 1))
    mat2 = np.full((n + 1, n_clases + 1), np.inf)
    mat1[1:, 1] = 1
    mat2[1, 1:] = 0
    var = 0.0
    for l in range(2, n + 1):
        s1 = s2 = w = 0.0
        for m in range(1, l + 1):
            i3 = l - m + 1
            val = v[i3 - 1]
            s2 += val * val; s1 += val; w += 1
            var = s2 - (s1 * s1) / w
            i4 = i3 - 1
            if i4 != 0:
                for j in range(2, n_clases + 1):
                    if mat2[l, j] >= var + mat2[i4, j - 1]:
                        mat1[l, j] = i3
                        mat2[l, j] = var + mat2[i4, j - 1]
        mat1[l, 1] = 1
        mat2[l, 1] = var
    k = n
    kclass = [0.0] * (n_clases + 1)
    kclass[n_clases] = v[-1]
    kclass[0] = v[0]
    for j in range(n_clases, 1, -1):
        idx = int(mat1[k, j]) - 2
        kclass[j - 1] = v[idx]
        k = int(mat1[k, j]) - 1
    return kclass

def calcular_cortes(vals, metodo):
    """Devuelve la lista de cortes segun el metodo elegido."""
    import numpy as np
    vmin, vmax = float(vals.min()), float(vals.max())
    if metodo == "Rangos técnicos (alerta)":
        # 5 niveles de alerta con bordes inferiores fijos y nivel superior
        # ABIERTO. Se toman los umbrales por debajo del maximo y se cierra la
        # escala con el maximo real: asi siempre hay a lo sumo 5 clases (una por
        # etiqueta) y nunca sobra una sin nombre. 'or [RANGOS_TECNICOS[0]]' cubre
        # el caso vmax<=0 (todo en 0) evitando IndexError.
        inf = [c for c in RANGOS_TECNICOS if c < vmax] or [RANGOS_TECNICOS[0]]
        cortes = inf + [max(vmax, inf[-1] + 1)]
        if cortes[0] > vmin:
            cortes = [vmin] + cortes
        return [float(c) for c in sorted(set(cortes))]
    if metodo == "Cortes naturales (Jenks)":
        cortes = jenks_breaks(vals, 5)
        return [float(c) for c in np.unique(cortes)]
    # Cuantiles (por defecto de respaldo)
    return [float(c) for c in np.unique(np.quantile(vals, [0, 0.2, 0.4, 0.6, 0.8, 1.0]))]

def cat_confidence(v):
    if v is None or pd.isna(v):
        return "Sin dato"
    return f"{float(v):.1f}"

@st.cache_data(show_spinner="Cargando incendios...")
def cargar_incendios():
    df = pd.read_parquet(F_PARQUET) if F_PARQUET.exists() else pd.read_csv(F_CSV)
    # --- Optimizacion de memoria: descartar columnas que el dashboard no usa ---
    cols_descartar = ["algorithms", "newest_acquisition", "sub_area_name",
                      "cod_depto", "Depto", "type_string", "cause_string"]
    df = df.drop(columns=[c for c in cols_descartar if c in df.columns], errors="ignore")
    # Categoria de confianza (vectorizado, sin apply fila a fila)
    if "confidence" in df.columns:
        df["cat"] = df["confidence"].round(1).map(lambda v: f"{v:.1f}" if pd.notna(v) else "Sin dato")
    else:
        df["cat"] = "Sin dato"
    # Fecha del incendio = oldest_acquisition (inicio); fallback a oldest_detection
    campo_fecha = "oldest_acquisition" if "oldest_acquisition" in df.columns else "oldest_detection"
    df["fecha"] = pd.to_datetime(df[campo_fecha], errors="coerce", utc=True)
    df["mes"] = df["fecha"].dt.month.astype("Int16")
    # --- Tipos livianos: texto repetido -> category, enteros mas pequeños ---
    # NOTA: departamento y municipio se dejan como texto (no category) porque
    # se usan en muchos groupby y concatenaciones; category causaria grupos
    # fantasma (conteo 0) y errores de concatenacion.
    for c in ["fire_confidence", "cod_muni", "archivo_origen"]:
        if c in df.columns:
            df[c] = df[c].astype("category")
    for c in ["num_fires", "lifetime"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int32")
    for c in ["lon", "lat", "area", "confidence"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df

# GeoJSON cacheados como RECURSO: se cargan una sola vez y se comparten entre
# todas las sesiones de usuario (no se duplican por visitante => menos memoria).
@st.cache_resource(show_spinner="Cargando mapa base...")
def cargar_geojson(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)

def feature_por_nombre(gj, campo, valor):
    """Devuelve un FeatureCollection con el unico feature cuyo campo == valor."""
    feats = [ft for ft in gj["features"] if ft["properties"].get(campo) == valor]
    return {"type": "FeatureCollection", "features": feats}

for k, v in {"nivel": "Nacional", "departamento": None, "municipio": None}.items():
    if k not in st.session_state:
        st.session_state[k] = v

def ir_a_departamento(n):
    st.session_state.nivel = "Departamental"
    st.session_state.departamento = n
    st.session_state.municipio = None

if not F_DEPTOS.exists() or not (F_PARQUET.exists() or F_CSV.exists()):
    st.error("No se encontraron los datos procesados. Ejecuta primero "
             "`python preprocesar.py` para generar `data/processed/`.")
    st.stop()

df = cargar_incendios()
gj_deptos = cargar_geojson(F_DEPTOS)
gj_munis = cargar_geojson(F_MUNIS)

st.sidebar.title(":fire: Incendios Forestales")
st.sidebar.caption("Fuente de datos: WildFire Solution - Ororatech")

# ---- RANGO DE FECHAS (selector rapido de año + calendario fino) ----
fmin = df["fecha"].min()
fmax = df["fecha"].max()
fmin_d = fmin.date() if pd.notna(fmin) else dt.date(2016, 1, 1)
fmax_d = fmax.date() if pd.notna(fmax) else dt.date.today()

# Años disponibles en los datos (descendente: el mas reciente primero)
anios_disp = sorted(df["fecha"].dt.year.dropna().astype(int).unique(), reverse=True)
opciones_anio = ["Todos los años"] + [str(a) for a in anios_disp]
sel_anio = st.sidebar.selectbox(
    "Año", opciones_anio, index=0,
    help="Atajo: elige un año para ver el periodo completo. "
         "Para un rango más fino (días o meses), usa el calendario de abajo.")

# Segun el año elegido, definir el rango por defecto del calendario
if sel_anio == "Todos los años":
    # "Todos los años" = todo el dataset disponible (de la fecha mas antigua a la
    # mas reciente). Asi el visor muestra la serie completa (2019-2026 y lo que se
    # agregue en el futuro) sin topes fijos que oculten años.
    rango_def = (fmin_d, fmax_d)
else:
    a = int(sel_anio)
    ini_a = max(dt.date(a, 1, 1), fmin_d)
    fin_a = min(dt.date(a, 12, 31), fmax_d)
    rango_def = (ini_a, fin_a)

# La 'key' depende del año elegido y del rango de datos: al cambiar el año
# (o al cargar datos nuevos) el calendario se reinicia al periodo correcto.
key_fechas = f"rango_{sel_anio}_{fmin_d.isoformat()}_{fmax_d.isoformat()}"
rango = st.sidebar.date_input(
    "Rango de fechas (inicio del incendio)",
    value=rango_def, min_value=fmin_d, max_value=fmax_d,
    key=key_fechas,
    help="Filtra por la fecha de primera detección satelital (oldest_acquisition). "
         "Puedes ajustar el rango día a día aquí.")
# date_input puede devolver 1 fecha (mientras el usuario elige el rango) o 2.
if isinstance(rango, (tuple, list)) and len(rango) == 2:
    fecha_ini, fecha_fin = rango
elif isinstance(rango, (tuple, list)) and len(rango) == 1:
    fecha_ini, fecha_fin = rango[0], rango_def[1]
else:
    fecha_ini = fecha_fin = rango
etiqueta_periodo = (f"{fecha_ini:%d/%m/%Y} – {fecha_fin:%d/%m/%Y}"
                    if fecha_ini != fecha_fin else f"{fecha_ini:%d/%m/%Y}")
# Boton para recargar datos tras regenerar data/processed (limpia el cache)
if st.sidebar.button("🔄 Recargar datos", help="Úsalo si actualizaste los datos "
                     "(p. ej. agregaste un nuevo año) y no aparecen."):
    st.cache_data.clear()
    st.rerun()

# ============ NAVEGACION (incluye depto/municipio) ============
st.sidebar.divider()
st.sidebar.subheader("Navegación")
nivel = st.sidebar.radio("Nivel de vista", ["Nacional", "Departamental", "Municipal"],
                         index=["Nacional", "Departamental", "Municipal"].index(st.session_state.nivel))
st.session_state.nivel = nivel

# Filtrar por fecha (universo para navegacion)
mask_fecha = (df["fecha"].dt.date >= fecha_ini) & (df["fecha"].dt.date <= fecha_fin)
base = df[mask_fecha]
deptos_disponibles = sorted(base["departamento"].dropna().unique())

if nivel in ("Departamental", "Municipal") and deptos_disponibles:
    if st.session_state.departamento not in deptos_disponibles:
        st.session_state.departamento = deptos_disponibles[0]
    st.session_state.departamento = st.sidebar.selectbox(
        "Departamento", deptos_disponibles,
        index=deptos_disponibles.index(st.session_state.departamento))

if nivel == "Municipal" and st.session_state.departamento:
    munis_disp = sorted(base[base["departamento"] == st.session_state.departamento]
                        ["municipio"].dropna().unique())
    if munis_disp:
        if st.session_state.municipio not in munis_disp:
            st.session_state.municipio = munis_disp[0]
        st.session_state.municipio = st.sidebar.selectbox(
            "Municipio", munis_disp, index=munis_disp.index(st.session_state.municipio))

# ============ FILTRO DE CONFIANZA (solo confidence) ============
st.sidebar.divider()
st.sidebar.subheader("Confianza del incendio")
st.sidebar.caption(
    "La confianza indica cuántos satélites y algoritmos detectaron la fuente de "
    "calor. A mayor confianza, detección más precisa.")
with st.sidebar.expander("¿Qué significa cada valor?"):
    st.markdown(
        "| Confianza | Satélites | Algoritmos |\n"
        "|:--:|:--:|:--:|\n"
        "| 0.2 | 1 | 1 |\n"
        "| 0.4 | 2 | 2 |\n"
        "| 0.6 | 3 | 3 |\n"
        "| 0.8 | 4 | 4 |\n"
        "| 1.0 | 5 | 5 |\n")
    st.caption("Fuente: WildFire Solution – OroraTech. "
               "El valor 0.0 corresponde a detecciones con datos insuficientes.")
cats_presentes = [c for c in ORDEN_CONF if c in df["cat"].unique()]
mostrar_insuf = st.sidebar.checkbox("Mostrar incidentes con datos insuficientes", value=False)
cats_filtrables = [c for c in cats_presentes if c != "0.0"]
# Por defecto, activar solo los niveles de confianza alta (0.6, 0.8, 1.0).
# Si alguno no existe en los datos, simplemente no aparece.
default_conf = [c for c in ["0.6", "0.8", "1.0"] if c in cats_filtrables]
if not default_conf:                       # respaldo: si no hay altos, mostrar todos
    default_conf = cats_filtrables
cats_sel = st.sidebar.multiselect(
    "Filtrar niveles de confianza", cats_filtrables, default=default_conf,
    format_func=lambda x: ETIQUETAS.get(x, x))
if not cats_sel:
    cats_sel = cats_filtrables

# Aplicar filtro de categorias sobre la base ya filtrada por fecha
categorias_visibles = list(cats_sel)
if mostrar_insuf and "0.0" in cats_presentes:
    categorias_visibles.append("0.0")
dff = base[base["cat"].isin(categorias_visibles)]

# ---- REPORTE PDF (control; la generacion va al final cuando 'datos' existe) ----
st.sidebar.divider()
st.sidebar.subheader("Reporte PDF")
alcance = st.sidebar.radio(
    "Alcance del reporte",
    ["Vista actual", "Nacional completo"],
    help="'Vista actual' usa el nivel y filtros en pantalla. "
         "'Nacional completo' genera un resumen de todo el país.")
generar_clic = st.sidebar.button("Generar reporte PDF", width="stretch")
st.sidebar.caption(
    "Tras generarlo, el botón **⬇️ Descargar reporte PDF** aparece al final del "
    "dashboard, debajo de la tabla de eventos. Haz clic allí para descargarlo.")

# ============ ENCABEZADO + KPIs ============
# Logo institucional + titulo (logo a la izquierda del titulo)
RUTA_LOGO = None
for cand in ["logo/ungrd-horizontal.png", "logo/UNGRD- Horizontal.png",
             "logo/UNGRD-Horizontal.png"]:
    if (RAIZ / cand).exists():
        RUTA_LOGO = str(RAIZ / cand)
        break

col_logo, col_tit = st.columns([1, 5])
with col_logo:
    if RUTA_LOGO:
        st.image(RUTA_LOGO, width="stretch")
with col_tit:
    st.markdown("## Incendios Forestales - Colombia")
    st.markdown(
        "<div style='font-size:14px;color:#555;line-height:1.3;'>"
        "Unidad Nacional para la Gestión del Riesgo de Desastres<br>"
        "Subdirección para el Conocimiento del Riesgo</div>",
        unsafe_allow_html=True)
st.write("")

if nivel == "Nacional":
    datos = dff
    st.subheader(f"Vista nacional · {etiqueta_periodo}")
elif nivel == "Departamental":
    datos = dff[dff["departamento"] == st.session_state.departamento]
    st.subheader(f"{st.session_state.departamento} · {etiqueta_periodo}")
else:
    datos = dff[(dff["departamento"] == st.session_state.departamento)
                & (dff["municipio"] == st.session_state.municipio)]
    st.subheader(f"{st.session_state.municipio} ({st.session_state.departamento}) · {etiqueta_periodo}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Eventos", f"{len(datos):,}")
c2.metric("Área total (ha)", f"{datos['area'].sum():,.0f}")
c3.metric("Área promedio (ha)", f"{datos['area'].mean():,.1f}" if len(datos) else "0")
n_alta = datos[datos["cat"].isin(["0.6", "0.8", "1.0"])].shape[0]
pct = (100 * n_alta / len(datos)) if len(datos) else 0
c4.metric("% confianza alta (≥0.6)", f"{pct:.0f}%")

# ============ MAPA (mas grande, ocupa todo el ancho) ============
def contorno_depto(nombre):
    fc = feature_por_nombre(gj_deptos, "DeNombre", nombre)
    return folium.GeoJson(fc, style_function=lambda x: {
        "color": "#1f3b57", "weight": 3, "fillOpacity": 0}, name="contorno")

def contorno_muni(nombre):
    fc = feature_por_nombre(gj_munis, "MpNombre", nombre)
    return folium.GeoJson(fc, style_function=lambda x: {
        "color": "#1f3b57", "weight": 3, "fillOpacity": 0}, name="contorno")

if nivel == "Nacional":
    conteo = dff.groupby("departamento").size()
    nombres_geo = [ft["properties"]["DeNombre"] for ft in gj_deptos["features"]]
    conteo = conteo.reindex(nombres_geo).fillna(0)
    import numpy as np
    vals = conteo.values

    # Selector de metodo de clasificacion (Jenks por defecto)
    metodo_clas = st.radio(
        "Método de clasificación del mapa",
        ["Cortes naturales (Jenks)", "Rangos técnicos (alerta)", "Cuantiles"],
        horizontal=True,
        help="Jenks: agrupa por saltos naturales en los datos (mejor para explorar). "
             "Rangos técnicos: umbrales fijos de alerta, comparables en el tiempo. "
             "Cuantiles: reparte los departamentos en grupos del mismo tamaño.")
    # Descripcion del metodo, justo debajo del selector (donde el usuario mira)
    explica_metodo = {
        "Cortes naturales (Jenks)":
            "Agrupa los departamentos buscando los saltos naturales en los datos. "
            "Refleja bien la estructura real cuando hay pocos departamentos con mucha "
            "actividad y muchos con poca. Ideal para explorar la situación de un periodo.",
        "Rangos técnicos (alerta)":
            "Usa umbrales fijos de alerta (Bajo, Moderado, Alto, Muy alto, Crítico). "
            "Como no cambian, permiten comparar el mapa entre distintas fechas. "
            "Ideal para seguimiento institucional en el tiempo.",
        "Cuantiles":
            "Reparte los departamentos en grupos del mismo tamaño. Útil para un reparto "
            "parejo, aunque puede juntar en una clase a departamentos muy distintos.",
    }.get(metodo_clas, "")
    if explica_metodo:
        st.caption(explica_metodo)

    cortes = calcular_cortes(vals, metodo_clas)
    # folium exige bins crecientes que cubran min..max y al menos 3 cortes
    usa_bins = (len(cortes) >= 3 and cortes[0] <= vals.min() and cortes[-1] >= vals.max())

    m = folium.Map(location=[4.6, -74.1], zoom_start=5, tiles=TILES_URL, attr=TILES_ATTR)
    choro = folium.Choropleth(
        geo_data=gj_deptos, data=conteo,
        key_on="feature.properties.DeNombre",
        fill_color="YlOrRd", fill_opacity=0.7, line_opacity=0.3,
        nan_fill_color="#eeeeee",
        bins=cortes if usa_bins else 6,
        legend_name="Frecuencia (total eventos)")
    choro.add_to(m)
    for key in list(choro._children.keys()):
        if key.startswith("color_map"):
            del choro._children[key]
    folium.GeoJson(gj_deptos,
                   style_function=lambda x: {"fillColor": "#00000000", "color": "#00000000",
                                             "weight": 0, "fillOpacity": 0},
                   highlight_function=lambda x: {"weight": 2, "color": "#333", "fillOpacity": 0.1},
                   tooltip=folium.GeoJsonTooltip(fields=["DeNombre"],
                                                 aliases=["Departamento:"], sticky=True)).add_to(m)
    salida = st_folium(m, width=None, height=620, returned_objects=["last_active_drawing"])

    # Leyenda de colores (clases): debajo del mapa, junto a los colores
    paleta = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026", "#7a0019"]
    es_tecnico = metodo_clas == "Rangos técnicos (alerta)"
    if usa_bins and len(cortes) >= 3:
        items = []
        for i in range(len(cortes) - 1):
            ini, fin = cortes[i], cortes[i + 1]
            col = paleta[i] if i < len(paleta) else paleta[-1]
            es_ultimo = (i == len(cortes) - 2)
            if es_tecnico and i < len(ETIQUETAS_ALERTA):
                # El nivel superior (Crítico) es abierto: se muestra como "≥ ini".
                txt = (f"{ETIQUETAS_ALERTA[i]} (≥ {ini:,.0f})" if es_ultimo
                       else f"{ETIQUETAS_ALERTA[i]} ({ini:,.0f}–{fin:,.0f})")
            else:
                txt = f"{ini:,.0f} – {fin:,.0f}"
            items.append(
                f"<span style='display:inline-flex;align-items:center;margin-right:14px;"
                f"margin-bottom:4px;'>"
                f"<span style='width:16px;height:16px;background:{col};"
                f"border:1px solid #ccc;border-radius:3px;margin-right:5px;'></span>"
                f"{txt}</span>")
        nombre_metodo = {"Cortes naturales (Jenks)": "cortes naturales (Jenks)",
                         "Rangos técnicos (alerta)": "rangos técnicos de alerta",
                         "Cuantiles": "cuantiles"}.get(metodo_clas, metodo_clas)
        st.markdown(
            f"<div style='margin-top:-8px;'><b>Número total de eventos registrados</b> · "
            f"{nombre_metodo}<br>" + "".join(items) + "</div>", unsafe_allow_html=True)
    if salida and salida.get("last_active_drawing"):
        clic = salida["last_active_drawing"].get("properties", {}).get("DeNombre")
        if clic and clic in deptos_disponibles:
            ir_a_departamento(clic); st.rerun()

elif nivel == "Departamental":
    sub = datos.dropna(subset=["lat", "lon"])
    centro = [sub["lat"].mean(), sub["lon"].mean()] if len(sub) else [4.6, -74.1]
    m = folium.Map(location=centro, zoom_start=8, tiles=TILES_URL, attr=TILES_ATTR)
    # 1) Heatmap primero (capa de fondo)
    if len(sub):
        HeatMap(sub[["lat", "lon"]].values.tolist(), radius=12, blur=18, min_opacity=0.3).add_to(m)
    # 2) Municipios DESPUES, para que queden encima y capturen el cursor (tooltip)
    munis_depto = feature_por_nombre(gj_munis, "Depto", st.session_state.departamento)
    if munis_depto["features"]:
        folium.GeoJson(
            munis_depto,
            style_function=lambda x: {"color": "#9aa6b2", "weight": 1,
                                      "fillColor": "#ffffff", "fillOpacity": 0.01,
                                      "dashArray": "3"},
            highlight_function=lambda x: {"color": "#1f3b57", "weight": 2,
                                          "fillOpacity": 0.08},
            tooltip=folium.GeoJsonTooltip(fields=["MpNombre"],
                                          aliases=["Municipio:"], sticky=True),
        ).add_to(m)
        # Etiquetas solo en municipios CON incendios en el periodo, priorizando
        # los de mayor numero de eventos (evita saturar deptos con muchos municipios).
        conteo_muni = datos.groupby("municipio").size().to_dict()
        # Maximo de etiquetas a mostrar; el resto queda solo con su contorno + tooltip
        MAX_ETIQUETAS = 15
        munis_top = sorted(conteo_muni, key=conteo_muni.get, reverse=True)[:MAX_ETIQUETAS]
        for ft in munis_depto["features"]:
            nombre_m = ft["properties"]["MpNombre"]
            if nombre_m not in munis_top:
                continue
            try:
                geom = ft["geometry"]
                coords = geom["coordinates"]
                ring = coords[0][0] if geom["type"] == "MultiPolygon" else coords[0]
                lons = [c[0] for c in ring]; lats = [c[1] for c in ring]
                clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
                folium.Marker(
                    [clat, clon],
                    icon=folium.DivIcon(
                        icon_size=(0, 0), icon_anchor=(0, 0),
                        html=(f"<div style='font-size:10px;font-weight:600;"
                              f"color:#3a4450;opacity:0.6;white-space:nowrap;"
                              f"text-shadow:0 0 3px #fff,0 0 3px #fff;"
                              f"transform:translate(-50%,-50%);'>"
                              f"{nombre_m}</div>")),
                ).add_to(m)
            except Exception:
                pass
    # 3) Contorno del departamento por encima
    contorno_depto(st.session_state.departamento).add_to(m)
    st.caption("Densidad de incendios. Se etiquetan los municipios con más eventos; "
               "pasa el cursor sobre cualquiera para ver su nombre.")
    salida = st_folium(m, width=None, height=620)
    # Leyenda del mapa de calor: gradiente azul (baja) -> rojo (alta concentracion)
    st.markdown(
        "<div style='font-size:13px;color:#444;'><b>Densidad de incendios</b>: "
        "concentración de eventos por zona "
        "<span style='display:inline-block;width:130px;height:13px;vertical-align:middle;"
        "border:1px solid #ccc;border-radius:3px;margin:0 6px;"
        "background:linear-gradient(to right,#2b3abb,#00d0ff,#33ff66,#ffff33,#ff3300);'>"
        "</span> "
        "<span style='color:#2b3abb;'>baja</span> → "
        "<span style='color:#ff3300;'>alta</span></div>",
        unsafe_allow_html=True)

else:  # Municipal
    sub = datos.dropna(subset=["lat", "lon"])
    centro = [sub["lat"].mean(), sub["lon"].mean()] if len(sub) else [4.6, -74.1]

    # El usuario elige como visualizar los incendios del municipio
    modo_vis = st.radio(
        "Forma de visualización",
        ["Puntos agrupados", "Solo puntos", "Mapa de calor"],
        horizontal=True,
        help="Puntos agrupados: agrupa en clústeres al alejar (recomendado si hay "
             "muchos). Solo puntos: muestra cada incendio sin agrupar. "
             "Mapa de calor: densidad de concentración.")

    m = folium.Map(location=centro, zoom_start=11, tiles=TILES_URL, attr=TILES_ATTR)
    contorno_muni(st.session_state.municipio).add_to(m)

    if modo_vis == "Mapa de calor":
        if len(sub):
            HeatMap(sub[["lat", "lon"]].values.tolist(), radius=12, blur=18,
                    min_opacity=0.3).add_to(m)
        st.caption("Densidad de incendios en el municipio.")
        salida = st_folium(m, width=None, height=620)
        st.markdown(
            "<div style='font-size:13px;color:#444;'><b>Densidad de incendios</b>: "
            "concentración de eventos por zona "
            "<span style='display:inline-block;width:130px;height:13px;vertical-align:middle;"
            "border:1px solid #ccc;border-radius:3px;margin:0 6px;"
            "background:linear-gradient(to right,#2b3abb,#00d0ff,#33ff66,#ffff33,#ff3300);'>"
            "</span> <span style='color:#2b3abb;'>baja</span> → "
            "<span style='color:#ff3300;'>alta</span></div>", unsafe_allow_html=True)
    else:
        # Puntos (agrupados o no)
        agrupar = (modo_vis == "Puntos agrupados") and len(sub) > 0
        if agrupar:
            from folium.plugins import MarkerCluster
            contenedor = MarkerCluster(name="incendios").add_to(m)
        else:
            contenedor = m
        for _, r in sub.iterrows():
            color = COLOR_CONF.get(r["cat"], "#95a5a6")
            radio = 4 + (r["area"] ** 0.5) / 4 if pd.notna(r["area"]) else 4
            folium.CircleMarker(
                location=[r["lat"], r["lon"]], radius=radio,
                color=color, fill=True, fill_color=color, fill_opacity=0.7, weight=1,
                tooltip=(f"Confianza: {r['cat']} · Área: {r['area']:.1f} ha · "
                         f"Focos: {int(r['num_fires']) if pd.notna(r.get('num_fires')) else '-'}"
                         if pd.notna(r['area']) else f"Confianza: {r['cat']}")
            ).add_to(contenedor)
        cap = "El contorno marca el límite del municipio."
        if agrupar:
            cap += (" Los puntos se agrupan en círculos numerados; acércate con el zoom "
                    "para ver los incendios individuales.")
        st.caption(cap)
        salida = st_folium(m, width=None, height=620)
        # Leyenda de los puntos: color = confianza, tamaño = área
        items_conf = "".join(
            f"<span style='display:inline-flex;align-items:center;margin-right:12px;'>"
            f"<span style='width:13px;height:13px;border-radius:50%;background:{COLOR_CONF[c]};"
            f"margin-right:4px;'></span>{ETIQUETAS.get(c, c)}</span>"
            for c in ORDEN_CONF if c in categorias_visibles)
        st.markdown(
            "<div style='font-size:13px;color:#444;'>"
            "<b>Cada punto es un incendio.</b> El <b>color</b> indica el nivel de confianza "
            "de la detección; el <b>tamaño</b> es proporcional al área quemada (ha).<br>"
            "<span style='margin-right:10px;'><b>Confianza:</b></span>" + items_conf +
            "<br><span style='color:#888;'>Pasa el cursor sobre un punto para ver confianza, "
            "área y número de focos.</span></div>",
            unsafe_allow_html=True)

# ============ GRAFICAS: dos por fila, debajo del mapa ============
st.divider()
st.markdown("### 📊 Análisis")

def barras_top(data, campo, titulo, color, valor="eventos"):
    st.markdown(f"**{titulo}**")
    if valor == "eventos":
        g = data.groupby(campo).size().sort_values(ascending=False).head(10).reset_index()
        g.columns = [campo, "valor"]; ytt = "Eventos"
    else:
        g = data.groupby(campo)["area"].sum().sort_values(ascending=False).head(10).reset_index()
        g.columns = [campo, "valor"]; ytt = "Área (ha)"
    ch = (alt.Chart(g).mark_bar(color=color).encode(
        x=alt.X("valor:Q", title=ytt),
        y=alt.Y(f"{campo}:N", sort="-x", title=None),
        tooltip=[campo, alt.Tooltip("valor:Q", title=ytt, format=",.0f")]
    ).properties(height=280))
    st.altair_chart(ch, width="stretch")

# Fila 1: distribucion por confianza + eventos por mes
g1, g2 = st.columns(2)
with g1:
    st.markdown("**Distribución por nivel de confianza**")
    # Mostrar SOLO los niveles seleccionados por el usuario (orden canonico)
    niveles_mostrar = [c for c in ORDEN_CONF if c in categorias_visibles]
    dist = datos.groupby("cat").size().reindex(niveles_mostrar).fillna(0).reset_index()
    dist.columns = ["nivel", "eventos"]
    ch = (alt.Chart(dist).mark_bar().encode(
        x=alt.X("nivel:N", sort=niveles_mostrar, title="Nivel de confianza"),
        y=alt.Y("eventos:Q", title="Eventos"),
        color=alt.Color("nivel:N", scale=alt.Scale(domain=list(COLOR_CONF.keys()),
                                                    range=list(COLOR_CONF.values())), legend=None),
        tooltip=["nivel", "eventos"]).properties(height=280))
    st.altair_chart(ch, width="stretch")
with g2:
    st.markdown("**Eventos por mes (comparativo por año)**")
    if datos["fecha"].notna().any():
        meses_abrev = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
                       7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}
        orden_meses = list(meses_abrev.values())
        tmp = datos.dropna(subset=["fecha"]).copy()
        tmp["anio"] = tmp["fecha"].dt.year.astype(int)
        tmp["mes"] = tmp["fecha"].dt.month.astype(int)
        # Conteo por año y mes
        serie = (tmp.groupby(["anio", "mes"]).size().reset_index(name="eventos"))
        serie["mes_nombre"] = serie["mes"].map(meses_abrev)
        serie["anio"] = serie["anio"].astype(str)
        # Linea de promedio entre años (promedio de cada mes a traves de los años)
        prom = (serie.groupby("mes")["eventos"].mean().reindex(range(1, 13))
                .reset_index())
        prom["mes_nombre"] = prom["mes"].map(meses_abrev)
        prom["serie"] = "Promedio"

        base_x = alt.X("mes_nombre:N", sort=orden_meses, title="Mes",
                       axis=alt.Axis(labelAngle=0))
        # Lineas por año, con color FIJO por año (consistente con las
        # graficas de analisis comparativo y estable al agregar años nuevos).
        anios_ord = sorted(serie["anio"].unique())
        dom_anios, rng_anios = escala_color_anios(anios_ord)
        lineas = (alt.Chart(serie).mark_line(point=True).encode(
            x=base_x,
            y=alt.Y("eventos:Q", title="Eventos"),
            color=alt.Color("anio:N", title="Año",
                            scale=alt.Scale(domain=dom_anios, range=rng_anios)),
            tooltip=[alt.Tooltip("anio:N", title="Año"),
                     alt.Tooltip("mes_nombre:N", title="Mes"),
                     alt.Tooltip("eventos:Q", title="Eventos")]))
        # Linea de promedio (negra, punteada)
        linea_prom = (alt.Chart(prom).mark_line(
            strokeDash=[6, 4], color="#000000", point=False).encode(
            x=base_x,
            y=alt.Y("eventos:Q"),
            tooltip=[alt.Tooltip("mes_nombre:N", title="Mes"),
                     alt.Tooltip("eventos:Q", title="Promedio", format=",.0f")]))
        ch = (lineas + linea_prom).properties(height=280).resolve_scale(color="independent")
        st.altair_chart(ch, width="stretch")
        st.caption("Cada línea de color es un año; la línea negra punteada es el promedio mensual entre años.")
    else:
        st.info("Sin datos de fecha para el periodo seleccionado.")

# Fila: serie DIARIA a todo el ancho (rango seleccionado por el usuario)
st.markdown("**Eventos por día (rango seleccionado)**")
if datos["fecha"].notna().any():
    diaria = (datos.dropna(subset=["fecha"])
              .assign(dia=lambda d: d["fecha"].dt.floor("D"))
              .groupby("dia").size().reset_index(name="eventos"))
    if len(diaria):
        # Rellenar dias sin eventos con 0 para una serie continua
        rango_dias = pd.date_range(diaria["dia"].min(), diaria["dia"].max(),
                                   freq="D", tz="UTC")
        diaria = (diaria.set_index("dia").reindex(rango_dias, fill_value=0)
                  .rename_axis("dia").reset_index())
        # Promedio movil de 7 dias
        diaria["media7"] = diaria["eventos"].rolling(7, min_periods=1, center=True).mean()

        base_dia = alt.Chart(diaria).encode(
            x=alt.X("dia:T", title="Fecha"))
        linea_dia = base_dia.mark_line(color="#bdc3c7", opacity=0.8).encode(
            y=alt.Y("eventos:Q", title="Eventos"),
            tooltip=[alt.Tooltip("dia:T", title="Día"),
                     alt.Tooltip("eventos:Q", title="Eventos")])
        linea_media = base_dia.mark_line(color="#e74c3c", strokeWidth=2).encode(
            y=alt.Y("media7:Q"),
            tooltip=[alt.Tooltip("dia:T", title="Día"),
                     alt.Tooltip("media7:Q", title="Media 7 días", format=",.1f")])
        ch_dia = (linea_dia + linea_media).properties(height=300)
        st.altair_chart(ch_dia, width="stretch")
        st.caption("Línea gris: eventos diarios. Línea roja: promedio móvil de 7 días (suaviza el ruido).")
    else:
        st.info("Sin datos diarios para el periodo seleccionado.")
else:
    st.info("Sin datos de fecha para el periodo seleccionado.")

# ============ Análisis por AÑO (tortas + tendencia) ============
# Usa 'datos' (filtrado por fecha, confianza Y nivel geografico seleccionado),
# para que al navegar a un departamento o municipio estas graficas reflejen
# esa zona, igual que los KPIs y las demas graficas.
serie_anual = (datos.dropna(subset=["fecha"])
               .assign(anio=lambda d: d["fecha"].dt.year.astype(int)))
if len(serie_anual):
    resumen = (serie_anual.groupby("anio")
               .agg(eventos=("id", "size"), area=("area", "sum"))
               .reset_index().sort_values("anio"))
    # Un año es "parcial" SOLO si es el año donde termina el dataset completo
    # (fmax global) y esa fecha de corte es anterior a fin de año. Asi no se
    # marca parcial un año completo solo porque el FILTRO recorte fechas.
    anio_corte = fmax.year if pd.notna(fmax) else None
    mes_corte = fmax.month if pd.notna(fmax) else 12
    corte_parcial = mes_corte < 12
    def etq_anio(a):
        return (f"{int(a)} parcial"
                if (anio_corte is not None and int(a) == anio_corte and corte_parcial)
                else str(int(a)))
    resumen["anio_str"] = resumen["anio"].apply(etq_anio)
    # ¿Hay algun año parcial visible en este resumen?
    es_parcial = (anio_corte is not None and corte_parcial
                  and anio_corte in resumen["anio"].values)
    # Variacion % respecto al año anterior (eventos y area)
    resumen["var_pct"] = resumen["eventos"].pct_change() * 100
    resumen["var_pct_area"] = resumen["area"].pct_change() * 100
    # Porcentaje del total (para etiquetas de las donas)
    resumen["pct_ev"] = 100 * resumen["eventos"] / resumen["eventos"].sum()
    resumen["pct_ar"] = 100 * resumen["area"] / resumen["area"].sum()

    st.markdown("**Análisis comparativo por año**")
    if es_parcial:
        st.caption(f"Nota: {anio_corte} es un periodo parcial (los datos llegan "
                   f"hasta el mes {mes_corte:02d}); su comparación con años completos "
                   f"es solo referencial.")
    pa, pb, pc = st.columns(3)

    # Escala de color FIJA por año (usa la etiqueta, incluida 'parcial')
    orden_anios = resumen["anio_str"].tolist()
    rng_a = [COLOR_ANIO.get(int(a), "#95a5a6") for a in resumen["anio"]]
    escala_anio = alt.Scale(domain=orden_anios, range=rng_a)

    # (a) Tortas con etiqueta de porcentaje por año
    with pa:
        st.markdown("<div style='font-size:13px;font-weight:600;text-align:center;'>"
                    "Eventos por año (%)</div>", unsafe_allow_html=True)
        base_ev = alt.Chart(resumen).encode(
            theta=alt.Theta("eventos:Q", stack=True),
            color=alt.Color("anio_str:N", title="Año", scale=escala_anio,
                            sort=orden_anios),
            order=alt.Order("anio:Q"))
        torta_ev = base_ev.mark_arc(innerRadius=40, outerRadius=85)
        texto_ev = base_ev.mark_text(radius=102, fontSize=10).encode(
            text=alt.Text("pct_ev:Q", format=".0f"),
            color=alt.value("#333"))
        st.altair_chart((torta_ev + texto_ev).properties(height=240), width="stretch")

        st.markdown("<div style='font-size:13px;font-weight:600;text-align:center;"
                    "margin-top:6px;'>Área quemada por año (%)</div>",
                    unsafe_allow_html=True)
        base_ar = alt.Chart(resumen).encode(
            theta=alt.Theta("area:Q", stack=True),
            color=alt.Color("anio_str:N", title="Año", scale=escala_anio,
                            sort=orden_anios),
            order=alt.Order("anio:Q"))
        torta_ar = base_ar.mark_arc(innerRadius=40, outerRadius=85)
        texto_ar = base_ar.mark_text(radius=102, fontSize=10).encode(
            text=alt.Text("pct_ar:Q", format=".0f"),
            color=alt.value("#333"))
        st.altair_chart((torta_ar + texto_ar).properties(height=240), width="stretch")

    # (b) Barras de eventos por año con variacion %
    with pb:
        st.caption("Eventos por año y su variación porcentual (%) frente al año anterior")
        barras_ev = (alt.Chart(resumen).mark_bar().encode(
            x=alt.X("anio_str:N", title="Año", sort=orden_anios),
            y=alt.Y("eventos:Q", title="Eventos"),
            color=alt.Color("anio_str:N", scale=escala_anio, sort=orden_anios, legend=None),
            tooltip=[alt.Tooltip("anio_str:N", title="Año"),
                     alt.Tooltip("eventos:Q", title="Eventos", format=","),
                     alt.Tooltip("var_pct:Q", title="Var. % vs año anterior", format="+.1f")]
        ).properties(height=300))
        etiquetas = (alt.Chart(resumen[resumen["var_pct"].notna()])
                     .mark_text(dy=-8, fontSize=11, fontWeight="bold")
                     .encode(x=alt.X("anio_str:N", sort=orden_anios), y=alt.Y("eventos:Q"),
                             text=alt.Text("var_pct:Q", format="+.0f"),
                             color=alt.condition(alt.datum.var_pct >= 0,
                                                 alt.value("#c0392b"), alt.value("#27ae60"))))
        st.altair_chart(barras_ev + etiquetas, width="stretch")

    # (c) Barras de area quemada por año CON variacion %
    with pc:
        st.caption("Superficie quemada por año y su variación porcentual (%) frente al año anterior")
        barras_ar = (alt.Chart(resumen).mark_bar().encode(
            x=alt.X("anio_str:N", title="Año", sort=orden_anios),
            y=alt.Y("area:Q", title="Área (ha)"),
            color=alt.Color("anio_str:N", scale=escala_anio, sort=orden_anios, legend=None),
            tooltip=[alt.Tooltip("anio_str:N", title="Año"),
                     alt.Tooltip("area:Q", title="Área (ha)", format=",.0f"),
                     alt.Tooltip("var_pct_area:Q", title="Var. % vs año anterior", format="+.1f")]
        ).properties(height=300))
        etiquetas_ar = (alt.Chart(resumen[resumen["var_pct_area"].notna()])
                        .mark_text(dy=-8, fontSize=11, fontWeight="bold")
                        .encode(x=alt.X("anio_str:N", sort=orden_anios), y=alt.Y("area:Q"),
                                text=alt.Text("var_pct_area:Q", format="+.0f"),
                                color=alt.condition(alt.datum.var_pct_area >= 0,
                                                    alt.value("#c0392b"), alt.value("#27ae60"))))
        st.altair_chart(barras_ar + etiquetas_ar, width="stretch")

# ============ TOPS (al final, antes de la tabla) ============
g3, g4 = st.columns(2)
if nivel == "Nacional":
    with g3: barras_top(dff, "departamento", "Top 10 departamentos por eventos", "#e74c3c", "eventos")
    with g4: barras_top(dff, "departamento", "Top 10 departamentos por área (ha)", "#c0392b", "area")
elif nivel == "Departamental":
    with g3: barras_top(datos, "municipio", "Top 10 municipios por eventos", "#e74c3c", "eventos")
    with g4: barras_top(datos, "municipio", "Top 10 municipios por área (ha)", "#c0392b", "area")
else:
    with g3:
        st.markdown("**Distribución de área por evento (ha)**")
        if len(datos):
            ch = (alt.Chart(datos.assign(a=datos["area"].clip(upper=datos["area"].quantile(0.98))))
                  .mark_bar(color="#e67e22").encode(
                      x=alt.X("a:Q", bin=alt.Bin(maxbins=25), title="Área (ha)"),
                      y=alt.Y("count():Q", title="Eventos")).properties(height=280))
            st.altair_chart(ch, width="stretch")
    with g4:
        st.markdown("**Eventos por satélite**")
        if "satellites" in datos.columns and len(datos):
            sat = (datos.assign(sat=datos["satellites"].fillna("").str.split(","))
                   .explode("sat"))
            sat = sat[sat["sat"] != ""]
            top = sat.groupby("sat").size().sort_values(ascending=False).head(10).reset_index()
            top.columns = ["satelite", "eventos"]
            ch = (alt.Chart(top).mark_bar(color="#16a085").encode(
                x=alt.X("eventos:Q", title="Eventos"),
                y=alt.Y("satelite:N", sort="-x", title=None),
                tooltip=["satelite", "eventos"]).properties(height=280))
            st.altair_chart(ch, width="stretch")

# ============================================================================
# ANÁLISIS PARA GESTIÓN DEL RIESGO (recurrencia, estacionalidad, anticipación)
# A nivel municipal NO se muestra: no aporta (un solo municipio) y es pesado.
# ============================================================================
if nivel != "Municipal":
    st.divider()
    st.markdown("### \U0001F50E An\u00e1lisis para gesti\u00f3n del riesgo")
    st.caption("Herramientas para anticipar y priorizar ante la temporada seca / Fen\u00f3meno de El Ni\u00f1o. "
               "Usan el periodo y filtros seleccionados arriba.")

    if nivel == "Departamental" and st.session_state.departamento:
        ambito = dff[dff["departamento"] == st.session_state.departamento]
        txt_ambito = f"departamento de {st.session_state.departamento}"
    else:
        ambito = dff
        txt_ambito = "nivel nacional"

    amb = ambito.dropna(subset=["fecha", "municipio", "departamento"]).copy()
    if len(amb):
        amb["anio"] = amb["fecha"].dt.year.astype(int)
        amb["mes"] = amb["fecha"].dt.month.astype(int)
        MESES_AB = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
                    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}
        etq_muni = lambda d, m: f"{m} ({d})"

        # ---------- 1) RECURRENCIA ESTACIONAL ----------
        st.markdown("**1. Recurrencia estacional**")
        st.caption("Municipios que se queman **en el mismo mes** a lo largo de varios a\u00f1os. "
                   "Alta recurrencia = patr\u00f3n estacional predecible que permite anticipar.")
        rec = (amb.groupby(["departamento", "municipio", "mes"])
               .agg(anios=("anio", "nunique"), eventos=("id", "size")).reset_index())
        rec = rec[rec["anios"] >= 2]
        if len(rec):
            rec["Municipio"] = rec["municipio"].astype(str) + " (" + rec["departamento"].astype(str) + ")"
            rec["Mes"] = rec["mes"].map(MESES_AB)
            rec = rec.sort_values(["anios", "eventos"], ascending=False)
            tabla_rec = (rec[["Municipio", "Mes", "anios", "eventos"]]
                         .rename(columns={"anios": "A\u00f1os recurrentes", "eventos": "Eventos totales"})
                         .head(15).reset_index(drop=True))
            st.dataframe(tabla_rec, width="stretch", hide_index=True)
        else:
            tabla_rec = None
            st.info("No hay suficientes a\u00f1os en el periodo para detectar recurrencia estacional. "
                    "Ampl\u00eda el rango de fechas para incluir varios a\u00f1os.")

        # ---------- 2) MAPA DE CALOR municipio x mes ----------
        st.markdown("**2. Mapa de calor de estacionalidad (municipio × mes)**")
        st.caption("Para los municipios con m\u00e1s eventos: en qu\u00e9 meses concentran su actividad. "
                   "Revela las ventanas de riesgo de cada territorio.")
        top_munis = (amb.groupby(["departamento", "municipio"]).size()
                     .sort_values(ascending=False).head(15).reset_index())
        top_munis["Municipio"] = top_munis["municipio"].astype(str) + " (" + top_munis["departamento"].astype(str) + ")"
        nombres_top = set(zip(top_munis["departamento"], top_munis["municipio"]))
        mask_top = amb.set_index(["departamento", "municipio"]).index.isin(nombres_top)
        heat = amb[mask_top].copy()
        if len(heat):
            heat["Municipio"] = heat["municipio"].astype(str) + " (" + heat["departamento"].astype(str) + ")"
            heat_g = heat.groupby(["Municipio", "mes"]).size().reset_index(name="eventos")
            heat_g["Mes"] = heat_g["mes"].map(MESES_AB)
            orden_muni = top_munis["Municipio"].tolist()
            ch_heat = (alt.Chart(heat_g).mark_rect().encode(
                x=alt.X("Mes:N", sort=list(MESES_AB.values()), title="Mes"),
                y=alt.Y("Municipio:N", sort=orden_muni, title=None),
                color=alt.Color("eventos:Q", scale=alt.Scale(scheme="orangered"), title="Eventos"),
                tooltip=["Municipio", "Mes", alt.Tooltip("eventos:Q", title="Eventos")]
            ).properties(height=380))
            st.altair_chart(ch_heat, width="stretch")

        # ---------- 3) y 4) ----------
        cR, cE = st.columns(2)
        with cR:
            st.markdown("**3. Municipios reincidentes**")
            st.caption("M\u00e1s a\u00f1os distintos con incendios = vigilancia prioritaria.")
            reinc = (amb.groupby(["departamento", "municipio"])
                     .agg(anios=("anio", "nunique"), eventos=("id", "size")).reset_index())
            reinc["Municipio"] = reinc["municipio"].astype(str) + " (" + reinc["departamento"].astype(str) + ")"
            reinc = reinc.sort_values(["anios", "eventos"], ascending=False)
            tabla_reinc = (reinc[["Municipio", "anios", "eventos"]]
                           .rename(columns={"anios": "A\u00f1os activos", "eventos": "Eventos"})
                           .head(12).reset_index(drop=True))
            st.dataframe(tabla_reinc, width="stretch", hide_index=True)
        with cE:
            st.markdown("**4. Distribución histórica por mes**")
            st.caption("Distribuci\u00f3n hist\u00f3rica por mes. Se\u00f1ala las ventanas en que "
                       "concentrar recursos de prevenci\u00f3n y respuesta.")
            cal = amb.groupby("mes").size().reindex(range(1, 13)).fillna(0).reset_index()
            cal.columns = ["mes", "eventos"]
            cal["Mes"] = cal["mes"].map(MESES_AB)
            pico = int(cal.loc[cal["eventos"].idxmax(), "mes"])
            ch_cal = (alt.Chart(cal).mark_bar().encode(
                x=alt.X("Mes:N", sort=list(MESES_AB.values()), title="Mes", axis=alt.Axis(labelAngle=0)),
                y=alt.Y("eventos:Q", title="Eventos (hist\u00f3rico)"),
                color=alt.condition(alt.datum.mes == pico, alt.value("#c0392b"), alt.value("#e67e22")),
                tooltip=["Mes", alt.Tooltip("eventos:Q", title="Eventos")]
            ).properties(height=300))
            st.altair_chart(ch_cal, width="stretch")
            st.caption(f"Mes pico hist\u00f3rico en {txt_ambito}: **{MESES_AB[pico]}**.")

        if tabla_rec is not None:
            st.download_button("Descargar an\u00e1lisis de recurrencia (CSV)",
                               tabla_rec.to_csv(index=False).encode("utf-8"),
                               file_name="recurrencia_estacional.csv", mime="text/csv")
    else:
        st.info("No hay datos suficientes en el periodo seleccionado para el an\u00e1lisis de gesti\u00f3n del riesgo.")

st.divider()
st.markdown("#### Tabla de eventos")
cols = ["id", "departamento", "municipio", "area", "confidence", "fire_confidence",
        "num_fires", "lifetime", "satellites", "oldest_acquisition", "newest_detection"]
cols = [c for c in cols if c in datos.columns]
st.dataframe(datos[cols].sort_values("area", ascending=False), width="stretch", hide_index=True)
st.download_button("Descargar esta selección (CSV)",
                   datos[cols].to_csv(index=False).encode("utf-8"),
                   file_name="incendios_seleccion.csv", mime="text/csv")

# ============ GENERACION DEL REPORTE PDF (al final: 'datos' ya existe) ============
@st.cache_data(show_spinner=False)
def cargar_gdf(ruta):
    import geopandas as gpd
    return gpd.read_file(ruta)

if generar_clic:
    with st.spinner("Generando reporte PDF..."):
        try:
            gdf_dep = cargar_gdf(F_DEPTOS)
            # Metodo de clasificacion para el mapa del PDF (jenks por defecto si
            # el selector no existe, p.ej. cuando no se esta en vista nacional).
            _map_metodo = {"Cortes naturales (Jenks)": "jenks",
                           "Rangos técnicos (alerta)": "tecnicos",
                           "Cuantiles": "cuantiles"}
            metodo_pdf = _map_metodo.get(globals().get("metodo_clas", ""), "jenks")
            if alcance == "Nacional completo":
                pdf_bytes = reporte.generar_pdf(
                    dff, "Nacional", "Vista nacional", etiqueta_periodo,
                    cats_presentes, gdf_deptos=gdf_dep,
                    conteo_por_depto=dff.groupby("departamento").size().to_dict(),
                    ruta_logo=RUTA_LOGO, metodo_clasificacion=metodo_pdf, fecha_corte=fmax)
                fname = "reporte_nacional.pdf"
            elif nivel == "Nacional":
                pdf_bytes = reporte.generar_pdf(
                    datos, "Nacional", "Vista nacional", etiqueta_periodo,
                    cats_presentes, gdf_deptos=gdf_dep,
                    conteo_por_depto=datos.groupby("departamento").size().to_dict(),
                    ruta_logo=RUTA_LOGO, metodo_clasificacion=metodo_pdf, fecha_corte=fmax)
                fname = "reporte_nacional.pdf"
            elif nivel == "Departamental":
                limite = gdf_dep[gdf_dep["DeNombre"] == st.session_state.departamento]
                pdf_bytes = reporte.generar_pdf(
                    datos, "Departamental", st.session_state.departamento,
                    etiqueta_periodo, cats_presentes, gdf_limite=limite,
                    ruta_logo=RUTA_LOGO, fecha_corte=fmax)
                fname = f"reporte_{st.session_state.departamento}.pdf"
            else:
                gdf_mun = cargar_gdf(F_MUNIS)
                limite = gdf_mun[gdf_mun["MpNombre"] == st.session_state.municipio]
                pdf_bytes = reporte.generar_pdf(
                    datos, "Municipal",
                    f"{st.session_state.municipio} ({st.session_state.departamento})",
                    etiqueta_periodo, cats_presentes, gdf_limite=limite,
                    ruta_logo=RUTA_LOGO, fecha_corte=fmax)
                fname = f"reporte_{st.session_state.municipio}.pdf"
            st.session_state["pdf_bytes"] = pdf_bytes
            st.session_state["pdf_fname"] = fname
        except Exception as e:
            st.error(f"No se pudo generar el reporte: {e}")

if st.session_state.get("pdf_bytes"):
    st.success("Reporte listo para descargar.")
    st.download_button(
        "⬇️ Descargar reporte PDF", st.session_state["pdf_bytes"],
        file_name=st.session_state.get("pdf_fname", "reporte.pdf"),
        mime="application/pdf")

# ============ PIE DE PAGINA: terminos de uso, autoria, creditos y cita ============
st.divider()
st.markdown("#### Términos de uso")
st.markdown(
    "<div style='font-size:13px;color:#444;text-align:justify;'>"
    "Los GeoVisores de la Unidad Nacional para la Gestión del Riesgo de Desastres "
    "– UNGRD tienen como propósito facilitar al público en general el acceso, consulta "
    "y visualización de información geográfica y temática relacionada con la gestión "
    "del riesgo de desastres en Colombia, en sus versiones oficiales más recientes.<br><br>"
    "Le solicitamos leer atentamente los presentes términos de uso antes de hacer uso "
    "de este portal web. La consulta, visualización, descarga o utilización de la "
    "información contenida en los GeoVisores de la UNGRD implica la aceptación y "
    "cumplimiento de las siguientes condiciones:"
    "<ul style='margin-top:6px;'>"
    "<li>Utilizar la información y los contenidos de manera adecuada, responsable y "
    "conforme a la normatividad vigente.</li>"
    "<li>Respetar los derechos de autor y citar adecuadamente la fuente de información: "
    "Unidad Nacional para la Gestión del Riesgo de Desastres – UNGRD y las entidades "
    "proveedoras de datos cuando corresponda.</li>"
    "<li>No copiar, modificar, distribuir, comercializar o utilizar con fines indebidos "
    "la información publicada en los GeoVisores de la UNGRD sin la debida autorización.</li>"
    "<li>No eliminar, alterar u ocultar avisos, logotipos, marcas, créditos, metadatos "
    "o cualquier elemento relacionado con la propiedad intelectual de la UNGRD o de las "
    "entidades aliadas.</li>"
    "<li>La información publicada tiene carácter informativo y de apoyo para la toma de "
    "decisiones; su uso e interpretación es responsabilidad exclusiva del usuario.</li>"
    "<li>La UNGRD no garantiza que la información esté libre de errores o interrupciones "
    "y podrá actualizar, modificar o retirar contenidos sin previo aviso.</li>"
    "<li>Queda prohibido incorporar publicidad, alterar la integridad de la información "
    "o realizar acciones que afecten el funcionamiento, seguridad o disponibilidad de "
    "los GeoVisores.</li>"
    "</ul>"
    "<b>UNIDAD NACIONAL PARA LA GESTIÓN DEL RIESGO DE DESASTRES – UNGRD</b>"
    "</div>", unsafe_allow_html=True)

st.write("")
cred1, cred2 = st.columns(2)
with cred1:
    st.markdown(
        "<div style='font-size:13px;color:#444;'>"
        "<b>Realizado por:</b> Jorge Alpala<br>"
        "(jorge.alpala@gestiondelriesgo.gov.co, UNGRD - SCR)<br>"
        "<b>Elaboración:</b> Mayo de 2026</div>",
        unsafe_allow_html=True)
with cred2:
    st.markdown(
        "<div style='font-size:13px;color:#444;'>"
        "<b>Fuentes y créditos:</b><br>"
        "Datos: OroraTech WildFire Solution (WFS) · "
        "Límites: DIVIPOLA - DANE.<br>"
        "Desarrollado con Python, Streamlit, Folium/Leaflet, GeoPandas, "
        "Shapely, Pandas, Altair, Matplotlib y ReportLab. "
        "Mapas base: OpenStreetMap · CARTO."
        "</div>", unsafe_allow_html=True)

st.write("")
st.markdown(
    "<div style='font-size:12px;color:#666;border-top:1px solid #ddd;padding-top:8px;'>"
    "<b>Cómo citar este visor:</b><br>"
    "Alpala, J. (2026). <i>Incendios Forestales – Colombia: GeoVisor de monitoreo y "
    "análisis de incendios con datos de WildFire Solution (OroraTech)</i>. Unidad "
    "Nacional para la Gestión del Riesgo de Desastres (UNGRD), Subdirección para el "
    "Conocimiento del Riesgo. Recuperado de https://incendios-colombia-wfs.streamlit.app"
    "</div>", unsafe_allow_html=True)
