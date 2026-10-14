# Data sources and licenses

This first slice does **not** download OpenStreetMap or Copernicus data. The road graph is a small synthetic network written in `backend/floodline/network.py` so the closure story can be checked by hand.

| Data in this slice | Source | License |
|---|---|---|
| 10 intersections, 13 undirected roads, travel times, capacities | Written for Floodline | MIT, same as the code |
| Place names (Paiporta, Catarroja, Alfafar, Sedaví, Benetússer, Massanassa, Hospital Doctor Peset, Hospital La Fe) | Real place names, used as labels | Names only. Coordinates are approximate and chosen so the schematic map is readable. They are not an OSM extract. |
| Ambulance A-7, bed counts, the citizen report, the 14:01/14:02 clock | Synthetic | MIT. The clock is **reconstructed**. It is not the real DANA timeline. |
| District population `25000` on `Paiporta` | Synthetic estimate | Property `population_source = 'synthetic'` |

## Licenses reserved for a later, fuller city load

Not loaded in this slice. Listed so the attribution is already decided:

| Data | Source | License / attribution |
|---|---|---|
| Drive network, hospitals, shelters, admin boundaries | OpenStreetMap via OSMnx | **ODbL 1.0**. Credit "© OpenStreetMap contributors". Share-alike applies to a published derived database. |
| Flood extent | Copernicus EMS Rapid Mapping **EMSR773** (Valencia, October 2024) | **CC BY 4.0**. Credit "Copernicus Emergency Management Service (EMSR773)". |
| Population | INE or Ajuntament de València, or a clearly marked synthetic estimate | Check the page license before copying figures. |

No private or personal data is used. The October 2024 DANA floods killed more than 200 people. This project is a coordination exercise, not a reconstruction of any real person's movements.
