"use client";

import { useEffect, useState } from "react";
import { loadMe, saveMe, type FamilyMember, type SavedPlace } from "@/lib/storage";

type Props = {
  pose: { lat: number; lon: number };
  onGo: (place: SavedPlace) => void;
};

export function MePanel({ pose, onGo }: Props) {
  const [places, setPlaces] = useState<SavedPlace[]>([]);
  const [family, setFamily] = useState<FamilyMember[]>([]);
  const [placeName, setPlaceName] = useState("");
  const [person, setPerson] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = loadMe();
    setPlaces(stored.places);
    setFamily(stored.family);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    saveMe({ places, family });
  }, [places, family, ready]);

  function addPlace() {
    const name = placeName.trim();
    if (!name) return;
    setPlaces((current) => [
      { id: crypto.randomUUID(), name, lat: pose.lat, lon: pose.lon },
      ...current,
    ]);
    setPlaceName("");
  }

  function addPerson() {
    const name = person.trim();
    if (!name) return;
    setFamily((current) => [
      { id: crypto.randomUUID(), name, status: "safe", at: new Date().toISOString() },
      ...current,
    ]);
    setPerson("");
  }

  return (
    <div className="stack">
      <p className="kicker">Me</p>
      <h2 className="display tight">Your people</h2>
      <p className="lede">Places and check-ins stay on this phone. Nothing here is sent to the graph.</p>
      <label className="field" htmlFor="place-name">
        Saved places
        <input
          id="place-name"
          value={placeName}
          placeholder="Name this spot"
          onChange={(event) => setPlaceName(event.target.value)}
        />
      </label>
      <button type="button" className="btn" onClick={addPlace} disabled={placeName.trim().length === 0}>
        Save current spot
      </button>
      <ul className="plain">
        {places.map((place) => (
          <li key={place.id}>
            <button type="button" className="place" onClick={() => onGo(place)}>
              <strong>{place.name}</strong>
              <span>
                {place.lat.toFixed(4)}, {place.lon.toFixed(4)}
              </span>
            </button>
            <button
              type="button"
              className="text-btn"
              onClick={() => setPlaces((current) => current.filter((item) => item.id !== place.id))}
            >
              Remove
            </button>
          </li>
        ))}
        {places.length === 0 && <li className="empty">No saved places yet.</li>}
      </ul>
      <label className="field" htmlFor="family-name">
        Family check-in
        <input
          id="family-name"
          value={person}
          placeholder="Name"
          onChange={(event) => setPerson(event.target.value)}
        />
      </label>
      <button type="button" className="btn ghost" onClick={addPerson} disabled={person.trim().length === 0}>
        Add
      </button>
      <ul className="plain">
        {family.map((member) => (
          <li key={member.id} className="kin">
            <div>
              <strong>{member.name}</strong>
              <span>{member.status === "safe" ? "Safe" : "Needs help"}</span>
            </div>
            <button
              type="button"
              className={member.status === "safe" ? "chip ok" : "chip help"}
              onClick={() =>
                setFamily((current) =>
                  current.map((item) =>
                    item.id === member.id
                      ? {
                          ...item,
                          status: item.status === "safe" ? "help" : "safe",
                          at: new Date().toISOString(),
                        }
                      : item,
                  ),
                )
              }
            >
              {member.status === "safe" ? "Mark needs help" : "Mark safe"}
            </button>
          </li>
        ))}
        {family.length === 0 && <li className="empty">No one on the list yet.</li>}
      </ul>
    </div>
  );
}
