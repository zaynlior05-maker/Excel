"""
Admin panel — Telegram commands + inline menu.

All handlers are registered via register_admin_handlers(app).
Every handler silently ignores non-admin callers.

Commands:
  /admin              — open the panel
  /credit ID AMOUNT   — quick credit user balance
  /deduct ID AMOUNT   — quick deduct user balance
  /userinfo ID        — quick user lookup
  /broadcast TEXT     — quick broadcast (no confirmation)
  /upload SUBL_ID     — prime the bot then send a file on the next message

Bulk upload (easiest way):
  Send a .txt or .csv file directly to the bot.
  • With caption   → "dd-28th"  — goes straight into that list.
  • Without caption → bot shows a list picker, then processes.
  • Or: /upload dd-28th, then send the file.

Supported file formats (one item per line, blank lines / #comments skipped):
  BIN|YEAR|CODE|PRICE|CONTENT       ← pipe-separated  (preferred)
  BIN,YEAR,CODE,PRICE,CONTENT       ← comma-separated
  BIN\tYEAR\tCODE\tPRICE\tCONTENT  ← tab-separated
  Content may contain the delimiter — everything after the 4th separator
  is treated as content.

Inline panel sections:
  📊 Stats      — live dashboard
  📦 Stock      — per-list counts, add / delete items, upload file
  👥 Users      — lookup, adjust balance, ban / unban
  📋 Orders     — last 20 transactions
  📢 Broadcast  — type + confirm before sending
"""

import logging
import random
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config

class _NullLog:
    def __getattr__(self, _):
        async def _noop(*a, **kw): pass
        return _noop
    def init(self, bot): pass

try:
    import channel_log
    if not hasattr(channel_log, 'init'):
        channel_log.init = lambda bot: None
except ImportError:
    channel_log = _NullLog()

import db

logger = logging.getLogger(__name__)

# ============================================================
#  UK Outward Codes (used by auto-generation engine)
# ============================================================
UK_OUTCODES = [
    # London
    "E1","E2","E3","E4","E5","E6","E7","E8","E9","E10","E11","E12","E13","E14","E15","E16","E17","E18","E20",
    "EC1","EC2","EC3","EC4",
    "N1","N2","N3","N4","N5","N6","N7","N8","N9","N10","N11","N12","N13","N14","N15","N16","N17","N18","N19","N20","N21","N22",
    "NW1","NW2","NW3","NW4","NW5","NW6","NW7","NW8","NW9","NW10","NW11",
    "SE1","SE2","SE3","SE4","SE5","SE6","SE7","SE8","SE9","SE10","SE11","SE12","SE13","SE14","SE15","SE16","SE17","SE18","SE19","SE20","SE21","SE22","SE23","SE24","SE25","SE26","SE27","SE28",
    "SW1","SW2","SW3","SW4","SW5","SW6","SW7","SW8","SW9","SW10","SW11","SW12","SW13","SW14","SW15","SW16","SW17","SW18","SW19","SW20",
    "W1","W2","W3","W4","W5","W6","W7","W8","W9","W10","W11","W12","W13","W14",
    "WC1","WC2",
    # Midlands
    "B1","B2","B3","B4","B5","B6","B7","B8","B9","B10","B11","B12","B13","B14","B15","B16","B17","B18","B19","B20",
    "CV1","CV2","CV3","CV4","CV5","CV6","CV7","CV8",
    "DE1","DE3","DE21","DE22","DE23","DE24",
    "DY1","DY2","DY3","DY4","DY5","DY6","DY7","DY8","DY9","DY10","DY11",
    "LE1","LE2","LE3","LE4","LE5","LE7","LE8","LE9","LE10",
    "NG1","NG2","NG3","NG4","NG5","NG6","NG7","NG8","NG9","NG10",
    "ST1","ST2","ST3","ST4","ST5","ST6","ST7","ST8",
    "WR1","WR2","WR3","WR4","WR5",
    "WS1","WS2","WS3","WS4","WS5","WS6","WS7","WS8","WS9","WS10","WS11","WS12",
    "WV1","WV2","WV3","WV4","WV5","WV6","WV10","WV11","WV12","WV13","WV14",
    # North West
    "BB1","BB2","BB3","BB4","BB5","BB6","BB7","BB8","BB9","BB10","BB11","BB12",
    "BL0","BL1","BL2","BL3","BL4","BL5","BL6","BL7","BL8","BL9",
    "FY1","FY2","FY3","FY4","FY5","FY6","FY7","FY8",
    "L1","L2","L3","L4","L5","L6","L7","L8","L9","L10","L11","L12","L13","L14","L15","L16","L17","L18","L19","L20",
    "LA1","LA2","LA3","LA4","LA5",
    "M1","M2","M3","M4","M5","M6","M7","M8","M9","M11","M12","M13","M14","M15","M16","M17","M18","M19","M20","M21","M22",
    "OL1","OL2","OL3","OL4","OL5","OL6","OL7","OL8","OL9","OL10","OL11","OL12","OL13","OL14","OL15","OL16",
    "PR1","PR2","PR3","PR4","PR5","PR6","PR7","PR8","PR9",
    "SK1","SK2","SK3","SK4","SK5","SK6","SK7","SK8","SK9","SK10","SK11","SK12",
    "WA1","WA2","WA3","WA4","WA5","WA6","WA7","WA8","WA9","WA10","WA11","WA12","WA13","WA14","WA15","WA16",
    "WN1","WN2","WN3","WN4","WN5","WN6","WN7","WN8",
    # Yorkshire
    "BD1","BD2","BD3","BD4","BD5","BD6","BD7","BD8","BD9","BD10","BD11","BD12","BD13","BD14","BD15","BD16","BD17","BD18","BD19","BD20","BD21","BD22","BD23",
    "DN1","DN2","DN3","DN4","DN5","DN6","DN7","DN8","DN9","DN10","DN11","DN12",
    "HD1","HD2","HD3","HD4","HD5","HD6","HD7","HD8","HD9",
    "HG1","HG2","HG3","HG4","HG5",
    "HX1","HX2","HX3","HX4","HX5","HX6","HX7",
    "LS1","LS2","LS3","LS4","LS5","LS6","LS7","LS8","LS9","LS10","LS11","LS12","LS13","LS14","LS15","LS16","LS17","LS18","LS19","LS20","LS21","LS22","LS23","LS24","LS25","LS26","LS27","LS28","LS29",
    "S1","S2","S3","S4","S5","S6","S7","S8","S9","S10","S11","S12","S13","S14",
    "WF1","WF2","WF3","WF4","WF5","WF6","WF7","WF8","WF9","WF10","WF11","WF12","WF13","WF14","WF15","WF16","WF17",
    "YO1","YO8","YO10","YO11","YO12","YO13","YO14","YO15","YO16","YO17","YO18","YO19","YO21","YO22","YO23","YO24","YO25","YO26",
    # North East
    "DH1","DH2","DH3","DH4","DH5","DH6","DH7","DH8","DH9",
    "DL1","DL2","DL3","DL4","DL5","DL6","DL7","DL8","DL9","DL10","DL11","DL12","DL13","DL14","DL15","DL16","DL17",
    "NE1","NE2","NE3","NE4","NE5","NE6","NE7","NE8","NE9","NE10","NE11","NE12","NE13","NE15","NE16","NE17","NE18","NE19","NE20","NE21","NE22","NE23","NE24","NE25","NE26","NE27","NE28","NE29","NE30","NE31","NE32","NE33","NE34","NE36","NE37","NE38","NE39","NE40","NE41","NE42","NE43","NE44","NE45","NE46","NE47","NE48","NE49",
    "SR1","SR2","SR3","SR4","SR5","SR6","SR7",
    "TS1","TS2","TS3","TS4","TS5","TS6","TS7","TS8","TS9","TS10","TS11","TS12","TS13","TS14","TS15","TS16","TS17","TS18","TS19","TS20","TS21","TS22","TS23","TS24","TS25","TS26","TS27","TS28","TS29",
    # South East
    "BN1","BN2","BN3","BN7","BN8","BN9","BN10","BN11","BN12","BN13","BN14","BN15","BN16","BN17","BN18",
    "BR1","BR2","BR3","BR4","BR5","BR6","BR7","BR8",
    "CR0","CR2","CR3","CR4","CR5","CR6","CR7","CR8","CR9",
    "CT1","CT2","CT3","CT4","CT5","CT6","CT7","CT8","CT9","CT10","CT11","CT12","CT13","CT14","CT15","CT16","CT17","CT18","CT19","CT20","CT21",
    "DA1","DA2","DA5","DA6","DA7","DA8","DA9","DA10","DA11","DA12","DA13","DA14","DA15","DA16","DA17","DA18",
    "GU1","GU2","GU3","GU4","GU5","GU6","GU7","GU8","GU9","GU10","GU11","GU12","GU14","GU15","GU16","GU17","GU18","GU19","GU20","GU21","GU22","GU23","GU24","GU25","GU26","GU27",
    "HA0","HA1","HA2","HA3","HA4","HA5","HA6","HA7","HA8","HA9",
    "HP1","HP2","HP3","HP4","HP5","HP6","HP7","HP8","HP9","HP10","HP11","HP12","HP13","HP14","HP15","HP16","HP17","HP18","HP19","HP20","HP21","HP22","HP23",
    "IG1","IG2","IG3","IG4","IG5","IG6","IG7","IG8","IG9","IG10","IG11",
    "KT1","KT2","KT3","KT4","KT5","KT6","KT7","KT8","KT9","KT10","KT11","KT12","KT13","KT14","KT15","KT16","KT17","KT18","KT19","KT20","KT21","KT22","KT23","KT24",
    "ME1","ME2","ME3","ME4","ME5","ME6","ME7","ME8","ME9","ME10","ME11","ME12","ME13","ME14","ME15","ME16","ME17","ME18","ME19","ME20",
    "MK1","MK2","MK3","MK4","MK5","MK6","MK7","MK8","MK9","MK10","MK11","MK12","MK13","MK14","MK15","MK16","MK17","MK18","MK19","MK40","MK41","MK42","MK43","MK44","MK45","MK46",
    "OX1","OX2","OX3","OX4","OX5","OX7","OX9","OX10","OX11","OX12","OX13","OX14","OX15","OX16","OX17","OX18","OX20","OX25","OX26","OX27","OX28","OX29","OX33","OX39","OX44","OX49",
    "RG1","RG2","RG4","RG5","RG6","RG7","RG8","RG9","RG10","RG12","RG14","RG17","RG18","RG19","RG20","RG21","RG22","RG23","RG24","RG25","RG26","RG27","RG28","RG29","RG40","RG41","RG42","RG45",
    "RH1","RH2","RH3","RH4","RH5","RH6","RH7","RH8","RH9","RH10","RH11","RH12","RH13","RH14","RH15","RH16","RH17","RH18","RH19","RH20",
    "RM1","RM2","RM3","RM4","RM5","RM6","RM7","RM8","RM9","RM10","RM11","RM12","RM13","RM14","RM15","RM16","RM17","RM18","RM19","RM20",
    "SL0","SL1","SL2","SL3","SL4","SL5","SL6","SL7","SL8","SL9",
    "SM1","SM2","SM3","SM4","SM5","SM6","SM7",
    "SS0","SS1","SS2","SS3","SS4","SS5","SS6","SS7","SS8","SS9","SS11","SS12","SS13","SS14","SS15","SS16","SS17",
    "TN1","TN2","TN3","TN4","TN5","TN6","TN7","TN8","TN9","TN10","TN11","TN12","TN13","TN14","TN15","TN16","TN17","TN18","TN19","TN20","TN21","TN22","TN23","TN24","TN25","TN26","TN27","TN28","TN29","TN30","TN31","TN32","TN33","TN34","TN35","TN36","TN37","TN38","TN39","TN40",
    "TW1","TW2","TW3","TW4","TW5","TW6","TW7","TW8","TW9","TW10","TW11","TW12","TW13","TW14","TW15","TW16","TW17","TW18","TW19","TW20",
    "UB1","UB2","UB3","UB4","UB5","UB6","UB7","UB8","UB9","UB10","UB11",
    "WD1","WD2","WD3","WD4","WD5","WD6","WD7","WD17","WD18","WD19","WD23","WD24","WD25",
    # South West
    "BA1","BA2","BA3","BA4","BA5","BA6","BA7","BA8","BA9","BA10","BA11","BA12","BA13","BA14","BA15","BA16","BA20","BA21","BA22",
    "BH1","BH2","BH3","BH4","BH5","BH6","BH7","BH8","BH9","BH10","BH11","BH12","BH13","BH14","BH15","BH16","BH17","BH18","BH19","BH20","BH21","BH22","BH23","BH24","BH25",
    "BS1","BS2","BS3","BS4","BS5","BS6","BS7","BS8","BS9","BS10","BS11","BS13","BS14","BS15","BS16","BS20","BS21","BS22","BS23","BS24","BS25","BS26","BS27","BS28","BS29","BS30","BS31","BS32","BS34","BS35","BS36","BS37","BS39","BS40","BS41","BS48","BS49",
    "DT1","DT2","DT3","DT4","DT5","DT6","DT7","DT8","DT9","DT10","DT11",
    "EX1","EX2","EX3","EX4","EX5","EX6","EX7","EX8","EX9","EX10","EX11","EX12","EX13","EX14","EX15","EX16","EX17","EX18","EX19","EX20","EX21","EX22","EX23","EX24","EX31","EX32","EX33","EX34","EX35","EX36","EX37","EX38","EX39",
    "GL1","GL2","GL3","GL4","GL5","GL6","GL7","GL8","GL9","GL10","GL11","GL12","GL13","GL14","GL15","GL16","GL17","GL18","GL19","GL20","GL50","GL51","GL52","GL53","GL54","GL55","GL56",
    "PL1","PL2","PL3","PL4","PL5","PL6","PL7","PL8","PL9","PL10","PL11","PL12","PL14","PL15","PL17","PL18","PL19","PL20","PL21","PL22","PL23","PL24","PL25","PL26","PL27","PL28","PL30","PL31","PL32","PL33","PL34","PL35",
    "SN1","SN2","SN3","SN4","SN5","SN6","SN7","SN8","SN9","SN10","SN11","SN12","SN13","SN14","SN15","SN16","SN25","SN26",
    "SO14","SO15","SO16","SO17","SO18","SO19","SO20","SO21","SO22","SO23","SO24","SO30","SO31","SO32","SO40","SO41","SO42","SO43","SO45","SO50","SO51","SO52","SO53",
    "SP1","SP2","SP3","SP4","SP5","SP6","SP7","SP8","SP9","SP10","SP11",
    "TA1","TA2","TA3","TA4","TA5","TA6","TA7","TA8","TA9","TA10","TA11","TA12","TA19","TA20","TA21","TA22","TA23","TA24",
    "TQ1","TQ2","TQ3","TQ4","TQ5","TQ6","TQ7","TQ8","TQ9","TQ10","TQ11","TQ12","TQ13","TQ14",
    "TR1","TR2","TR3","TR4","TR5","TR6","TR7","TR8","TR9","TR10","TR11","TR12","TR13","TR14","TR15","TR16","TR17","TR18","TR19","TR20","TR26","TR27",
    # East of England
    "CB1","CB2","CB3","CB4","CB5","CB6","CB7","CB8","CB9","CB10","CB11","CB21","CB22","CB23","CB24","CB25",
    "CM1","CM2","CM3","CM4","CM5","CM6","CM7","CM8","CM9","CM11","CM12","CM13","CM14","CM15","CM16","CM17","CM18","CM19","CM20","CM21","CM22","CM23","CM24",
    "CO1","CO2","CO3","CO4","CO5","CO6","CO7","CO9","CO10","CO11","CO12","CO13","CO15","CO16",
    "IP1","IP2","IP3","IP4","IP5","IP6","IP7","IP8","IP9","IP10","IP11","IP12","IP13","IP14","IP17","IP18","IP19","IP20","IP21","IP22","IP23","IP24","IP25","IP26","IP27","IP28","IP29","IP30","IP31","IP32","IP33",
    "LU1","LU2","LU3","LU4","LU5","LU6","LU7",
    "NR1","NR2","NR3","NR4","NR5","NR6","NR7","NR8","NR9","NR10","NR11","NR12","NR13","NR14","NR15","NR16","NR17","NR18","NR19","NR20","NR21","NR22","NR23","NR24","NR25","NR26","NR27","NR28","NR29","NR30","NR31","NR32","NR33","NR34","NR35",
    "PE1","PE2","PE3","PE4","PE5","PE6","PE7","PE8","PE9","PE10","PE11","PE12","PE13","PE14","PE15","PE16","PE19","PE20","PE21","PE22","PE23","PE24","PE25","PE26","PE27","PE28","PE29","PE30","PE31","PE32","PE33","PE34","PE35","PE36","PE37","PE38",
    "SG1","SG2","SG3","SG4","SG5","SG6","SG7","SG8","SG9","SG10","SG11","SG12","SG13","SG14","SG15","SG16","SG17","SG18","SG19",
    # East Midlands
    "DE55","DE56","DE65","DE72","DE73","DE74",
    "LE11","LE12","LE13","LE14","LE15","LE16","LE17","LE18","LE19",
    "LN1","LN2","LN3","LN4","LN5","LN6","LN7","LN8","LN9","LN10","LN11","LN12","LN13",
    "MK40","MK41","MK42","MK43","MK44","MK45","MK46",
    "NN1","NN2","NN3","NN4","NN5","NN6","NN7","NN8","NN9","NN10","NN11","NN12","NN13","NN14","NN15","NN16","NN17","NN18","NN29",
    "PE9","PE10","PE11",
    # South
    "PO1","PO2","PO3","PO4","PO5","PO6","PO7","PO8","PO9","PO10","PO11","PO12","PO13","PO14","PO15","PO16","PO17","PO18","PO19","PO20","PO21","PO22","PO30","PO31","PO32","PO33","PO34","PO35","PO36","PO37","PO38","PO39","PO40","PO41",
    # Wales
    "CF10","CF11","CF14","CF15","CF23","CF24","CF3","CF5","CF62","CF63","CF64","CF71",
    "NP1","NP4","NP7","NP8","NP10","NP11","NP12","NP13","NP15","NP16","NP18","NP19","NP20","NP22","NP23","NP24","NP25","NP26","NP44",
    "SA1","SA2","SA3","SA4","SA5","SA6","SA7","SA8","SA9","SA10","SA11","SA12","SA13","SA14","SA15","SA16","SA17","SA18","SA19","SA20",
    # Scotland
    "AB10","AB11","AB12","AB13","AB14","AB15","AB16","AB21","AB22","AB23","AB24","AB25","AB30","AB31","AB32","AB33","AB34","AB35","AB36","AB37","AB38","AB39","AB41","AB42","AB43","AB44","AB45","AB51","AB52","AB53","AB54","AB55","AB56",
    "DD1","DD2","DD3","DD4","DD5","DD6","DD7","DD8","DD9","DD10","DD11",
    "EH1","EH2","EH3","EH4","EH5","EH6","EH7","EH8","EH9","EH10","EH11","EH12","EH13","EH14","EH15","EH16","EH17","EH18","EH19","EH20","EH21","EH22","EH23","EH24","EH25","EH26","EH27","EH28","EH29","EH30","EH31","EH32","EH33","EH34","EH35","EH36","EH37","EH38","EH39","EH40","EH41","EH42","EH43","EH44","EH45","EH46","EH47","EH48","EH49","EH51","EH52","EH53","EH54","EH55",
    "FK1","FK2","FK3","FK4","FK5","FK6","FK7","FK8","FK9","FK10","FK11","FK12","FK13","FK14","FK15","FK16","FK17","FK18","FK19","FK20","FK21",
    "G1","G2","G3","G4","G5","G11","G12","G13","G14","G15","G20","G21","G22","G23","G31","G32","G33","G34","G40","G41","G42","G43","G44","G45","G46","G51","G52","G53","G60","G61","G62","G63","G64","G65","G66","G67","G68","G69","G71","G72","G73","G74","G75","G76","G77","G78","G81","G82","G83","G84",
    "KA1","KA2","KA3","KA4","KA5","KA6","KA7","KA8","KA9","KA10","KA11","KA12","KA13","KA14","KA15","KA16","KA17","KA18","KA19","KA20","KA21","KA22","KA23","KA24","KA25","KA26","KA27","KA28","KA29","KA30",
    "KY1","KY2","KY3","KY4","KY5","KY6","KY7","KY8","KY9","KY10","KY11","KY12","KY13","KY14","KY15","KY16",
    "ML1","ML2","ML3","ML4","ML5","ML6","ML7","ML8","ML9","ML10","ML11","ML12",
    "PA1","PA2","PA3","PA4","PA5","PA6","PA7","PA8","PA9","PA10","PA11","PA12","PA13","PA14","PA15","PA16","PA17","PA18","PA19","PA20","PA21","PA22","PA23","PA24","PA25","PA26","PA27","PA28","PA29","PA30","PA31","PA32","PA33","PA34","PA35","PA36","PA37","PA38","PA41","PA42","PA43","PA44","PA45","PA46","PA47","PA48","PA49","PA60","PA61","PA62","PA63","PA64","PA65","PA66","PA67","PA68","PA69","PA70","PA71","PA72","PA73","PA74","PA75","PA76","PA77","PA78",
    "PH1","PH2","PH3","PH4","PH5","PH6","PH7","PH8","PH9","PH10","PH11","PH12","PH13","PH14","PH15","PH16","PH17","PH18","PH19","PH20","PH21","PH22","PH23","PH24","PH25","PH26","PH30","PH31","PH32","PH33","PH34","PH35","PH36","PH37","PH38","PH39","PH40","PH41","PH42","PH43","PH44","PH49","PH50",
    "TD1","TD2","TD3","TD4","TD5","TD6","TD7","TD8","TD9","TD10","TD11","TD12","TD13","TD14","TD15",
]


