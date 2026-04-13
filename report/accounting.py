"""
accounting.py — Parsing and reconciliation of Czech accounting data (Money S3 Hlavní kniha, účet 315).

Ported from archive/reconcile.py. Provides:
- CSV parsing of Hlavní kniha exports (cp1250, semicolon-delimited)
- Object name normalization and alias resolution
- L3 matching: payout aggregates (from DB) vs accounting entries (from imported CSV)
"""

import csv
import io
import re
import unicodedata
from datetime import datetime


# ---------------------------------------------------------------------------
# Object name mapping tables (ported from archive/reconcile.py)
# ---------------------------------------------------------------------------

LISTING_TO_OBJEKT = {
    "10 minutes Charles bridge modern APT": "Pštrossova 5 nová",
    "10 minutes National museum Prague | Compact APT": "Rumunská 32/2",
    "10 minutes National museum Prague Homey APT": "Rumunska 32/1",
    "10 minutes Wenceslas Square Apartment Metro": "Vinohradská 208/14",
    "2 min walk O2 Arena NEW Dinopark APT": "Ocelarska 17",
    "4 minutes National Theather Apartment": "Pštrossova 35",
    "5 min Dancing House APT · Luxury · Modern": "Křemencova 2",
    "Artsy Attic 1 BR · National Museum · Parking · AC": "Skolska 20",
    "By the River • Warm & Cozy Stay in Prague": "V Háji 12",
    "Central 2BR Apartment |2 Baths | Parking": "Opletalova 8_4P nova",
    "Central Studio Apartment near Charles Square": "Žitná 208 NOVÁ",
    "Charles Bridge APT • Prague Historic Stay": "Malostranska 1P",
    "Charles Square Central Studio Apartment": "Pricna 4",
    "Charming Stylish Peaceful 2BR Nest": "Oblouková 545/28",
    "City Vibes Only! 2BR Near National Museum 2001": "Václavské náměstí 2001",
    "Compact Modern Loft • Peaceful Prague Center": "Lublanska 13, leva",
    "Fresh Apartment next to Prague City Center at SoHo": "V Háji 10",
    "Friendly Apartment close to Wenceslas Square": "Řeznická 21/3P",
    "Live Like a Local! 1BR by National Museum 2002": "Václavské náměstí 2002",
    "Marilyn Monroe APT with AC in Prague centre": "Opletalova 10",
    "Marilyn Monroe APT with AC in Prague center": "Opletalova 10",
    "Michelska residence Scandinavian Quiet Apartment": "Michelská 9d",
    "Minimalistic Organic APT • Relaxed Prague Centre": "Lublanska 13, prava",
    "Minimalistic Organic APT • Relaxed Prague Center": "Lublanska 13, prava",
    "Modern 5 min Wenceslas Square Apartment": "Navratilova 14",
    "Modern APT City Hideaway": "28. Pluku 58",
    "Modern High Ceiling Loft with Terrace": "Kremencova 2b",
    "Modern Industrial 8min Dancing House APT | Prague": "Preslova 19",
    "Modern Smichov APT| Balcony| Near Public Transport": "Nádražní 9",
    "NEW 2BR Smíchov 2Bathrooms | Prague, Tram Access": "Siklove 2",
    "NEW Anděl Skyline Apartment • Prague 5": "Toyen 7",
    "NEW Modern 1BDR Apartment 604 w Parking": "MyMozart 604",
    "NEW Modern Central located APT *Netflix": "Reznicka 21",
    "NEW Modern studio 221 w Netflix": "MyMozart 221",
    "NEW Modern, River View APT near Prague City Center": "Tusarova 57",
    "National Theatre Apartment • 2 Bathrooms": "Ostrovni 4",
    "New & Peaceful Flat in the Heart of Prague 3103": "Vaclavske namesti 3103",
    "New Rentero Central Loft APT Parking & Netflix": "U Pujcovny 5",
    "New Town Central Studio Apartment | Prague 1": "Žitná 308",
    "New stylish studio 508": "MyMozart 411",
    "Parking Modern Apartment 515 NEW": "MyMozart 515",
    "Parking New stylish studio 507 Netflix": "MyMozart 507",
    "Parking*Deluxe apartment with Netflix 616": "MyMozart 616",
    "Parking*Deluxe studio apartment 211*Netflix": "MyMozart 211",
    "Parking*Modern deluxe studio apartment 206": "MyMozart 206",
    "Parking*Modern one-bedroom APT*Netflix 213": "MyMozart 213",
    "Parking*NEW | Prague Deluxe studio 207 Netflix": "MyMozart 207",
    "Parking*One bedroom stylish apartment 414 Netflix": "MyMozart 414",
    "Prague Central •.• White Jewel": "Washingtonova 9",
    "Prague Designer Petite Stay | near Vltava River": "Svornosti 1497/1",
    "Prague Haven – Steps from National Museum 2101": "Vaclavske namesti 2101",
    "Quiet Courtyard Apt | Prague Centre | 3102": "Vaclavske namesti 3102",
    "Riverside Serenity Residence • Prague 7": "U Parního mlýna 6",
    "Smart & Compact Historic Central Suite": "Malostranska 3P",
    "Smíchov Riverside & Station Residence | Prague 5": "Strakonicka 21 / NOVA",
    "Spacious 2BR APT Prague | 2 Baths": "Opletalova 8_4P NOVÁ",
    "Spacious quiet APT · Near Prague Castle": "Kroftova 8A",
    "Step Into Prague! 1BR by National Museum 3001": "Vaclavske namesti 3001",
    "Stylish APT in picturesque Prague district": "Moskevska 58",
    "Stylish • Central 2BDR APT• 2 min Metro": "Ječná 43",
    "Two Bedroom | Near Prague\u2019s Iconic Landmarks |3101": "Vaclavske namesti 3101",
    "Urban Elegance Next to National Museum 2102": "Vaclavske namesti 2102",
    "Urban Elegance Suite • Balcony • Prague 7": "Dělnická 44",
    "Warm & Bright Apartment • Prague Vinohrady • A/C": "Jičínská 11",
    "Wenceslas Square central apartment · AC": "Opletalova 8",
    "• Golden •.• Wabi-Sabi • APT •": "Francouzská 50",
    "• Royal blue • Wenceslas • APT •": "Václavské náměstí 48",
    # New listings
    "Cozy apartment in a Historic part of Prague": "Žitomírská 36",
    "Cozy apartment near Holešovice market": "Dělnická 49",
    "Cute Gem in the City Center": "Petrska 33",
    "Design & Stylish Studio | Black Decor | Parking": "MyMozart 506",
    "Havlíček gardens sunny studio apartment": "Varšavská 12",
    "Newly renovated · City Center · Quiet · Studio APT": "Putova 2",
    "Parking*Stylish new APT 210*Netflix": "MyMozart 210",
    "Peaceful 1BD Spot | Fast Public Transit Access": "Na Spojce 10",
    "Scandinavian* Private Garden APT": "Michelska 9a",
    "Spacious 2BR APT Prague | 2 Baths | Parking": "Opletalova 8_4P",
    "Spacious APT near Prague Castle": "Kroftova 8A",
    "Spacious Modern APT near city center": "Prokopova",
    "Stylish Stay by Vyšehrad APT": "Lounskych 10",
    "Trendy Holesovice Area · 1BDR APT  · Balcony": "Delnicka 2",
    "Trendy Holesovice Area · 1BDR APT · Balcony": "Delnicka 2",
    "Urban Elegance Suite • Balcony • 2 Bathrooms": "Dělnická 44 - nová",
    "Washington Central Apartment • Parking": "Washingtonova 206",
}


