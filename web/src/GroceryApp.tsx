import { useState, type FormEvent } from "react";
import {
  demoPlannedDate,
  groceryDemo,
  rankGroceries,
  uniqueItems,
  validDate,
  type GrocerySuggestion,
  type GroceryTrip,
} from "./grocery";
import "./grocery.css";

const storageKey = "repeat-order:grocery-trips:v1";

function savedTrips(): GroceryTrip[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
    if (
      Array.isArray(value) &&
      value.every(
        (trip) =>
          trip &&
          typeof trip.id === "string" &&
          typeof trip.date === "string" &&
          validDate(trip.date) &&
          Array.isArray(trip.items) &&
          trip.items.length > 0 &&
          trip.items.every((item: unknown) => typeof item === "string"),
      )
    ) {
      return value;
    }
  } catch {
    // The page still works when persistent storage is unavailable.
  }
  return [];
}

function today(): string {
  const now = new Date();
  return [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((part) => String(part).padStart(2, "0"))
    .join("-");
}

function dateLabel(date: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(date));
}

export function GroceryApp() {
  const [trips, setTrips] = useState(savedTrips);
  const [useSample, setUseSample] = useState(trips.length === 0);
  const [planned, setPlanned] = useState(useSample ? demoPlannedDate : today());
  const [tripDate, setTripDate] = useState(today);
  const [items, setItems] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [tripError, setTripError] = useState("");
  const [persistError, setPersistError] = useState(false);
  const [suggestions, setSuggestions] = useState<GrocerySuggestion[] | null>(
    null,
  );
  const [selected, setSelected] = useState<string[]>([]);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "manual">(
    "idle",
  );
  const history = useSample ? groceryDemo : trips;
  const groceryList = (suggestions ?? [])
    .filter(({ item }) => selected.includes(item))
    .map(({ item }) => item)
    .join("\n");

  function clearSuggestions() {
    setSuggestions(null);
    setSelected([]);
    setCopyStatus("idle");
  }

  function chooseMode(sample: boolean) {
    if (sample === useSample) return;
    setUseSample(sample);
    setPlanned(sample ? demoPlannedDate : today());
    clearSuggestions();
  }

  function saveTrips(next: GroceryTrip[]) {
    setTrips(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
      setPersistError(false);
    } catch {
      setPersistError(true);
    }
    clearSuggestions();
  }

  function resetEditor() {
    setItems("");
    setTripDate(today());
    setEditing(null);
    setTripError("");
  }

  function addTrip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const names = uniqueItems(items.split("\n"));
    if (!validDate(tripDate) || names.length === 0) {
      setTripError("Enter a purchase date and at least one item.");
      return;
    }
    const trip = {
      id: editing ?? crypto.getRandomValues(new Uint32Array(4)).join("-"),
      date: tripDate,
      items: names,
    };
    saveTrips(
      editing
        ? trips.map((entry) => (entry.id === editing ? trip : entry))
        : [...trips, trip],
    );
    resetEditor();
  }

  function suggest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!validDate(planned)) return;
    setSuggestions(rankGroceries(history, planned));
    setSelected([]);
    setCopyStatus("idle");
  }

  async function copyList() {
    try {
      await navigator.clipboard.writeText(groceryList);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("manual");
    }
  }

  return (
    <>
      <header>
        <a className="wordmark" href={import.meta.env.BASE_URL}>
          re<span>/</span>order<span>.</span>
        </a>
        <nav aria-label="Demos">
          <a href={import.meta.env.BASE_URL}>Retail demo</a>
          <a href="?mode=grocery" aria-current="page">
            Grocery
          </a>
        </nav>
      </header>
      <main className="grocery-page">
        <section className="grocery-intro">
          <h1>Before your grocery trip</h1>
          <p>
            See what you might need to buy again. Pick the reminders you want
            and copy them into your shopping list.
          </p>
          <div
            className="grocery-mode"
            role="group"
            aria-label="Grocery history"
          >
            <button
              type="button"
              onClick={() => chooseMode(true)}
              aria-pressed={useSample}
            >
              Try sample
            </button>
            <button
              type="button"
              onClick={() => chooseMode(false)}
              aria-pressed={!useSample}
            >
              My trips
            </button>
          </div>
        </section>

        <div className="grocery-layout">
          <section className="grocery-panel" aria-labelledby="grocery-history">
            <h2 id="grocery-history">
              {useSample ? "Sample trips" : "Your past trips"}
            </h2>
            {useSample ? (
              <p className="grocery-note">
                Four fictional grocery trips. Select “My trips” to enter your
                own.
              </p>
            ) : (
              <p className="grocery-note">
                Your trips stay in this browser. Each entry should be a
                completed shopping trip.
              </p>
            )}
            {!useSample && (
              <form className="grocery-entry" onSubmit={addTrip}>
                <label>
                  Purchase date
                  <input
                    type="date"
                    value={tripDate}
                    required
                    onChange={(event) => setTripDate(event.target.value)}
                  />
                </label>
                <label>
                  Items bought
                  <textarea
                    rows={4}
                    placeholder={"Eggs\nGreek yogurt\nRice"}
                    value={items}
                    onChange={(event) => setItems(event.target.value)}
                    maxLength={8000}
                    aria-describedby="grocery-item-hint"
                  />
                </label>
                <p id="grocery-item-hint" className="grocery-note">
                  Type one item per line, or paste a list.
                </p>
                <div className="grocery-actions">
                  <button className="grocery-primary" type="submit">
                    {editing ? "Save trip" : "Add past trip"}
                  </button>
                  {editing && (
                    <button
                      className="grocery-secondary"
                      type="button"
                      onClick={resetEditor}
                    >
                      Cancel edit
                    </button>
                  )}
                </div>
                {tripError && (
                  <p className="grocery-note" role="alert">
                    {tripError}
                  </p>
                )}
              </form>
            )}
            {persistError && !useSample && (
              <p role="status" className="grocery-note">
                Browser storage is unavailable. These trips will last until you
                leave or reload.
              </p>
            )}
            {history.length > 0 && (
              <details className="grocery-history">
                <summary>
                  View {history.length}{" "}
                  {history.length === 1 ? "trip" : "trips"}
                </summary>
                <ul className="grocery-trips">
                  {[...history]
                    .sort((a, b) =>
                      a.date < b.date ? 1 : a.date > b.date ? -1 : 0,
                    )
                    .map((trip) => (
                      <li key={trip.id}>
                        <strong>
                          <time dateTime={trip.date}>
                            {dateLabel(trip.date)}
                          </time>
                        </strong>
                        <p>{trip.items.join(" · ")}</p>
                        {!useSample && (
                          <div className="grocery-actions">
                            <button
                              className="grocery-secondary"
                              type="button"
                              aria-label={`Edit trip for ${dateLabel(trip.date)}`}
                              onClick={() => {
                                setEditing(trip.id);
                                setTripDate(trip.date);
                                setItems(trip.items.join("\n"));
                                setTripError("");
                              }}
                            >
                              Edit
                            </button>
                            <button
                              className="grocery-secondary"
                              type="button"
                              aria-label={`Remove trip for ${dateLabel(trip.date)}`}
                              onClick={() => {
                                saveTrips(
                                  trips.filter((entry) => entry.id !== trip.id),
                                );
                                if (editing === trip.id) resetEditor();
                              }}
                            >
                              Remove
                            </button>
                          </div>
                        )}
                      </li>
                    ))}
                </ul>
              </details>
            )}
          </section>

          <section className="grocery-panel" aria-labelledby="grocery-next">
            <h2 id="grocery-next">Your next trip</h2>
            <form className="grocery-plan" onSubmit={suggest}>
              <label>
                Planned shopping date
                <input
                  type="date"
                  required
                  value={planned}
                  onChange={(event) => {
                    setPlanned(event.target.value);
                    clearSuggestions();
                  }}
                />
              </label>
              <button
                type="submit"
                className="grocery-primary"
                disabled={!history.length}
              >
                Suggest items
              </button>
            </form>
            <p className="grocery-note">
              Only trips dated before your planned shopping date count.
            </p>
            {suggestions !== null && (
              <div className="grocery-results">
                {suggestions.length === 0 ? (
                  <p role="status">No items to suggest for this date.</p>
                ) : (
                  <>
                    <h3>Pick what to buy</h3>
                    <ul className="grocery-suggestions">
                      {suggestions.map(({ item, reason }) => (
                        <li key={item}>
                          <label>
                            <input
                              type="checkbox"
                              checked={selected.includes(item)}
                              onChange={(event) => {
                                setSelected(
                                  event.target.checked
                                    ? [...selected, item]
                                    : selected.filter((name) => name !== item),
                                );
                                setCopyStatus("idle");
                              }}
                            />
                            <span>
                              <strong>{item}</strong>
                              <small>{reason}</small>
                            </span>
                          </label>
                        </li>
                      ))}
                    </ul>
                    <button
                      type="button"
                      className="grocery-primary"
                      disabled={!groceryList}
                      onClick={copyList}
                    >
                      Copy shopping list
                    </button>
                    {copyStatus === "copied" && (
                      <p role="status" className="grocery-note">
                        Shopping list copied.
                      </p>
                    )}
                    {copyStatus === "manual" && (
                      <label className="grocery-manual">
                        Select and copy your list
                        <textarea
                          readOnly
                          value={groceryList}
                          onFocus={(event) => event.target.select()}
                        />
                      </label>
                    )}
                  </>
                )}
              </div>
            )}
          </section>
        </div>
      </main>
    </>
  );
}