# ============================================================
#  Guard
# ============================================================
SESSION_HOURS = 2   # how long a password login lasts


def is_admin(user_id: int, user_data: dict | None = None) -> bool:
    """Admin by permanent ID or by active password session."""
    if user_id in config.ADMIN_IDS:
        return True
    if user_data:
        expires = user_data.get("admin_session_expires", 0)
        if expires and time.time() < expires:
            return True
    return False


def admin_only(fn):
    """Decorator — passes if user is a permanent admin OR has an active session."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid, context.user_data):
            return
        return await fn(update, context)
    wrapper.__name__ = fn.__name__
    return wrapper


# ============================================================
#  Keyboards
# ============================================================
def admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",    callback_data="adm_stats"),
            InlineKeyboardButton("📦 Stock",    callback_data="adm_stock"),
        ],
        [
            InlineKeyboardButton("👥 Users",    callback_data="adm_users"),
            InlineKeyboardButton("📋 Orders",   callback_data="adm_orders"),
        ],
        [
            InlineKeyboardButton("💰 Prices",   callback_data="adm_prices"),
            InlineKeyboardButton("💳 Payments", callback_data="adm_payments"),
        ],
        [
            InlineKeyboardButton("🏷️ Labels",   callback_data="adm_labels"),
            InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"),
        ],
        [InlineKeyboardButton("❌ Close",        callback_data="adm_close")],
    ])


def back_to_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")]]
    )


def stock_overview_kb(counts: dict) -> InlineKeyboardMarkup:
    rows = []
    for cat in config.CATEGORIES:
        for subl in cat.get("sublists", []):
            sid = subl["id"]
            n = counts.get(sid, 0)
            rows.append([InlineKeyboardButton(
                f"{subl['label']}  [{n} in stock]",
                callback_data=f"adm_slist:{sid}",
            )])
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def stock_list_kb(subl_id: str, items: list) -> InlineKeyboardMarkup:
    rows   = []
    locked = db.is_sublist_locked(subl_id)
    for it in items[:30]:
        label = f"{it['bin']} - {it['year']} - {it['code']} - {config.CURRENCY_SYMBOL}{float(it['price']):g}"
        rows.append([InlineKeyboardButton(f"❌ {label}",      callback_data=f"adm_sdel:{it['id']}")])
        rows.append([InlineKeyboardButton("✏️ £ Set Price",   callback_data=f"adm_itemprice:{it['id']}:{subl_id}")])
    if locked:
        rows.append([InlineKeyboardButton("🔓 Unlock Base  (currently locked)", callback_data=f"adm_unlockbase:{subl_id}")])
    else:
        rows.append([InlineKeyboardButton("🔒 Lock Base",                        callback_data=f"adm_lockbase:{subl_id}")])
    rows.append([InlineKeyboardButton("✏️ Rename This Base",          callback_data=f"adm_renamebase:{subl_id}")])
    rows.append([InlineKeyboardButton("➕ Add Item",                   callback_data=f"adm_sadd:{subl_id}")])
    rows.append([InlineKeyboardButton("📤 Upload File",                callback_data=f"adm_upload_prompt:{subl_id}")])
    rows.append([InlineKeyboardButton("🏷️ Select Items to Reprice",   callback_data=f"adm_pricesel:{subl_id}")])
    rows.append([InlineKeyboardButton("💰 Change Price (All Items)",   callback_data=f"adm_setprice:{subl_id}")])
    rows.append([InlineKeyboardButton("🗑️ Delete This Base",          callback_data=f"adm_delbase_confirm:{subl_id}")])
    rows.append([InlineKeyboardButton("⬅️ Back",                      callback_data="adm_stock")])
    return InlineKeyboardMarkup(rows)


PRICE_SEL_PER_PAGE = 10   # items per page in the admin price selector


def reorder_kb(cat_id: str) -> InlineKeyboardMarkup:
    """Show all bases with ⬆️⬇️ buttons to move them up or down."""
    sublists = db.get_sublists(cat_id)
    rows = []
    for i, s in enumerate(sublists):
        label = db.get_label(f"subl:{s['id']}", s["label"])
        row   = []
        # Up arrow (disabled at top)
        row.append(InlineKeyboardButton(
            "⬆️" if i > 0 else "  ",
            callback_data=f"adm_moveup:{s['id']}:{cat_id}" if i > 0 else "noop",
        ))
        # Base name + position
        row.append(InlineKeyboardButton(
            f"{i + 1}. {label}",
            callback_data="noop",
        ))
        # Down arrow (disabled at bottom)
        row.append(InlineKeyboardButton(
            "⬇️" if i < len(sublists) - 1 else "  ",
            callback_data=f"adm_movedown:{s['id']}:{cat_id}" if i < len(sublists) - 1 else "noop",
        ))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back to Stock", callback_data="adm_stock")])
    return InlineKeyboardMarkup(rows)


def price_select_kb(subl_id: str, items: list,
                    selected: set, page: int = 0) -> InlineKeyboardMarkup:
    """Paginated item list with ✅/☐ toggles for multi-select repricing."""
    total_pages = max(1, (len(items) + PRICE_SEL_PER_PAGE - 1) // PRICE_SEL_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))
    page_items  = items[page * PRICE_SEL_PER_PAGE : (page + 1) * PRICE_SEL_PER_PAGE]

    rows = []
    for it in page_items:
        tick  = "✅" if it["id"] in selected else "☐ "
        label = f"{tick} {it['bin']} - {it['year']} - {it['code']} · {config.CURRENCY_SYMBOL}{float(it['price']):g}"
        rows.append([InlineKeyboardButton(
            label, callback_data=f"adm_pstoggle:{it['id']}:{subl_id}:{page}"
        )])

    # Pagination navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_pspage:{subl_id}:{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm_pspage:{subl_id}:{page+1}"))
    if nav:
        rows.append(nav)

    if selected:
        n = len(selected)
        rows.append([InlineKeyboardButton(
            f"💰 Set Price for {n} Item{'s' if n > 1 else ''}",
            callback_data=f"adm_psconfirm:{subl_id}",
        )])
    rows.append([InlineKeyboardButton("✅ Select All",       callback_data=f"adm_psall:{subl_id}:{page}")])
    rows.append([InlineKeyboardButton("☐  Clear Selection", callback_data=f"adm_psclear:{subl_id}:{page}")])
    rows.append([InlineKeyboardButton("⬅️ Back to Stock",   callback_data=f"adm_slist:{subl_id}")])
    return InlineKeyboardMarkup(rows)


def upload_list_picker_kb() -> InlineKeyboardMarkup:
    """Shown when a file arrives with no caption — pick which list to import into."""
    rows = []
    for cat in config.CATEGORIES:
        for subl in cat.get("sublists", []):
            rows.append([InlineKeyboardButton(
                subl["label"], callback_data=f"adm_upload_to:{subl['id']}"
            )])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="adm_stock")])
    return InlineKeyboardMarkup(rows)


def labels_kb(overrides: dict) -> InlineKeyboardMarkup:
    rows = []

    # ── Editable bot texts ──
    rows.append([InlineKeyboardButton("── Bot Texts ──", callback_data="noop")])
    for key, display in [
        ("welcome_text",     "✏️ Welcome Message"),
        ("refund_text",      "✏️ Refund Policy (shown on /start)"),
        ("rules_text",       "✏️ Rules Text (shown on Rules button)"),
        ("store_cat_text",   "✏️ Store: 'Choose a category'"),
        ("store_subl_text",  "✏️ Store: 'Select a list'"),
        ("store_items_text", "✏️ Store: 'Tap items to add to cart'"),
    ]:
        label = f"🔄 {display}" if key in overrides else display
        rows.append([InlineKeyboardButton(label, callback_data=f"adm_label_edit:{key}")])

    # ── Static menu button labels ──
    rows.append([InlineKeyboardButton("── Menu Buttons ──", callback_data="noop")])
    for key, default in config.RENAMEABLE.items():
        if key.startswith("subl:"):
            continue
        current    = overrides.get(key, default)
        overridden = key in overrides
        edit_btn   = InlineKeyboardButton(
            f"{'🔄 ' if overridden else ''}✏️ {current}",
            callback_data=f"adm_label_edit:{key}",
        )
        if overridden:
            rows.append([edit_btn, InlineKeyboardButton("↩️", callback_data=f"adm_label_reset:{key}")])
        else:
            rows.append([edit_btn])

    # ── Dynamic base labels ──
    rows.append([InlineKeyboardButton("── Bases ──", callback_data="noop")])
    for s in db.get_all_sublists():
        key        = f"subl:{s['id']}"
        default    = s["label"]
        current    = overrides.get(key, default)
        overridden = key in overrides
        edit_btn   = InlineKeyboardButton(
            f"{'🔄 ' if overridden else ''}✏️ {current}",
            callback_data=f"adm_label_edit:{key}",
        )
        if overridden:
            rows.append([edit_btn, InlineKeyboardButton("↩️", callback_data=f"adm_label_reset:{key}")])
        else:
            rows.append([edit_btn])

    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def user_detail_kb(user_id: int, banned: bool) -> InlineKeyboardMarkup:
    ban_label = "✅ Unban" if banned else "🚫 Ban"
    ban_cb    = f"adm_unban:{user_id}" if banned else f"adm_ban:{user_id}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Balance", callback_data=f"adm_badd:{user_id}"),
            InlineKeyboardButton("➖ Deduct Balance", callback_data=f"adm_bsub:{user_id}"),
        ],
        [InlineKeyboardButton(ban_label, callback_data=ban_cb)],
        [InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")],
    ])


# ============================================================
#  /admin command
# ============================================================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    # Permanent admin — straight in
    if is_admin(uid, context.user_data):
        await update.message.reply_text(
            "🛠️ <b>Admin Panel</b>\n\nChoose a section:",
            reply_markup=admin_home_kb(),
            parse_mode="HTML",
        )
        return
    # Not verified yet — ask for password
    if not config.ADMIN_PASSWORD:
        await update.message.reply_text("⚠️ Admin password not configured.")
        return
    context.user_data["adm_awaiting_pw"] = True
    await update.message.reply_text(
        "🔐 <b>Admin Login</b>\n\nEnter the admin password:",
        parse_mode="HTML",
    )


async def handle_admin_password(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from bot.py when a user sends text while adm_awaiting_pw is set.
    Returns True if handled (regardless of success/fail).
    """
    context.user_data.pop("adm_awaiting_pw", None)
    password = update.message.text.strip()

    # Delete the password message for security
    try:
        await update.message.delete()
    except Exception:
        pass

    if not config.ADMIN_PASSWORD or password != config.ADMIN_PASSWORD:
        await context.bot.send_message(
            update.effective_user.id,
            "❌ Wrong password. Access denied.",
        )
        await channel_log.log(
            f"🚨 <b>Failed Admin Login</b>\n"
            f"User: <code>{update.effective_user.id}</code>\n"
            f"Username: @{update.effective_user.username or 'none'}"
        )
        return True

    # Grant session
    context.user_data["admin_session_expires"] = time.time() + SESSION_HOURS * 3600
    uid = update.effective_user.id
    await channel_log.log(
        f"🔑 <b>Admin Login via Password</b>\n"
        f"User: <code>{uid}</code>\n"
        f"Username: @{update.effective_user.username or 'none'}\n"
        f"Session expires in {SESSION_HOURS}h"
    )
    await context.bot.send_message(
        uid,
        f"✅ <b>Access granted!</b> Session lasts {SESSION_HOURS} hours.\n\n"
        "🛠️ <b>Admin Panel</b>\n\nChoose a section:",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )
    return True