def _norm_key(s):
    return s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')


LISTING_TO_OBJEKT = {_norm_key(k): v for k, v in LISTING_TO_OBJEKT.items()}

BOOKING_PROPERTY_TO_OBJEKT = {
    "Rentero MyMozart Apartments Prague": "MyMozart",
    "Rentero Wenceslas Square Apartments": "Vaclavske namesti",
    "Rentero Lublanska Residence - Stylish Lofts": "Lublanska 13",
    "Modern APT City Hideaway": "28. Pluku 58",
    "Cozy apartment near Holesovice market": "Dělnická 49",
    "Cozy apartment near Holešovice market": "Dělnická 49",
    "Trendy Holesovice market 1 Bedroom Apartment": "Delnicka 2",
    "Rentero Urban Elegance Suite - Prague 7": "Dělnická 44",
    "Rentero Riverside Serenity Residence - Prague 7": "U Parního mlýna 6",
    "Rentero Warm & Cozy Stay in Prague, By the River": "V Háji 12",
    "Rentero Warm and Bright Apartment at Prague": "Jičínská 11",
    "Rentero Golden Wabi-Sabi Apartment, Prague": "Francouzská 50",
    "Rentero Artsy Roof Top Apartment in City Centre": "Školská 20",
    "Rentero Marilyn Monroe Apartment with AC in Prague center": "Opletalova 10",
    "Rentero Wenceslas square Apartment, Prague": "Opletalova 8",
    "Rentero Central Two Bedroom Apartment in Prague - Two bathroom - Parking": "Opletalova 8_4P nova",
    "Rentero National Theatre Apartment with 2 Bathrooms Prague City Center": "Ostrovni 4",
    "Rentero Cozy Industrial 8min Dancing House Apartment, Prague": "Preslova 19",
    "Rentero 5 min Dancing House Apartment Prague, Luxury and Modern": "Křemencova 2",
    "Rentero Modern High Ceiling Loft with Terrace Apartment, Prague": "Kremencova 2b",
    "Rentero Charles square studio apartment Parking on request": "Příčná",
    "Rentero Scandinavian - Private Garden APT": "Michelska 9a",
    "Spacious APT near Prague Castle": "Kroftova 8A",
    "Rentero Spacious Apartment near Prague Castle": "Kroftova 8A",
    "Rentero Modern 5 min Wenceslas Square Apartment, Prague": "Navratilova 14",
    "Rentero Apartment, 10 minutes Wenceslas Square, Prague": "Vinohradská 208/14",
    "Rentero Royal blue Wenceslas Apartment, Prague": "Václavské náměstí 48",
    "Rentero Michelska Residence Scandinavian Quiet Apartment, Prague": "Michelská 9d",
    "Rentero Cozy Apartment in picturesque Prague district": "Moskevska 58",
    "Rentero Friendly Apartment near Wenceslas Square": "Řeznická 21/3P",
    "Rentero modern Apartment, 10 minutes Charles bridge, Prague": "Pštrossova 5",
    "Rentero Modern Prague Smichov Apartment with Balcony": "Nádražní 9",
    "Rentero Smíchov Riverside & Station Residence": "Strakonicka 21 / NOVA",
    "Rentero Sm\u00edchov Riverside & Station Residence": "Strakonicka 21 / NOVA",
    "Rentero Spacious Sm\u00edchov 2 Bedroom Apartment with Excellent Tram Connection": "Siklove 2",
    "Rentero Spacious Smíchov 2 Bedroom Apartment with Excellent Tram Connection": "Siklove 2",
    "Rentero Fresh Apartment next to Prague City Center": "V Háji 10",
    "Rentero NEW Modern, River View APT near Prague City Center": "Tusarova 57",
    "Rentero Modern, River View Apartment near Prague City Center": "Tusarova 57",
    "Rentero Designer Petite Stay, Prague": "Svornosti 1497/1",
    "Rentero Central Loft Apartment": "U Půjčovny 5",
    "Rentero Prague Central Apartment": "Washingtonova 9",
    "Rentero One bedroom O2 Arena Dinopark Apartment": "Ocelarska 17",
    "Rentero One Bedroom Washington Apartment": "Washingtonova 9",
    "Rentero Varsavska Apartment Residence, Prague": "Varšavská 12",
    "Putova Apartment Residence, Prague": "Putova 2",
    "Rentero NEW And\u011bl Skyline Apartment - Prague 5": "Toyen 7",
    "Rentero NEW Anděl Skyline Apartment - Prague 5": "Toyen 7",
    "Rentero Apartment, 10 minutes National museum, Prague": "Rumunska 32/1",
    "Rentero natural ground base Apartment, Prague": "Rumunska 32/2",
    "Rentero Apartment, 5 min National Muzeum Modern Black Rose": "Ječná 43",
    "Rentero Cozy Wenceslas square 700m Apartment, Prague": "Řeznická 21",
    "Rentero Historic Central Suite - Prague 1": "Malostranska",
    "Cute Gem in the City Center": "Petrska 33",
    "Peaceful One Bedroom Apartment by Rentero": "Na Spojce 10",
    "Spacious Apartment near Prague center": "Prokopova",
    "Stylish Stay by Vyšehrad Apartment": "Lounskych 10",
    "Stylish Stay by Vy\u0161ehrad Apartment": "Lounskych 10",
    "Rentero Central Apartment near Charles Square": "Žitná 208",
    "Rentero Modern APT City Hideaway": "28. Pluku 58",
    "Rentero apartment, 4 minutes National Theather, Prague": "Pštrossova 35",
}

