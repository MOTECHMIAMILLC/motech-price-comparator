import os
import re
import io
import html
import hashlib
import datetime
from typing import Optional, List, Dict, Tuple

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Motech LLC — Comparador Multiproveedor",
    page_icon="🛍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# CONFIG / SCHEMA
# ============================================================
BASE_COLUMNS = [
    "Proveedor", "SKU", "SKU_Fuente", "Descripcion", "Precio", "Stock",
    "Stock_Miami", "Stock_NY", "En_Transito", "MOQ", "ETA", "Ubicacion",
]

MOTIVATIONAL_QUOTES = [
    "🚀 'El éxito en las ventas no es cuestión de suerte, sino de constancia y disciplina.'",
    "💡 'Cada no que recibes te acerca un paso más al sí definitivo.'",
    "🎯 'Enfoque, determinación y excelente atención: la fórmula para superar cualquier meta.'",
    "🔥 'Grandes resultados requieren grandes esfuerzos. ¡Hoy es un excelente día para cerrar tratos!'",
    "⭐ 'El cliente no busca solo un producto, busca la confianza que tú le transmites.'",
]

PROVIDER_CODES = {
    "HAIFA": "HAI",
    "ALL PLUS": "APL",
    "ALLBRANDS": "ABR",
    "AQUA CONSUMER": "AQC",
    "AQUA LAPTOPS": "AQL",
    "ASE": "ASE",
    "CELLUTECH": "CEL",
    "EMOLO": "EMO",
    "H&Z": "HZ",
    "INFLUENTIAL": "INF",
    "PRT": "PRT",
    "SNK": "SNK",
    "SUNRISE": "SUN",
    "TECH SUPPLIER": "TEC",
    "TRADING": "TRA",
    "WORLDWIDE": "WWD",
}