# ============================================================
#  Quick commands
# ============================================================
@admin_only
async def cmd_credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /credit USER_ID AMOUNT"""
    parts = (context.args or [])
    if len(parts) != 2:
        await update.message.reply_text("Usage: /credit USER_ID AMOUNT")
        return
    try:
        uid    = int(parts[0])
        amount = Decimal(parts[1])
        if amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await update.message.reply_text("Invalid ID or amount.")
        return
    await db.ensure_user(uid)
    new_bal = await db.adjust_balance(uid, amount)
    await update.message.reply_text(
        f"✅ Credited {config.CURRENCY_SYMBOL}{amount:g} to user {uid}.\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}"
    )


@admin_only
async def cmd_deduct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /deduct USER_ID AMOUNT"""
    parts = (context.args or [])
    if len(parts) != 2:
        await update.message.reply_text("Usage: /deduct USER_ID AMOUNT")
        return
    try:
        uid    = int(parts[0])
        amount = Decimal(parts[1])
        if amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await update.message.reply_text("Invalid ID or amount.")
        return
    new_bal = await db.adjust_balance(uid, -amount)
    await update.message.reply_text(
        f"✅ Deducted {config.CURRENCY_SYMBOL}{amount:g} from user {uid}.\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}"
    )


@admin_only
async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /userinfo USER_ID"""
    parts = (context.args or [])
    if not parts:
        await update.message.reply_text("Usage: /userinfo USER_ID")
        return
    try:
        uid = int(parts[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    info = await db.get_user_info(uid)
    if not info:
        await update.message.reply_text(f"User {uid} not found.")
        return
    await update.message.reply_text(
        _user_info_text(info),
        reply_markup=user_detail_kb(uid, info["banned"]),
        parse_mode="HTML",
    )


@admin_only
@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /broadcast YOUR MESSAGE HERE"""
    if not context.args:
        await update.message.reply_text("Usage: /broadcast YOUR MESSAGE")
        return
    msg      = " ".join(context.args)
    admin_id = update.effective_user.id
    chat_id  = update.effective_chat.id
    await update.message.reply_text("📢 Sending broadcast… Please wait.")
    import asyncio as _asyncio
    _asyncio.create_task(_do_broadcast(context.bot, msg, admin_id, chat_id))


