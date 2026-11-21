(() => {
  "use strict";

  const PRESETS = [
    { id: "W2519887557", label: "GCN" },
    { id: "W2624431344", label: "GraphSAGE" },
    { id: "W2766453196", label: "GAT" },
    { id: "W2962756421", label: "node2vec" },
    { id: "W2127795553", label: "TransE" },
    { id: "W3010336026", label: "Knowledge Graphs (survey)" },
    { id: "W4395686609", label: "GraphRAG" },
    { id: "W7094459024", label: "Think-on-Graph" },
  ];

  const state = {
    paperId: null,
    paperData: null,
    related: [],
    lastDrawerInfo: null,
    cy: null,
  };

  // ---------- small DOM helpers (textContent only, never innerHTML with data) ----------

  function el(tag, props, children) {
    const node = document.createElement(tag);
    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on") && typeof value === "function") {
          node.addEventListener(key.slice(2), value);
        } else {
          node.setAttribute(key, value);
        }
      }
    }
    for (const child of children || []) {
      if (child == null) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function clearSearchBox() {
    const input = document.getElementById("search-input");
    const results = document.getElementById("search-results");
    const clearButton = document.getElementById("search-clear-button");
    if (input) input.value = "";
    if (results) clear(results);
    if (clearButton) clearButton.hidden = true;
  }

  function wireSearchClearButton(inputEl, resultsEl, buttonEl, onClear) {
    inputEl.addEventListener("input", () => {
      buttonEl.hidden = inputEl.value.length === 0;
    });
    const doClear = () => {
      inputEl.value = "";
      clear(resultsEl);
      buttonEl.hidden = true;
      if (onClear) onClear();
    };
    buttonEl.addEventListener("click", () => {
      doClear();
      inputEl.focus();
    });
    inputEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") doClear();
    });
  }

  // ---------- API ----------

  async function api(path) {
    const resp = await fetch(path);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      const err = new Error(body.detail || `request failed (${resp.status})`);
      err.status = resp.status;
      throw err;
    }
    return resp.json();
  }

  // ---------- header stats ----------

  async function loadHeaderStats() {
    const statsEl = document.getElementById("header-stats");
    try {
      const health = await api("/api/health");
      const s = health.stats;
      statsEl.textContent =
        `${s.papers.toLocaleString()} papers · ${s.edges.cites.toLocaleString()} citations · ` +
        `${s.authors.toLocaleString()} authors`;
    } catch (e) {
      statsEl.textContent = "offline data unavailable";
    }
  }

  // ---------- presets ----------

  function renderPresets() {
    const container = document.getElementById("preset-chips");
    clear(container);
    for (const preset of PRESETS) {
      container.appendChild(
        el(
          "button",
          { class: "preset-chip", type: "button", onclick: () => { clearSearchBox(); navigateTo(preset.id); } },
          [preset.label]
        )
      );
    }
  }

  // ---------- search / autocomplete ----------

  function setupSearch(inputEl, resultsEl, onPick) {
    let debounceTimer = null;
    let activeIndex = -1;
    let currentResults = [];

    function renderResults(results) {
      currentResults = results;
      activeIndex = -1;
      clear(resultsEl);
      for (const [i, r] of results.entries()) {
        const item = el("li", { role: "option", id: `opt-${i}`, onclick: () => pick(r) }, [
          el("span", { class: "search-result-title", text: r.title }),
          el("span", { class: "search-result-meta", text: `${r.year || "n.d."} · ${r.venue || ""}` }),
        ]);
        resultsEl.appendChild(item);
      }
    }

    function pick(r) {
      clear(resultsEl);
      inputEl.value = r.title;
      onPick(r);
    }

    function updateActive() {
      [...resultsEl.children].forEach((child, i) => {
        if (i === activeIndex) child.setAttribute("aria-selected", "true");
        else child.removeAttribute("aria-selected");
      });
      if (activeIndex >= 0) {
        resultsEl.children[activeIndex].scrollIntoView({ block: "nearest" });
      }
    }

    inputEl.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      const q = inputEl.value.trim();
      if (!q) {
        clear(resultsEl);
        return;
      }
      debounceTimer = setTimeout(async () => {
        try {
          const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
          renderResults(data.results);
        } catch (e) {
          clear(resultsEl);
        }
      }, 200);
    });

    inputEl.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        activeIndex = Math.min(activeIndex + 1, currentResults.length - 1);
        updateActive();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        updateActive();
      } else if (ev.key === "Enter") {
        if (activeIndex >= 0 && currentResults[activeIndex]) {
          pick(currentResults[activeIndex]);
        }
      } else if (ev.key === "Escape") {
        clear(resultsEl);
      }
    });
  }

  // ---------- paper panel ----------

  function renderSkeleton() {
    document.getElementById("paper-empty").hidden = true;
    const content = document.getElementById("paper-content");
    content.hidden = false;
    document.getElementById("paper-title").textContent = "";
    const meta = document.getElementById("paper-meta");
    clear(meta);
    meta.appendChild(el("span", { class: "skeleton", style: "width: 60%" }));
    meta.appendChild(el("span", { class: "skeleton", style: "width: 40%" }));
  }

  function renderError(message) {
    const content = document.getElementById("paper-content");
    content.hidden = true;
    const empty = document.getElementById("paper-empty");
    empty.hidden = false;
    clear(empty);
    empty.appendChild(el("p", { class: "error-message", text: message }));
  }

  function renderPaper(paper) {
    document.getElementById("paper-empty").hidden = true;
    document.getElementById("paper-content").hidden = false;
    document.getElementById("paper-title").textContent = paper.title;

    const meta = document.getElementById("paper-meta");
    clear(meta);
    meta.textContent = paper.venue ? `${paper.year || "n.d."} · ${paper.venue}` : String(paper.year || "n.d.");

    const AUTHOR_PREVIEW_COUNT = 5;
    const authorsEl = document.getElementById("paper-authors");
    clear(authorsEl);
    authorsEl.appendChild(document.createTextNode("Authors: "));
    const appendAuthorLink = (a, i) => {
      if (i > 0) authorsEl.appendChild(document.createTextNode(", "));
      authorsEl.appendChild(el("a", { onclick: () => openAuthor(a.id), text: a.name }));
    };
    const shown = paper.authors.slice(0, AUTHOR_PREVIEW_COUNT);
    const rest = paper.authors.slice(AUTHOR_PREVIEW_COUNT);
    shown.forEach(appendAuthorLink);
    if (rest.length) {
      authorsEl.appendChild(document.createTextNode(", "));
      const moreButton = el("button", {
        class: "authors-more-button",
        type: "button",
        text: `+ ${rest.length} more`,
      });
      authorsEl.appendChild(moreButton);
      moreButton.addEventListener("click", () => {
        moreButton.remove();
        rest.forEach((a) => {
          authorsEl.appendChild(document.createTextNode(", "));
          authorsEl.appendChild(el("a", { onclick: () => openAuthor(a.id), text: a.name }));
        });
      });
    }

    const topicsEl = document.getElementById("paper-topics");
    clear(topicsEl);
    if (paper.topics.length) {
      topicsEl.appendChild(document.createTextNode("Topics: "));
      paper.topics.forEach((t, i) => {
        if (i > 0) topicsEl.appendChild(document.createTextNode(", "));
        topicsEl.appendChild(el("a", { onclick: () => openTopic(t.id), text: t.name }));
      });
    }

    const linksEl = document.getElementById("paper-links");
    clear(linksEl);
    if (paper.doi) {
      const doiText = paper.doi.replace(/^https:\/\/doi\.org\//, "");
      linksEl.appendChild(el("a", { href: paper.doi, target: "_blank", rel: "noopener", text: doiText }));
    }
    if (paper.arxiv_id) {
      linksEl.appendChild(
        el("a", { href: `https://arxiv.org/abs/${paper.arxiv_id}`, target: "_blank", rel: "noopener", text: "arXiv" })
      );
    }

    const citationsEl = document.getElementById("paper-citations");
    citationsEl.textContent =
      `${paper.worldwide_citations.toLocaleString()} citations worldwide · ` +
      `${paper.in_set_citations.toLocaleString()} within this dataset`;
  }

  async function openAuthor(authorId) {
    const panel = document.getElementById("author-panel");
    const nameEl = document.getElementById("author-name");
    const body = document.getElementById("author-body");
    panel.hidden = false;
    resetGraphInteractionState();
    nameEl.textContent = "Loading…";
    clear(body);
    try {
      const data = await api(`/api/author/${authorId}`);
      const authorName = state.paperData?.authors.find((a) => a.id === authorId)?.name || "Author";
      nameEl.textContent = authorName;
      clear(body);

      body.appendChild(el("h3", { text: `Papers in this dataset (${data.papers.length})` }));
      body.appendChild(
        el(
          "ul",
          {},
          data.papers.map((p) =>
            el("li", {}, [
              el("a", {
                onclick: () => { panel.hidden = true; navigateTo(p.id); },
                text: `${p.title} (${p.year || "n.d."})`,
              }),
            ])
          )
        )
      );

      body.appendChild(el("h3", { text: `Co-authors (${data.co_authors.length})` }));
      body.appendChild(
        el(
          "ul",
          {},
          data.co_authors.map((c) => el("li", { text: `${c.name} — ${c.shared_papers} shared paper${c.shared_papers === 1 ? "" : "s"}` }))
        )
      );
    } catch (e) {
      nameEl.textContent = "Author";
      clear(body);
      body.appendChild(el("div", { class: "error-message", text: "Could not load this author." }));
    }
  }

  async function openTopic(topicId) {
    const panel = document.getElementById("topic-panel");
    const nameEl = document.getElementById("topic-name");
    const body = document.getElementById("topic-body");
    panel.hidden = false;
    resetGraphInteractionState();
    const topicName = state.paperData?.topics.find((t) => t.id === topicId)?.name || "Topic";
    nameEl.textContent = `Key papers in: ${topicName}`;
    clear(body);
    body.appendChild(el("div", { class: "loading-state", text: "Loading…" }));
    try {
      const data = await api(`/api/topic/${topicId}/key-papers`);
      clear(body);
      if (!data.results.length) {
        body.appendChild(el("div", { class: "empty-state", text: "No key papers found for this topic." }));
        return;
      }
      body.appendChild(
        el(
          "ul",
          {},
          data.results.map((p) =>
            el("li", {}, [
              el("a", {
                onclick: () => { panel.hidden = true; navigateTo(p.id); },
                text: `${p.title} (${p.year || "n.d."}) — ${p.in_set_citations} in-set citations`,
              }),
            ])
          )
        )
      );
    } catch (e) {
      clear(body);
      body.appendChild(el("div", { class: "error-message", text: "Could not load key papers for this topic." }));
    }
  }

  // ---------- related panel ----------

  function scoreBar(label, value, cssClass) {
    const pct = Math.max(0, Math.min(1, value)) * 100;
    return el("div", { class: "score-bar-row" }, [
      el("span", { class: "score-bar-label", text: label }),
      el("span", { class: "score-bar-track" }, [el("span", { class: `score-bar-fill ${cssClass}`, style: `width:${pct}%` })]),
      el("span", { class: "score-value", text: value.toFixed(2) }),
    ]);
  }

  function similarityTier(value) {
    if (value >= 0.66) return "very similar topic";
    if (value >= 0.33) return "similar topic";
    return "related topic";
  }

  function reasonText(r) {
    const parts = [];
    if (r.shared_references_count > 0) {
      parts.push(`Shares ${r.shared_references_count} reference${r.shared_references_count === 1 ? "" : "s"}`);
    }
    if (r.co_citing_count > 0) {
      parts.push(`cited together by ${r.co_citing_count} paper${r.co_citing_count === 1 ? "" : "s"}`);
    }
    parts.push(similarityTier(r.similarity));
    return parts.join(" · ");
  }

  function renderRelated(results) {
    const list = document.getElementById("related-list");
    clear(list);
    if (!results.length) {
      list.appendChild(el("li", { class: "empty-state", text: "No related papers found for this paper yet." }));
      return;
    }
    for (const r of results) {
      const titleChildren = [el("a", { onclick: () => { clearSearchBox(); navigateTo(r.id); }, text: r.title })];
      if (!r.found_via.includes("similarity")) {
        titleChildren.push(el("span", { class: "badge-graph", text: "Found by graph" }));
      }
      const card = el("li", { class: "related-card", id: `related-card-${r.id}` }, [
        el("div", { class: "related-card-title" }, titleChildren),
        el("div", { class: "related-card-year", text: String(r.year || "n.d.") }),
        el("div", { class: "related-card-reason", text: reasonText(r) }),
        scoreBar("overall", r.score, "overall"),
        scoreBar("coupling", r.coupling, "coupling"),
        scoreBar("co-citation", r.cocitation, "cocitation"),
        scoreBar("similarity", r.similarity, "similarity"),
      ]);

      const evidence = el("div", { class: "related-card-evidence", hidden: "" });
      card.appendChild(evidence);

      card.addEventListener("click", (ev) => {
        if (ev.target.tagName === "A") return;
        const wasHidden = evidence.hidden;
        evidence.hidden = !wasHidden;
        if (wasHidden && !evidence.dataset.built) {
          buildEvidence(evidence, r);
          evidence.dataset.built = "1";
        }
        highlightGraphNode(r.id, true);
      });
      card.addEventListener("mouseenter", () => highlightGraphNode(r.id, true));
      card.addEventListener("mouseleave", () => highlightGraphNode(r.id, false));

      list.appendChild(card);
    }
  }

  function buildEvidence(container, r) {
    clear(container);
    const refs = r.evidence.shared_references;
    const cociters = r.evidence.co_citing_papers;

    container.appendChild(el("div", { text: `Shared references (${refs.length}):` }));
    const refsList = el("ul", {}, refs.map((p) => el("li", {}, [el("a", { onclick: () => navigateTo(p.id), text: p.title })])));
    container.appendChild(refsList);

    container.appendChild(el("div", { text: `Papers citing both (${cociters.length}):` }));
    const cociteList = el(
      "ul",
      {},
      cociters.map((p) => el("li", {}, [el("a", { onclick: () => navigateTo(p.id), text: p.title })]))
    );
    container.appendChild(cociteList);
  }

  function highlightRelatedCard(paperId, on) {
    const card = document.getElementById(`related-card-${paperId}`);
    if (card) card.classList.toggle("highlighted", on);
  }

  function highlightGraphNode(paperId, on) {
    highlightRelatedCard(paperId, on);
    setGraphFocus(paperId, on);
  }

  // ---------- graph ----------

  const GRAPH_TOP_N = 8;
  const GRAPH_LABEL_MAX_CHARS = 30;
  const CENTER_CONCENTRIC_VALUE = 1000;

  function truncateLabel(title) {
    if (title.length <= GRAPH_LABEL_MAX_CHARS) return title;
    return title.slice(0, GRAPH_LABEL_MAX_CHARS - 1).trimEnd() + "…";
  }

  function setGraphFocus(cyNodeId, on) {
    if (!state.cy) return;
    const node = state.cy.getElementById(cyNodeId);
    if (!node || !node.length) return;
    if (on) {
      const neighborhood = node.closedNeighborhood();
      state.cy.elements().difference(neighborhood).addClass("faded");
      node.addClass("hovered");
    } else {
      state.cy.elements().removeClass("faded");
      node.removeClass("hovered");
    }
  }

  function clearGraphFocus() {
    if (!state.cy) return;
    state.cy.elements().removeClass("faded").removeClass("hovered");
  }

  function resetGraphInteractionState() {
    // An overlay opening on top of the graph canvas can cover a hovered node
    // without Cytoscape ever seeing the mouse actually leave it (the canvas
    // just stops getting mouse events), which otherwise leaves it faded or
    // highlighted after the overlay closes. Call this whenever an overlay
    // opens or closes so the graph is always left in a clean state.
    clearGraphFocus();
    const tooltip = document.getElementById("graph-tooltip");
    if (tooltip) tooltip.hidden = true;
  }

  function highlightPathInGraph(path) {
    if (!state.cy) return;
    state.cy.elements().removeClass("path-highlight");
    for (const p of path) {
      const node = state.cy.getElementById(p.id);
      if (node && node.length) node.addClass("path-highlight");
    }
    for (let i = 0; i < path.length - 1; i++) {
      const a = path[i].id;
      const b = path[i + 1].id;
      state.cy
        .edges()
        .filter((e) => (e.source().id() === a && e.target().id() === b) || (e.source().id() === b && e.target().id() === a))
        .addClass("path-highlight");
    }
  }

  function buildLegendLineSwatch(color, kind) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "26");
    svg.setAttribute("height", "10");
    svg.setAttribute("class", "legend-line-swatch");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "1");
    line.setAttribute("y1", "5");
    line.setAttribute("x2", kind === "cites" ? "18" : "25");
    line.setAttribute("y2", "5");
    line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", kind === "cites" ? "1.5" : "3.5");
    svg.appendChild(line);
    if (kind === "cites") {
      const arrow = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      arrow.setAttribute("points", "18,1 25,5 18,9");
      arrow.setAttribute("fill", color);
      svg.appendChild(arrow);
    }
    return svg;
  }

  let graphResizeObserverSetup = false;

  function setupGraphResizeHandling() {
    if (graphResizeObserverSetup) return;
    graphResizeObserverSetup = true;
    const refit = () => {
      if (state.cy) {
        state.cy.resize();
        state.cy.fit(undefined, 30);
      }
    };
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(refit);
      observer.observe(document.getElementById("cy"));
    }
    window.addEventListener("resize", refit);
  }

  function renderGraph(paper, related, citesEdges) {
    const container = document.getElementById("cy");
    const styleOf = getComputedStyle(document.documentElement);
    const nodeColor = styleOf.getPropertyValue("--node-color").trim();
    const edgeRelated = styleOf.getPropertyValue("--edge-related").trim();
    const edgeCites = styleOf.getPropertyValue("--edge-cites").trim();
    const textColor = styleOf.getPropertyValue("--text").trim();
    const accent = styleOf.getPropertyValue("--accent").trim();
    const panelBg = styleOf.getPropertyValue("--panel-bg").trim();

    const topN = related.slice(0, GRAPH_TOP_N);
    const shownIds = new Set([paper.id, ...topN.map((r) => r.id)]);

    const nodes = [
      {
        data: { id: paper.id, label: truncateLabel(paper.title), fullTitle: paper.title, score: CENTER_CONCENTRIC_VALUE },
        classes: "center",
      },
    ];
    const edges = [];
    for (const r of topN) {
      nodes.push({ data: { id: r.id, label: truncateLabel(r.title), fullTitle: r.title, score: r.score } });
      edges.push({
        data: { id: `related-${paper.id}-${r.id}`, source: paper.id, target: r.id, width: 2 + r.score * 6 },
        classes: "related-edge",
      });
    }
    for (const e of citesEdges || []) {
      if (!shownIds.has(e.from_id) || !shownIds.has(e.to_id)) continue;
      edges.push({
        data: { id: `cites-${e.from_id}-${e.to_id}`, source: e.from_id, target: e.to_id },
        classes: "cites-edge",
      });
    }

    if (state.cy) state.cy.destroy();
    state.cy = window.cytoscape({
      container,
      elements: { nodes, edges },
      userZoomingEnabled: false,
      userPanningEnabled: true,
      style: [
        {
          selector: "node",
          style: {
            "background-color": nodeColor,
            label: "data(label)",
            color: textColor,
            "font-size": 9,
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 8,
            "text-wrap": "ellipsis",
            "text-max-width": "90px",
            "text-background-color": panelBg,
            "text-background-opacity": 0.85,
            "text-background-shape": "roundrectangle",
            "text-background-padding": "2px",
            width: 20,
            height: 20,
          },
        },
        { selector: "node.center", style: { width: 34, height: 34, "font-size": 11, "font-weight": "bold" } },
        { selector: "node.hovered", style: { "border-width": 3, "border-color": accent } },
        { selector: ".faded", style: { opacity: 0.15 } },
        { selector: "node.path-highlight", style: { "border-width": 4, "border-color": accent } },
        {
          selector: "edge.path-highlight",
          style: { "line-color": accent, "target-arrow-color": accent, width: 4, opacity: 1 },
        },
        {
          selector: "edge.related-edge",
          style: {
            width: "data(width)",
            "line-color": edgeRelated,
            "curve-style": "straight",
            "target-arrow-shape": "none",
            "source-arrow-shape": "none",
          },
        },
        {
          selector: "edge.cites-edge",
          style: {
            width: 1,
            "line-color": edgeCites,
            opacity: 0.55,
            "curve-style": "unbundled-bezier",
            "control-point-distances": [16],
            "control-point-weights": [0.5],
            "target-arrow-shape": "triangle",
            "target-arrow-color": edgeCites,
            "arrow-scale": 0.6,
          },
        },
      ],
      layout: {
        name: "concentric",
        concentric: (n) => n.data("score"),
        levelWidth: () => 1,
        minNodeSpacing: 55,
        padding: 30,
        fit: true,
      },
    });

    const citesToggle = document.getElementById("show-citations-toggle");
    state.cy.elements(".cites-edge").style("display", citesToggle.checked ? "element" : "none");
    const tooltip = document.getElementById("graph-tooltip");

    setupGraphResizeHandling();
    state.cy.resize();

    document.getElementById("reset-view-button").onclick = () => {
      state.cy.fit(undefined, 30);
    };

    // Ctrl/Cmd + wheel zooms the graph (centered on the cursor); a plain
    // wheel scrolls the page instead of fighting it for the scroll gesture.
    container.onwheel = (ev) => {
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
      const rect = container.getBoundingClientRect();
      state.cy.zoom({
        level: state.cy.zoom() * factor,
        renderedPosition: { x: ev.clientX - rect.left, y: ev.clientY - rect.top },
      });
    };

    container.onmouseleave = () => {
      clearGraphFocus();
      tooltip.hidden = true;
    };

    state.cy.on("mouseover", "node", (ev) => {
      const id = ev.target.id();
      setGraphFocus(id, true);
      if (id !== paper.id) highlightRelatedCard(id, true);
      tooltip.textContent = ev.target.data("fullTitle");
      const pos = ev.target.renderedPosition();
      tooltip.style.left = `${pos.x + 12}px`;
      tooltip.style.top = `${pos.y + 12}px`;
      tooltip.hidden = false;
    });
    state.cy.on("mousemove", "node", (ev) => {
      const pos = ev.target.renderedPosition();
      tooltip.style.left = `${pos.x + 12}px`;
      tooltip.style.top = `${pos.y + 12}px`;
    });
    state.cy.on("mouseout", "node", (ev) => {
      const id = ev.target.id();
      setGraphFocus(id, false);
      if (id !== paper.id) highlightRelatedCard(id, false);
      tooltip.hidden = true;
    });
    state.cy.on("tap", "node", (ev) => {
      const id = ev.target.id();
      if (id !== paper.id) { clearSearchBox(); navigateTo(id); }
    });

    const legend = document.getElementById("graph-legend");
    clear(legend);
    legend.appendChild(el("span", {}, [buildLegendLineSwatch(edgeRelated, "related"), "related"]));
    legend.appendChild(el("span", {}, [buildLegendLineSwatch(edgeCites, "cites"), "cites"]));
  }

  // ---------- drawer ----------

  function setupDrawer() {
    const toggle = document.getElementById("drawer-toggle");
    const drawer = document.getElementById("drawer");
    toggle.addEventListener("click", () => {
      const willShow = drawer.hidden;
      drawer.hidden = !willShow;
      toggle.setAttribute("aria-expanded", String(willShow));
      if (willShow) renderDrawer();
    });
  }

  function renderDrawer() {
    const content = document.getElementById("drawer-content");
    if (!state.lastDrawerInfo) {
      content.textContent = "No query run yet.";
      return;
    }
    content.textContent = state.lastDrawerInfo;
  }

  // ---------- navigation ----------

  async function navigateTo(paperId) {
    window.location.hash = `#paper-${paperId}`;
  }

  async function loadPaper(paperId) {
    renderSkeleton();
    try {
      const paper = await api(`/api/paper/${paperId}`);
      state.paperId = paperId;
      state.paperData = paper;
      renderPaper(paper);

      const relatedResp = await api(`/api/related/${paperId}`);
      state.related = relatedResp.results;
      renderRelated(relatedResp.results);
      renderGraph(paper, relatedResp.results, relatedResp.cites_edges);

      state.lastDrawerInfo =
        `MATCH (p:Paper {id: $id}) ... \nrelated: vector SEARCH top-50 ∪ coupling top-50 ∪ co-citation top-50\n` +
        `paper query_ms=${paper.query_ms}  related query_ms=${relatedResp.query_ms}`;
      const drawer = document.getElementById("drawer");
      if (!drawer.hidden) renderDrawer();
    } catch (e) {
      if (e.status === 404) {
        renderError("No paper found with that id — try a different one, such as one of the presets.");
      } else if (e.status === 400) {
        renderError("That doesn't look like a valid paper id.");
      } else {
        renderError("Something went wrong loading this paper.");
      }
    }
  }

  function showRelatedEmpty() {
    const list = document.getElementById("related-list");
    clear(list);
    list.appendChild(el("li", { class: "empty-state", id: "related-empty", text: "Pick a paper to see related papers." }));
  }

  function resetToStart() {
    document.getElementById("paper-content").hidden = true;
    document.getElementById("paper-empty").hidden = false;
    if (state.cy) {
      state.cy.destroy();
      state.cy = null;
    }
    showRelatedEmpty();
    state.paperId = null;
    state.paperData = null;
    state.related = [];
  }

  function handleHashChange() {
    const match = /^#paper-(.+)$/.exec(window.location.hash);
    if (match) {
      loadPaper(match[1]);
    } else {
      resetToStart();
    }
  }

  // ---------- path tool ----------

  function setupPathTool() {
    const openBtn = document.getElementById("path-tool-button");
    const panel = document.getElementById("path-panel");
    const closeBtn = document.getElementById("path-close");
    const fromInput = document.getElementById("path-from");
    const toInput = document.getElementById("path-to");
    const chain = document.getElementById("path-chain");

    let fromId = null;
    let toId = null;

    const closePanel = () => {
      panel.hidden = true;
      resetGraphInteractionState();
    };

    openBtn.addEventListener("click", () => {
      panel.hidden = false;
      resetGraphInteractionState();
      fromInput.value = "";
      toInput.value = "";
      fromId = null;
      toId = null;
      clear(chain);
      document.getElementById("path-from-clear").hidden = true;
      document.getElementById("path-to-clear").hidden = true;
      fromInput.focus();
    });
    closeBtn.addEventListener("click", closePanel);
    panel.addEventListener("click", (ev) => {
      if (ev.target === panel) closePanel();
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !panel.hidden) closePanel();
    });

    wireSearchClearButton(
      fromInput,
      document.getElementById("path-from-results"),
      document.getElementById("path-from-clear"),
      () => { fromId = null; clear(chain); }
    );
    wireSearchClearButton(
      toInput,
      document.getElementById("path-to-results"),
      document.getElementById("path-to-clear"),
      () => { toId = null; clear(chain); }
    );

    setupSearch(fromInput, document.getElementById("path-from-results"), (r) => {
      fromId = r.id;
      maybeRunPath();
    });
    setupSearch(toInput, document.getElementById("path-to-results"), (r) => {
      toId = r.id;
      maybeRunPath();
    });

    async function maybeRunPath() {
      if (!fromId || !toId) return;
      clear(chain);
      chain.appendChild(el("div", { class: "loading-state", text: "Finding path…" }));
      try {
        const data = await api(`/api/path?from=${fromId}&to=${toId}`);
        clear(chain);
        if (!data.path) {
          chain.appendChild(
            el("div", { class: "path-note", text: "No path within 6 hops — try two papers in the same topic." })
          );
          return;
        }
        let hasOlderCitingNewer = false;
        data.path.forEach((p, i) => {
          if (i > 0) {
            chain.appendChild(el("span", { class: "path-arrow", text: data.directed ? "cites ↓" : "↓" }));
            const citing = data.path[i - 1];
            const cited = p;
            if (citing.year != null && cited.year != null && citing.year < cited.year) {
              hasOlderCitingNewer = true;
            }
          }
          chain.appendChild(
            el(
              "span",
              { class: "path-chip", onclick: () => { panel.hidden = true; navigateTo(p.id); } },
              [p.title, el("span", { class: "path-chip-year", text: String(p.year || "n.d.") })]
            )
          );
        });
        chain.appendChild(
          el("div", {
            class: "path-note",
            text: data.directed
              ? "Directed path: each arrow is a real citation, pointing from the citing paper down to the cited paper."
              : "No fully directed path exists — this chain follows citation edges in either direction.",
          })
        );
        if (hasOlderCitingNewer) {
          chain.appendChild(
            el("div", {
              class: "path-note",
              text: "Some sources update their versions over time, so a paper can cite newer work.",
            })
          );
        }
        highlightPathInGraph(data.path);
      } catch (e) {
        clear(chain);
        chain.appendChild(el("div", { class: "error-message", text: "Could not compute a path for these papers." }));
      }
    }
  }

  function setupOverlayPanel(panelId, closeId) {
    const panel = document.getElementById(panelId);
    const closePanel = () => {
      panel.hidden = true;
      resetGraphInteractionState();
    };
    document.getElementById(closeId).addEventListener("click", closePanel);
    panel.addEventListener("click", (ev) => {
      if (ev.target === panel) closePanel();
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !panel.hidden) closePanel();
    });
  }

  // ---------- keyboard shortcuts ----------

  function setupKeyboardShortcuts() {
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "/" && document.activeElement.tagName !== "INPUT") {
        ev.preventDefault();
        document.getElementById("search-input").focus();
      }
    });
  }

  // ---------- init ----------

  function init() {
    // Some browsers restore typed-but-not-submitted form values on a plain
    // reload, independent of autocomplete="off". Force the search box back
    // to its true empty starting state regardless of what got restored.
    clearSearchBox();

    renderPresets();
    loadHeaderStats();
    setupDrawer();
    setupPathTool();
    setupOverlayPanel("author-panel", "author-close");
    setupOverlayPanel("topic-panel", "topic-close");
    setupKeyboardShortcuts();

    const searchInput = document.getElementById("search-input");
    const searchClearButton = document.getElementById("search-clear-button");

    setupSearch(searchInput, document.getElementById("search-results"), (r) => {
      navigateTo(r.id);
    });

    searchInput.addEventListener("input", () => {
      searchClearButton.hidden = searchInput.value.length === 0;
    });
    searchClearButton.addEventListener("click", () => {
      clearSearchBox();
      searchInput.focus();
    });
    searchInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") clearSearchBox();
    });

    document.getElementById("home-link").addEventListener("click", (ev) => {
      ev.preventDefault();
      clearSearchBox();
      window.location.hash = "";
      resetToStart();
    });

    document.getElementById("try-gat-button").addEventListener("click", () => {
      clearSearchBox();
      navigateTo("W2766453196");
    });

    document.getElementById("show-citations-toggle").addEventListener("change", (ev) => {
      if (state.cy) {
        state.cy.elements(".cites-edge").style("display", ev.target.checked ? "element" : "none");
      }
    });

    window.addEventListener("hashchange", handleHashChange);
    handleHashChange();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
