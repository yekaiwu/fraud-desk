"""Hand-written industry and occupation catalogues for the FIT data (docs 9.3).

Pure data. Industries are assigned to ``dev`` or ``held_out`` by hand (eight each) and a held-out
case only ever uses held-out industries, both as the declared business and as the "wrong" donor, so
a prompt tuned on the dev industries cannot have seen the reported ones. Prices are SGD and are
our own plausible guesses; SSIC codes are illustrative, not looked up. All names are fictitious.
"""

from __future__ import annotations

from dataclasses import dataclass

# (description, unit price low, unit price high)
Item = tuple[str, int, int]


@dataclass(frozen=True)
class Industry:
    id: str
    split: str
    family: str
    activity: tuple[str, str]  # two wordings of the principal activity
    ssic: str
    name_words: str  # company name descriptor
    sells: tuple[Item, ...]
    customer_words: tuple[str, ...]
    supplier_words: tuple[str, ...]
    memos: tuple[str, ...]  # statement memo words (upper case)
    sidelines: tuple[Item, ...]  # legitimate adjacent items for the controls


# Lines that are legitimate on almost any invoice
GENERIC_LINES: tuple[Item, ...] = (
    ("Delivery charge", 25, 120),
    ("Installation and commissioning", 150, 900),
    ("Packaging and handling", 15, 90),
    ("Extended warranty, 12 months", 60, 400),
    ("Freight surcharge", 30, 180),
    ("Technical consultation, per hour", 80, 220),
    ("Training session, half day", 300, 900),
    ("Equipment rental, per month", 200, 1200),
)