# ============================================================
#  Inline panel router
# ============================================================
@admin_only
async def adm_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data
    uid   = query.from_user.id

    # ---- Home / close ----
    if data == "adm_menu":
        await _safe_edit(query, "🛠️ <b>Admin Panel</b>\n\nChoose a section:",
                         admin_home_kb())

    elif data == "adm_close":
        await query.delete_message()

    # ---- Stats ----
    elif data == "adm_stats":
        s = await db.get_stats()
        text = (
            "📊 <b>Stats</b>\n\n"
            f"👤 Total users:    <b>{s['total_users']}</b>\n"
            f"🚫 Banned users:   <b>{s['banned_users']}</b>\n"
            f"📦 Stock (live):   <b>{s['total_stock']}</b>\n"
            f"✅ Sold items:     <b>{s['sold_stock']}</b>\n"
            f"🛒 Total orders:   <b>{s['total_orders']}</b>\n"
            f"💰 Total revenue:  <b>{config.CURRENCY_SYMBOL}{s['total_revenue']:.2f}</b>\n"
            f"⏳ Pending topups: <b>{s['pending_pays']}</b>"
        )
        await _safe_edit(query, text, back_to_admin())

    # ---- Stock overview ----
    elif data == "adm_stock":
        counts = await db.get_stock_counts()
        # Build overview with Add New Base button per category
        rows = []
        for cat in config.CATEGORIES:
            sublists = db.get_sublists(cat["id"])
            for s in sublists:
                cnt = counts.get(s["id"], 0)
                lbl = db.get_label(f"subl:{s['id']}", s["label"])
                rows.append([InlineKeyboardButton(
                    f"{lbl}  ·  {cnt} items",
                    callback_data=f"adm_slist:{s['id']}",
                )])
            rows.append([InlineKeyboardButton(
                f"➕ Add New Base to {cat['label']}",
                callback_data=f"adm_addbase:{cat['id']}",
            )])
            rows.append([InlineKeyboardButton(
                f"↕️ Reorder {cat['label']} Bases",
                callback_data=f"adm_reorder:{cat['id']}",
            )])
        rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
        await _safe_edit(query,
            "📦 <b>Stock Management</b>\n\nTap a base to manage it:",
            InlineKeyboardMarkup(rows))

    elif data.startswith("adm_reorder:"):
        cat_id = data.split(":", 1)[1]
        cat    = next((c for c in config.CATEGORIES if c["id"] == cat_id), None)
        label  = cat["label"] if cat else cat_id.upper()
        sublists = db.get_sublists(cat_id)
        await _safe_edit(query,
            f"↕️ <b>Reorder Bases — {label}</b>\n\n"
            f"Use ⬆️ ⬇️ to move bases up or down.\n"
            f"Changes appear instantly in the store.",
            reorder_kb(cat_id))

    elif data.startswith("adm_moveup:"):
        _, subl_id, cat_id = data.split(":", 2)
        await db.move_sublist(subl_id, -1)
        cat   = next((c for c in config.CATEGORIES if c["id"] == cat_id), None)
        label = cat["label"] if cat else cat_id.upper()
        await _safe_edit(query,
            f"↕️ <b>Reorder Bases — {label}</b>\n\n"
            f"Use ⬆️ ⬇️ to move bases up or down.\n"
            f"Changes appear instantly in the store.",
            reorder_kb(cat_id))

    elif data.startswith("adm_movedown:"):
        _, subl_id, cat_id = data.split(":", 2)
        await db.move_sublist(subl_id, +1)
        cat   = next((c for c in config.CATEGORIES if c["id"] == cat_id), None)
        label = cat["label"] if cat else cat_id.upper()
        await _safe_edit(query,
            f"↕️ <b>Reorder Bases — {label}</b>\n\n"
            f"Use ⬆️ ⬇️ to move bases up or down.\n"
            f"Changes appear instantly in the store.",
            reorder_kb(cat_id))

    elif data.startswith("adm_lockbase:"):
        subl_id = data.split(":", 1)[1]
        await db.set_sublist_locked(subl_id, True)
        label = _subl_label(subl_id)
        items = await db.get_stock(subl_id)
        await _safe_edit(query,
            f"🔒 <b>{label}</b> is now <b>locked</b>.\n\n"
            "Users who tap this base will see:\n"
            "<i>Database Locked 🔒</i>",
            stock_list_kb(subl_id, items))

    elif data.startswith("adm_unlockbase:"):
        subl_id = data.split(":", 1)[1]
        await db.set_sublist_locked(subl_id, False)
        label = _subl_label(subl_id)
        items = await db.get_stock(subl_id)
        await _safe_edit(query,
            f"🔓 <b>{label}</b> is now <b>unlocked</b>.\n\n"
            "Users can browse this base normally.",
            stock_list_kb(subl_id, items))

    elif data.startswith("adm_renamebase:"):
        subl_id = data.split(":", 1)[1]
        current = _subl_label(subl_id)
        context.user_data["adm_awaiting"]        = "rename_base"
        context.user_data["adm_rename_subl"]     = subl_id
        await _safe_edit(query,
            f"✏️ <b>Rename Base</b>\n\n"
            f"Base:    <code>{subl_id}</code>\n"
            f"Current: <b>{current}</b>\n\n"
            "Send the new display name.\nYou can include emojis e.g. <code>🔸 28th Base</code>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]))

    elif data.startswith("adm_addbase:"):
        cat_id = data.split(":", 1)[1]
        context.user_data["adm_awaiting"]  = "add_base_id"
        context.user_data["adm_base_cat"]  = cat_id
        await _safe_edit(query,
            f"➕ <b>Add New Base to {cat_id.upper()}</b>\n\n"
            "Step 1 of 2 — Send the base <b>ID</b>\n\n"
            "Rules: lowercase, no spaces, hyphens OK\n"
            "Examples: <code>dd-15th</code>  <code>weekly</code>  <code>gold</code>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_stock")
            ]]))

    elif data.startswith("adm_delbase_confirm:"):
        subl_id = data.split(":", 1)[1]
        label   = _subl_label(subl_id)
        items   = await db.get_stock(subl_id)
        await _safe_edit(query,
            f"🗑️ <b>Delete Base: {label}</b>\n\n"
            f"⚠️ This will permanently delete:\n"
            f"• The base itself\n"
            f"• All <b>{len(items)}</b> stock item(s) inside it\n\n"
            "This cannot be undone. Are you sure?",
            InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"🗑️ Yes, Delete {label}",
                    callback_data=f"adm_delbase_do:{subl_id}",
                )],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")],
            ]))

    elif data.startswith("adm_delbase_do:"):
        subl_id = data.split(":", 1)[1]
        label   = _subl_label(subl_id)
        deleted = await db.remove_sublist(subl_id)
        await _safe_edit(query,
            f"✅ <b>Base Deleted</b>\n\n"
            f"Base: <b>{label}</b>\n"
            f"Stock deleted: <b>{deleted}</b> item(s)\n\n"
            "The base has been removed from the store.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("📦 Back to Stock", callback_data="adm_stock")
            ]]))

    elif data.startswith("adm_slist:"):
        subl_id = data.split(":", 1)[1]
        items = await db.get_stock(subl_id)
        label = _subl_label(subl_id)
        text = (
            f"📦 <b>{label}</b>  —  {len(items)} item(s) in stock\n\n"
            "Tap ❌ next to an item to delete it.\n"
            "Add new items with ➕ Add Item.\n\n"
            "<i>Format you'll be asked for:</i>\n"
            "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n"
            "<i>e.g. 459667|2012|Ex3|5|4597...:2025 exp...:123</i>"
        )
        await _safe_edit(query, text, stock_list_kb(subl_id, items))

    elif data.startswith("adm_pricesel:"):
        subl_id = data.split(":", 1)[1]
        context.user_data["adm_price_sel"] = set()
        items  = await db.get_stock(subl_id)
        label  = _subl_label(subl_id)
        await _safe_edit(query,
            f"🏷️ <b>Select Items to Reprice — {label}</b>\n"
            f"<code>──────────────────────</code>\n"
            f"{len(items)} items total · tap to select/deselect:",
            price_select_kb(subl_id, items, set(), page=0))

    elif data.startswith("adm_pstoggle:"):
        parts   = data.split(":")
        item_id = parts[1]
        subl_id = parts[2]
        page    = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        sel = context.user_data.setdefault("adm_price_sel", set())
        if item_id in sel:
            sel.discard(item_id)
        else:
            sel.add(item_id)
        items = await db.get_stock(subl_id)
        label = _subl_label(subl_id)
        n     = len(sel)
        note  = f" · <b>{n}</b> selected" if n else ""
        await _safe_edit(query,
            f"🏷️ <b>Select Items to Reprice — {label}</b>{note}\n"
            f"<code>──────────────────────</code>\n"
            f"{len(items)} items total · tap to select/deselect:",
            price_select_kb(subl_id, items, sel, page=page))

    elif data.startswith("adm_pspage:"):
        _, subl_id, page_s = data.split(":", 2)
        page  = int(page_s) if page_s.isdigit() else 0
        sel   = context.user_data.get("adm_price_sel", set())
        items = await db.get_stock(subl_id)
        label = _subl_label(subl_id)
        n     = len(sel)
        note  = f" · <b>{n}</b> selected" if n else ""
        await _safe_edit(query,
            f"🏷️ <b>Select Items to Reprice — {label}</b>{note}\n"
            f"<code>──────────────────────</code>\n"
            f"{len(items)} items total · tap to select/deselect:",
            price_select_kb(subl_id, items, sel, page=page))

    elif data.startswith("adm_psall:"):
        parts   = data.split(":")
        subl_id = parts[1]
        page    = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        items   = await db.get_stock(subl_id)
        sel     = {it["id"] for it in items}
        context.user_data["adm_price_sel"] = sel
        label   = _subl_label(subl_id)
        await _safe_edit(query,
            f"🏷️ <b>Select Items to Reprice — {label}</b> · <b>{len(sel)}</b> selected\n"
            f"<code>──────────────────────</code>\n"
            "All items selected — tap 💰 Set Price to continue:",
            price_select_kb(subl_id, items, sel, page=page))

    elif data.startswith("adm_psclear:"):
        parts   = data.split(":")
        subl_id = parts[1]
        page    = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        context.user_data["adm_price_sel"] = set()
        items   = await db.get_stock(subl_id)
        label   = _subl_label(subl_id)
        await _safe_edit(query,
            f"🏷️ <b>Select Items to Reprice — {label}</b>\n"
            f"<code>──────────────────────</code>\n"
            "Selection cleared. Tap items to select:",
            price_select_kb(subl_id, items, set(), page=page))

    elif data.startswith("adm_psconfirm:"):
        subl_id = data.split(":", 1)[1]
        sel     = context.user_data.get("adm_price_sel", set())
        if not sel:
            await query.answer("No items selected!", show_alert=True)
            return
        context.user_data["adm_awaiting"]         = "price_selected_items"
        context.user_data["adm_price_sel_subl"]   = subl_id
        n = len(sel)
        await _safe_edit(query,
            f"💰 <b>Set Price for {n} Selected Item{'s' if n > 1 else ''}</b>\n\n"
            "Send the new price (number only):",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_pricesel:{subl_id}")
            ]]))

    elif data.startswith("adm_itemprice:"):
        _, item_id, subl_id = data.split(":", 2)
        item  = await db.get_stock_item(item_id)
        if not item:
            await query.answer("Item not found.", show_alert=True)
            return
        context.user_data["adm_awaiting"]        = "item_price"
        context.user_data["adm_item_price_id"]   = item_id
        context.user_data["adm_item_price_subl"] = subl_id
        label = f"{item['bin']} - {item['year']} - {item['code']}"
        await _safe_edit(query,
            f"✏️ <b>Set Individual Price</b>\n\n"
            f"Item: <code>{label}</code>\n"
            f"Current price: <b>{config.CURRENCY_SYMBOL}{float(item['price']):g}</b>\n\n"
            "Send the new price:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]))

    elif data.startswith("adm_sdel:"):
        item_id = data.split(":", 1)[1]
        item = await db.get_stock_item(item_id)
        if not item:
            await query.answer("Item not found or already sold.", show_alert=True)
            return
        await db.remove_stock_item(item_id)
        await query.answer("✅ Item deleted.", show_alert=False)
        # refresh the list
        subl_id = item["subl_id"]
        items   = await db.get_stock(subl_id)
        label   = _subl_label(subl_id)
        await _safe_edit(
            query,
            f"📦 <b>{label}</b>  —  {len(items)} item(s) in stock",
            stock_list_kb(subl_id, items),
        )

    elif data.startswith("adm_sadd:"):
        subl_id = data.split(":", 1)[1]
        context.user_data["adm_awaiting"] = "add_item"
        context.user_data["adm_subl"]     = subl_id
        label = _subl_label(subl_id)
        await _safe_edit(
            query,
            f"➕ <b>Add Items to {label}</b>\n\n"
            "<b>Auto-generate mode (multiplier):</b>\n"
            "<code>BIN|YEAR|CODE x[COUNT]</code>\n"
            "<code>BIN|YEAR|CODE|PRICE x[COUNT]</code>\n"
            "e.g. <code>459667|2012|Ex3 x10</code> → generates 10 items\n"
            "     <code>459667|2012|Ex3|8 x10</code> → 10 items at £8\n\n"
            "<b>Direct import mode:</b>\n"
            "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n\n"
            "Mix both formats in the same file.\n"
            "Lines starting with <code>#</code> are skipped.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]),
        )

    elif data.startswith("adm_upload_prompt:"):
        subl_id = data.split(":", 1)[1]
        context.user_data["adm_awaiting"]    = "upload_file"
        context.user_data["adm_upload_subl"] = subl_id
        label = _subl_label(subl_id)
        await _safe_edit(
            query,
            f"📤 <b>Upload File → {label}</b>\n\n"
            "Send your <code>.txt</code> or <code>.csv</code> file now.\n\n"
            "<b>Format</b> (one item per line):\n"
            "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n\n"
            "e.g. <code>459667|2012|Ex3|5|4597xx 09/28 123 John Doe</code>\n\n"
            "Comma and tab delimiters also accepted.\n"
            "Lines starting with <code>#</code> and blank lines are skipped.\n"
            "Content may contain the delimiter — everything after the 4th\n"
            "separator is treated as content.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]),
        )

    elif data.startswith("adm_upload_to:"):
        subl_id = data.split(":", 1)[1]
        file_id = context.user_data.pop("adm_pending_file_id", None)
        if not file_id:
            await query.answer("Session expired. Please send the file again.",
                               show_alert=True)
            return
        await query.delete_message()
        fresh = await context.bot.send_message(
            query.from_user.id, "⏳ Processing…"
        )
        await _run_upload(fresh, subl_id, file_id, context)

    elif data.startswith("adm_setprice:"):
        subl_id = data.split(":", 1)[1]
        label   = _subl_label(subl_id)
        items   = await db.get_stock(subl_id)
        context.user_data["adm_awaiting"]      = "set_price"
        context.user_data["adm_price_subl"]    = subl_id
        await _safe_edit(
            query,
            f"💰 <b>Change Price — {label}</b>\n\n"
            f"Currently <b>{len(items)}</b> unsold item(s) in this list.\n\n"
            f"Send the new price (number only, e.g. <code>8</code> for {config.CURRENCY_SYMBOL}8):\n\n"
            "<i>This updates every unsold item in this list at once.</i>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]),
        )

    # ---- Users ----
    elif data == "adm_users":
        context.user_data["adm_awaiting"] = "lookup_user"
        await _safe_edit(
            query,
            "👥 <b>User Lookup</b>\n\nSend the Telegram user ID you want to look up:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    elif data.startswith("adm_ban:"):
        target = int(data.split(":", 1)[1])
        await db.set_banned(target, True)
        await channel_log.user_banned(target, query.from_user.id, True)
        await query.answer("🚫 User banned.", show_alert=True)
        await _refresh_user(query, target)

    elif data.startswith("adm_unban:"):
        target = int(data.split(":", 1)[1])
        await db.set_banned(target, False)
        await channel_log.user_banned(target, query.from_user.id, False)
        await query.answer("✅ User unbanned.", show_alert=True)
        await _refresh_user(query, target)

    elif data.startswith("adm_badd:"):
        target = int(data.split(":", 1)[1])
        context.user_data["adm_awaiting"] = "bal_delta"
        context.user_data["adm_bal_uid"]  = target
        context.user_data["adm_bal_sign"] = "+"
        await _safe_edit(
            query,
            f"➕ <b>Add Balance</b> to user {target}\n\nSend the amount to add (number only):",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    elif data.startswith("adm_bsub:"):
        target = int(data.split(":", 1)[1])
        context.user_data["adm_awaiting"] = "bal_delta"
        context.user_data["adm_bal_uid"]  = target
        context.user_data["adm_bal_sign"] = "-"
        await _safe_edit(
            query,
            f"➖ <b>Deduct Balance</b> from user {target}\n\nSend the amount to deduct:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    # ---- Orders ----
    elif data == "adm_orders":
        orders = await db.get_recent_orders(20)
        if not orders:
            await _safe_edit(query, "📋 No orders yet.", back_to_admin())
            return
        lines = ["📋 <b>Last 20 Orders</b>\n"]
        for o in orders:
            bin_  = o.get("bin") or "?"
            year  = o.get("year") or "?"
            code  = o.get("code") or "?"
            lines.append(
                f"• <code>{o['user_id']}</code>  "
                f"{bin_} - {year} - {code}  "
                f"{config.CURRENCY_SYMBOL}{o['amount']:.2f}  "
                f"<i>{o['created_at'].strftime('%d/%m %H:%M')}</i>"
            )
        await _safe_edit(query, "\n".join(lines), back_to_admin())

    # ---- Prices ----
    elif data == "adm_prices":
        await _safe_edit(query,
            "💰 <b>Price Manager</b>\n\n"
            "Choose how you want to change prices:",
            InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🌍 Global — change ALL items at once",
                    callback_data="adm_price_global",
                )],
                [InlineKeyboardButton(
                    "🔢 By BIN — all items with a specific BIN",
                    callback_data="adm_price_bin",
                )],
                [InlineKeyboardButton(
                    "📋 By Base — all items in one base",
                    callback_data="adm_price_bybase",
                )],
                [InlineKeyboardButton(
                    "🎯 Individual — one specific item",
                    callback_data="adm_price_individual",
                )],
                [InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")],
            ]))

    elif data == "adm_price_global":
        context.user_data["adm_awaiting"] = "global_price"
        counts = await db.get_stock_counts()
        total  = sum(counts.values())
        await _safe_edit(query,
            f"🌍 <b>Global Price</b>\n\n"
            f"This will update <b>{total}</b> unsold items across every base.\n\n"
            "Send the new price (number only):",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_prices")
            ]]))

    elif data == "adm_price_bin":
        context.user_data["adm_awaiting"] = "bin_price_bin"
        await _safe_edit(query,
            "🔢 <b>Price by BIN</b>\n\n"
            "Send the BIN number you want to reprice:\n"
            "e.g. <code>492181</code>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_prices")
            ]]))

    elif data == "adm_price_bybase":
        rows = []
        counts = await db.get_stock_counts()
        for s in db.get_all_sublists():
            cnt   = counts.get(s["id"], 0)
            label = db.get_label(f"subl:{s['id']}", s["label"])
            price_str = ""
            items = await db.get_stock(s["id"])
            if items:
                price_str = f"  ·  {config.CURRENCY_SYMBOL}{float(items[0]['price']):g}"
            rows.append([InlineKeyboardButton(
                f"{label}{price_str}  ·  {cnt} items",
                callback_data=f"adm_setprice:{s['id']}",
            )])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_prices")])
        await _safe_edit(query,
            "📋 <b>Price by Base</b>\n\nTap a base to update its price:",
            InlineKeyboardMarkup(rows))

    elif data == "adm_price_individual":
        context.user_data["adm_awaiting"] = "individual_price_search"
        await _safe_edit(query,
            "🎯 <b>Individual Item Price</b>\n\n"
            "Send the BIN to find the item, then pick which one to reprice:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_prices")
            ]]))

    # ---- Payments ----
    elif data == "adm_payments":
        pending = await db.get_pending_payments()
        if not pending:
            await _safe_edit(query,
                "💳 <b>Payments</b>\n\nNo pending payments right now. ✅",
                back_to_admin())
            return
        lines = [f"💳 <b>Pending Payments</b> ({len(pending)})\n"]
        for p in pending:
            ref = p['tx_ref'].replace("txid:", "").replace("photo:", "📷 ")
            ref = ref[:30] + "…" if len(ref) > 30 else ref
            lines.append(
                f"• <code>{p['payment_id']}</code>\n"
                f"  User: <code>{p['user_id']}</code>  "
                f"{config.CURRENCY_SYMBOL}{p['amount']:.2f} {p['coin']}\n"
                f"  Ref: {ref or 'awaiting proof'}\n"
            )
        rows = []
        for p in pending[:10]:
            rows.append([
                InlineKeyboardButton(
                    f"✅ {config.CURRENCY_SYMBOL}{p['amount']:.2f} – {p['user_id']}",
                    callback_data=f"adm_pay_approve:{p['payment_id']}"),
                InlineKeyboardButton("❌",
                    callback_data=f"adm_pay_reject:{p['payment_id']}"),
            ])
        rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
        await _safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(rows))

    elif data.startswith("adm_pay_approve:"):
        payment_id = data.split(":", 1)[1]
        result = await db.approve_payment(payment_id)
        if not result:
            await query.answer("Already handled or not found.", show_alert=True)
            return
        uid, amt = result["user_id"], result["amount"]
        bal = await db.get_balance(uid)
        await channel_log.payment_approved(uid, float(amt), float(bal), query.from_user.id)
        await query.answer(f"✅ Approved! {config.CURRENCY_SYMBOL}{amt:.2f} credited.", show_alert=True)
        # Notify the user
        try:
            await context.bot.send_message(
                uid,
                f"✅ <b>Top-up Approved!</b>\n\n"
                f"{config.CURRENCY_SYMBOL}{amt:.2f} has been added to your wallet.\n"
                f"New balance: <b>{config.CURRENCY_SYMBOL}{bal:.2f}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        # Refresh payments list
        pending = await db.get_pending_payments()
        if not pending:
            await _safe_edit(query, "💳 <b>Payments</b>\n\nNo pending payments. ✅",
                             back_to_admin())
        else:
            await query.answer()

    elif data.startswith("adm_pay_reject:"):
        payment_id = data.split(":", 1)[1]
        result = await db.reject_payment(payment_id)
        if not result:
            await query.answer("Already handled or not found.", show_alert=True)
            return
        uid = result["user_id"]
        await channel_log.payment_rejected(uid, float(result["amount"]), query.from_user.id)
        await query.answer("❌ Rejected.", show_alert=True)
        try:
            await context.bot.send_message(
                uid,
                f"❌ <b>Top-up Rejected</b>\n\n"
                "Your payment could not be verified.\n"
                f"Please contact {config.SUPPORT_HANDLE} if you believe this is an error.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        pending = await db.get_pending_payments()
        if not pending:
            await _safe_edit(query, "💳 <b>Payments</b>\n\nNo pending payments. ✅",
                             back_to_admin())
        else:
            await query.answer()

    # ---- Labels ----
    elif data == "adm_labels":
        overrides = await db.get_all_label_overrides()
        changed   = len(overrides)
        heading   = (
            "🏷️ <b>Labels</b>\n\n"
            "Tap any label to rename it.\n"
            "🔄 = currently overridden  ↩️ = reset to default\n"
            f"<i>{changed} override(s) active</i>"
        )
        await _safe_edit(query, heading, labels_kb(overrides))

    elif data.startswith("adm_label_edit:"):
        key     = data.split(":", 1)[1]
        default = config.default_label(key)
        current = db.get_label(key, default)
        context.user_data["adm_awaiting"]   = "label_edit"
        context.user_data["adm_label_key"]  = key
        long_text_keys = {"welcome_text", "refund_text", "rules_text", "store_cat_text",
                          "store_subl_text", "store_items_text", "store_select_text"}
        if key in long_text_keys:
            hint = (
                f"📝 <b>Edit Content: {key}</b>\n\n"
                "Send your new text now.\n"
                "✅ Multi-line, emojis, any length\n"
                "✅ HTML bold: <code>&lt;b&gt;text&lt;/b&gt;</code>\n\n"
                f"Current preview:\n<i>{current[:200]}{'...' if len(current) > 200 else ''}</i>"
            )
        elif key.startswith("menu:"):
            hint = (
                f"🏷️ <b>Rename Button: {key}</b>\n\n"
                f"Current name: <b>{current}</b>\n\n"
                "⚠️ <b>This is the BUTTON NAME only</b> — keep it short!\n\n"
                "💡 To change the <b>text shown when users tap this button</b>:\n"
                "Go back → Labels → <b>── Bot Texts ──</b> section at the top\n\n"
                "Send the new button name (max 64 chars):"
            )
        else:
            hint = (
                f"🏷️ <b>Rename Label</b>\n\n"
                f"Key: <code>{key}</code>\n"
                f"Current: <b>{current}</b>\n"
                f"Default: <i>{default}</i>\n\n"
                "Send the new display name now.\n"
                "You can use emojis — e.g. <code>🗂️ Fresh Files</code>"
            )
        await _safe_edit(
            query, hint,
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_labels")
            ]]),
        )

    elif data.startswith("adm_label_reset:"):
        key     = data.split(":", 1)[1]
        default = config.default_label(key)
        await db.reset_label(key)
        await query.answer(f"↩️ Reset to: {default}", show_alert=False)
        overrides = await db.get_all_label_overrides()
        await _safe_edit(
            query,
            "🏷️ <b>Labels</b>\n\n"
            "Tap any label to rename it.\n"
            "🔄 = currently overridden  ↩️ = reset to default\n"
            f"<i>{len(overrides)} override(s) active</i>",
            labels_kb(overrides),
        )

    # ---- Broadcast ----
    elif data == "adm_broadcast":
        context.user_data["adm_awaiting"] = "broadcast_compose"
        await _safe_edit(
            query,
            "📢 <b>Broadcast</b>\n\nType the message you want to send to all users.\n"
            "HTML formatting is supported (<b>bold</b>, <i>italic</i>, <code>code</code>).",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    elif data.startswith("adm_bc_confirm:"):
        msg_key = data.split(":", 1)[1]
        msg     = context.bot_data.get(msg_key, "")
        if not msg:
            await query.answer("Message expired. Please start over.", show_alert=True)
            return
        context.bot_data.pop(msg_key, None)
        admin_id = query.from_user.id
        chat_id  = query.message.chat_id
        await _safe_edit(query, "📢 <b>Sending to all users…</b>\n\nPlease wait.", back_to_admin())
        # Run broadcast in background so it doesn't block or timeout
        import asyncio as _asyncio
        _asyncio.create_task(
            _do_broadcast(context.bot, msg, admin_id, chat_id)
        )

    elif data.startswith("adm_bc_cancel:"):
        msg_key = data.split(":", 1)[1]
        context.bot_data.pop(msg_key, None)
        await _safe_edit(query, "❌ Broadcast cancelled.", admin_home_kb())


# ============================================================
#  Admin text input router
# ============================================================
@admin_only
async def adm_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("adm_awaiting")
    if not awaiting:
        return

    if awaiting == "add_item":
        await _handle_add_item(update, context)
    elif awaiting == "rename_base":
        await _handle_rename_base(update, context)
    elif awaiting == "add_base_id":
        await _handle_add_base_id(update, context)
    elif awaiting == "add_base_label":
        await _handle_add_base_label(update, context)
    elif awaiting == "global_price":
        await _handle_global_price(update, context)
    elif awaiting == "bin_price_bin":
        await _handle_bin_price_bin(update, context)
    elif awaiting == "bin_price_amount":
        await _handle_bin_price_amount(update, context)
    elif awaiting == "individual_price_search":
        await _handle_individual_price_search(update, context)
    elif awaiting == "price_selected_items":
        await _handle_price_selected_items(update, context)
    elif awaiting == "item_price":
        await _handle_item_price(update, context)
    elif awaiting == "lookup_user":
        await _handle_lookup_user(update, context)
    elif awaiting == "bal_delta":
        await _handle_bal_delta(update, context)
    elif awaiting == "broadcast_compose":
        await _handle_broadcast_compose(update, context)
    elif awaiting == "label_edit":
        await _handle_label_edit(update, context)
    elif awaiting == "set_price":
        await _handle_set_price(update, context)
    elif awaiting == "upload_file":
        # File expected — remind admin to send the actual file
        await update.message.reply_text(
            "⚠️ Please send a <code>.txt</code> or <code>.csv</code> file, "
            "not a text message.",
            parse_mode="HTML",
        )


# ============================================================
#  Text sub-handlers
# ============================================================
async def _parse_price(text: str) -> Decimal | None:
    try:
        p = Decimal(text.strip().lstrip(f"£$€{config.CURRENCY_SYMBOL}"))
        return p if p > 0 else None
    except InvalidOperation:
        return None


async def _handle_global_price(update, context) -> None:
    context.user_data["adm_awaiting"] = None
    price = await _parse_price(update.message.text)
    if not price:
        await update.message.reply_text("⚠️ Invalid price. Send a number e.g. <code>30</code>", parse_mode="HTML")
        return
    count = await db.set_global_price(price)
    await update.message.reply_text(
        f"✅ <b>Global Price Updated</b>\n\n"
        f"New price: <b>{config.CURRENCY_SYMBOL}{price:g}</b>\n"
        f"Items updated: <b>{count}</b> (across all bases)",
        parse_mode="HTML",
    )


async def _handle_bin_price_bin(update, context) -> None:
    bin_ = update.message.text.strip()
    if not bin_.isdigit() or len(bin_) < 4:
        await update.message.reply_text("⚠️ Send a valid BIN number, e.g. <code>492181</code>", parse_mode="HTML")
        return
    # Check how many items exist with this BIN
    async with db._pool.acquire() as con:
        count = await con.fetchval(
            "SELECT COUNT(*) FROM stock WHERE bin=$1 AND sold=FALSE", bin_
        )
    if count == 0:
        await update.message.reply_text(f"❌ No unsold items found with BIN <code>{bin_}</code>.", parse_mode="HTML")
        context.user_data["adm_awaiting"] = None
        return
    context.user_data["adm_awaiting"]  = "bin_price_amount"
    context.user_data["adm_price_bin"] = bin_
    await update.message.reply_text(
        f"🔢 BIN <code>{bin_}</code> has <b>{count}</b> unsold items.\n\n"
        f"Send the new price:",
        parse_mode="HTML",
    )


async def _handle_bin_price_amount(update, context) -> None:
    bin_  = context.user_data.pop("adm_price_bin", "")
    context.user_data["adm_awaiting"] = None
    price = await _parse_price(update.message.text)
    if not price:
        await update.message.reply_text("⚠️ Invalid price. Send a number e.g. <code>30</code>", parse_mode="HTML")
        return
    count = await db.set_bin_price(bin_, price)
    await update.message.reply_text(
        f"✅ <b>BIN Price Updated</b>\n\n"
        f"BIN:          <code>{bin_}</code>\n"
        f"New price:    <b>{config.CURRENCY_SYMBOL}{price:g}</b>\n"
        f"Items updated: <b>{count}</b>",
        parse_mode="HTML",
    )


async def _handle_individual_price_search(update, context) -> None:
    bin_ = update.message.text.strip()
    if not bin_.isdigit() or len(bin_) < 4:
        await update.message.reply_text("⚠️ Send a valid BIN number.", parse_mode="HTML")
        return
    # Fetch all items with this BIN
    async with db._pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id,bin,year,code,price,subl_id FROM stock "
            "WHERE bin=$1 AND sold=FALSE ORDER BY added_at LIMIT 20",
            bin_,
        )
    if not rows:
        await update.message.reply_text(f"❌ No unsold items with BIN <code>{bin_}</code>.", parse_mode="HTML")
        context.user_data["adm_awaiting"] = None
        return
    context.user_data["adm_awaiting"] = None
    rows_kb = []
    for r in rows:
        label = f"{r['bin']} - {r['year']} - {r['code']} · {config.CURRENCY_SYMBOL}{float(r['price']):g}"
        rows_kb.append([InlineKeyboardButton(
            f"✏️ {label}",
            callback_data=f"adm_itemprice:{r['id']}:{r['subl_id']}",
        )])
    rows_kb.append([InlineKeyboardButton("❌ Cancel", callback_data="adm_prices")])
    await update.message.reply_text(
        f"🎯 Found <b>{len(rows)}</b> item(s) with BIN <code>{bin_}</code>.\nTap one to set its price:",
        reply_markup=InlineKeyboardMarkup(rows_kb),
        parse_mode="HTML",
    )


