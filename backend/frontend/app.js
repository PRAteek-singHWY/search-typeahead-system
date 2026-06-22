// Frontend logic for the typeahead UI.
//
// Things worth being able to explain:
//  1. Debouncing: we don't fire a request on every keystroke. We wait until the user
//     pauses (120ms). Without it, typing "iphone" sends 6 requests in a burst; with
//     it, usually one. This is the key client-side optimisation for a typeahead and
//     satisfies the "avoid unnecessary backend calls" requirement.
//  2. Submitting a search (Enter / Search button / clicking a suggestion) calls
//     POST /search. That records the query (batched write + trending), and we show
//     the dummy {"message":"Searched"} response.
//  3. Keyboard navigation: ArrowUp/ArrowDown move the highlight, Enter selects.
//  4. Loading and error states are shown in the status tag.

const searchBox = document.getElementById("search-box");
const searchBtn = document.getElementById("search-btn");
const suggestionsList = document.getElementById("suggestions");
const statusTag = document.getElementById("status-tag");
const responseBox = document.getElementById("response-box");
const trendingList = document.getElementById("trending-list");

let debounceTimer = null;
let currentSuggestions = [];
let activeIndex = -1; // which suggestion is highlighted for keyboard nav

function ranking() {
  return document.querySelector('input[name="ranking"]:checked').value;
}

// --- Typing: debounce, then fetch suggestions ---
searchBox.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fetchSuggestions, 120);
});

// Re-query when the ranking mode changes, so the effect is visible immediately.
document.querySelectorAll('input[name="ranking"]').forEach((radio) =>
  radio.addEventListener("change", fetchSuggestions)
);

// --- Keyboard navigation + submit ---
searchBox.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveActive(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    moveActive(-1);
  } else if (event.key === "Enter") {
    // If a suggestion is highlighted, submit that; otherwise submit the typed text.
    const term = activeIndex >= 0 ? currentSuggestions[activeIndex].query : searchBox.value;
    commitSearch(term);
  }
});

searchBtn.addEventListener("click", () => commitSearch(searchBox.value));

async function fetchSuggestions() {
  const query = searchBox.value.trim();
  if (!query) {
    renderSuggestions([]);
    statusTag.textContent = "";
    return;
  }

  statusTag.textContent = "loading…";
  try {
    const res = await fetch(
      `/suggest?q=${encodeURIComponent(query)}&ranking=${ranking()}&limit=10`
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentSuggestions = data.suggestions;
    renderSuggestions(data.suggestions);
    // Show where the result came from (cache vs store) and the owning cache node —
    // makes the caching + consistent hashing visible during a demo.
    statusTag.textContent = data.suggestions.length
      ? `from ${data.source} · node ${data.owner_node}`
      : "no matches";
  } catch (err) {
    statusTag.textContent = `error: ${err.message}`;
    renderSuggestions([]);
  }
}

function renderSuggestions(suggestions) {
  activeIndex = -1;
  suggestionsList.innerHTML = "";
  suggestions.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "suggestion";
    li.setAttribute("role", "option");
    li.innerHTML = `<span class="term">${escapeHtml(s.query)}</span>
                    <span class="count">${s.count.toLocaleString()}</span>`;
    li.addEventListener("click", () => commitSearch(s.query));
    li.addEventListener("mouseenter", () => setActive(i));
    suggestionsList.appendChild(li);
  });
}

function moveActive(delta) {
  if (currentSuggestions.length === 0) return;
  let next = activeIndex + delta;
  if (next < 0) next = currentSuggestions.length - 1;
  if (next >= currentSuggestions.length) next = 0;
  setActive(next);
}

function setActive(index) {
  const items = suggestionsList.querySelectorAll(".suggestion");
  items.forEach((el) => el.classList.remove("active"));
  activeIndex = index;
  if (index >= 0 && items[index]) items[index].classList.add("active");
}

// --- Submit a search: record it, show the dummy response, refresh trending ---
async function commitSearch(term) {
  const clean = term.trim();
  if (!clean) return;

  searchBox.value = clean;
  renderSuggestions([]);
  statusTag.textContent = "";

  try {
    const res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: clean }),
    });
    const data = await res.json();
    responseBox.hidden = false;
    responseBox.textContent = `Server response: ${JSON.stringify(data)}  (searched "${clean}")`;
  } catch (err) {
    responseBox.hidden = false;
    responseBox.textContent = `error submitting search: ${err.message}`;
  }

  refreshTrending();
}

async function refreshTrending() {
  try {
    const res = await fetch("/trending?k=10");
    const data = await res.json();
    trendingList.innerHTML = "";
    for (const item of data.trending) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="term">${escapeHtml(item.query)}</span>
                      <span class="count">${item.count}</span>`;
      trendingList.appendChild(li);
    }
  } catch (err) {
    /* trending is non-critical; ignore transient errors */
  }
}

// Prevent any query text from being interpreted as HTML.
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Load trending once, then keep it fresh.
refreshTrending();
setInterval(refreshTrending, 5000);