BOOKING_ID_TO_OBJEKT = {
    "13751792": "Washingtonova 206",
    "13286534": "Washingtonova 9",
    "14317160": "Opletalova 8_4P nova",
    "10289700": "Opletalova 8",
}

_BOOKING_FOLD_PATTERNS = [
    (re.compile(r'^lublanska 13[, ]'), "lublanska 13"),
    (re.compile(r'^malostranska \d'), "malostranska"),
]

_OBJEKT_315_ALIASES = {
    "mi_b": "michelska 9a",
    "delnicka2": "delnicka 2",
    "kroftova 8": "kroftova 8a",
    "nadrazni9": "nadrazni 9",
    "jecn43": "jecna 43",
    "deln2": "delnicka 2",
    "delnicka44": "delnicka 44",
    "delnicka 49 orphan": "delnicka 49",
    "u pujcovny": "u pujcovny 5",
    "reznicka 21 - 3.patro": "reznicka 21/3p",
    "kremencova 2 3kk": "kremencova 2",
    "kremencova 2b 2kk": "kremencova 2b",
    "moskevska": "moskevska 58",
    "navratilova": "navratilova 14",
    "rumunska32/1": "rumunska 32/1",
    "vinohr 14": "vinohradska 208/14",
    "vinohradska208/14": "vinohradska 208/14",
    "vaclavske namesti 58": "vaclavske namesti 48",
    "soho": "tusarova 57",
    "soho511": "tusarova 57",
    "soho 406 nove": "v haji 10",
    "soho406": "v haji 10",
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def safe_float(v):
    try:
        return float(str(v).replace(",", ".").replace("\xa0", "").strip())
    except Exception:
        return 0.0


def normalize_objekt(s):
    s = str(s).lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip()


def parse_date(s):
    s = str(s).strip()
    for fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def norm_listing(s):
    s = str(s)
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    return s.strip()


# ---------------------------------------------------------------------------
# Document classification
# ---------------------------------------------------------------------------

def classify_315(code):
    c = str(code).strip().upper()
    if c.startswith("FKV"):
        return "FKV"
    if c.startswith("FHS"):
        return "FHS"
    if c.startswith("FHO"):
        return "FHO"
    if c.startswith("FU"):
        return "FU"
    if c.startswith("RF"):
        return "RF"
    return "JINY"


# ---------------------------------------------------------------------------
# Object name expansion (FKV/FHS/FHO popis)
# ---------------------------------------------------------------------------

def expand_objekt_315(s, stredisko_map=None):
    if not s:
        return s
    s = s.strip()
    ns = normalize_objekt(s)

    if stredisko_map:
        mapped = stredisko_map.get(ns)
        if mapped:
            s = mapped
            ns = normalize_objekt(s)

    m = re.match(r'^m(\d+)$', ns)
    if m:
        return f"MyMozart {m.group(1)}"
    m = re.match(r'^my\s+mozart\s+(\d+)$', ns)
    if m:
        return f"MyMozart {m.group(1)}"
    if re.match(r'^mi_?b$', ns) or re.match(r'^michle\s*b$', ns):
        return "Michelská 9a"
    if re.match(r'^mi_?e$', ns) or re.match(r'^michle(?:\s*e)?$', ns):
        return "Michelská 9d"
    if re.match(r'^michle\s*e\d+$', ns):
        return "Michelská 9d"
    m = re.match(r'^soho[\s\-]*(?:tusarova\s*[#]?\s*)?(\d+)(?:\s+nov[aey])?$', ns)
    if m:
        if m.group(1) == "406":
            return "V Háji 10"
        if m.group(1) == "511":
            return "Tusarova 57"
        return f"Soho {m.group(1)}"
    if ns == "soho":
        return "Tusarova 57"
    if re.match(r'^opletalova\s*8(?:[ _/]|$)4p', ns) or re.match(r'^opletalova\s*8[ /]4[p.]', ns):
        return "Opletalova 8_4P nova"
    if re.match(r'^opletalova\s*4\.?\s*patro$', ns):
        return "Opletalova 8_4P nova"
    if ns == "opletalova":
        return "Opletalova 8"
    if re.match(r'^kroftova\s*8a?$', ns):
        return "Kroftova 8A"
    if re.match(r'^28\.?\s*pluku\s+28$', ns):
        return "28. Pluku 58"
    if re.match(r'^lublan(?:ska)? 13[, ]*leva$', ns):
        return "Lublanska 13, leva"
    if re.match(r'^lublan(?:ska)? 13[, ]*prava$', ns):
        return "Lublanska 13, prava"
    if re.match(r'^pstrosssova\b', ns):
        return "Pštrossova 35"
    if re.match(r'^pstrossova\s*35$', ns):
        return "Pštrossova 35"
    if re.match(r'^strakonicka\b', ns):
        return "Strakonicka 21 / NOVA"
    if re.match(r'^vhaji[_\s]?12$', ns):
        return "V Háji 12"
    if ns == "mosk":
        return "Moskevska 58"
    if re.match(r'^soho406$', ns):
        return "V Háji 10"
    if ns == "wash206":
        return "Washingtonova 206"
    if ns == "wash" or re.match(r'^wash\s*9$', ns):
        return "Washingtonova 9"
    return s


def expand_rf_objekt(s):
    s = s.strip()
    ns = normalize_objekt(s)

    m = re.match(r'^m{1,2}(\d+)$', ns)
    if m:
        return f"MyMozart {m.group(1)}"
    m = re.match(r'^vn(\d+)$', ns)
    if m:
        return f"Vaclavske namesti {m.group(1)}"
    m = re.match(r'^s(\d+)$', ns)
    if m:
        if m.group(1) == "406":
            return "V Háji 10"
        if m.group(1) == "511":
            return "Tusarova 57"
        return f"Soho {m.group(1)}"
    if re.match(r'^soho\s*406(?:\s+nov[aey])?$', ns):
        return "V Háji 10"
    if re.match(r'^soho\s*511$', ns) or ns == "soho":
        return "Tusarova 57"
    if re.match(r'^vino\d*$', ns):
        return "Vinohradská 208/14"
    if re.match(r'^kroft(?:ova)?\s*8a$', ns):
        return "Kroftova 8A"
    if re.match(r'^kroft(?:ova)?\s*8$', ns):
        return "Kroftova 8A"
    if re.match(r'^kroftova\s*$', ns):
        return "Kroftova 8A"
    m = re.match(r'^v[aá]cl\.?\s*(?:n[aá]m\.?)?\s*(\d+)$', ns)
    if m:
        return f"Václavské náměstí {m.group(1)}"
    if re.match(r'^je[cč](?:n[aá])?\s*43?$', ns):
        return "Ječná 43"
    if re.match(r'^k?[rř]emenc(?:ova)?\s*2b$', ns):
        return "Kremencova 2b"
    if re.match(r'^k?[rř]emenc(?:ova)?\s*2$', ns):
        return "Kremencova 2"
    if re.match(r'^[rř]ezni[ck]', ns) and re.search(r'3\.?p\.?|3p|21.?3p', ns):
        return "Řeznická 21/3P"
    if re.match(r'^rezn?\s*21/?3p$', ns) or re.match(r'^rezn21/3p$', ns):
        return "Řeznická 21/3P"
    if re.match(r'^[rř]ezni[ck]', ns):
        return "Řeznická 21"
    if re.match(r'^pstrossova\s*35$', ns):
        return "Pštrossova 35"
    if re.match(r'^francouzska\s*50$', ns):
        return "Francouzská 50"
    if re.match(r'^navratilova\s*$', ns):
        return "Navratilova 14"
    if re.match(r'^vinohradsk[aá]\s*$', ns):
        return "Vinohradská 208/14"
    if re.match(r'^je[cč]n[aá]\s*$', ns):
        return "Ječná 43"
    m = re.match(r'^wash\s*(\d+)$', ns)
    if m:
        return f"Washingtonova {m.group(1)}"
    if re.match(r'^wash\s*$', ns):
        return "Washingtonova 9"
    return s


# ---------------------------------------------------------------------------
# Channel detection from popis
# ---------------------------------------------------------------------------

def parse_fkv_channel(popis):
    p = str(popis).lower()
    if "airbnb" in p or "od air" in p or "od ai" in p or "platby air" in p:
        return "Airbnb"
    if "booking" in p or "od boo" in p or "platby boo" in p:
        return "Booking"
    return None


# ---------------------------------------------------------------------------
# Popis parsing
# ---------------------------------------------------------------------------

_PROPERTY_SUFFIXES = {"leva", "prava", "nova", "stara", "horni", "dolni"}

def _looks_like_guest_token(token):
    token = str(token or "").strip(" -\u2013/.,")
    if len(token) < 3:
        return False
    if token.lower() in _PROPERTY_SUFFIXES:
        return False
    if any(ch.isdigit() for ch in token):
        return False
    if "/" in token or "_" in token:
        return False
    return all(ch.isalpha() or ch in "-''." for ch in token)


def _cleanup_accounting_objekt_text(text):
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if not s:
        return ""
    s = re.sub(r"\s+\d{2}/\d{2}\s*\+\s*\d{2}/\d{2}\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s*[-\u2013]\s*(?:v[yý]plata|vy[úu]čtov[aá]n[ií]|z[aá]po[cč]et).*$", "", s, flags=re.I)
    s = s.strip(" -\u2013/.,")
    tokens = s.split()
    while len(tokens) > 1 and _looks_like_guest_token(tokens[-1]):
        prefix = " ".join(tokens[:-1])
        if not re.search(r"\d", prefix):
            break
        tokens.pop()
    return " ".join(tokens).strip(" -\u2013/.,")


def parse_rf_popis(popis):
    p = str(popis).strip()
    pm = re.search(r'\b0*(\d{1,2})/(\d{2,4})\b', p)
    if not pm:
        return None, None, None
    mm = pm.group(1).zfill(2)
    yy = pm.group(2)[-2:]
    try:
        mesic = datetime.strptime(f"01/{mm}/{yy}", "%d/%m/%y").strftime("%Y-%m")
    except Exception:
        return None, None, None
    work = p[:pm.start()] + p[pm.end():]
    work = re.sub(
        r'z[aá]po[cč]et\s*'
        r'(?:platb[ay]\s*ubyt\.?|pl\.?\s*ubytov[aá]n[íi]|pl\.?\s*ubyt\.?|'
        r'paltby|platb[ay]|ubytov[aá]n[íi]|provize)'
        r'\s*',
        ' ', work, flags=re.I
    )
    work = re.sub(r'\s*[-\u2013]\s*\S+\s+FV\b.*$', '', work, flags=re.I)
    work = re.sub(r'\s+FV\b.*$', '', work, flags=re.I)
    work = re.sub(r'\s+[A-Z]{1,2}\s*$', '', work)
    channel = None
    for pat, ch in [
        (r'\b(?:Airbnb|Aibnb)\b', "Airbnb"),
        (r'\bBooking\b', "Booking"),
        (r'\bA\b', "Airbnb"),
        (r'\bB\b', "Booking"),
    ]:
        m = re.search(pat, work, re.I)
        if m:
            channel = ch
            work = work[:m.start()] + work[m.end():]
            break
    work = re.sub(r'(?<=\S)\s+[A-ZÁČŠŽŘŮÝĚ][a-záčšžřůýě]{2,}\s*$', '', work)
    objekt_abbrev = _cleanup_accounting_objekt_text(
        re.sub(r'[\s\-\u2013]+', ' ', work).strip(' -\u2013/.')
    )
    if not objekt_abbrev:
        return None, mesic, channel
    return expand_rf_objekt(objekt_abbrev), mesic, channel


def parse_objekt_period(popis):
    popis = str(popis).strip()

    def _strip_guest(s):
        return re.sub(r'(?<=\S)\s+[A-ZÁČŠŽŘŮÝĚ][a-záčšžřůýě]{2,}\s*$', '', s).strip()

    m0 = re.match(r"^(.+?)\s+(\d{2}/\d{2})\s*\+\s*(\d{2}/\d{2})\s*[-\u2013]", popis)
    if m0:
        objekt = _cleanup_accounting_objekt_text(m0.group(1))
        period_raw = m0.group(3)
        try:
            dt = datetime.strptime("01/" + period_raw, "%d/%m/%y")
            return objekt, dt.strftime("%Y-%m")
        except Exception:
            pass

    m = re.match(r"^(\d{2}/\d{2})\s+(.+?)\s*[-\u2013]", popis)
    if m:
        period_raw = m.group(1)
        objekt = _cleanup_accounting_objekt_text(_strip_guest(m.group(2).strip()))
        try:
            dt = datetime.strptime("01/" + period_raw, "%d/%m/%y")
            return objekt, dt.strftime("%Y-%m")
        except Exception:
            pass

    m2 = re.match(r"^(.+?)\s+(\d{2}/\d{2})\s*[-\u2013]", popis)
    if m2:
        objekt = _cleanup_accounting_objekt_text(
            _strip_guest(re.sub(r'[\s\-\u2013]+$', '', m2.group(1)))
        )
        period_raw = m2.group(2)
        try:
            dt = datetime.strptime("01/" + period_raw, "%d/%m/%y")
            return objekt, dt.strftime("%Y-%m")
        except Exception:
            pass
    return None, None


def _objekt_specificity_score(s):
    ns = normalize_objekt(s)
    if not ns:
        return -1
    tokens = [t for t in re.split(r"\W+", ns) if t]
    score = len(tokens)
    if re.search(r"\d", ns):
        score += 3
    if any(marker in ns for marker in ("#", "_", " leva", " prava", " nova", "1p", "3p", "2b", "4p", "2kk", "3kk")):
        score += 1
    return score


def choose_accounting_objekt(popis_objekt, stredisko_objekt):
    popis_objekt = str(popis_objekt or "").strip()
    stredisko_objekt = str(stredisko_objekt or "").strip()
    if not popis_objekt:
        return stredisko_objekt
    if not stredisko_objekt:
        return popis_objekt
    if normalize_objekt(popis_objekt) == normalize_objekt(stredisko_objekt):
        return popis_objekt
    if _objekt_specificity_score(stredisko_objekt) > _objekt_specificity_score(popis_objekt):
        return stredisko_objekt
    return popis_objekt


# ---------------------------------------------------------------------------
# Středisko map loading
# ---------------------------------------------------------------------------

def load_stredisko_from_bytes(content: bytes) -> list[dict]:
    text = content.decode("cp1250", errors="replace")
    entries = []
    for row in csv.reader(io.StringIO(text), delimiter=";"):
        if len(row) < 5 or row[0] != "Detail 1" or row[1] != "1":
            continue
        popis = _clean_stredisko_popis(row[2])
        zkratka = row[4].strip()
        if not popis or not zkratka:
            continue
        entries.append({"zkratka": zkratka, "popis": popis})
    return entries


def _clean_stredisko_popis(popis):
    s = str(popis or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = re.sub(r"\s*-\s*skon[cč]il.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s


def build_stredisko_map_dict(entries: list[dict]) -> dict:
    return {normalize_objekt(e["zkratka"]): e["popis"] for e in entries}


# ---------------------------------------------------------------------------
# Hlavní kniha CSV parsing
# ---------------------------------------------------------------------------

def load_hlavni_kniha_from_bytes(content: bytes, stredisko_map: dict | None = None) -> list[dict]:
    text = content.decode("cp1250", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    raw = list(reader)
    rows_out = []
    for row in raw:
        if not row or row[0] != "Detail 2 - Detail 1" or row[1] != "1":
            continue
        if len(row) < 18:
            continue

        doc = row[3].strip()
        datum = parse_date(row[5].strip())
        popis = row[8].strip()
        ucet = row[17].strip()
        stredisko = row[16].strip() if len(row) > 16 else ""

        objekt_stredisko = ""
        if stredisko and stredisko_map:
            mapped_popis = stredisko_map.get(normalize_objekt(stredisko))
            if mapped_popis:
                objekt_stredisko = expand_objekt_315(mapped_popis, stredisko_map)

        sloupec_d = abs(safe_float(row[10])) if len(row) > 10 else 0.0
        sloupec_md = abs(safe_float(row[13])) if len(row) > 13 else 0.0
        castka = sloupec_d if sloupec_d > 0 else sloupec_md

        if datum is None:
            continue

        doc_type = classify_315(doc)

        if ucet == "315001":
            channel = "Airbnb"
        elif ucet == "315002":
            channel = "Booking"
        else:
            channel = None

        if doc_type == "RF":
            objekt, mesic, ch_rf = parse_rf_popis(popis)
            if ch_rf and not channel:
                channel = ch_rf
        elif doc_type == "FHO" and stredisko:
            objekt = objekt_stredisko or expand_objekt_315(stredisko, stredisko_map)
            mesic = None
            m_per = re.match(r'^(\d{2}/\d{2})', popis)
            if m_per:
                try:
                    dt_per = datetime.strptime("01/" + m_per.group(1), "%d/%m/%y")
                    mesic = dt_per.strftime("%Y-%m")
                except ValueError:
                    pass
        else:
            objekt, mesic = parse_objekt_period(popis)
            if doc_type in ("FKV", "FHS", "FU"):
                objekt = choose_accounting_objekt(
                    expand_objekt_315(objekt, stredisko_map),
                    objekt_stredisko,
                )

        objekt_raw = objekt
        if objekt:
            objekt = expand_objekt_315(objekt, stredisko_map)

        rows_out.append({
            "datum": datum.isoformat() if datum else None,
            "popis": popis,
            "doc": doc,
            "doc_type": doc_type,
            "castka": round(castka, 2),
            "objekt": objekt,
            "objekt_raw": objekt_raw,
            "mesic": mesic,
            "channel": channel,
            "stredisko": stredisko,
            "ucet": ucet,
        })
    return rows_out


# ---------------------------------------------------------------------------
# Fuzzy object matching
# ---------------------------------------------------------------------------

def objekt_similarity(a, b):
    na, nb = normalize_objekt(a), normalize_objekt(b)
    if na == nb:
        return 1.0
    ta = set(re.split(r"\W+", na)) - {""}
    tb = set(re.split(r"\W+", nb)) - {""}
    nums_a = {t for t in ta if re.match(r'^\d', t)}
    nums_b = {t for t in tb if re.match(r'^\d', t)}
    if nums_a and nums_b and not (nums_a & nums_b):
        return 0.0
    words_a = ta - nums_a
    words_b = tb - nums_b
    if words_a and words_b and not (words_a & words_b):
        return 0.0
    unit_a = {t for t in ta if re.match(r'^\d+(?:p|kk)$', t)}
    unit_b = {t for t in tb if re.match(r'^\d+(?:p|kk)$', t)}
    if unit_a != unit_b and (unit_a or unit_b):
        return 0.0
    if na in nb or nb in na:
        return 0.85
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / max(len(ta), len(tb))


# ---------------------------------------------------------------------------
# Source normalization helpers
# ---------------------------------------------------------------------------

def _normalize_source_match_obj(obj, channel):
    norm = normalize_objekt(expand_objekt_315(obj or "") or "")
    norm = _OBJEKT_315_ALIASES.get(norm, norm)
    if str(channel or "").lower() == "booking":
        for pattern, group_key in _BOOKING_FOLD_PATTERNS:
            if pattern.match(norm):
                return group_key
    return norm


def _fold_booking_315(fkv_agg, fkv_detail, group_map):
    folded_agg = {}
    folded_detail = {}
    for (obj, mes), val in fkv_agg.items():
        key = (group_map.get(obj, obj), mes)
        folded_agg[key] = folded_agg.get(key, 0.0) + val
        folded_detail.setdefault(key, []).extend(fkv_detail.get((obj, mes), []))
    return folded_agg, folded_detail


def get_accounting_match_month(row):
    mesic = str(row.get("mesic") or "").strip()
    if re.match(r"^\d{4}-\d{2}$", mesic):
        return mesic
    datum_str = row.get("datum") or ""
    if datum_str:
        d = parse_date(datum_str) if isinstance(datum_str, str) else datum_str
        if d and hasattr(d, "strftime"):
            return d.strftime("%Y-%m")
    return None


# ---------------------------------------------------------------------------
# Payout aggregate from DB
# ---------------------------------------------------------------------------

def build_payout_aggregate(conn, channel: str, year: int, month: int) -> dict:
    """Build {(norm_objekt, YYYY-MM): total_czk} from report_rows.

    Uses report_rows which already have reservations distributed to the correct
    (slug, year, month) — including assign_report_month logic and manual
    reservation_month_assignments.  No need to recalculate anything here.
    """
    import json as _json

    mesic = f"{year:04d}-{month:02d}"
    channel_lower = channel.lower()
    source_map = {"airbnb": "airbnb", "booking.com": "booking", "booking": "booking"}

    # Build slug → normalized objekt name using expand_objekt_315
    # (the same normalization that accounting side uses).
    slug_to_norm: dict[str, str] = {}
    for r in conn.execute("SELECT slug, display_name FROM report_objects").fetchall():
        slug = r["slug"]
        # expand_objekt_315 understands short forms like "soho 406" → "V Háji 10"
        slug_name = slug.replace("_", " ")
        expanded = expand_objekt_315(slug_name)
        norm = normalize_objekt(expanded)
        norm = _OBJEKT_315_ALIASES.get(norm, norm)
        slug_to_norm[slug] = norm

    rows = conn.execute(
        "SELECT slug, data FROM report_rows WHERE year = ? AND month = ?",
        (year, month),
    ).fetchall()

    agg: dict[tuple[str, str], float] = {}
    for r in rows:
        d = _json.loads(r["data"])
        if d.get("is_excluded"):
            continue
        src = source_map.get((d.get("source") or "").lower(), "")
        if src != channel_lower:
            continue
        amount = d.get("payout_czk") or 0.0
        norm_obj = slug_to_norm.get(r["slug"], normalize_objekt(r["slug"].replace("_", " ")))
        # Apply Booking fold patterns (MyMozart rooms → group, VN units → group, etc.)
        if channel_lower == "booking":
            for pattern, group_key in _BOOKING_FOLD_PATTERNS:
                if pattern.match(norm_obj):
                    norm_obj = group_key
                    break
        key = (norm_obj, mesic)
        agg[key] = agg.get(key, 0.0) + amount

    return agg


# ---------------------------------------------------------------------------
# L3 matching: payout aggregate vs accounting entries
# ---------------------------------------------------------------------------

def compute_l3_reconciliation(payout_agg, accounting_entries, channel, tolerance=1.0):
    """
    Match payout aggregates by (objekt, YYYY-MM) against accounting entries (account 315).
    Returns list of result dicts with status MATCHED/PARTIAL/UNMATCHED/NO_SOURCE.
    """
    DOC_TYPES = {"FKV", "FHS", "FHO", "FU", "RF"}
    channel_kw = channel.lower()

    # Aggregate accounting entries by (objekt_norm, month)
    fkv_agg = {}
    fkv_detail = {}
    for row in accounting_entries:
        if row.get("doc_type") not in DOC_TYPES:
            continue
        if (row.get("channel") or "").lower() != channel_kw:
            continue
        obj_norm = normalize_objekt(row.get("objekt") or "")
        if not obj_norm:
            continue
        obj_norm = _OBJEKT_315_ALIASES.get(obj_norm, obj_norm)
        mesic = get_accounting_match_month(row)
        if not mesic:
            continue
        key = (obj_norm, mesic)
        fkv_agg[key] = fkv_agg.get(key, 0.0) + (row.get("castka") or 0.0)
        fkv_detail.setdefault(key, []).append(row)

    # Fold multi-room groups for Booking
    if channel_kw == "booking":
        group_map = {}
        for (obj_norm, _) in fkv_agg:
            for pattern, group_key in _BOOKING_FOLD_PATTERNS:
                if pattern.match(obj_norm):
                    group_map[obj_norm] = group_key
                    break
        if group_map:
            fkv_agg, fkv_detail = _fold_booking_315(fkv_agg, fkv_detail, group_map)

    results = []
    used_fkv = set()

    # Step 1: match each payout key to best accounting key
    for (src_obj, mes), src_sum in sorted(payout_agg.items()):
        best_key = None
        best_score = -1.0
        for fkv_key in fkv_agg:
            if fkv_key[1] != mes:
                continue
            sc = objekt_similarity(src_obj, fkv_key[0])
            if sc > best_score:
                best_score = sc
                best_key = fkv_key

        if best_key and best_score >= 0.5:
            used_fkv.add(best_key)
            fkv_sum = fkv_agg[best_key]
            diff = round(src_sum - fkv_sum, 2)
            status = "MATCHED" if abs(diff) <= tolerance else "PARTIAL"
            results.append({
                "objekt_src": src_obj,
                "mesic": mes,
                "sum_src": round(src_sum, 2),
                "objekt_315": best_key[0],
                "sum_315": round(fkv_sum, 2),
                "diff": diff,
                "score": round(best_score, 2),
                "status": status,
                "detail_315": fkv_detail.get(best_key, []),
            })
        else:
            results.append({
                "objekt_src": src_obj,
                "mesic": mes,
                "sum_src": round(src_sum, 2),
                "objekt_315": "",
                "sum_315": None,
                "diff": None,
                "score": 0.0,
                "status": "UNMATCHED",
                "detail_315": [],
            })

    # Step 2: NO_SOURCE — accounting entries without payout pair
    for key, fkv_sum in sorted(fkv_agg.items()):
        if key not in used_fkv:
            results.append({
                "objekt_src": "",
                "mesic": key[1],
                "sum_src": None,
                "objekt_315": key[0],
                "sum_315": round(fkv_sum, 2),
                "diff": None,
                "score": 0.0,
                "status": "NO_SOURCE",
                "detail_315": fkv_detail.get(key, []),
            })

    return results