async def _handle_price_selected_items(update, context) -> None:
    subl_id  = context.user_data.pop("adm_price_sel_subl", "")
    sel      = context.user_data.pop("adm_price_sel", set())
    context.user_data["adm_awaiting"] = None
    price = await _parse_price(update.message.text)
    if not price:
        await update.message.reply_text(
            "⚠️ Invalid price. Send a number e.g. <code>30</code>", parse_mode="HTML")
        return
    if not sel:
        await update.message.reply_text("No items were selected.")
        return
    # Update each selected item
    updated = 0
    async with db._pool.acquire() as con:
        for item_id in sel:
            result = await con.execute(
                "UPDATE stock SET price=$2 WHERE id=$1 AND sold=FALSE", item_id, price
            )
            updated += int(result.split()[-1])
    label = _subl_label(subl_id)
    items = await db.get_stock(subl_id)
    await update.message.reply_text(
        f"✅ <b>Done!</b>\n\n"
        f"Base:          <b>{label}</b>\n"
        f"Items updated: <b>{updated}</b> of {len(sel)} selected\n"
        f"New price:     <b>{config.CURRENCY_SYMBOL}{price:g}</b>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏷️ Select More", callback_data=f"adm_pricesel:{subl_id}"),
            InlineKeyboardButton("📦 Stock List",  callback_data=f"adm_slist:{subl_id}"),
        ]]),
        parse_mode="HTML",
    )


