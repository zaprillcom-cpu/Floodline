# Data sources and licenses

Two road networks can be loaded. The Valencia corridor extract is OpenStreetMap. The 10-node graph is synthetic, and it is what `python -m floodline.demo` resets to.

| Data in this slice | Source | License |
|---|---|---|
| 10 intersections, 13 undirected roads, travel times, capacities | Written for Floodline | MIT, same as the code |
| Place names (Paiporta, Catarroja, Alfafar, Sedaví, Benetússer, Massanassa, Hospital Doctor Peset, Hospital La Fe) | Real place names, used as labels | Names only. Coordinates are approximate and chosen so the schematic map is readable. They are not an OSM extract. |
| Ambulance A-7, bed counts, the citizen report, the 14:01/14:02 clock | Synthetic | MIT. The clock is **reconstructed**. It is not the real DANA timeline. |
| District population `25000` on `Paiporta` | Synthetic estimate | Property `population_source = 'synthetic'` |

## Valencia corridor extract (loaded)

| Data | Source | License / attribution |
|---|---|---|
| Drive network in `backend/data/osm/valencia_corridor.json` | OpenStreetMap via OSMnx 2.1.1, `network_type='drive'`, bbox west `-0.430`, south `39.415`, east `-0.372`, north `39.458` | **ODbL 1.0**. Credit "© OpenStreetMap contributors". This repository is public. Share-alike applies to that derived database. |
| `travel_s` | Edge length and OSMnx `add_edge_speeds` / `add_edge_travel_times` (fallback 30 km/h) | Derived from the OSM extract above. |
| `capacity` (veh/h) | Highway-class defaults in `backend/floodline/osm_network.py`, not observed traffic | Project default, documented in code. |
| Hospital and shelter coordinates | Public locations of Hospital Doctor Peset, Hospital La Fe, Alfafar, and Benetússer, snapped to the nearest intersection | Coordinates only. Bed counts and shelter capacity are synthetic. |
| District membership | Nearest of five town centroids (Paiporta, Alfafar, Sedaví, Benetússer, Valencia) | Not an official boundary. `population_source = 'synthetic'`. |
| Ambulance A-7 and the closure clock | Synthetic | MIT. The clock is **reconstructed**. |

`scripts/load_valencia.sh --refresh` contacts the Overpass API to rebuild the extract. The committed JSON is enough to load offline.

## Not loaded yet

| Data | Source | License / attribution |
|---|---|---|
| Flood extent | Copernicus EMS Rapid Mapping **EMSR773** (Valencia, October 2024) | **CC BY 4.0**. Credit "Copernicus Emergency Management Service (EMSR773)". |
| Population | INE or Ajuntament de València, or a clearly marked synthetic estimate | Check the page license before copying figures. The numbers in the graph are synthetic. |

No private or personal data is used. The October 2024 DANA floods killed more than 200 people. This project is a coordination exercise, not a reconstruction of any real person's movements.
