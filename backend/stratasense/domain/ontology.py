"""Drilling domain ontology for offset-well intelligence.

The single source of truth for formations, hazards, mitigations, causes and
their natural-language surface forms. The synthetic data generator, the NLP
extractor, the search engine and the UI all read from here, so the vocabulary
stays consistent end to end.

Formations are regional. STRATASENSE_REGION picks the stratigraphic column at start-up:
  assam   Upper Assam Shelf (synthetic demo data, default)
  norway  Norwegian North Sea at lithostratigraphic GROUP level (real public Sodir data)
Hazards, mitigations and the language resources are shared by all regions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config


@dataclass(frozen=True)
class Formation:
    code: str
    name: str
    age: str
    lithology: str
    color: str
    aliases: tuple[str, ...] = ()


# Upper Assam Shelf stratigraphic column, shallow to deep (DGH / USGS 2208-D).
ASSAM_FORMATIONS: list[Formation] = [
    Formation("ALLUVIUM", "Alluvium", "Recent", "Unconsolidated sand, silt and clay", "#d9c89e", ("alluvium", "alluvial")),
    Formation("DHEKIAJULI", "Dhekiajuli", "Pleistocene", "Sand, clay, pebble beds", "#e8d38a", ("dhekiajuli",)),
    Formation("NAMSANG", "Namsang", "Pliocene", "Loose sand, gravel, clay", "#f2b84b", ("namsang",)),
    Formation("GIRUJAN", "Girujan Clay", "Upper Miocene", "Mottled reactive clays with thin sands", "#b07aa1", ("girujan clay", "girujan clays", "girujan")),
    Formation("TIPAM", "Tipam Sandstone", "Miocene", "Massive sandstone with clay bands", "#f28e2b", ("tipam sandstone", "tipam sands", "tipam sand", "tipam sst", "tipam")),
    Formation("BARAIL", "Barail", "Oligocene", "Sand-shale-coal alternations", "#59a14f", ("barail coal shale", "barail main sand", "barail sand", "barail shale", "barail coal", "barail")),
    Formation("KOPILI", "Kopili", "Upper Eocene", "Dark shale with thin sands", "#4e79a7", ("kopili shale", "kopili")),
    Formation("SYLHET", "Sylhet", "Middle Eocene", "Fractured limestone (incl. Lakadong)", "#76b7b2", ("sylhet limestone", "sylhet lst", "sylhet", "lakadong")),
    Formation("LANGPAR", "Langpar", "Paleocene", "Sandstone, shale, gas-bearing", "#9c755f", ("langpar", "langpur")),
    Formation("BASEMENT", "Basement", "Precambrian", "Granitic basement", "#7f7f7f", ("basement", "granite wash")),
]

# Norwegian North Sea, lithostratigraphic GROUPS as reported by the Norwegian Offshore Directorate (Sodir),
# shallow to deep. Aliases map well-known formation names to their group so narrative text resolves to a group.
# Groups that occur only in some sub-basins (Brent in the north, Vestland in the south) both stay in the list;
# a well simply has no top for a group it did not penetrate.
NORWAY_FORMATIONS: list[Formation] = [
    Formation("NORDLAND", "Nordland Gp", "Miocene-Recent", "Clays and sands (Utsira sand)", "#e8d38a",
              ("nordland group", "nordland gp", "nordland", "utsira formation", "utsira fm", "utsira", "naust")),
    Formation("HORDALAND", "Hordaland Gp", "Eocene-Miocene", "Mudstones, reactive clays, local sands", "#b07aa1",
              ("hordaland group", "hordaland gp", "hordaland", "skade formation", "frigg formation", "grid formation")),
    Formation("ROGALAND", "Rogaland Gp", "Paleocene-Eocene", "Mudstones, tuff (Balder), sands (Heimdal)", "#f28e2b",
              ("rogaland group", "rogaland gp", "rogaland", "balder formation", "balder", "sele formation", "sele",
               "lista formation", "lista", "vale formation", "heimdal formation", "heimdal", "hermod", "ty formation")),
    Formation("SHETLAND", "Shetland Gp", "Late Cretaceous", "Chalk and marl", "#76b7b2",
              ("shetland group", "shetland gp", "shetland", "chalk", "ekofisk formation", "ekofisk fm", "tor formation",
               "hod formation", "blodoeks", "hardraade", "kyrre formation", "jorsalfare", "tryggvason")),
    Formation("CROMER_KNOLL", "Cromer Knoll Gp", "Early Cretaceous", "Marls and calcareous claystones", "#59a14f",
              ("cromer knoll group", "cromer knoll gp", "cromer knoll", "rodby formation", "sola formation",
               "asgard formation", "tuxen formation", "mime formation")),
    Formation("VIKING", "Viking Gp", "Late Jurassic", "Organic-rich shales (Draupne), Heather", "#4e79a7",
              ("viking group", "viking gp", "draupne formation", "draupne", "heather formation", "heather")),
    Formation("BOKNFJORD", "Boknfjord Gp", "Late Jurassic", "Marine shales (Egersund basin)", "#8cd17d",
              ("boknfjord group", "boknfjord gp", "tau formation", "egersund formation", "flekkefjord formation",
               "sauda formation")),
    Formation("BRENT", "Brent Gp", "Middle Jurassic", "Deltaic sandstones and coals", "#e15759",
              ("brent group", "brent gp", "tarbert formation", "ness formation", "etive formation", "rannoch formation",
               "broom formation")),
    Formation("VESTLAND", "Vestland Gp", "Middle-Late Jurassic", "Shallow-marine sandstones (Hugin, Sleipner)", "#f1ce63",
              ("vestland group", "vestland gp", "hugin formation", "hugin", "sleipner formation", "bryne formation",
               "sandnes formation")),
    Formation("DUNLIN", "Dunlin Gp", "Early Jurassic", "Marine shales and sands", "#9d7660",
              ("dunlin group", "dunlin gp", "drake formation", "cook formation", "burton formation",
               "amundsen formation", "johansen formation")),
    Formation("STATFJORD", "Statfjord Gp", "Triassic-Jurassic", "Fluvial sandstones", "#d4a6c8",
              ("statfjord group", "statfjord gp", "statfjord formation", "eiriksson formation", "raude formation",
               "nansen formation")),
    Formation("HEGRE", "Hegre Gp", "Triassic", "Red-bed sandstones and claystones (Skagerrak)", "#ff9d9a",
              ("hegre group", "hegre gp", "skagerrak formation", "skagerrak", "smith bank formation", "smith bank",
               "lomvi formation", "teist formation", "lunde formation")),
    Formation("ZECHSTEIN", "Zechstein Gp", "Late Permian", "Evaporites (salt)", "#bab0ac",
              ("zechstein group", "zechstein gp", "zechstein", "zechstein salt")),
    Formation("ROTLIEGEND", "Rotliegend Gp", "Early Permian", "Aeolian sandstones and volcanics", "#d37295",
              ("rotliegend group", "rotliegend gp", "rotliegend")),
    Formation("BASEMENT", "Basement", "Pre-Devonian", "Crystalline basement", "#7f7f7f",
              ("basement", "crystalline basement")),
]

REGIONS = {
    "assam": {
        "label": "Upper Assam (synthetic demo data)", "formations": ASSAM_FORMATIONS, "default_td": "SYLHET",
        "synthetic": True,
        "data_notice": "All wells, reports and streams in this demo are synthetic, generated from published Upper-Assam geology.",
        "ui": {"search_examples": ["losses in Tipam within 5 km after 2012", "what worked for losses in fractured Sylhet limestone",
                                   "kick in lower Barail below 3000 m", "bit balling Girujan clay", "stuck pipe differential sticking",
                                   "poor cement bond 9-5/8 casing"],
               "ask_examples": ["Kicks in Barail within 8 km — what MW did offsets need?", "What worked for losses in Sylhet?",
                                "Bit balling in Girujan clay after 2010", "Stuck pipe in Tipam near NDH-09"],
               "ask_default": "What problems did offsets within 5 km have in Tipam, and what worked?",
               "default_formation": "TIPAM", "default_radius_km": 8},
    },
    "norway": {
        "label": "Norwegian North Sea (real public data, Sodir)", "formations": NORWAY_FORMATIONS, "default_td": "VESTLAND",
        "synthetic": False,
        "data_notice": "Real public wellbore data from the Norwegian Offshore Directorate FactPages, used under the Norwegian "
                       "licence for Open Government data (NLOD). North Sea geology, not Assam: this checks StrataSense on real records. "
                       "Live Ops, when a Volve stream is imported: real-time drilling data from the Volve field, © Equinor and "
                       "the former Volve licence partners (ExxonMobil Exploration and Production Norway AS, Bayerngas Norge AS), "
                       "Equinor Open Data Licence.",
        "ui": {"search_examples": ["gas kick in Rogaland", "lost circulation in the Shetland chalk", "stuck pipe in Hordaland",
                                   "shallow gas in Nordland", "fishing and sidetrack", "losses within 10 km after 2000"],
               "ask_examples": ["What happened with kicks in Rogaland?", "What worked for losses in the chalk?",
                                "Stuck pipe in Hordaland within 15 km", "Shallow gas in Nordland"],
               "ask_default": "What drilling problems did offsets within 15 km have in Rogaland, and what worked?",
               "default_formation": "ROGALAND", "default_radius_km": 25},
    },
}
if config.REGION not in REGIONS:
    raise ValueError(f"STRATASENSE_REGION must be one of {sorted(REGIONS)}, got {config.REGION!r}")
REGION = REGIONS[config.REGION]
FORMATIONS: list[Formation] = REGION["formations"]
FORMATION_BY_CODE = {f.code: f for f in FORMATIONS}
FORMATION_ORDER = [f.code for f in FORMATIONS]
SURFACE = FORMATION_ORDER[0]       # shallowest unit: every well starts in it
BOTTOM = FORMATION_ORDER[-1]       # basement: never a drilling target
DEFAULT_TD = REGION["default_td"]
IS_ASSAM = config.REGION == "assam"


@dataclass(frozen=True)
class Hazard:
    code: str
    label: str
    color: str
    description: str
    # Surface forms that signal the hazard *occurred* (used for extraction + search expansion).
    terms: tuple[str, ...]
    in_ribbon: bool = True


HAZARDS: list[Hazard] = [
    Hazard("LOSS", "Lost circulation", "#e15759", "Partial/total loss of mud returns to the formation",
           ("lost circulation", "loss of circulation", "partial losses", "partial loss", "total losses", "total loss",
            "complete loss", "seepage losses", "seepage loss", "mud losses", "mud loss", "losses", "loss of returns",
            "lost returns", "no returns", "dynamic losses", "static losses", "losing mud", "loss rate")),
    Hazard("KICK", "Kick / overpressure", "#b10026", "Influx of formation fluid, gas-cut mud, well control",
           ("kick", "influx", "well flowing", "well flowed", "pit gain", "gas cut", "gas-cut", "shut in the well",
            "shut-in", "well control", "connection gas", "high gas", "flow check positive", "positive flow check",
            "overpressure", "over pressure", "abnormal pressure", "drilling break with gas")),
    Hazard("STUCK", "Stuck pipe", "#6b4c9a", "Pipe could not be moved or rotated (differential, pack-off, mechanical)",
           ("stuck pipe", "pipe stuck", "string stuck", "got stuck", "differential sticking", "differentially stuck",
            "pack-off", "pack off", "packed off", "packing off", "unable to move string", "unable to rotate",
            "could not pull free", "pipe sticking",
            # narrative phrasing in well histories / completion reports
            "became stuck", "was stuck", "got stuck in", "stuck drill string", "drill string stuck", "stuck in hole",
            "stuck at", "string became stuck", "pipe became stuck", "drill pipe stuck", "drillpipe stuck")),
    Hazard("TIGHT", "Tight hole / bit balling", "#edc948", "Overpull, drag, reaming needed, balled bit or BHA",
           ("tight hole", "tight spot", "tight spots", "overpull", "over pull", "o/p", "excessive drag", "high drag",
            "bit balling", "balled bit", "balled up", "balling", "reaming", "back reaming", "backreaming", "hole drag",
            "swelling clay")),
    Hazard("INSTAB", "Wellbore instability", "#ff9da7", "Cavings, hole collapse, washouts, fill on bottom",
           ("cavings", "caving", "splintery cavings", "hole collapse", "wellbore instability", "hole instability",
            "sloughing", "fill on bottom", "hole enlargement", "washed out hole", "unstable hole", "spalling")),
    Hazard("TORQUE", "Torque spikes / stick-slip", "#af7aa1", "Erratic or high torque, stick-slip, stalling",
           ("torque spike", "torque spikes", "erratic torque", "high torque", "stick-slip", "stick slip",
            "torqued up", "torquing", "top drive stalled", "string stalled", "torque fluctuation")),
    Hazard("CEMENT", "Cementing problem", "#86bcb6", "Losses during cementing, poor bond, low TOC, remedial job",
           ("losses during cementing", "poor cbl", "poor bond", "poor cement bond", "channeling", "channelling",
            "cement did not reach", "low toc", "top of cement below", "remedial cement", "squeeze job",
            "cement job failure", "partial returns during cementing", "no cement returns")),
    Hazard("FISH", "Fishing operation", "#9d7660", "Fishing for stuck/lost string or junk, back-off, sidetrack",
           ("fishing", "fish recovered", "back off", "backed off", "overshot", "spear", "junk basket", "junk in hole",
            "twist off", "twisted off", "sidetrack", "side track", "left in hole"), in_ribbon=False),
]
HAZARD_BY_CODE = {h.code: h for h in HAZARDS}
RIBBON_HAZARDS = [h.code for h in HAZARDS if h.in_ribbon]


@dataclass(frozen=True)
class Mitigation:
    code: str
    hazard: str
    label: str
    # Phrase variants used by the generator and matched by the extractor.
    phrases: tuple[str, ...]
    preventive: str = ""  # proactive version shown in look-ahead recommendations


MITIGATIONS: list[Mitigation] = [
    # --- Lost circulation
    Mitigation("LCM_FINE", "LOSS", "Sized CaCO3 / fine LCM pill",
               ("pumped fine lcm pill", "spotted sized caco3 pill", "pumped 30 ppb sized calcium carbonate pill",
                "spotted fine lcm pill", "pumped sized caco3 lcm pill"),
               "Pre-treat active system with 10-15 ppb sized CaCO3 before entering the zone"),
    Mitigation("LCM_COARSE", "LOSS", "Coarse fibrous/flake LCM pill (40-60 ppb)",
               ("pumped coarse lcm pill", "spotted 50 ppb coarse lcm pill", "pumped fibrous lcm pill",
                "spotted 60 ppb flake and fibre lcm pill", "pumped coarse fibre lcm pill"),
               "Keep 60 ppb coarse LCM pill mixed and ready on surface"),
    Mitigation("REDUCE_FLOW", "LOSS", "Reduce flow rate / ECD",
               ("reduced flow rate", "reduced pump rate", "reduced spm to lower ecd", "lowered flow rate to control ecd"),
               "Limit flow rate and ROP to keep ECD inside the offset-derived window"),
    Mitigation("CEMENT_PLUG", "LOSS", "Cement plug / squeeze across loss zone",
               ("set cement plug across loss zone", "squeezed cement across loss zone", "placed cement plug to cure losses"),
               "Have cement plug contingency (slurry design ready) for total losses"),
    Mitigation("POOH_HEAL", "LOSS", "Pull to shoe and allow to heal",
               ("pulled to shoe and allowed hole to heal", "pooh to casing shoe and waited", "pulled back to shoe, static observation"),
               ""),
    # --- Kick / well control
    Mitigation("SHUT_IN_DM", "KICK", "Shut in + Driller's method",
               ("shut in the well and circulated out influx by driller's method", "shut in well, killed by driller's method",
                "closed bop and circulated out kick using driller's method"),
               "Review kick tolerance and shut-in procedure with crew before entering the interval"),
    Mitigation("WAIT_WEIGHT", "KICK", "Wait & Weight kill",
               ("killed well by wait and weight method", "shut in and killed with wait & weight", "performed wait and weight kill"),
               ""),
    Mitigation("RAISE_MW", "KICK", "Raise mud weight",
               ("raised mud weight", "increased mud weight", "weighted up mud", "increased mw"),
               "Raise MW gradually to the P(kick)<10% bound before the overpressure top"),
    Mitigation("FLOW_CHECK", "KICK", "Flow check / circulate bottoms up through choke",
               ("flow checked and circulated bottoms up through choke", "circulated bottoms up through gas buster",
                "performed flow check and circulated out gas"),
               "Flow-check on every connection; watch connection gas and dxc trend"),
    # --- Stuck pipe
    Mitigation("JAR_DOWN", "STUCK", "Jar down",
               ("jarred down", "jarring down", "worked jar down"), ""),
    Mitigation("JAR_UP", "STUCK", "Jar up",
               ("jarred up", "jarring up", "worked jar up with overpull"), ""),
    Mitigation("SPOT_PIPE_LAX", "STUCK", "Spot pipe-lax / diesel pill",
               ("spotted pipe-lax pill", "spotted diesel pill around bha", "spotted pipe lax and soaked",
                "spotted spotting fluid"),
               "Minimise static time; keep pipe moving across depleted sands"),
    Mitigation("CIRC_HIVIS", "STUCK", "Circulate hi-vis sweep / restore circulation",
               ("circulated hi-vis sweep", "established circulation and pumped hi-vis sweep", "pumped high viscosity sweep"),
               "Pump hi-vis sweeps every stand; monitor cuttings return"),
    Mitigation("BACKOFF", "STUCK", "Back-off and fish",
               ("backed off string", "string back off performed", "backed off above bha"), ""),
    # --- Tight hole / balling
    Mitigation("BACKREAM", "TIGHT", "Back-ream / wiper trip",
               ("backreamed through tight spot", "back reamed", "performed wiper trip", "reamed and backreamed"),
               "Plan wiper trip after each 150 m in reactive clay"),
    Mitigation("INHIBITION", "TIGHT", "Increase inhibition (KCl / glycol)",
               ("increased kcl concentration", "added glycol to improve inhibition", "raised kcl to 7%",
                "increased inhibition with kcl and glycol"),
               "Maintain KCl 5-7% + glycol 3% before drilling reactive clay"),
    Mitigation("BIT_CLEAN", "TIGHT", "Bit cleaning / anti-balling sweep",
               ("pumped anti-balling sweep", "pumped detergent pill to clean bit", "pumped nut plug sweep to clean bit"),
               "Use anti-balling additive and high flow; limit WOB in sticky clay"),
    # --- Instability
    Mitigation("RAISE_MW_STAB", "INSTAB", "Raise MW for wellbore stability",
               ("raised mud weight to stabilise hole", "increased mw to control cavings", "weighted up for hole stability"),
               "Drill with MW at/above the collapse bound for this inclination"),
    Mitigation("ASPHALT", "INSTAB", "Add asphalt/gilsonite (coal & shale sealing)",
               ("added gilsonite", "added asphalt based additive", "treated mud with sulphonated asphalt"),
               "Pre-treat with 4-6 ppb sulphonated asphalt before coal seams"),
    Mitigation("CONTROL_ROP", "INSTAB", "Control ROP / improve hole cleaning",
               ("controlled rop", "reduced rop and circulated", "control drilled and cleaned hole"), ""),
    # --- Torque
    Mitigation("LUBRICANT", "TORQUE", "Add lubricant",
               ("added lubricant", "added 2% lubricant", "treated mud with lubricant"),
               "Add 2-3% lubricant; monitor torque vs T&D model"),
    Mitigation("REDUCE_PARAMS", "TORQUE", "Reduce RPM/WOB",
               ("reduced rpm and wob", "reduced drilling parameters", "lowered rpm to reduce stick-slip"), ""),
    # --- Cementing
    Mitigation("TOP_JOB", "CEMENT", "Top job",
               ("performed top job", "carried out top-up cement job"), ""),
    Mitigation("SQUEEZE", "CEMENT", "Remedial squeeze",
               ("performed remedial squeeze", "carried out squeeze cementation", "squeezed cement through perforations"), ""),
    Mitigation("LIGHT_SLURRY", "CEMENT", "Lightweight slurry / staged cementing",
               ("used lightweight slurry", "cemented in two stages with dv tool", "switched to lightweight lead slurry"),
               "Design lightweight/two-stage cement job for depleted Tipam sands"),
    # --- Fishing
    Mitigation("OVERSHOT", "FISH", "Overshot fishing",
               ("ran overshot and recovered fish", "fished with overshot", "latched fish with overshot"), ""),
    Mitigation("SPEAR", "FISH", "Spear",
               ("ran spear and recovered fish", "fished with spear"), ""),
    Mitigation("SIDETRACK", "FISH", "Plug back and sidetrack",
               ("plugged back and sidetracked", "set kick-off plug and sidetracked", "decided to sidetrack the well"), ""),
]
MITIGATION_BY_CODE = {m.code: m for m in MITIGATIONS}

CAUSES = {
    "LOSS": ["induced fracture (high ECD)", "depleted sand", "natural fractures", "unconsolidated formation"],
    "KICK": ["underbalance in overpressured zone", "swabbing on trip", "gas-bearing sand"],
    "STUCK": ["differential sticking", "pack-off / poor hole cleaning", "swelling clay", "mechanical (coal/ledges)"],
    "TIGHT": ["reactive clay swelling", "bit balling", "ledges / doglegs"],
    "INSTAB": ["shear failure (MW below collapse)", "coal cleat failure", "shale hydration"],
    "TORQUE": ["coal stringers", "high dogleg / tortuosity", "poor lubricity"],
    "CEMENT": ["losses into depleted zone", "poor centralisation", "gas migration"],
    "FISH": ["stuck pipe not freed", "twist-off / fatigue", "junk in hole"],
}

# Words signalling a mitigation *worked* / *failed* (extractor + generator share them).
OUTCOME_SUCCESS = ("losses cured", "full returns regained", "regained full returns", "pipe came free", "string freed",
                   "pipe freed", "well dead", "well killed successfully", "hole stabilised", "hole in good condition after",
                   "torque normalised", "recovered fish", "fish recovered", "cement returns observed", "problem resolved",
                   "freed the string", "situation under control")
OUTCOME_FAIL = ("losses continued", "no improvement", "unsuccessful", "not successful", "without success",
                "still stuck", "could not free", "failed to", "losses persisted", "no success")

# NegEx-style triggers.
NEGATION_PRE = ("no ", "nil ", "without ", "free of ", "absence of ", "not observed", "did not observe", "none ",
                "neither ", "no sign of ", "no signs of ", "no indication of ", "not encountered")
NEGATION_POST = ("not observed", "not seen", "not encountered", "nil", "- nil", ": nil", "none observed", "absent")
HYPOTHETICAL = ("precautionary", "as a precaution", "precaution", "planned", "anticipated", "expected", "prognosed",
                "to avoid", "to prevent", "watch for", "watch out for", "risk of", "may encounter", "possible",
                "prognosis", "contingency", "if losses", "in case of", "be prepared")

# Query-time synonym expansion for search.
SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = {
    "loss": ("losses", "lost circulation", "returns", "seepage", "lcm"),
    "losses": ("loss", "lost circulation", "returns", "seepage", "lcm"),
    "lc": ("lost circulation", "losses"),
    "kick": ("influx", "well control", "pit gain", "gas cut", "overpressure"),
    "influx": ("kick", "well control", "pit gain"),
    "overpressure": ("kick", "abnormal pressure", "gas", "dxc"),
    "gas": ("connection gas", "gas cut", "background gas"),
    "stuck": ("stuck pipe", "differential sticking", "pack-off", "jarred"),
    "sticking": ("stuck", "differential sticking", "pack-off"),
    "tight": ("overpull", "drag", "reaming", "bit balling"),
    "balling": ("bit balling", "balled", "anti-balling", "clay"),
    "instability": ("cavings", "collapse", "sloughing", "washout"),
    "cavings": ("instability", "collapse", "sloughing"),
    "torque": ("stick-slip", "erratic torque", "torque spikes"),
    "cement": ("cementing", "cbl", "bond", "squeeze", "top job"),
    "fishing": ("overshot", "spear", "back off", "sidetrack", "fish"),
    "npt": ("non productive time", "lost time", "hrs"),
    "lcm": ("lost circulation material", "pill", "caco3"),
}

HOLE_SECTIONS = [
    # (hole size, casing size, label)
    ("26\"", "20\"", "Conductor"),
    ("17-1/2\"", "13-3/8\"", "Surface"),
    ("12-1/4\"", "9-5/8\"", "Intermediate"),
    ("8-1/2\"", "7\"", "Production liner"),
]


def formation_index(code: str) -> int:
    return FORMATION_ORDER.index(code)


def formation_name(code: str | None) -> str:
    """Display name that never raises (imported data can carry a unit this region's list does not know)."""
    f = FORMATION_BY_CODE.get(code or "")
    return f.name if f else (code or "unknown formation")


def ontology_payload() -> dict:
    """Serialisable ontology for the frontend."""
    return {
        "region": {"code": config.REGION, "label": REGION["label"], "synthetic": REGION["synthetic"],
                   "data_notice": REGION["data_notice"], "default_td": DEFAULT_TD, **REGION["ui"]},
        "formations": [f.__dict__ | {"aliases": list(f.aliases)} for f in FORMATIONS],
        "hazards": [{"code": h.code, "label": h.label, "color": h.color, "description": h.description,
                     "in_ribbon": h.in_ribbon} for h in HAZARDS],
        "mitigations": [{"code": m.code, "hazard": m.hazard, "label": m.label, "preventive": m.preventive}
                        for m in MITIGATIONS],
        "causes": CAUSES,
    }