async def _handle_item_price(update, context) -> None:
    item_id = context.user_data.pop("adm_item_price_id", "")
    subl_id = context.user_data.pop("adm_item_price_subl", "")
    context.user_data["adm_awaiting"] = None
    price = await _parse_price(update.message.text)
    if not price:
        await update.message.reply_text("⚠️ Invalid price. Send a number e.g. <code>30</code>", parse_mode="HTML")
        return
    updated = await db.set_item_price(item_id, price)
    if updated:
        item = await db.get_stock_item(item_id)
        label = f"{item['bin']} - {item['year']} - {item['code']}" if item else item_id
        await update.message.reply_text(
            f"✅ <b>Item Price Updated</b>\n\n"
            f"Item:      <code>{label}</code>\n"
            f"New price: <b>{config.CURRENCY_SYMBOL}{price:g}</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("❌ Item not found or already sold.")


async def _handle_rename_base(update, context) -> None:
    subl_id = context.user_data.pop("adm_rename_subl", "")
    context.user_data["adm_awaiting"] = None
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("❌ Name cannot be empty.")
        return
    old_name = _subl_label(subl_id)
    # Update both: the label override table AND the sublists table label column
    await db.set_label(f"subl:{subl_id}", new_name)
    async with db._pool.acquire() as con:
        await con.execute(
            "UPDATE sublists SET label=$1 WHERE id=$2", new_name, subl_id
        )
    await db._refresh_sublist_cache()
    await update.message.reply_text(
        f"✅ <b>Base Renamed</b>\n\n"
        f"Before: <i>{old_name}</i>\n"
        f"After:  <b>{new_name}</b>\n\n"
        "Live immediately in the store.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Back to Stock", callback_data=f"adm_slist:{subl_id}")
        ]]),
        parse_mode="HTML",
    )


async def _handle_add_base_id(update, context) -> None:
    import re
    raw    = update.message.text.strip().lower()
    cat_id = context.user_data.get("adm_base_cat", "ff")

    # Validate ID
    if not re.match(r'^[a-z0-9][a-z0-9\-]{0,29}$', raw):
        await update.message.reply_text(
            "❌ Invalid ID. Use only lowercase letters, numbers, hyphens.\n"
            "Examples: <code>dd-15th</code>  <code>weekly</code>  <code>vip</code>\n\n"
            "Try again:",
            parse_mode="HTML",
        )
        return
    # Check not already taken
    existing = db.find_sublist_by_id(raw)
    if existing:
        await update.message.reply_text(
            f"❌ A base with ID <code>{raw}</code> already exists.\n"
            "Please choose a different ID:",
            parse_mode="HTML",
        )
        return

    context.user_data["adm_base_id"]      = raw
    context.user_data["adm_awaiting"]     = "add_base_label"
    await update.message.reply_text(
        f"✅ ID set: <code>{raw}</code>\n\n"
        "Step 2 of 2 — Send the <b>display name</b> for this base\n\n"
        "This is what users see in the store.\n"
        "Examples: <code>🔸 DD-15th</code>  <code>⭐ VIP Base</code>",
        parse_mode="HTML",
    )


async def _handle_add_base_label(update, context) -> None:
    label  = update.message.text.strip()
    cat_id = context.user_data.get("adm_base_cat", "ff")
    subl_id = context.user_data.get("adm_base_id", "")
    context.user_data["adm_awaiting"] = None

    if not label:
        await update.message.reply_text("❌ Name cannot be empty. Try again:")
        context.user_data["adm_awaiting"] = "add_base_label"
        return

    success = await db.add_sublist(subl_id, cat_id, label)
    if not success:
        await update.message.reply_text(
            f"❌ Could not create base <code>{subl_id}</code> — ID may already exist.",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        f"✅ <b>Base Created!</b>\n\n"
        f"ID:       <code>{subl_id}</code>\n"
        f"Name:     <b>{label}</b>\n"
        f"Category: {cat_id.upper()}\n\n"
        "It's now live in the store. Use /upload or ➕ Add Item to add stock.\n"
        "Use /rename to rename it anytime.",
        parse_mode="HTML",
    )


async def _handle_add_item(update, context) -> None:
    subl_id = context.user_data.get("adm_subl", "")
    raw     = update.message.text.strip()
    # Fetch live price first — uploads never reset it
    current_price = await db.get_sublist_price(subl_id)
    rows, skipped, errors = _parse_stock_file(raw, subl_id, current_price)

    context.user_data["adm_awaiting"] = None
    added, duplicate = 0, 0
    if rows:
        result    = await db.bulk_add_stock_items(rows)
        added     = result["inserted"]
        duplicate = result["duplicate"]

    items  = await db.get_stock(subl_id)
    label  = _subl_label(subl_id)
    msg    = f"✅ Added <b>{added}</b> item(s)."
    if duplicate:
        msg += f"  ♻️ {duplicate} duplicate(s) skipped."
    if skipped:
        msg += f"  ⚠️ {skipped} line(s) skipped (bad format)."
    if errors:
        msg += "\n\nSample bad lines:\n" + "\n".join(f"  • {e}" for e in errors)
    await update.message.reply_text(
        f"{msg}\n\n📦 <b>{label}</b> now has <b>{len(items)}</b> item(s) in stock.",
        reply_markup=stock_list_kb(subl_id, items),
        parse_mode="HTML",
    )


async def _handle_lookup_user(update, context) -> None:
    context.user_data["adm_awaiting"] = None
    raw = update.message.text.strip()
    try:
        uid = int(raw)
    except ValueError:
        await update.message.reply_text("Please send a numeric user ID.")
        return
    info = await db.get_user_info(uid)
    if not info:
        await update.message.reply_text(f"User {uid} not found in the database.")
        return
    await update.message.reply_text(
        _user_info_text(info),
        reply_markup=user_detail_kb(uid, info["banned"]),
        parse_mode="HTML",
    )