# ============================================================
# UI STYLE
# ============================================================
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .main-header {font-size: 27px; font-weight: 800; color:#1B365D; margin-bottom:2px;}
    .sub-header {font-size:14px; color:#555; margin-bottom:15px;}
    .welcome-card {background:linear-gradient(135deg,#1B365D 0%,#2E7D32 100%);color:white;
        padding:16px 20px;border-radius:10px;margin-bottom:20px;box-shadow:0 4px 6px rgba(0,0,0,.1);}
    .welcome-title {font-size:20px;font-weight:700;margin-bottom:4px;}
    .quote-text {font-size:14px;font-style:italic;opacity:.95;}
    .card-deal {background-color:#E8F5E9;border-left:6px solid #2E7D32;padding:12px 16px;
        border-radius:8px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);}
    .card-title {font-size:15px;font-weight:700;color:#1B5E20;margin-bottom:4px;}
    .badge-meta {background-color:#F0F4F8;color:#2D3748;padding:3px 6px;border-radius:4px;
        font-size:12px;margin-right:6px;border:1px solid #CBD5E0;display:inline-block;margin-top:4px;}
    .badge-savings {background-color:#C8E6C9;color:#1B5E20;font-weight:700;padding:3px 8px;
        border-radius:4px;font-size:12px;float:right;}
</style>
""", unsafe_allow_html=True)

# ============================================================
# NORMALIZATION HELPERS
# ============================================================
def _norm_col(x) -> str:
    return re.sub(r"\s+", " ", str(x).strip()).upper()


def clean_price(v) -> Optional[float]:
    if pd.isna(v):
        return None
    s = str(v).strip().upper()
    if not s or any(x in s for x in ["SOLD OUT", "N/A", "CALL", "TBD"]):
        return None
    s = s.replace("$", "").replace(",", "").replace("USD", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        p = float(m.group())
        return p if p > 0 else None
    except Exception:
        return None


def clean_qty(v) -> Optional[float]:
    if pd.isna(v):
        return None
    if isinstance(v, (int, float)) and not pd.isna(v):
        return max(float(v), 0)
    s = str(v).strip().upper().replace(",", "")
    if s in {"", "-", "N/A", "NA", "NONE", "NAN"}:
        return 0.0
    m = re.search(r"\d+(?:\.\d+)?", s)
    return max(float(m.group()), 0) if m else None


def safe_text(v, default="") -> str:
    if pd.isna(v):
        return default
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none"} else s


def synthetic_sku(desc: str, provider: str) -> str:
    digest = hashlib.md5(desc.upper().encode("utf-8", errors="ignore")).hexdigest()[:10].upper()
    return f"{PROVIDER_CODES.get(provider, 'UNK')}-AUTO-{digest}"


def infer_sku_from_description(desc: str, provider: str) -> Tuple[str, str]:
    text = safe_text(desc).upper()
    # Common manufacturer/model/MPN patterns. Avoid obvious capacity/spec tokens.
    tokens = re.findall(r"\b[A-Z0-9][A-Z0-9#./_-]{4,30}\b", text)
    bad = re.compile(r"^(?:\d+(?:GB|TB|HZ|W)|WIN(?:DOWS)?11|DDR\d|LPDDR\d|RTX|WUXGA|FHD|OLED|IPS)$")
    candidates = []
    for t in tokens:
        t = t.strip(".,;:()[]{}")
        if bad.match(t):
            continue
        if not re.search(r"[A-Z]", t) or not re.search(r"\d", t):
            continue
        # score model-like tokens higher than specs
        score = 0
        score += 3 if "-" in t or "/" in t or "#" in t else 0
        score += min(len(t), 20) / 20
        if re.search(r"\d{3,}", t): score += 1
        if any(x in t for x in ["GB", "TB", "HZ", "GHZ", "MHZ"]): score -= 3
        candidates.append((score, t))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1], "INFERRED"
    return synthetic_sku(text, provider), "SYNTHETIC"


def first_col(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    normalized = {c: _norm_col(c) for c in df.columns}
    aliases_n = [_norm_col(a) for a in aliases]
    # exact first
    for c, n in normalized.items():
        if n in aliases_n:
            return c
    # then safe substring
    for c, n in normalized.items():
        if any(a in n for a in aliases_n if len(a) >= 4):
            return c
    return None


def header_row(raw: pd.DataFrame, require_price=True, max_rows=25) -> Optional[int]:
    sku_words = ["SKU", "MODEL", "MPN", "PART NUMBER", "PART #", "ITEM", "ITEM NUMBER", "ITEM CODE"]
    price_words = ["PRICE", "COST", "FOB", "CASH", "EXW"]
    desc_words = ["DESCRIPTION", "PRODUCT", "ITEM NAME", "ITEM DESCRIPTION", "PRODUCTS"]
    best = None
    for i in range(min(max_rows, len(raw))):
        vals = [_norm_col(v) for v in raw.iloc[i].tolist() if pd.notna(v)]
        has_sku = any(any(k in v for k in sku_words) for v in vals)
        has_price = any(any(k in v for k in price_words) for v in vals)
        has_desc = any(any(k in v for k in desc_words) for v in vals)
        score = int(has_sku) * 3 + int(has_price) * 3 + int(has_desc) * 2
        if best is None or score > best[0]: best = (score, i, has_price)
    if not best or best[0] < 5 or (require_price and not best[2]):
        return None
    return best[1]


def excel_file(uploaded):
    uploaded.seek(0)
    data = uploaded.read()
    uploaded.seek(0)
    name = uploaded.name.lower()
    if name.endswith(".xls"):
        try:
            import xlrd  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Para leer archivos .xls (por ejemplo Cellutech) instala xlrd. "
                "Agrega 'xlrd==2.0.1' a requirements.txt."
            ) from e
    return pd.ExcelFile(io.BytesIO(data))


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BASE_COLUMNS)


def make_row(provider, sku, desc, price, stock=None, stock_miami=None, stock_ny=None,
             transit=None, moq=None, eta="", location="", sku_source="FILE") -> Optional[dict]:
    p = clean_price(price)
    d = safe_text(desc)
    sku = safe_text(sku).upper()
    if not sku:
        sku, sku_source = infer_sku_from_description(d, provider)
    if not d or p is None:
        return None
    return {
        "Proveedor": provider,
        "SKU": sku,
        "SKU_Fuente": sku_source,
        "Descripcion": d,
        "Precio": p,
        "Stock": clean_qty(stock),
        "Stock_Miami": clean_qty(stock_miami),
        "Stock_NY": clean_qty(stock_ny),
        "En_Transito": clean_qty(transit),
        "MOQ": clean_qty(moq),
        "ETA": safe_text(eta),
        "Ubicacion": safe_text(location),
    }


def finalize(rows: List[dict]) -> pd.DataFrame:
    if not rows:
        return empty_frame()

    # Build every expected column first so Pandas does not infer an incompatible
    # dtype and later reject assignments (stricter in newer Pandas versions).
    df = pd.DataFrame(rows).reindex(columns=BASE_COLUMNS)

    numeric_cols = [
        "Precio", "Stock", "Stock_Miami", "Stock_NY", "En_Transito", "MOQ"
    ]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Float64")

    text_cols = [
        "Proveedor", "SKU", "SKU_Fuente", "Descripcion", "ETA", "Ubicacion"
    ]
    for c in text_cols:
        df[c] = df[c].where(df[c].notna(), "").astype("string")

    # Prefer explicit Stock; otherwise use Miami + NY where available.
    has_city = df[["Stock_Miami", "Stock_NY"]].notna().any(axis=1)
    needs_stock = df["Stock"].isna() & has_city
    if bool(needs_stock.any()):
        summed = (
            df[["Stock_Miami", "Stock_NY"]]
            .fillna(0)
            .sum(axis=1)
            .astype("Float64")
        )
        df.loc[needs_stock, "Stock"] = summed.loc[needs_stock].to_numpy()

    df["SKU"] = df["SKU"].str.strip().str.upper()
    df = df[(df["SKU"] != "") & df["Precio"].notna()]
    return df[BASE_COLUMNS].drop_duplicates().reset_index(drop=True)


# ============================================================
# CATEGORY / SPEC EXTRACTION
# ============================================================
def clasificar_categoria(desc, sku):
    text = (str(desc) + " " + str(sku)).upper()
    gaming = ["PLAYSTATION", "PS5", "XBOX", "NINTENDO", "SWITCH", "ROG", "TUF", "LEGION", "NITRO",
              "ALIENWARE", "PREDATOR", "OMEN", "VICTUS", "ZEPHYRUS", "CYBORG", "RAIDER", "KATANA",
              "STEALTH", "STRIX", "SCAR", "LOQ", "GAMING"]
    desktop = ["DESKTOP", "ALL IN ONE", "ALL-IN-ONE", "AIO", "THINKCENTRE", "ELITEDESK", "NUC", "SERVER", "TOWER", "WORKSTATION"]
    laptop = ["MACBOOK", "LAPTOP", "NOTEBOOK", "CHROMEBOOK", "ASPIRE", "VIVOBOOK", "IDEAPAD", "LATITUDE",
              "PAVILION", "THINKPAD", "ZENBOOK", "PROBOOK", "ELITEBOOK", "ZBOOK", "OMNIBOOK"]
    if any(k in text for k in gaming): return "🎮 Gaming"
    if any(k in text for k in desktop): return "🖥️ Desktops & PC"
    if any(k in text for k in laptop): return "💻 Laptops"
    if any(k in text for k in ["IPAD", "TABLET", " TAB "]): return "📱 Tablets"
    if any(k in text for k in ["MONITOR", "DISPLAY", "144HZ", "165HZ", "180HZ"]): return "🖥️ Monitores"
    if any(k in text for k in ["AIRPODS", "EARBUD", "HEADPHONE", "HEADSET", "SPEAKER", "MOUSE", "KEYBOARD", "CABLE", "PENCIL"]):
        return "🎧 Accesorios & Audio"
    return "📦 Otros / Varios"


def extraer_especificaciones(text):
    t = str(text).upper()
    cpu = "Otro / No especificado"
    cpu_patterns = [
        (r"\bCORE\s+ULTRA\s*9\b|\bULTRA\s*9\b", "Intel Core Ultra 9"),
        (r"\bCORE\s+ULTRA\s*7\b|\bULTRA\s*7\b", "Intel Core Ultra 7"),
        (r"\bCORE\s+ULTRA\s*5\b|\bULTRA\s*5\b", "Intel Core Ultra 5"),
        (r"\bI9[-\s]|\bCORE\s*I9\b", "Intel Core i9"),
        (r"\bI7[-\s]|\bCORE\s*I7\b", "Intel Core i7"),
        (r"\bI5[-\s]|\bCORE\s*I5\b", "Intel Core i5"),
        (r"\bI3[-\s]|\bCORE\s*I3\b", "Intel Core i3"),
        (r"\bRYZEN\s*(?:AI\s*)?9\b|\bR9[-\s]", "AMD Ryzen 9"),
        (r"\bRYZEN\s*(?:AI\s*)?7\b|\bR7[-\s]", "AMD Ryzen 7"),
        (r"\bRYZEN\s*(?:AI\s*)?5\b|\bR5[-\s]", "AMD Ryzen 5"),
        (r"\bRYZEN\s*(?:AI\s*)?3\b|\bR3[-\s]", "AMD Ryzen 3"),
        (r"\bAPPLE\s*M5\b|\bM5 CHIP\b", "Apple M5"),
        (r"\bAPPLE\s*M4\b|\bM4 CHIP\b", "Apple M4"),
        (r"\bAPPLE\s*M3\b|\bM3 CHIP\b", "Apple M3"),
        (r"\bAPPLE\s*M2\b|\bM2 CHIP\b", "Apple M2"),
        (r"\bAPPLE\s*M1\b|\bM1 CHIP\b", "Apple M1"),
    ]
    for pat, label in cpu_patterns:
        if re.search(pat, t): cpu = label; break

    ram = "Otro / No especificado"
    for n in [64, 48, 32, 24, 16, 12, 8, 4]:
        if re.search(rf"\b{n}\s*GB\b", t): ram = f"{n} GB"; break

    storage = "Otro / No especificado"
    if re.search(r"\b2\s*TB\b|\b2000\s*GB\b", t): storage = "2 TB SSD"
    elif re.search(r"\b1\s*TB\b|\b1000\s*GB\b|\b1024\s*GB\b", t): storage = "1 TB SSD"
    elif re.search(r"\b512\s*GB\b", t): storage = "512 GB SSD"
    elif re.search(r"\b256\s*GB\b", t): storage = "256 GB SSD"
    elif re.search(r"\b128\s*GB\b", t): storage = "128 GB"

    screen = "Otro / No especificado"
    m = re.search(r"\b(1[0-9]|2[0-9])(?:\.\d)?\s*(?:\"|INCH|IN)\b", t)
    if m: screen = m.group(0).replace("INCH", '"').replace("IN", '"')
    return pd.Series([cpu, ram, storage, screen])


# ============================================================
# PROVIDER DETECTION
# ============================================================
def detect_provider(filename: str) -> str:
    n = os.path.splitext(filename)[0].upper()
    if "HAIFA" in n: return "HAIFA"
    if "ALL PLUS" in n: return "ALL PLUS"
    if "ALLBRAND" in n: return "ALLBRANDS"
    if "AQUA" in n and ("LATOP" in n or "LAPTOP" in n): return "AQUA LAPTOPS"
    if "AQUA" in n: return "AQUA CONSUMER"
    if re.search(r"\bASE\b", n): return "ASE"
    if "CELLU" in n: return "CELLUTECH"
    if "EMOLO" in n: return "EMOLO"
    if "H&Z" in n or "H Z" in n: return "H&Z"
    if "INFLUENTIAL" in n: return "INFLUENTIAL"
    if "PRT" in n: return "PRT"
    if "SNK" in n: return "SNK"
    if "SUNRISE" in n or re.search(r"\bSUN\b", n): return "SUNRISE"
    if "TECH SUPPLIER" in n: return "TECH SUPPLIER"
    if "TRADING" in n: return "TRADING"
    if "WORLDWIDE" in n: return "WORLDWIDE"
    return re.sub(r"\(\d+\)$", "", n).strip()


# ============================================================
# PARSERS
# ============================================================
def parse_simple_table(xls, provider, sheet, hdr=None, aliases=None) -> pd.DataFrame:
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)
    h = hdr if hdr is not None else header_row(raw)
    if h is None: return empty_frame()
    df = pd.read_excel(xls, sheet_name=sheet, header=h)
    aliases = aliases or {}
    c_sku = first_col(df, aliases.get("sku", ["SKU", "MODEL", "MPN", "PART NUMBER", "PART #", "ITEM", "ITEM NUMBER", "ITEM CODE"]))
    c_desc = first_col(df, aliases.get("desc", ["DESCRIPTION", "PRODUCT", "PRODUCTS", "ITEM DESCRIPTION", "ITEM NAME", "PRODUCT DESCRIPTION"]))
    c_price = first_col(df, aliases.get("price", ["PRICE", "PRICE (USD)", "COST", "FOB", "FOB $", "CASH", "EXW"]))
    c_qty = first_col(df, aliases.get("qty", ["QTY", "QUANTITY", "STOCK", "IN STOCK", "UNITS"]))
    c_eta = first_col(df, aliases.get("eta", ["ETA"]))
    c_loc = first_col(df, aliases.get("location", ["LOCATION", "EXW"]))
    c_moq = first_col(df, aliases.get("moq", ["MOQ"]))
    if c_price is None: return empty_frame()
    rows = []
    for _, r in df.iterrows():
        desc = safe_text(r[c_desc]) if c_desc else ""
        sku = safe_text(r[c_sku]) if c_sku else ""
        if not desc and sku: desc = sku
        row = make_row(provider, sku, desc, r[c_price],
                       stock=r[c_qty] if c_qty else None,
                       moq=r[c_moq] if c_moq else None,
                       eta=r[c_eta] if c_eta else "",
                       location=r[c_loc] if c_loc else "")
        if row: rows.append(row)
    return finalize(rows)


def parse_haifa(xls):
    sheet = next((s for s in xls.sheet_names if s.strip().upper() == "FULL LIST"), xls.sheet_names[0])
    return parse_simple_table(xls, "HAIFA", sheet)


def parse_allplus(xls):
    return parse_simple_table(xls, "ALL PLUS", xls.sheet_names[0], aliases={"sku":["MODEL"], "desc":["DESCRIPTION"], "price":["PRICE"]})


def parse_allbrands(xls):
    raw = pd.read_excel(xls, sheet_name=xls.sheet_names[0], header=None)
    h = header_row(raw)
    df = pd.read_excel(xls, sheet_name=xls.sheet_names[0], header=h)
    c_desc, c_stock, c_price = first_col(df,["DESCRIPTION"]), first_col(df,["STOCK"]), first_col(df,["PRICE"])
    rows=[]
    for _,r in df.iterrows():
        desc=safe_text(r[c_desc]) if c_desc else ""
        sku,source=infer_sku_from_description(desc,"ALLBRANDS")
        row=make_row("ALLBRANDS",sku,desc,r[c_price] if c_price else None,stock=r[c_stock] if c_stock else None,sku_source=source)
        if row: rows.append(row)
    return finalize(rows)


def parse_aqua(xls, provider):
    raw=pd.read_excel(xls,sheet_name="Sheet1",header=None)
    h=header_row(raw)
    df=pd.read_excel(xls,sheet_name="Sheet1",header=h)
    c_sku=first_col(df,["PART NUMBER"]); c_desc=first_col(df,["DESCRIPTION"]); c_price=first_col(df,["FOB $","FOB"])
    c_mia=first_col(df,["MIAMI"]); c_ny=first_col(df,["NEW YORK"])
    rows=[]
    for _,r in df.iterrows():
        mia=clean_qty(r[c_mia]) if c_mia else None; ny=clean_qty(r[c_ny]) if c_ny else None
        stock=(mia or 0)+(ny or 0) if (mia is not None or ny is not None) else None
        loc=[]
        if mia: loc.append(f"Miami {int(mia)}")
        if ny: loc.append(f"NY {int(ny)}")
        row=make_row(provider,r[c_sku] if c_sku else "",r[c_desc] if c_desc else "",r[c_price] if c_price else None,
                     stock=stock,stock_miami=mia,stock_ny=ny,location=" / ".join(loc))
        if row: rows.append(row)
    return finalize(rows)


def parse_ase(xls):
    raw=pd.read_excel(xls,sheet_name=xls.sheet_names[0],header=None)
    rows=[]
    # ASE repeats the same header for sections. Detect data rows by price in col 3.
    for _,r in raw.iterrows():
        vals=r.tolist()
        # ASE places Model/Description/Price at columns 3/4/5 and repeats headers by section.
        sku=safe_text(vals[3] if len(vals)>3 else "")
        desc=safe_text(vals[4] if len(vals)>4 else "")
        price=clean_price(vals[5] if len(vals)>5 else None)
        if not desc or price is None or sku.upper() in {"MODEL","LOC"}: continue
        if not sku:
            sku,source=infer_sku_from_description(desc,"ASE")
        else:
            source="FILE"
        stock=vals[6] if len(vals)>6 else None
        transit=vals[7] if len(vals)>7 else None
        eta=vals[8] if len(vals)>8 else ""
        row=make_row("ASE",sku,desc,price,stock=stock,transit=transit,eta=eta,sku_source=source)
        if row: rows.append(row)
    return finalize(rows)


def parse_emolo(xls):
    return parse_simple_table(xls,"EMOLO",xls.sheet_names[0],aliases={"sku":["MODEL"],"desc":["DESCRIPTION"],"qty":["QTY"],"price":["PRICE"]})


def parse_hz(xls):
    sheet=next((s for s in xls.sheet_names if s.strip().upper()=="ALL PRODUCTS"),xls.sheet_names[0])
    # Model Number is the more useful cross-vendor MPN. Fall back to SKU/UPC.
    df=pd.read_excel(xls,sheet_name=sheet,header=0)
    rows=[]
    for _,r in df.iterrows():
        sku=safe_text(r.get("Model Number")) or safe_text(r.get("SKU"))
        row=make_row("H&Z",sku,r.get("Description"),r.get("Price (USD)"),stock=r.get("Qty"),moq=r.get("MOQ"),eta=r.get("ETA"),location=r.get("EXW"))
        if row: rows.append(row)
    return finalize(rows)


def parse_influential(xls):
    df=pd.read_excel(xls,sheet_name=xls.sheet_names[0],header=0)
    rows=[]
    for _,r in df.iterrows():
        sku=safe_text(r.get("MPN")) or safe_text(r.get("SKU")) or safe_text(r.get("UPC"))
        row=make_row("INFLUENTIAL",sku,r.get("Description"),r.get("Price"),stock=r.get("Qty"))
        if row: rows.append(row)
    return finalize(rows)


def parse_prt(xls):
    return parse_simple_table(xls,"PRT",xls.sheet_names[0],hdr=0,aliases={"sku":["PART NUMBER"],"desc":["DESCRIPTION"],"qty":["QTY"],"price":["PRICE"]})


def parse_snk(xls):
    rows=[]
    for sheet in xls.sheet_names:
        su=sheet.strip().upper()
        if "IPHONE" in su:  # layout is a matrix, not a normal SKU table; skip safely for now
            continue
        raw=pd.read_excel(xls,sheet_name=sheet,header=None)
        h=header_row(raw)
        if h is None: continue
        df=pd.read_excel(xls,sheet_name=sheet,header=h)
        c_sku=first_col(df,["MODEL","SKU"])
        c_desc=first_col(df,["ALL PRODUCTS","PRODUCTS","DESCRIPTION"])
        c_price=first_col(df,["PRICE (USD$)","EXW - NY","PRICE"])
        c_qty=first_col(df,["UNITS","UNITS - MIAMI","QTY"])
        c_ny=first_col(df,["UNITS - NY"])
        c_mia=first_col(df,["UNITS - MIAMI"])
        c_loc=first_col(df,["LOCATION"])
        if c_price is None: continue
        for _,r in df.iterrows():
            desc=safe_text(r[c_desc]) if c_desc else ""
            sku=safe_text(r[c_sku]) if c_sku else ""
            if not sku or not desc: continue
            mia=clean_qty(r[c_mia]) if c_mia else (clean_qty(r[c_qty]) if c_qty and "MIAMI" in su else None)
            ny=clean_qty(r[c_ny]) if c_ny else (clean_qty(r[c_qty]) if c_qty and "NY STOCK" in su else None)
            stock=clean_qty(r[c_qty]) if c_qty else None
            if c_mia or c_ny: stock=(mia or 0)+(ny or 0)
            loc=safe_text(r[c_loc]) if c_loc else sheet.strip()
            row=make_row("SNK",sku,desc,r[c_price],stock=stock,stock_miami=mia,stock_ny=ny,location=loc)
            if row: rows.append(row)
    return finalize(rows)


def parse_sunrise(xls):
    sheet=next((s for s in xls.sheet_names if "PRICE" in s.upper()),xls.sheet_names[0])
    raw=pd.read_excel(xls,sheet_name=sheet,header=None)
    rows=[]
    # Columns in this workbook: QTY, category, description, stock/transit, Cash, take-all.
    for _,r in raw.iterrows():
        vals=r.tolist()
        if len(vals)<5: continue
        qty=clean_qty(vals[0]); desc=safe_text(vals[2] if len(vals)>2 else "")
        status=safe_text(vals[3] if len(vals)>3 else "").upper()
        cash=clean_price(vals[4] if len(vals)>4 else None)
        if not desc or cash is None or qty is None: continue
        sku,source=infer_sku_from_description(desc,"SUNRISE")
        stock=qty if "STOCK" in status else 0
        transit=qty if "TRANSIT" in status else 0
        row=make_row("SUNRISE",sku,desc,cash,stock=stock,transit=transit,location=status,sku_source=source)
        if row: rows.append(row)
    return finalize(rows)


def parse_tech(xls):
    # Consolidate duplicate item/location rows into one row per SKU at same price.
    df=parse_simple_table(xls,"TECH SUPPLIER",xls.sheet_names[0],hdr=0,
                          aliases={"sku":["ITEM NUMBER"],"desc":["ITEM DESCRIPTION"],"qty":["QUANTITY"],"price":["PRICE"],"location":["LOCATION"]})
    if df.empty: return df
    # Convert rows tagged in transit to En_Transito; Miami rows remain Stock.
    transit_mask=df["Ubicacion"].astype("string").str.upper().str.contains("TRANSIT",na=False)
    df["Stock"] = pd.to_numeric(df["Stock"], errors="coerce").astype("Float64")
    df["En_Transito"] = pd.to_numeric(df["En_Transito"], errors="coerce").astype("Float64")
    if bool(transit_mask.any()):
        df.loc[transit_mask,"En_Transito"] = df.loc[transit_mask,"Stock"].to_numpy()
        df.loc[transit_mask,"Stock"] = 0.0
    grouped=[]
    for (sku,price),g in df.groupby(["SKU","Precio"],dropna=False):
        best=g.iloc[0].copy()
        best["Stock"]=g["Stock"].fillna(0).sum()
        best["En_Transito"]=g["En_Transito"].fillna(0).sum()
        best["Ubicacion"]=" / ".join(sorted(set(x for x in g["Ubicacion"].astype(str) if x and x.lower()!="nan")))
        best["Descripcion"]=max(g["Descripcion"].astype(str),key=len)
        grouped.append(best.to_dict())
    return finalize(grouped)


def parse_trading(xls):
    rows=[]
    for sheet in xls.sheet_names:
        raw=pd.read_excel(xls,sheet_name=sheet,header=None)
        h=header_row(raw)
        if h is None: continue
        df=pd.read_excel(xls,sheet_name=sheet,header=h)
        su=sheet.strip().upper()
        # aliases vary by brand sheet
        c_sku=first_col(df,["SKU","ITEM NUMBER","ITEMKEY","ITEM CODE","MODEL"])
        c_desc=first_col(df,["DESCRIPTION","PRODUCTS:","PRODUCT DESCRIPTION","ITEMNAME","KEY FEATURE DESCRIPTION","HP"])
        c_qty=first_col(df,["QUANTITY","MASTER","QTY","AVAILABILITY"])
        c_price=first_col(df,["PROMO PRICE","PRICE","PRICE ","COST"])
        if c_price is None: continue
        # House of Marley has continuation rows for color variants; carry description down.
        if c_desc is not None and "HOUSE OF MARLEY" in su:
            df[c_desc]=df[c_desc].ffill()
        for _,r in df.iterrows():
            sku=safe_text(r[c_sku]) if c_sku else ""
            desc=safe_text(r[c_desc]) if c_desc else ""
            if not sku and desc:
                sku,source=infer_sku_from_description(desc,"TRADING")
            else:
                source="FILE"
            price=r[c_price]
            # If PROMO PRICE exists but is blank, fall back to regular PRICE.
            if clean_price(price) is None and _norm_col(c_price)=="PROMO PRICE":
                regular=first_col(df,["PRICE"])
                price=r[regular] if regular else price
            stock=r[c_qty] if c_qty else None
            # Availability like NOW is not numeric; leave stock unknown.
            row=make_row("TRADING",sku,desc,price,stock=stock,location=sheet.strip(),sku_source=source)
            if row: rows.append(row)
    return finalize(rows)


def parse_worldwide(xls):
    df=pd.read_excel(xls,sheet_name=xls.sheet_names[0],header=0)
    rows=[]
    for _,r in df.iterrows():
        item=safe_text(r.get("ITEM"))
        sku=item.split(":",1)[1].strip() if ":" in item else item
        row=make_row("WORLDWIDE",sku,r.get("Description"),r.get("FOB"),stock=r.get("QTY"),location="Miami")
        if row: rows.append(row)
    return finalize(rows)


def parse_cellutech(xls):
    # Legacy .xls workbook with several different product families.
    # Requires xlrd when uploaded as .xls.
    parts=[]
    for sheet in xls.sheet_names:
        try:
            su=sheet.strip().upper()
            if su=="TABLETS":
                aliases={"sku":["PART NO"],"desc":["DESCRIPTION"],"qty":["ON HAND"],"price":["FOB DUBAI($)","FOB"]}
            elif su=="HP POLY":
                aliases={"sku":["PART NUMBER"],"desc":["DESCRIPTION"],"price":["PRICE"],"moq":["MOQ"]}
            elif su=="STEEL SERIES":
                aliases={"sku":["PART #"],"desc":["DESCRIPTION"],"qty":["QUANTITY"],"price":["PRICE IN USD","PRICE"]}
            elif su=="DUBAI LAPTOPS":
                aliases={"sku":["PART NO"],"desc":["DESCRIPTION"],"qty":["ON HAND"],"price":["FOB DUBAI($)","FOB"]}
            else:
                aliases=None
            d=parse_simple_table(xls,"CELLUTECH",sheet,hdr=0,aliases=aliases)
            if not d.empty:
                if "DUBAI" in su:
                    d["Ubicacion"]="Dubai"
                parts.append(d)
        except Exception:
            continue
    return pd.concat(parts,ignore_index=True).drop_duplicates() if parts else empty_frame()


PARSERS = {
    "HAIFA": parse_haifa,
    "ALL PLUS": parse_allplus,
    "ALLBRANDS": parse_allbrands,
    "AQUA CONSUMER": lambda x: parse_aqua(x,"AQUA CONSUMER"),
    "AQUA LAPTOPS": lambda x: parse_aqua(x,"AQUA LAPTOPS"),
    "ASE": parse_ase,
    "CELLUTECH": parse_cellutech,
    "EMOLO": parse_emolo,
    "H&Z": parse_hz,
    "INFLUENTIAL": parse_influential,
    "PRT": parse_prt,
    "SNK": parse_snk,
    "SUNRISE": parse_sunrise,
    "TECH SUPPLIER": parse_tech,
    "TRADING": parse_trading,
    "WORLDWIDE": parse_worldwide,
}


def parse_uploaded(uploaded) -> Tuple[pd.DataFrame, dict]:
    provider=detect_provider(uploaded.name)
    info={"Archivo":uploaded.name,"Proveedor":provider,"Productos":0,"Estado":"OK","Detalle":""}
    try:
        xls=excel_file(uploaded)
        parser=PARSERS.get(provider)
        if parser is None:
            # Last-resort generic parser across sheets.
            parts=[]
            for s in xls.sheet_names:
                d=parse_simple_table(xls,provider,s)
                if not d.empty: parts.append(d)
            df=pd.concat(parts,ignore_index=True).drop_duplicates() if parts else empty_frame()
        else:
            df=parser(xls)
        info["Productos"]=len(df)
        if df.empty:
            info["Estado"]="⚠️ Sin datos"
            info["Detalle"]="No se detectaron filas válidas con SKU/Descripción y precio."
        return df,info
    except Exception as e:
        info["Estado"]="❌ Error"
        info["Detalle"]=str(e)
        return empty_frame(),info


# ============================================================
# APP
# ============================================================
logo_file="logo.png"
col_logo,col_title=st.columns([1,5])
with col_logo:
    if os.path.exists(logo_file): st.image(logo_file,width=130)
with col_title:
    st.markdown('<div class="main-header">Motech Miami — Comparador Multiproveedor de Precios</div>',unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Compara precios, stock, tránsito y disponibilidad entre distribuidores.</div>',unsafe_allow_html=True)

day_index=datetime.datetime.now().day % len(MOTIVATIONAL_QUOTES)
st.markdown(f'''<div class="welcome-card"><div class="welcome-title">¡Hola! Bienvenido de nuevo 👋</div>
<div class="quote-text">{MOTIVATIONAL_QUOTES[day_index]}</div></div>''',unsafe_allow_html=True)

uploaded_files=st.sidebar.file_uploader(
    "Cargar listas de proveedores (.xlsx / .xls):",
    type=["xlsx","xls"],accept_multiple_files=True
)

if not uploaded_files:
    st.info("👋 Sube las listas de proveedores en el menú lateral para armar la matriz comparativa.")
    st.stop()

frames=[]; import_log=[]
for f in uploaded_files:
    d,info=parse_uploaded(f)
    import_log.append(info)
    if not d.empty: frames.append(d)

log_df=pd.DataFrame(import_log)
with st.expander("📥 Resultado de importación",expanded=True):
    st.dataframe(log_df,use_container_width=True,hide_index=True)

if not frames:
    st.error("No se pudieron extraer productos válidos de las listas cargadas.")
    st.stop()

df_all=pd.concat(frames,ignore_index=True)

# Enrich after normalization so every provider uses the same logic.
df_all["Categoria"]=df_all.apply(lambda r:clasificar_categoria(r["Descripcion"],r["SKU"]),axis=1)
specs=df_all.apply(lambda r:extraer_especificaciones(f'{r["Descripcion"]} {r["SKU"]}'),axis=1)
specs.columns=["Procesador","RAM","Disco_SSD","Pantalla"]
df_all[["Procesador","RAM","Disco_SSD","Pantalla"]]=specs

# KPIs
k1,k2,k3,k4=st.columns(4)
k1.metric("Filas importadas",f"{len(df_all):,}")
k2.metric("Proveedores",df_all["Proveedor"].nunique())
k3.metric("SKUs únicos",df_all["SKU"].nunique())
k4.metric("Con stock > 0",f"{int((df_all['Stock'].fillna(0)>0).sum()):,}")

# Price matrix
pivot_p=df_all.pivot_table(index="SKU",columns="Proveedor",values="Precio",aggfunc="min")
provs=list(pivot_p.columns)
desc_map=df_all.groupby("SKU")["Descripcion"].apply(lambda x:max(x.astype(str),key=len)).to_dict()
pivot_p.insert(0,"Descripcion",pivot_p.index.map(desc_map))
pivot_p.insert(1,"Categoria",pivot_p.index.map(df_all.groupby("SKU")["Categoria"].first().to_dict()))
for c in ["Procesador","RAM","Disco_SSD","Pantalla"]:
    pivot_p[c]=pivot_p.index.map(df_all.groupby("SKU")[c].first().to_dict())

pivot_p["Min Precio"]=pivot_p[provs].min(axis=1)
pivot_p["Max Precio"]=pivot_p[provs].max(axis=1)
pivot_p["Proveedor Menor Precio"]=pivot_p[provs].idxmin(axis=1)
pivot_p["Ahorro Val"]=pivot_p["Max Precio"]-pivot_p["Min Precio"]
pivot_p["Es Deal"]=(pivot_p[provs].notna().sum(axis=1)>=2)&(pivot_p["Ahorro Val"]>0)

# Attach stock/location from cheapest provider and highest-stock provider.
stock_cheapest=[]; loc_cheapest=[]; high_stock=[]; high_stock_provider=[]
for sku,row in pivot_p.iterrows():
    winner=row["Proveedor Menor Precio"]
    sub=df_all[(df_all["SKU"]==sku)&(df_all["Proveedor"]==winner)].sort_values("Stock",ascending=False,na_position="last")
    stock_cheapest.append(sub.iloc[0]["Stock"] if not sub.empty else None)
    loc_cheapest.append(sub.iloc[0]["Ubicacion"] if not sub.empty else "")
    allsku=df_all[df_all["SKU"]==sku].copy()
    if not allsku.empty and allsku["Stock"].notna().any():
        ix=allsku["Stock"].fillna(-1).idxmax()
        high_stock.append(allsku.loc[ix,"Stock"])
        high_stock_provider.append(allsku.loc[ix,"Proveedor"])
    else:
        high_stock.append(None); high_stock_provider.append("")
pivot_p["Stock (Menor Precio)"]=stock_cheapest
pivot_p["Stock"]=stock_cheapest
pivot_p["Ubicación / ETA"]=loc_cheapest
pivot_p["Mayor Stock"]=high_stock
pivot_p["Proveedor Mayor Stock"]=high_stock_provider

# Sidebar filters
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 Búsqueda Rápida")
search_query=st.sidebar.text_input("Buscar por SKU o Descripción:",placeholder="Ej: 15-FC0093, MacBook, i7...")
st.sidebar.markdown("---")
st.sidebar.markdown("### 💻 Filtros Técnicos")
def opts(col): return sorted([x for x in pivot_p[col].dropna().unique().tolist() if x!="Otro / No especificado"])
f_cpu=st.sidebar.multiselect("Procesador:",opts("Procesador"))
f_ram=st.sidebar.multiselect("Memoria RAM:",opts("RAM"))
f_ssd=st.sidebar.multiselect("Disco / SSD:",opts("Disco_SSD"))
f_screen=st.sidebar.multiselect("Pantalla:",opts("Pantalla"))

filtered=pivot_p.copy()
if search_query:
    q=search_query.strip().upper()
    filtered=filtered[
        filtered.index.astype(str).str.upper().str.contains(q,regex=False,na=False)|
        filtered["Descripcion"].astype(str).str.upper().str.contains(q,regex=False,na=False)
    ]
if f_cpu: filtered=filtered[filtered["Procesador"].isin(f_cpu)]
if f_ram: filtered=filtered[filtered["RAM"].isin(f_ram)]
if f_ssd: filtered=filtered[filtered["Disco_SSD"].isin(f_ssd)]
if f_screen: filtered=filtered[filtered["Pantalla"].isin(f_screen)]

# Deals
deals_df=filtered[filtered["Es Deal"]].sort_values("Ahorro Val",ascending=False)
with st.expander(f"🔥 Ofertas Destacadas & Oportunidades ({len(deals_df)})",expanded=True):
    if deals_df.empty:
        st.info("No se encontraron diferencias de precio entre dos o más proveedores para la selección actual.")
    else:
        cols=st.columns(3)
        for idx,(sku,row) in enumerate(deals_df.head(6).iterrows()):
            with cols[idx%3]:
                desc=html.escape(str(row["Descripcion"]))
                sku_safe=html.escape(str(sku))
                prov_safe=html.escape(str(row["Proveedor Menor Precio"]))
                st.markdown(f'''<div class="card-deal"><span class="badge-savings">Diferencia: ${row['Ahorro Val']:,.2f}</span>
                <div class="card-title">{desc[:80]}...</div><span class="badge-meta">SKU: {sku_safe}</span>
                <span class="badge-meta">Menor precio: {prov_safe} (${row['Min Precio']:,.2f})</span></div>''',unsafe_allow_html=True)

st.markdown("---")
categories=sorted(filtered["Categoria"].dropna().unique().tolist())
tab_list=["🌐 Todos los Productos"]+categories
tabs=st.tabs(tab_list)
cols_display=["Descripcion"]+provs+["Stock","Ubicación / ETA"]

def highlight_min_price(row):
    styles=[""]*len(row)
    vals=[row[p] for p in provs if p in row.index and pd.notna(row[p])]
    if vals:
        minv=min(vals)
        for p in provs:
            if p in row.index and pd.notna(row[p]) and row[p]==minv:
                styles[row.index.get_loc(p)]='background-color:#D4EDDA;font-weight:bold;color:#155724;'
    return styles

for tab,cat in zip(tabs,tab_list):
    with tab:
        d=filtered if cat=="🌐 Todos los Productos" else filtered[filtered["Categoria"]==cat]
        st.markdown(f"**Mostrando {len(d):,} productos**")
        if d.empty:
            st.warning("No hay productos disponibles en esta categoría.")
        else:
            fmt={p:"${:,.2f}" for p in provs if p in d.columns}
            fmt.update({"Min Precio":"${:,.2f}","Max Precio":"${:,.2f}","Stock":"{:,.0f}"})
            st.dataframe(d[cols_display].style.apply(highlight_min_price,axis=1).format(fmt,na_rep="—"),use_container_width=True)

# Diagnostics: inferred/synthetic SKUs cannot always match across vendors.
inferred=df_all[df_all["SKU_Fuente"].isin(["INFERRED","SYNTHETIC"])]
if not inferred.empty:
    with st.expander(f"🧩 SKUs inferidos automáticamente ({len(inferred)})"):
        st.caption("Estos proveedores no siempre entregan un SKU/MPN explícito. Revisa estos productos si necesitas comparación exacta entre proveedores.")
        st.dataframe(inferred[["Proveedor","SKU","SKU_Fuente","Descripcion","Precio","Stock"]],use_container_width=True,hide_index=True)