INDUSTRIES: tuple[Industry, ...] = (
    # ------------------------------------------------------------------ dev (8)
    Industry(
        "electronics_wholesale",
        "dev",
        "tech",
        (
            "Wholesale of electronic components and consumer electronics",
            "Trading of electronic parts, computers and accessories",
        ),
        "46521",
        "Electronics Trading",
        (
            ("Ceramic capacitor 10uF 50V (reel of 4000)", 28, 75),
            ("SMD resistor 0603 assorted (reel of 5000)", 12, 40),
            ("USB-C charging cable 1m (carton of 100)", 90, 240),
            ("Lithium-ion battery pack 3.7V 2000mAh (box of 20)", 120, 320),
            ("LED driver module 12V 30W (box of 10)", 85, 210),
            ("Microcontroller board, ARM Cortex-M4 (tray of 25)", 220, 560),
            ("Wi-Fi router, dual band (carton of 8)", 260, 640),
            ("HDMI cable 2m (carton of 50)", 110, 280),
            ("Power adapter 19V 65W (carton of 20)", 190, 420),
            ("Bluetooth speaker module (box of 12)", 150, 380),
            ("Solid state drive 512GB (carton of 10)", 480, 980),
            ("Thermal paste 4g tube (box of 50)", 60, 160),
        ),
        ("Digital Mart", "Retail", "Systems", "Devices", "Gadgets"),
        ("Semiconductor", "Components", "Circuits", "Electronics"),
        ("CAPACITORS", "PCB ORDER", "PO CABLES", "BATTERY LOT", "SSD STOCK", "ADAPTERS"),
        (
            ("Anti-static storage bags (pack of 500)", 15, 45),
            ("Soldering iron station 60W", 40, 120),
            ("Cardboard storage bins (pack of 10)", 20, 60),
            ("Cable management trays (box of 12)", 35, 90),
        ),
    ),  # fmt: skip
    Industry(
        "construction_materials",
        "dev",
        "build",
        (
            "Wholesale of building and construction materials",
            "Supply of cement, steel bar, tiles and other building materials",
        ),
        "46630",
        "Building Materials",
        (
            ("Portland cement 50kg bag (pallet of 40)", 220, 330),
            ("Steel reinforcement bar 12mm x 12m (tonne)", 780, 1100),
            ("Ready-mix mortar 25kg (pallet of 56)", 260, 380),
            ("Ceramic floor tile 600x600 (box of 4)", 28, 75),
            ("Plywood sheet 18mm 1220x2440 (unit)", 38, 70),
            ("Concrete block 100mm (pallet of 160)", 190, 300),
            ("Waterproofing membrane 20m roll", 85, 190),
            ("Galvanised roofing sheet 0.5mm x 3m (bundle of 10)", 210, 360),
            ("PVC pipe 100mm x 6m (bundle of 10)", 140, 260),
            ("Washed sand (tonne)", 24, 45),
            ("Gypsum board 12mm (bundle of 20)", 160, 290),
            ("Aluminium window frame set (unit)", 180, 420),
        ),
        ("Construction", "Builders", "Contractors", "Development"),
        ("Cement", "Steel", "Quarry", "Timber"),
        ("CEMENT LOT", "REBAR", "TILES BATCH", "PLYWOOD", "SAND DELIVERY", "ROOF SHEETS"),
        (
            ("Safety helmet (box of 20)", 90, 190),
            ("Work gloves (carton of 120 pairs)", 110, 230),
            ("Tarpaulin sheet 6x8m (bundle of 5)", 60, 140),
            ("Scaffold couplers (box of 50)", 120, 260),
        ),
    ),  # fmt: skip
    Industry(
        "seafood_trading",
        "dev",
        "food",
        (
            "Wholesale of fresh and frozen seafood",
            "Import and distribution of seafood to restaurants and markets",
        ),
        "46322",
        "Seafood Trading",
        (
            ("Frozen prawns 31/40, 2kg block (carton of 6)", 95, 180),
            ("Fresh salmon fillet, chilled (5kg tray)", 140, 260),
            ("Frozen squid tubes (10kg carton)", 70, 130),
            ("Live grouper (per kg)", 22, 48),
            ("Frozen yellowfin tuna loin (10kg)", 210, 420),
            ("Fresh sea bass, whole (5kg tray)", 60, 120),
            ("Frozen crab meat 1kg (carton of 10)", 180, 340),
            ("Fresh scallops, dayboat (per kg)", 38, 75),
            ("Dried anchovies 5kg bag", 45, 95),
            ("Frozen mussels, half shell (10kg)", 55, 110),
            ("Fish balls 1kg (carton of 12)", 48, 90),
            ("Fresh oysters (per dozen)", 18, 36),
        ),
        ("Restaurant", "Hotpot", "Seafood House", "Catering"),
        ("Fisheries", "Ocean", "Aquaculture", "Cold Storage"),
        ("PRAWN LOT", "SALMON", "SQUID", "COLD CHAIN", "TUNA", "CRAB SUPPLY"),
        (
            ("Styrofoam cooler box (pack of 10)", 25, 65),
            ("Ice packs (carton of 50)", 40, 95),
            ("Vacuum bags 25x35cm (pack of 500)", 30, 70),
            ("Insulated delivery bag (unit)", 18, 45),
        ),
    ),  # fmt: skip
    Industry(
        "it_services",
        "dev",
        "tech",
        (
            "Provision of IT support, software development and managed services",
            "Information technology consulting and managed IT services",
        ),
        "62021",
        "IT Solutions",
        (
            ("Managed IT support, monthly retainer", 900, 3200),
            ("Cloud migration project, milestone payment", 2500, 9000),
            ("Custom software development, per sprint", 3500, 8500),
            ("Cybersecurity assessment, per system", 1200, 4200),
            ("Helpdesk support, per seat per month", 25, 70),
            ("Network cabling and setup, per point", 45, 120),
            ("Website maintenance, per month", 180, 650),
            ("Data backup service, per TB per month", 60, 160),
            ("Software licence resale, annual", 300, 1800),
            ("Penetration test, per engagement", 2800, 7500),
            ("On-site engineer visit, per day", 450, 900),
            ("Mobile app UI design, per screen", 220, 520),
        ),
        ("Clinic", "Law Practice", "Retail", "Logistics"),
        ("Cloud", "Data Centre", "Licensing", "Hardware"),
        ("LICENCE RENEWAL", "CLOUD HOSTING", "SERVER LEASE", "SUBCONTRACT DEV", "DOMAIN FEES"),
        (
            ("Network switch, 24-port (unit)", 280, 760),
            ("Toner cartridge, laser (box of 4)", 220, 540),
            ("Server rack hardware resale (unit)", 650, 1900),
            ("Laptop, business model (unit)", 900, 2100),
        ),
    ),  # fmt: skip
    Industry(
        "garment_trading",
        "dev",
        "apparel",
        ("Wholesale of garments and textiles", "Trading of ready-made clothing and fabrics"),
        "46411",
        "Garment Trading",
        (
            ("Cotton T-shirt, assorted sizes (carton of 100)", 260, 520),
            ("Denim jeans, men's (carton of 40)", 420, 860),
            ("Polo shirt, embroidered (carton of 60)", 380, 760),
            ("Women's blouse, polyester (carton of 50)", 300, 640),
            ("School uniform set (carton of 30)", 330, 690),
            ("Cotton fabric roll 50m", 120, 280),
            ("Sports jacket, windproof (carton of 24)", 520, 980),
            ("Cotton socks (carton of 240 pairs)", 140, 300),
            ("Baby romper set (carton of 80)", 280, 560),
            ("Knitted sweater (carton of 36)", 400, 820),
            ("Silk scarf (carton of 50)", 320, 700),
            ("Hijab, chiffon (carton of 100)", 240, 520),
        ),
        ("Boutique", "Fashion", "Uniforms", "Retail"),
        ("Textile", "Garment Factory", "Fabrics", "Apparel"),
        ("T-SHIRT LOT", "FABRIC ROLL", "UNIFORMS", "DENIM", "KNITWEAR"),
        (
            ("Clothes hangers (pack of 500)", 40, 95),
            ("Garment bags (pack of 100)", 30, 75),
            ("Price tag labels (roll of 1000)", 14, 36),
            ("Mannequin display (unit)", 90, 240),
        ),
    ),  # fmt: skip
    Industry(
        "medical_supplies",
        "dev",
        "health",
        (
            "Wholesale of medical and surgical supplies",
            "Distribution of medical consumables and equipment to clinics",
        ),
        "46441",
        "Medical Supplies",
        (
            ("Disposable nitrile gloves, M (carton of 10 boxes)", 55, 110),
            ("Surgical mask 3-ply (carton of 40 boxes)", 70, 150),
            ("Digital thermometer (box of 24)", 90, 190),
            ("Syringe 5ml with needle (carton of 1000)", 60, 130),
            ("Wound dressing 10x10cm (carton of 20 boxes)", 85, 170),
            ("Blood pressure monitor, automatic (carton of 10)", 380, 820),
            ("Pulse oximeter, fingertip (box of 20)", 240, 520),
            ("Hand sanitiser 500ml (carton of 24)", 70, 150),
            ("Examination couch (unit)", 420, 980),
            ("Surgical gown, sterile (carton of 50)", 190, 390),
            ("IV infusion set (carton of 100)", 130, 280),
            ("Wheelchair, foldable (unit)", 160, 360),
        ),
        ("Clinic", "Medical Centre", "Pharmacy", "Nursing Home"),
        ("Medical", "Pharma", "Surgical", "Healthcare"),
        ("GLOVES LOT", "MASKS", "SYRINGES", "DISINFECTANT", "DRESSINGS"),
        (
            ("Waiting room chairs (set of 4)", 160, 380),
            ("Cleaning wipes (carton of 24)", 55, 120),
            ("Medical waste bins (pack of 10)", 70, 160),
            ("Reception stationery bundle (set)", 35, 90),
        ),
    ),  # fmt: skip
    Industry(
        "printing_packaging",
        "dev",
        "print",
        (
            "Commercial printing and manufacture of packaging",
            "Printing of brochures and labels and supply of packaging materials",
        ),
        "18110",
        "Printing and Packaging",
        (
            ("Corrugated carton 40x30x30cm (bundle of 100)", 90, 190),
            ("Offset printing, brochures A4 (per 1000)", 160, 380),
            ("Business cards, matte (per 500)", 28, 70),
            ("Paper bag with logo (pack of 500)", 180, 420),
            ("Product label, adhesive (roll of 1000)", 45, 110),
            ("Bubble wrap 1m x 100m (roll)", 38, 85),
            ("Shrink film 500mm (roll)", 42, 95),
            ("Folding box, full colour (per 1000)", 380, 860),
            ("Banner, vinyl 3x1m (unit)", 35, 90),
            ("Calendar printing, wall (per 500)", 420, 920),
            ("Packing tape 48mm (carton of 72)", 70, 150),
            ("Plastic food container 750ml (carton of 300)", 85, 190),
        ),
        ("Bakery", "Cosmetics", "Marketing", "Electronics"),
        ("Paper", "Ink", "Pulp", "Packaging"),
        ("PAPER ROLLS", "INK", "CARTON ORDER", "PLATES", "LAMINATE"),
        (
            ("Graphic design service, per hour", 50, 140),
            ("Office stationery bundle (set)", 30, 120),
            ("Gift wrap sheets (pack of 100)", 18, 55),
            ("Storage of printed stock, per pallet per month", 25, 70),
        ),
    ),  # fmt: skip
    Industry(
        "auto_parts",
        "dev",
        "auto",
        (
            "Wholesale of motor vehicle parts and accessories",
            "Distribution of car parts and lubricants to workshops",
        ),
        "45300",
        "Auto Parts",
        (
            ("Brake pad set, front (box of 10)", 280, 620),
            ("Engine oil 5W-30 4L (carton of 6)", 120, 260),
            ("Oil filter (carton of 24)", 90, 190),
            ("Spark plug, iridium (box of 40)", 260, 540),
            ("Car battery 12V 60Ah (unit)", 85, 170),
            ("Wiper blade 24in (box of 12)", 70, 150),
            ("Radiator coolant 4L (carton of 6)", 60, 130),
            ("Timing belt kit (unit)", 95, 220),
            ("Alloy wheel 17in (set of 4)", 380, 820),
            ("Air filter (carton of 20)", 110, 240),
            ("LED headlamp bulb H7 (box of 10)", 140, 320),
            ("Shock absorber, rear (pair)", 110, 260),
        ),
        ("Workshop", "Motors", "Auto Service", "Garage"),
        ("Auto Parts", "Lubricants", "Brake Systems", "Tyres"),
        ("BRAKE PADS", "OIL LOT", "BATTERIES", "FILTERS", "WHEELS"),
        (
            ("Car air freshener (box of 100)", 40, 95),
            ("Microfibre cloth (pack of 50)", 25, 60),
            ("Floor mats, universal (set)", 30, 80),
            ("Workshop overalls (pack of 5)", 70, 160),
        ),
    ),  # fmt: skip
    # ------------------------------------------------------------- held_out (8)
    Industry(
        "food_catering",
        "held_out",
        "food",
        ("Catering services and supply of prepared meals", "Event and corporate catering"),
        "56210",
        "Catering Services",
        (
            ("Corporate lunch box, 3 dishes (per pax)", 9, 16),
            ("Cocktail reception canapes (per pax)", 14, 32),
            ("Wedding banquet, 10-person table", 780, 1600),
            ("Buffet line, 50 pax (per event)", 900, 2100),
            ("Afternoon tea set (per pax)", 8, 18),
            ("Halal-certified bento (per pax)", 8, 15),
            ("Dessert platter, 30 pax", 160, 340),
            ("Beverage station rental (per event)", 120, 320),
            ("Live cooking station (per event)", 380, 880),
            ("Festive hamper (unit)", 60, 180),
            ("Weekly pantry restock (per week)", 120, 280),
            ("Event staff, per server per 4 hours", 60, 110),
        ),
        ("Associates", "Academy", "Holdings", "Clinic"),
        ("Produce", "Poultry", "Beverage", "Dairy"),
        ("RICE SUPPLY", "POULTRY", "VEGETABLES", "GAS CYLINDERS", "BEVERAGES"),
        (
            ("Disposable cutlery set (pack of 100)", 12, 35),
            ("Chafing dish rental (unit)", 18, 45),
            ("Tent hire, 6x6m (per event)", 220, 520),
            ("Table linen hire (set)", 25, 70),
        ),
    ),  # fmt: skip
    Industry(
        "marine_engineering",
        "held_out",
        "marine",
        (
            "Marine engineering, ship repair and supply of marine spare parts",
            "Ship repair and marine equipment servicing",
        ),
        "30110",
        "Marine Engineering",
        (
            ("Diesel engine overhaul, 4-cylinder (per job)", 6500, 14500),
            ("Propeller shaft alignment (per vessel)", 1800, 4600),
            ("Marine bilge pump (unit)", 380, 1100),
            ("Hull blasting and repainting (per sq m)", 28, 62),
            ("Anchor chain 20mm (per metre)", 55, 120),
            ("Marine-grade welding service (per day)", 520, 980),
            ("Fuel injector set (box of 6)", 780, 1900),
            ("Navigation light, LED (unit)", 90, 260),
            ("Life raft servicing (unit)", 380, 820),
            ("Heat exchanger retube (unit)", 2400, 6200),
            ("Mooring rope 40mm (coil of 200m)", 1400, 3100),
            ("Stainless steel valve DN50 (unit)", 160, 420),
        ),
        ("Shipping", "Offshore", "Tug and Barge", "Marine"),
        ("Steel", "Marine Paint", "Engine Parts", "Rigging"),
        ("PROPELLER", "HULL PAINT", "SPARES", "DRYDOCK", "ENGINE PARTS"),
        (
            ("Crew safety boots (pair)", 45, 110),
            ("Cleaning rags (bale of 10kg)", 20, 55),
            ("Tool chest (unit)", 120, 340),
            ("Ship chandler provisions (per order)", 150, 520),
        ),
    ),  # fmt: skip
    Industry(
        "furniture_manufacturing",
        "held_out",
        "home",
        (
            "Manufacture of wooden and office furniture",
            "Making and installing custom furniture for offices and homes",
        ),
        "31001",
        "Furniture Industries",
        (
            ("Office desk, 1.4m (unit)", 220, 520),
            ("Ergonomic office chair (unit)", 160, 420),
            ("Conference table, 10-seater (unit)", 1100, 2600),
            ("Bookshelf, 5-tier (unit)", 120, 320),
            ("Wardrobe, built-in (per metre)", 380, 820),
            ("Dining set, 6 chairs", 650, 1700),
            ("Filing cabinet, 4-drawer (unit)", 150, 380),
            ("Reception counter (unit)", 900, 2400),
            ("Single bed frame (unit)", 180, 430),
            ("Kitchen cabinet (per metre)", 420, 940),
            ("Sofa, 3-seater (unit)", 700, 1900),
            ("Display shelf, retail (unit)", 140, 360),
        ),
        ("Interiors", "Hotel", "Co-Working", "Design"),
        ("Timber", "Hardware", "Foam", "Upholstery"),
        ("TIMBER LOT", "FOAM", "VENEER", "FITTINGS", "HARDWARE"),
        (
            ("Cushion covers (pack of 20)", 60, 150),
            ("Curtain rail (set)", 35, 90),
            ("Desk lamp (unit)", 20, 60),
            ("Assembly service (per hour)", 35, 75),
        ),
    ),  # fmt: skip
    Industry(
        "chemical_trading",
        "held_out",
        "chem",
        (
            "Wholesale of industrial chemicals",
            "Trading and distribution of industrial and specialty chemicals",
        ),
        "46692",
        "Chemical Trading",
        (
            ("Caustic soda flakes 25kg bag (pallet of 40)", 780, 1300),
            ("Isopropyl alcohol 200L drum", 380, 760),
            ("Hydrochloric acid 30% IBC 1000L", 240, 520),
            ("Sodium hypochlorite 20L (carton of 4)", 70, 150),
            ("Acetone 190L drum", 420, 880),
            ("Polyethylene resin 25kg bag (pallet of 40)", 1500, 2700),
            ("Titanium dioxide 25kg bag (pallet of 40)", 2600, 4400),
            ("Citric acid 25kg bag (pallet of 40)", 900, 1700),
            ("Industrial solvent thinner 20L (carton of 4)", 110, 230),
            ("Epoxy resin 20kg pail", 180, 380),
            ("Lubricant base oil 200L drum", 320, 640),
            ("Ammonium nitrate, fertiliser grade 50kg bag", 40, 85),
        ),
        ("Coatings", "Laundry", "Water Treatment", "Plastics"),
        ("Chemicals", "Petrochem", "Resins", "Solvents"),
        ("CAUSTIC SODA", "SOLVENT", "DRUMS", "IBC TANKS", "RESIN"),
        (
            ("Chemical-resistant gloves (box of 100)", 40, 95),
            ("Spill kit (unit)", 90, 240),
            ("Empty drum 200L (unit)", 25, 60),
            ("Safety data sheet translation (per sheet)", 30, 80),
        ),
    ),  # fmt: skip
    Industry(
        "freight_logistics",
        "held_out",
        "logistics",
        (
            "Freight forwarding, warehousing and local transport",
            "Logistics and cargo handling services",
        ),
        "52291",
        "Freight Logistics",
        (
            ("Freight consolidation, LCL (per cbm)", 45, 120),
            ("Customs documentation handling (per declaration)", 80, 300),
            ("Port handling and transfer (per container)", 150, 600),
            ("Air freight (per kg)", 3, 9),
            ("FCL ocean freight 40ft (per container)", 1400, 3800),
            ("Warehouse storage (per pallet per month)", 18, 42),
            ("Last-mile delivery (per drop)", 12, 38),
            ("Trucking, 20ft (per trip)", 220, 520),
            ("Cargo insurance arrangement (per shipment)", 45, 160),
            ("Pallet wrapping and labelling (per pallet)", 6, 16),
            ("Cold-chain trucking (per trip)", 380, 880),
            ("Bonded warehouse handling (per pallet)", 14, 36),
        ),
        ("Trading", "Manufacturing", "Apparel", "Electronics"),
        ("Shipping Line", "Haulage", "Airline Agency", "Warehousing"),
        ("CONTAINER", "AIR WAYBILL", "TRUCKING", "DEMURRAGE", "PORT FEES"),
        (
            ("Wooden pallet (unit)", 12, 28),
            ("Packing materials (set)", 40, 120),
            ("Container rental, 20ft (per month)", 180, 420),
            ("Customs training session (per pax)", 150, 380),
        ),
    ),  # fmt: skip
    Industry(
        "jewellery_trading",
        "held_out",
        "luxury",
        (
            "Wholesale and retail of jewellery and watches",
            "Trading in gold, gemstone jewellery and timepieces",
        ),
        "47731",
        "Jewellery Trading",
        (
            ("18K gold chain necklace (per gram)", 85, 125),
            ("Diamond solitaire ring 0.5ct (unit)", 1200, 3600),
            ("Pearl earrings, freshwater (pair)", 90, 320),
            ("Jade bangle, grade A (unit)", 480, 2400),
            ("Silver bracelet 925 (unit)", 60, 210),
            ("Wristwatch, quartz (unit)", 120, 480),
            ("Gold wedding band set (pair)", 780, 1800),
            ("Sapphire pendant (unit)", 380, 1400),
            ("Gold bar 100g (unit)", 9000, 11500),
            ("Jewellery gift box (carton of 50)", 60, 140),
            ("Engraving service (per piece)", 15, 45),
            ("Watch battery replacement (per piece)", 8, 25),
        ),
        ("Boutique", "Gifts", "Pawn", "Wedding"),
        ("Gold Refinery", "Gems", "Pearl Farm", "Watch Parts"),
        ("GOLD LOT", "DIAMONDS", "JADE", "PEARLS", "WATCH PARTS"),
        (
            ("Jewellery cleaning cloth (pack of 50)", 20, 55),
            ("Velvet display tray (unit)", 25, 70),
            ("Appraisal certificate (per piece)", 30, 90),
            ("Repair and resizing (per piece)", 25, 95),
        ),
    ),  # fmt: skip
    Industry(
        "cleaning_services",
        "held_out",
        "facility",
        (
            "Cleaning and facility maintenance services",
            "Contract cleaning and pest control for offices and residences",
        ),
        "81210",
        "Cleaning Services",
        (
            ("Office daily cleaning, monthly contract", 1800, 5200),
            ("Deep clean, 3-room apartment (per visit)", 220, 420),
            ("Window cleaning, high rise (per floor)", 180, 520),
            ("Carpet shampoo (per sq m)", 2, 6),
            ("Pest control, quarterly contract", 260, 780),
            ("Post-renovation cleaning (per sq ft)", 1, 3),
            ("Janitor supply (per worker per month)", 1800, 2600),
            ("Floor polishing and sealing (per sq m)", 6, 16),
            ("Toilet consumables restock (monthly)", 150, 420),
            ("Grease trap cleaning (per visit)", 180, 420),
            ("Air-con chemical wash (per unit)", 45, 90),
            ("Waste disposal, bulk (per trip)", 160, 420),
        ),
        ("Property", "Office", "Condominium", "Mall"),
        ("Cleaning Supplies", "Chemicals", "Equipment", "Uniform"),
        ("DETERGENT", "MOP SET", "WORKER UNIFORM", "VACUUM", "CONSUMABLES"),
        (
            ("Hand soap refill 5L (carton of 4)", 45, 100),
            ("Trash bags (carton of 20 rolls)", 38, 90),
            ("Mops and buckets (set)", 35, 85),
            ("Cleaning equipment rental (per day)", 60, 180),
        ),
    ),  # fmt: skip
    Industry(
        "education_training",
        "held_out",
        "edu",
        (
            "Provision of tuition, enrichment and corporate training",
            "Tuition centre and professional training courses",
        ),
        "85500",
        "Education Services",
        (
            ("Primary Maths tuition, monthly (per student)", 160, 320),
            ("Secondary Science tuition, monthly (per student)", 200, 420),
            ("Corporate leadership workshop (per day)", 2400, 6200),
            ("Coding bootcamp, 12 weeks (per student)", 1800, 4800),
            ("English enrichment course, term (per student)", 280, 560),
            ("Exam preparation course (per student)", 320, 720),
            ("Language class, 10 sessions (per student)", 260, 520),
            ("Workplace safety course (per pax)", 120, 340),
            ("Holiday camp, 5 days (per child)", 220, 480),
            ("Teaching materials pack (per student)", 25, 70),
            ("Assessment book set (per student)", 18, 55),
            ("Online course licence (per seat per year)", 120, 420),
        ),
        ("Parents Association", "Enterprise", "Associates", "Community Club"),
        ("Publishing", "Learning Resources", "Venue", "Stationery"),
        ("TRAINER FEE", "BOOKS", "VENUE HIRE", "STATIONERY", "COURSE MATERIALS"),
        (
            ("Classroom stationery pack (set)", 30, 80),
            ("Whiteboard marker (box of 12)", 10, 28),
            ("Room rental (per hour)", 45, 140),
            ("Education consultancy (per hour)", 90, 240),
        ),
    ),  # fmt: skip
)
BY_ID = {i.id: i for i in INDUSTRIES}