async def _handle_bal_delta(update, context) -> None:
    uid  = context.user_data.get("adm_bal_uid")
    sign = context.user_data.get("adm_bal_sign", "+")
    context.user_data["adm_awaiting"] = None
    try:
        amount = Decimal(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await update.message.reply_text("Please send a positive number.")
        return
    delta   = amount if sign == "+" else -amount
    new_bal = await db.adjust_balance(uid, delta)
    await channel_log.balance_adjusted(uid, update.effective_user.id, float(delta), float(new_bal))
    verb    = "added to" if sign == "+" else "deducted from"
    await update.message.reply_text(
        f"✅ {config.CURRENCY_SYMBOL}{amount:g} {verb} user {uid}.\n"
        f"New balance: <b>{config.CURRENCY_SYMBOL}{new_bal:.2f}</b>",
        parse_mode="HTML",
    )


async def _handle_set_price(update, context) -> None:
    subl_id = context.user_data.pop("adm_price_subl", "")
    context.user_data["adm_awaiting"] = None
    raw = update.message.text.strip().lstrip(config.CURRENCY_SYMBOL)
    try:
        price = Decimal(raw)
        if price <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await update.message.reply_text(
            "⚠️ Please send a positive number, e.g. <code>8</code>",
            parse_mode="HTML",
        )
        return
    count   = await db.set_sublist_price(subl_id, price)
    label   = _subl_label(subl_id)
    items   = await db.get_stock(subl_id)
    await update.message.reply_text(
        f"✅ <b>Price Updated — {label}</b>\n\n"
        f"New price: <b>{config.CURRENCY_SYMBOL}{price:g}</b>\n"
        f"Items updated: <b>{count}</b>\n"
        f"Total in stock: {len(items)}",
        reply_markup=stock_list_kb(subl_id, items),
        parse_mode="HTML",
    )
    context.user_data["adm_awaiting"] = None
    msg     = update.message.text.strip()
    msg_key = f"bc_{update.message.message_id}"
    context.bot_data[msg_key] = msg

    preview = msg[:300] + ("…" if len(msg) > 300 else "")
    user_count = len(await db.get_all_user_ids())

    await update.message.reply_text(
        f"📢 <b>Broadcast Preview</b>\n\n"
        f"{preview}\n\n"
        f"This will be sent to <b>{user_count}</b> user(s). Confirm?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Send", callback_data=f"adm_bc_confirm:{msg_key}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_bc_cancel:{msg_key}"),
            ]
        ]),
        parse_mode="HTML",
    )


async def _handle_label_edit(update, context) -> None:
    key  = context.user_data.pop("adm_label_key", None)
    context.user_data["adm_awaiting"] = None
    if not key:
        return
    new_value = update.message.text.strip()
    if not new_value:
        await update.message.reply_text("Name cannot be empty. No changes made.")
        return
    # Only button labels (menu:, cat:, subl:) have a 64-char limit
    # Everything else (text content, rules, welcome) has NO limit
    is_button = any(key.startswith(p) for p in ("menu:", "cat:", "subl:"))
    if is_button and len(new_value) > 64:
        await update.message.reply_text(
            f"⚠️ Button labels must be under 64 characters.\n"
            f"Your text was {len(new_value)} characters. Try a shorter name.",
        )
        context.user_data["adm_awaiting"]  = "label_edit"
        context.user_data["adm_label_key"] = key
        return
    await db.set_label(key, new_value)
    if is_button:
        overrides = await db.get_all_label_overrides()
        await update.message.reply_text(
            f"✅ <b>{key}</b> renamed to: <b>{new_value}</b>\n\nLive immediately.",
            reply_markup=labels_kb(overrides),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"✅ Updated! Live immediately.",
            parse_mode="HTML",
        )


