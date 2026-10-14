"""Tiny synthetic road network for the Valencia DANA story.

Place names match towns south of Valencia. Coordinates are approximate so the
map is readable. Travel times, capacities, and the topology are synthetic:
this is not an OpenStreetMap extract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    osmid: int
    name: str
    lat: float
    lon: float
    district: str
    role: str  # junction | hospital | shelter | depot


@dataclass(frozen=True)
class Road:
    road_id: str
    name: str
    a: int
    b: int
    travel_s: int
    capacity: int


@dataclass(frozen=True)
class Facility:
    id: str
    name: str
    osmid: int
    beds_total: int
    beds_free: int
    er_open: bool


@dataclass(frozen=True)
class ShelterSite:
    id: str
    name: str
    osmid: int
    capacity: int
    occupancy: int


PLACES: tuple[Place, ...] = (
    Place(1001, "Base Sur", 39.4580, -0.3820, "Valencia", "depot"),
    Place(1003, "Hospital Doctor Peset", 39.4540, -0.3960, "Valencia", "hospital"),
    Place(1004, "Paiporta north", 39.4340, -0.4180, "Paiporta", "junction"),
    Place(1005, "Paiporta south", 39.4260, -0.4170, "Paiporta", "junction"),
    Place(1006, "Catarroja", 39.4020, -0.4040, "Catarroja", "junction"),
    Place(1007, "Alfafar", 39.4220, -0.3910, "Alfafar", "shelter"),
    Place(1008, "Sedaví", 39.4270, -0.3850, "Sedaví", "junction"),
    Place(1009, "Benetússer", 39.4400, -0.3970, "Benetússer", "junction"),
    Place(1010, "Massanassa", 39.4100, -0.3990, "Massanassa", "junction"),
    Place(1011, "Hospital La Fe", 39.4700, -0.3700, "Valencia", "hospital"),
)

ROADS: tuple[Road, ...] = (
    Road("v31-paiporta-bridge", "V-31 Paiporta bridge", 1005, 1004, 35, 600),
    Road("cv-400", "CV-400", 1004, 1009, 80, 800),
    Road("gaspar-aguilar", "Av. Gaspar Aguilar", 1009, 1003, 100, 700),
    Road("cami-poyo", "Camí del Poyo", 1005, 1007, 60, 400),
    Road("cv-36", "CV-36", 1007, 1008, 90, 450),
    Road("ronda-sud", "Ronda Sud", 1008, 1003, 220, 500),
    Road("catarroja-link", "Camí de Catarroja", 1006, 1010, 80, 500),
    Road("massanassa-paiporta", "Massanassa link", 1010, 1005, 70, 500),
    Road("cv-33", "CV-33", 1006, 1007, 110, 350),
    Road("base-access", "Base access", 1001, 1003, 50, 900),
    Road("via-local", "Carrer de la Via", 1009, 1008, 70, 400),
    Road("peset-lafe", "Peset–La Fe connector", 1003, 1011, 200, 700),
    Road("base-lafe", "Base–La Fe", 1001, 1011, 180, 700),
)

HOSPITALS: tuple[Facility, ...] = (
    Facility("peset", "Hospital Doctor Peset", 1003, 40, 12, True),
    Facility("lafe", "Hospital La Fe", 1011, 80, 30, True),
)

SHELTERS: tuple[ShelterSite, ...] = (
    ShelterSite("alfafar-sports", "Alfafar shelter (synthetic)", 1007, 800, 120),
)

DISTRICTS: tuple[dict, ...] = (
    {"name": "Paiporta", "population": 25000, "zone_id": "horta-sud"},
)

IN_DISTRICT: dict[int, str] = {1004: "Paiporta", 1005: "Paiporta"}

# Ambulance A-7 is south of the bridge, transporting toward the nearest open ER.
UNIT_CALLSIGN = "A-7"
UNIT_OSMID = 1005
CLOSURE_ROAD_ID = "v31-paiporta-bridge"
DEMO_REPORT = (
    "Citizen report: the Paiporta bridge on V-31 is under water and not passable."
)


def place(osmid: int) -> Place:
    for node in PLACES:
        if node.osmid == osmid:
            return node
    raise KeyError(osmid)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return int(round(2 * radius * math.asin(math.sqrt(a))))


def road_geometry(road: Road) -> dict:
    a, b = place(road.a), place(road.b)
    length_m = haversine_m(a.lat, a.lon, b.lat, b.lon)
    speed_kph = round(length_m / road.travel_s * 3.6, 1)
    return {"length_m": length_m, "speed_kph": speed_kph}