def industries_of(split: str) -> tuple[Industry, ...]:
    return tuple(i for i in INDUSTRIES if i.split == split)


# ------------------------------------------------------------------------------ occupations


@dataclass(frozen=True)
class Occupation:
    id: str
    split: str
    title: str  # as written in a declaration
    employer_noun: str
    monthly_lo: int  # gross, SGD (our estimates)
    monthly_hi: int

    @property
    def annual_max(self) -> int:
        """Most a plausible year can bring: the top of the monthly range for 12 months plus a
        three-month bonus."""
        return int(self.monthly_hi * 15)

    def savings_max(self, years: int) -> int:
        """Most that could plausibly be saved from this pay over ``years``: half of the top
        monthly range for 12 months a year."""
        return int(self.monthly_hi * 12 * years * 0.5)


OCCUPATIONS: tuple[Occupation, ...] = (
    # dev
    Occupation(
        "hawker_assistant", "dev", "hawker stall assistant", "Food Centre Stalls", 1800, 2800
    ),
    Occupation("cleaner", "dev", "cleaner", "Cleaning Services", 1400, 2200),
    Occupation("security_officer", "dev", "security officer", "Security Services", 1900, 3200),
    Occupation("delivery_rider", "dev", "delivery rider", "Delivery Services", 1800, 3800),
    Occupation("bus_captain", "dev", "bus captain", "Bus Services", 2800, 4500),
    Occupation("primary_teacher", "dev", "primary school teacher", "Primary School", 3800, 7500),
    Occupation("staff_nurse", "dev", "staff nurse", "General Hospital", 3500, 6500),
    Occupation("civil_engineer", "dev", "civil engineer", "Engineering Consultants", 5000, 11000),
    Occupation("accountant", "dev", "accountant", "Accounting Practice", 4500, 10000),
    Occupation("software_engineer", "dev", "software engineer", "Software", 5500, 14000),
    Occupation(
        "orthopaedic_surgeon", "dev", "orthopaedic surgeon", "Orthopaedic Centre", 25000, 60000
    ),
    Occupation(
        "investment_banker", "dev", "investment banking director", "Capital Markets", 30000, 90000
    ),
    # held_out
    Occupation("kitchen_helper", "held_out", "kitchen helper", "Restaurant Group", 1700, 2600),
    Occupation("warehouse_assistant", "held_out", "warehouse assistant", "Warehousing", 1900, 3000),
    Occupation("taxi_driver", "held_out", "taxi driver", "Taxi Fleet", 2200, 4200),
    Occupation(
        "retail_associate", "held_out", "retail sales associate", "Department Store", 1900, 3200
    ),
    Occupation(
        "polytechnic_lecturer", "held_out", "polytechnic lecturer", "Polytechnic", 5500, 9500
    ),
    Occupation("pharmacist", "held_out", "pharmacist", "Pharmacy Chain", 4800, 8500),
    Occupation("electrician", "held_out", "electrician", "Electrical Contractors", 2800, 5200),
    Occupation("architect", "held_out", "architect", "Architects LLP", 6000, 14000),
    Occupation("marine_pilot", "held_out", "marine pilot", "Port Authority", 15000, 35000),
    Occupation("airline_captain", "held_out", "airline captain", "Airline", 18000, 38000),
    Occupation(
        "litigation_partner", "held_out", "litigation partner", "Law Practice", 25000, 80000
    ),
    Occupation("data_scientist", "held_out", "data scientist", "Analytics", 7000, 16000),
)
OCC_BY_ID = {o.id: o for o in OCCUPATIONS}
MODEST_MAX = 5600  # monthly_hi at or below this is a "modest income" occupation


def occupations_of(split: str) -> tuple[Occupation, ...]:
    return tuple(o for o in OCCUPATIONS if o.split == split)