# ============================================================
#  /rename quick command
# ============================================================
@admin_only
async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /price all 30             — global price (all items)
    /price bin:459647 35      — all items with that BIN
    /price dd-28th 30         — all items in a base (ID or name)
    /price                    — show all options
    """
    args = context.args or []
    if len(args) < 2:
        lines = [
            "💰 <b>Price Commands</b>\n",
            "<code>/price all 30</code> — set price for every item",
            "<code>/price bin:459647 35</code> — all items with BIN 459647",
            "<code>/price dd-28th 30</code> — all items in a base\n",
            "Current bases:",
        ]
        for s in db.get_all_sublists():
            current = db.get_label(f"subl:{s['id']}", s["label"])
            lines.append(f"  <code>{s['id']}</code> — {current}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    target    = args[0].lower()
    price_raw = args[-1].lstrip(f"£$€{config.CURRENCY_SYMBOL}")
    try:
        price = Decimal(price_raw)
        if price <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await update.message.reply_text("⚠️ Invalid price. Example: <code>/price all 30</code>", parse_mode="HTML")
        return

    # ── Global ──
    if target == "all":
        count = await db.set_global_price(price)
        await update.message.reply_text(
            f"✅ <b>Global price set to {config.CURRENCY_SYMBOL}{price:g}</b>\n"
            f"Items updated: <b>{count}</b>",
            parse_mode="HTML",
        )
        return

    # ── By BIN ──
    if target.startswith("bin:"):
        bin_ = target[4:]
        count = await db.set_bin_price(bin_, price)
        await update.message.reply_text(
            f"✅ BIN <code>{bin_}</code> → <b>{config.CURRENCY_SYMBOL}{price:g}</b>\n"
            f"Items updated: <b>{count}</b>",
            parse_mode="HTML",
        )
        return

    # ── By Base (ID or label) ──
    matched_id = None
    for s in db.get_all_sublists():
        if s["id"] == target:
            matched_id = s["id"]; break
        current = db.get_label(f"subl:{s['id']}", s["label"])
        import re as _re
        if _re.sub(r'[^\w\s-]', '', current).strip().lower() == \
           _re.sub(r'[^\w\s-]', '', target).strip().lower():
            matched_id = s["id"]; break

    if not matched_id:
        await update.message.reply_text(
            f"❌ Unknown target: <code>{target}</code>\n"
            "Use <code>all</code>, <code>bin:XXXXXX</code>, or a base ID.",
            parse_mode="HTML",
        )
        return

    count = await db.set_sublist_price(matched_id, price)
    label = _subl_label(matched_id)
    await update.message.reply_text(
        f"✅ <b>{label}</b> → <b>{config.CURRENCY_SYMBOL}{price:g}</b>\n"
        f"Items updated: <b>{count}</b>",
        parse_mode="HTML",
    )

    """
    Usage:  /rename KEY New display name
    Examples:
      /rename subl:dd-28th 🔸 28th Base
      /rename cat:ff 🗓️ Fresh Files
      /rename menu:store 🏪 Shop
    Run /rename with no arguments to see all valid keys.
    """
    args = context.args or []

    # No args → show all valid keys + current values
    if not args:
        lines = ["🏷️ <b>Renameable Labels</b>\n",
                 "Usage: <code>/rename KEY New Name</code>\n"]
        for key, default in config.RENAMEABLE.items():
            current = db.get_label(key, default)
            changed = " 🔄" if current != default else ""
            lines.append(f"<code>{key}</code>{changed}\n  → {current}")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )
        return

    key       = args[0].lower()
    new_value = " ".join(args[1:]).strip()

    if key not in config.RENAMEABLE:
        valid = "\n".join(f"  <code>{k}</code>" for k in config.RENAMEABLE)
        await update.message.reply_text(
            f"❌ Unknown key: <code>{key}</code>\n\n"
            f"Valid keys:\n{valid}",
            parse_mode="HTML",
        )
        return

    if not new_value:
        await update.message.reply_text(
            "Please provide the new name after the key.\n"
            f"Example: <code>/rename {key} 🔸 New Name</code>",
            parse_mode="HTML",
        )
        return



    old_value = db.get_label(key, config.default_label(key))
    await db.set_label(key, new_value)
    await update.message.reply_text(
        f"✅ Renamed <code>{key}</code>\n"
        f"  Before: <i>{old_value}</i>\n"
        f"  After:  <b>{new_value}</b>\n\n"
        "Live immediately — no restart needed.",
        parse_mode="HTML",
    )


# ============================================================
#  Helpers
# ============================================================
def _subl_label(subl_id: str) -> str:
    s = db.find_sublist_by_id(subl_id)
    if s:
        return db.get_label(f"subl:{subl_id}", s["label"])
    return db.get_label(f"subl:{subl_id}", subl_id)


def _find_subl_by_name(text: str) -> str | None:
    """
    Match free text to a sublist ID.
    Tries exact ID match first, then partial label match.
    e.g. "dd-28th" → "dd-28th"  |  "DD28" → "dd-28th"  |  "28th" → "dd-28th"
    """
    if not text:
        return None
    lower = text.lower().strip()
    all_subls = [s for cat in config.CATEGORIES for s in cat.get("sublists", [])]
    # Exact ID
    for s in all_subls:
        if s["id"] == lower:
            return s["id"]
    # Partial ID
    for s in all_subls:
        if lower in s["id"] or s["id"] in lower:
            return s["id"]
    # Partial label (strip emoji)
    for s in all_subls:
        clean = s["label"].encode("ascii", "ignore").decode().lower().strip()
        if lower in clean or clean in lower:
            return s["id"]
    return None


def _user_info_text(info: dict) -> str:
    joined = info["joined"].strftime("%d %b %Y") if info.get("joined") else "?"
    status = "🚫 BANNED" if info["banned"] else "✅ Active"
    return (
        f"👤 <b>User {info['user_id']}</b>\n\n"
        f"Status:   {status}\n"
        f"Balance:  <b>{config.CURRENCY_SYMBOL}{info['balance']:.2f}</b>\n"
        f"Orders:   {info['orders']}\n"
        f"Spent:    {config.CURRENCY_SYMBOL}{info['spent']:.2f}\n"
        f"Joined:   {joined}"
    )


async def _refresh_user(query, user_id: int) -> None:
    info = await db.get_user_info(user_id)
    if info:
        await _safe_edit(query, _user_info_text(info),
                         user_detail_kb(user_id, info["banned"]))


async def _safe_edit(query, text, reply_markup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise



# ============================================================
#  File parser
# ============================================================
def _detect_delimiter(line: str) -> str:
    for sep in ("|", ",", "\t"):
        if len(line.split(sep)) >= 3:
            return sep
    return "|"


def _generate_items(bin_: str, price: Decimal,
                    subl_id: str, count: int) -> list[tuple]:
    rows = []
    for _ in range(count):
        year    = str(random.randint(1930, 1988))
        code    = random.choice(UK_OUTCODES)
        content = f"{bin_}|{year}|{code}"
        rows.append((uuid.uuid4().hex[:8], subl_id, bin_, year, code, price, content))
    return rows


def _parse_stock_file(raw: str, subl_id: str,
                      current_price: Decimal | None = None) -> tuple[list[tuple], int, list[str]]:
    """
    PRICE RULE: If upload line has no price → use current_price (list live price).
    This means uploading NEVER resets a list's price.

    FORMAT A  BIN|SEED|CODE x[N]         generate N items at current_price
    FORMAT A  BIN|SEED|CODE|PRICE x[N]   generate N items at PRICE
    FORMAT B  BIN|SEED|CODE              generate 1 item  at current_price  (no multiplier)
    FORMAT B  BIN|SEED|CODE|PRICE        generate 1 item  at PRICE
    FORMAT C  BIN|YEAR|CODE|PRICE|DATA   direct import, 5+ fields, no generation
    """
    lines      = [l.rstrip() for l in raw.splitlines()]
    data_lines = [l for l in lines if l.strip() and not l.startswith("#")]
    if not data_lines:
        return [], 0, []

    sep = _detect_delimiter(data_lines[0])
    fallback = current_price if current_price else Decimal("5")

    rows: list[tuple] = []
    skipped  = 0
    errors: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        mult_match = re.search(r'\bx(\d+)\s*$', line, re.IGNORECASE)
        parts_raw  = [p.strip() for p in line.split(sep)]

        if mult_match:
            count = int(mult_match.group(1))
            base  = line[:mult_match.start()].rstrip()
            parts = [p.strip() for p in base.split(sep)]
            use_gen = True
        elif len(parts_raw) >= 5:
            use_gen = False
            parts   = parts_raw
            count   = 1
        else:
            count   = 1
            parts   = parts_raw
            use_gen = True

        bin_ = parts[0] if parts else ""
        if not bin_.isdigit() or len(bin_) < 4:
            skipped += count if use_gen else 1
            if len(errors) < 3:
                errors.append(f"Bad BIN: <code>{line[:50]}</code>")
            continue

        if use_gen:
            price = fallback
            if len(parts) >= 4:
                try:
                    p = Decimal(parts[3].lstrip("£$€"))
                    if p > 0:
                        price = p
                except InvalidOperation:
                    pass
            rows.extend(_generate_items(bin_, price, subl_id, count))
        else:
            year    = parts[1]
            code    = parts[2]
            content = sep.join(parts[4:]).strip()
            if not content:
                skipped += 1
                continue
            try:
                price = Decimal(parts[3].lstrip("£$€"))
            except InvalidOperation:
                skipped += 1
                if len(errors) < 3:
                    errors.append(f"Bad price: <code>{line[:50]}</code>")
                continue
            rows.append((uuid.uuid4().hex[:8], subl_id, bin_, year, code, price, content))

    return rows, skipped, errors
async def _run_upload(message, subl_id: str, file_id: str, context) -> None:
    """Download the file, parse it, bulk-insert, reply with a report."""
    label = _subl_label(subl_id)
    status_msg = await message.reply_text(
        f"⏳ Downloading and parsing file for <b>{label}</b>…",
        parse_mode="HTML",
    )

    try:
        tg_file = await context.bot.get_file(file_id)
        raw_bytes = await tg_file.download_as_bytearray()
        # Decode — try UTF-8, fall back to latin-1.
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("latin-1")
    except Exception as exc:
        logger.exception("File download failed")
        await status_msg.edit_text(f"❌ Could not download the file: {exc}")
        return

    rows, skipped, sample_errors = _parse_stock_file(
        raw_text, subl_id, await db.get_sublist_price(subl_id)
    )

    if not rows and skipped == 0:
        await status_msg.edit_text(
            "⚠️ The file appears empty or contains no parseable lines.",
        )
        return

    result = await db.bulk_add_stock_items(rows)
    inserted  = result["inserted"]
    duplicate = result["duplicate"]
    total_now = len(await db.get_stock(subl_id))

    report_lines = [
        f"📤 <b>Upload complete — {label}</b>\n",
        f"✅ Inserted:    <b>{inserted}</b>",
        f"♻️ Duplicates:  <b>{duplicate}</b>",
        f"⚠️ Parse errors: <b>{skipped}</b>",
        f"📦 Total in stock now: <b>{total_now}</b>",
    ]
    if sample_errors:
        report_lines.append("\nSample bad lines (up to 3):")
        report_lines += [f"  • {e}" for e in sample_errors]

    await status_msg.edit_text("\n".join(report_lines), parse_mode="HTML")


# ============================================================
#  /upload command
# ============================================================
@admin_only
@admin_only
async def cmd_testlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a test message to the log channel to verify it's working."""
    if not config.LOG_CHANNEL_ID:
        await update.message.reply_text(
            "⚠️ <b>LOG_CHANNEL_ID is not set.</b>\n\n"
            "Add it in Railway → Variables:\n"
            "<code>LOG_CHANNEL_ID = -1001234567890</code>\n"
            "or <code>LOG_CHANNEL_ID = @YourChannel</code>",
            parse_mode="HTML",
        )
        return
    try:
        await context.bot.send_message(
            config.LOG_CHANNEL_ID,
            f"🔔 <b>Log Channel Test</b>\n\n"
            f"✅ Connected successfully!\n"
            f"Bot is delivering notifications to this channel.\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}",
            parse_mode="HTML",
        )
        await update.message.reply_text(
            f"✅ Test message sent to <code>{config.LOG_CHANNEL_ID}</code>.\n"
            "Check your log channel — it should have just received a message.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Failed to send to log channel</b>\n\n"
            f"Channel: <code>{config.LOG_CHANNEL_ID}</code>\n"
            f"Error: <code>{e}</code>\n\n"
            "Common fixes:\n"
            "1. Make sure the bot is added as <b>Admin</b> in the channel\n"
            "2. Check the channel ID format (use @username or -100XXXXXXXXXX)\n"
            "3. Forward a message from the channel to @userinfobot to get the correct ID",
            parse_mode="HTML",
        )
    """
    Usage:  /rename KEY New display name
    Examples:
      /rename subl:dd-28th 🔸 28th Base
      /rename cat:ff 🗓️ Fresh Files
      /rename menu:store 🏪 Shop
    Run /rename with no arguments to see all valid keys.
    """
    args = context.args or []

    if not args:
        lines = ["🏷️ <b>Renameable Labels</b>\n",
                 "Usage: <code>/rename KEY New Name</code>\n"]
        for key, default in config.RENAMEABLE.items():
            current = db.get_label(key, default)
            changed = " 🔄" if current != default else ""
            lines.append(f"<code>{key}</code>{changed}\n  → {current}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    key       = args[0].lower()
    new_value = " ".join(args[1:]).strip()

    if key not in config.RENAMEABLE:
        valid = "\n".join(f"  <code>{k}</code>" for k in config.RENAMEABLE)
        await update.message.reply_text(
            f"❌ Unknown key: <code>{key}</code>\n\nValid keys:\n{valid}",
            parse_mode="HTML",
        )
        return

    if not new_value:
        await update.message.reply_text(
            f"Please provide the new name.\nExample: <code>/rename {key} 🔸 New Name</code>",
            parse_mode="HTML",
        )
        return



    old_value = db.get_label(key, config.default_label(key))
    await db.set_label(key, new_value)
    await update.message.reply_text(
        f"✅ Renamed <code>{key}</code>\n"
        f"  Before: <i>{old_value}</i>\n"
        f"  After:  <b>{new_value}</b>\n\n"
        "Live immediately — no restart needed.",
        parse_mode="HTML",
    )


@admin_only
async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /rename KEY New name  — run /rename alone to see all keys."""
    args = context.args or []
    if not args:
        lines = ["🏷️ <b>Renameable Labels</b>\n",
                 "Usage: <code>/rename KEY New Name</code>\n"]
        for key, default in config.RENAMEABLE.items():
            current = db.get_label(key, default)
            changed = " 🔄" if current != default else ""
            lines.append(f"<code>{key}</code>{changed}\n  → {current}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return
    key       = args[0].lower()
    new_value = " ".join(args[1:]).strip()
    if key not in config.RENAMEABLE:
        valid = "\n".join(f"  <code>{k}</code>" for k in config.RENAMEABLE)
        await update.message.reply_text(
            f"❌ Unknown key: <code>{key}</code>\n\nValid keys:\n{valid}",
            parse_mode="HTML")
        return
    if not new_value:
        await update.message.reply_text(
            f"Please provide the new name.\nExample: <code>/rename {key} 🔸 New Name</code>",
            parse_mode="HTML")
        return

    old_value = db.get_label(key, config.default_label(key))
    await db.set_label(key, new_value)
    await update.message.reply_text(
        f"✅ Renamed <code>{key}</code>\n"
        f"  Before: <i>{old_value}</i>\n"
        f"  After:  <b>{new_value}</b>\n\n"
        "Live immediately — no restart needed.",
        parse_mode="HTML")


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /upload SUBL_ID  — bot will then wait for a file."""
    parts = context.args or []
    if not parts:
        await update.message.reply_text(
            "Usage: /upload <b>LIST_ID</b>\n"
            "Then send your .txt or .csv file as the next message.\n\n"
            "Available list IDs:\n" +
            "\n".join(
                f"  <code>{s['id']}</code>  {s['label']}"
                for cat in config.CATEGORIES
                for s in cat.get("sublists", [])
            ),
            parse_mode="HTML",
        )
        return
    subl_id = parts[0].lower()
    # Validate it exists
    valid = [s["id"] for cat in config.CATEGORIES for s in cat.get("sublists", [])]
    if subl_id not in valid:
        await update.message.reply_text(
            f"❌ Unknown list ID: <code>{subl_id}</code>\n"
            "Valid IDs: " + ", ".join(f"<code>{i}</code>" for i in valid),
            parse_mode="HTML",
        )
        return
    context.user_data["adm_awaiting"]    = "upload_file"
    context.user_data["adm_upload_subl"] = subl_id
    label = _subl_label(subl_id)
    await update.message.reply_text(
        f"📤 Ready to import into <b>{label}</b>.\n\n"
        "Now send your <code>.txt</code> or <code>.csv</code> file.\n\n"
        "<b>Required format</b> (one item per line):\n"
        "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n"
        "e.g. <code>459667|2012|Ex3|5|4597xx 09/28 123 John Doe</code>\n\n"
        "Comma and tab delimiters are also accepted.\n"
        "Lines starting with <code>#</code> and blank lines are skipped.",
        parse_mode="HTML",
    )


# ============================================================
#  Document (file) handler
# ============================================================
@admin_only
async def adm_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles .txt / .csv file uploads from admin for bulk stock import."""
    doc = update.message.document
    if not doc:
        return

    # Accept only text-like files.
    fname = (doc.file_name or "").lower()
    mime  = (doc.mime_type or "").lower()
    is_text = (fname.endswith(".txt") or fname.endswith(".csv")
               or "text" in mime or mime == "application/octet-stream")
    if not is_text:
        await update.message.reply_text(
            "⚠️ Please send a <code>.txt</code> or <code>.csv</code> file.",
            parse_mode="HTML",
        )
        return

    # Size guard — reject files over 5 MB to avoid memory issues.
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("⚠️ File too large (max 5 MB).")
        return

    caption = (update.message.caption or "").strip().lower()

    # 1. Admin used /upload LIST_ID and is now sending the file.
    if context.user_data.get("adm_awaiting") == "upload_file":
        subl_id = context.user_data.pop("adm_upload_subl", "")
        context.user_data["adm_awaiting"] = None
        await _run_upload(update.message, subl_id, doc.file_id, context)
        return

    # 2. Caption matches a list ID directly — e.g. file sent with caption "dd-28th".
    subl_id = _find_subl_by_name(caption)
    if subl_id:
        await _run_upload(update.message, subl_id, doc.file_id, context)
        return

    # 3. No hint — store file_id and show list picker.
    context.user_data["adm_pending_file_id"] = doc.file_id
    await update.message.reply_text(
        "📂 File received.\n\n"
        "Which list should this be imported into?\n"
        "<i>Tip: next time add the list ID as the file caption to skip this step.</i>",
        reply_markup=upload_list_picker_kb(),
        parse_mode="HTML",
    )


async def _handle_broadcast_compose(update, context) -> None:
    """Admin typed the broadcast message — show preview with Confirm/Cancel."""
    context.user_data["adm_awaiting"] = None
    msg = update.message.text.strip()
    if not msg:
        await update.message.reply_text("Message cannot be empty.")
        return

    # Store message in bot_data with a unique key (avoids callback_data length limit)
    import uuid as _uuid
    msg_key = _uuid.uuid4().hex[:12]
    context.bot_data[msg_key] = msg

    user_ids = await db.get_all_user_ids()
    count    = len(user_ids)

    await update.message.reply_text(
        f"📢 <b>Broadcast Preview</b>\n"
        f"<code>──────────────────────</code>\n"
        f"{msg}\n"
        f"<code>──────────────────────</code>\n"
        f"Recipients: <b>{count}</b> users\n\n"
        "Confirm to send to all users?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Send to All",  callback_data=f"adm_bc_confirm:{msg_key}")],
            [InlineKeyboardButton("❌ Cancel",        callback_data=f"adm_bc_cancel:{msg_key}")],
        ]),
        parse_mode="HTML",
    )


async def _do_broadcast(bot, msg: str, admin_id: int, chat_id: int) -> None:
    """Send message to all users. Runs as background task. Reports result to admin."""
    try:
        user_ids = await db.get_all_user_ids()
        ok, fail = 0, 0
        for uid in user_ids:
            try:
                await bot.send_message(uid, msg, parse_mode="HTML")
                ok += 1
            except Exception:
                fail += 1
        # Send result to admin
        await bot.send_message(
            chat_id,
            f"📢 <b>Broadcast Complete</b>\n\n"
            f"✅ Sent:   <b>{ok}</b>\n"
            f"❌ Failed: <b>{fail}</b>\n"
            f"Total:    <b>{len(user_ids)}</b> users",
            parse_mode="HTML",
        )
        await channel_log.broadcast_sent(admin_id, ok, fail)
    except Exception as e:
        logger.error("Broadcast error: %s", e)
        try:
            await bot.send_message(chat_id, f"⚠️ Broadcast error: {e}")
        except Exception:
            pass


# ============================================================
#  Register all handlers with the Application
# ============================================================
def register_admin_handlers(app: Application) -> None:
    # Commands
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("credit",    cmd_credit))
    app.add_handler(CommandHandler("deduct",    cmd_deduct))
    app.add_handler(CommandHandler("userinfo",  cmd_userinfo))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("upload",    cmd_upload))
    app.add_handler(CommandHandler("rename",    cmd_rename))
    app.add_handler(CommandHandler("price",     cmd_price))
    app.add_handler(CommandHandler("testlog",   cmd_testlog))

    # All adm_ callbacks — must run BEFORE the general on_button handler
    app.add_handler(CallbackQueryHandler(
        adm_button, pattern=r"^adm_"
    ))

    # File uploads from admin — group 1 so it never blocks user messages in group 0
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.UpdateType.MESSAGE,
        adm_document,
    ), group=1)

    # NOTE: admin TEXT input is NOT registered here.
    # bot.py's on_text() already routes to adm_text() when adm_awaiting is set.
    # Registering it here caused ALL user text messages to be silently swallowed.
